"""Unit tests for the shared barcode resolution + persistence service.

`backend/app/services/barcode_resolver.py` is the one engine every barcode
path shares: `external_all_codes` is THE gate for external (OFD /
SpoolmanDB-Community) lookups, `resolve_barcode` is the own-inventory-first
resolution chain, and `persist_spool_codes` / `persist_barcode_codes_for_spool`
are the one funnel that stores the scanned/typed code (`is_primary=True`) plus
every cross-referenced sibling as `SpoolCode` rows, deduped on
(spool_id, code).

Persistence tests run against a real SQLite engine (not a MagicMock DB) so the
actual SQL — including the delete-then-insert replacement semantics and the
(spool_id, code) dedupe — is what's exercised.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload

from backend.app.models.spool import Spool
from backend.app.models.spool_code import SpoolCode
from backend.app.services.barcode_resolver import (
    barcode_lookup_enabled,
    external_all_codes,
    persist_barcode_codes_for_spool,
    persist_spool_codes,
    resolve_barcode,
    resolve_codes_for_barcode,
)

ENABLED: dict[str, str] = {}  # missing key defaults to enabled
DISABLED = {"barcode_lookup_enabled": "false"}


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Spool.__table__.create)
        await conn.run_sync(SpoolCode.__table__.create)
    yield eng
    await eng.dispose()


async def _insert_spool(session: AsyncSession, spool_id: int, **overrides) -> None:
    fields = {
        "material": "PLA",
        "label_weight": 1000,
        "core_weight": 250,
        "weight_used": 0,
        "weight_used_baseline": 0,
        "weight_locked": False,
    }
    fields.update(overrides)
    session.add(Spool(id=spool_id, **fields))
    await session.commit()


async def _codes_for(session: AsyncSession, spool_id: int) -> list[SpoolCode]:
    result = await session.execute(select(SpoolCode).where(SpoolCode.spool_id == spool_id))
    return list(result.scalars().all())


@contextmanager
def _forbid_external():
    """Patch every external client function to fail the test if called at all —
    the strong form of 'no external activity' used by the toggle-gating and
    own-inventory-priority tests."""
    boom = AssertionError("external barcode lookup must not be called")
    with ExitStack() as stack:
        for target in (
            "backend.app.services.ofd_client.lookup",
            "backend.app.services.ofd_client.lookup_article",
            "backend.app.services.spoolmandb_community_client.lookup",
            "backend.app.services.spoolmandb_community_client.lookup_sku",
        ):
            stack.enter_context(patch(target, new=AsyncMock(side_effect=boom)))
        yield


@contextmanager
def _patch_external(ofd=None, ofd_article=None, smdb=None, smdb_sku=None):
    with ExitStack() as stack:
        mocks = {}
        for name, target, value in (
            ("ofd", "backend.app.services.ofd_client.lookup", ofd),
            ("ofd_article", "backend.app.services.ofd_client.lookup_article", ofd_article),
            ("smdb", "backend.app.services.spoolmandb_community_client.lookup", smdb),
            ("smdb_sku", "backend.app.services.spoolmandb_community_client.lookup_sku", smdb_sku),
        ):
            mock = AsyncMock(return_value=value)
            stack.enter_context(patch(target, new=mock))
            mocks[name] = mock
        yield mocks


class TestBarcodeLookupEnabled:
    def test_defaults_to_enabled(self):
        assert barcode_lookup_enabled({}) is True

    def test_false_disables(self):
        assert barcode_lookup_enabled(DISABLED) is False

    def test_explicit_true_enables(self):
        assert barcode_lookup_enabled({"barcode_lookup_enabled": "true"}) is True


class TestExternalAllCodes:
    async def test_disabled_setting_returns_none_without_any_lookup(self):
        """THE toggle gate (review-5's second bug): with barcode_lookup_enabled
        off, external_all_codes must return None before touching either client —
        on a first-ever offline instance the OFD/tarball timeouts would
        otherwise block spool saves for minutes."""
        with _forbid_external():
            assert await external_all_codes("6938936716785", "gtin", DISABLED) is None

    async def test_gtin_kind_routes_to_gtin_lookups(self):
        with _patch_external() as mocks:
            assert await external_all_codes("6938936716785", "gtin", ENABLED) is None
        mocks["ofd"].assert_awaited_once_with("6938936716785")
        mocks["smdb"].assert_awaited_once_with("6938936716785")
        mocks["ofd_article"].assert_not_called()
        mocks["smdb_sku"].assert_not_called()

    async def test_sku_kind_routes_to_sku_lookups(self):
        with _patch_external() as mocks:
            assert await external_all_codes("ALZMNTABS01", "sku", ENABLED) is None
        mocks["ofd_article"].assert_awaited_once_with("ALZMNTABS01")
        mocks["smdb_sku"].assert_awaited_once_with("ALZMNTABS01")
        mocks["ofd"].assert_not_called()
        mocks["smdb"].assert_not_called()

    async def test_ofd_only_hit_returns_its_fields_and_codes(self):
        ofd_hit = (
            {"material": "PETG", "brand": "Overture"},
            [{"code": "12345678905", "kind": "gtin", "is_refill": False}],
        )
        with _patch_external(ofd=ofd_hit):
            result = await external_all_codes("12345678905", "gtin", ENABLED)

        assert result is not None
        fields, source, all_codes = result
        assert source == "ofd"
        assert fields["material"] == "PETG"
        assert [c["code"] for c in all_codes] == ["12345678905"]

    async def test_both_direct_hits_merge_fields_and_union_codes_without_probing(self):
        """When both databases resolve the code directly, OFD's values win where
        both are set, SpoolmanDB-Community fills OFD's gaps, all_codes dedupes
        on code, and no sibling probing happens."""
        ofd_hit = (
            {"material": "PLA", "brand": "Sunlu", "nozzle_temp_min": None},
            [{"code": "6938936716785", "kind": "gtin", "is_refill": False}],
        )
        smdb_hit = (
            {"material": "PLA+", "nozzle_temp_min": 190},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        with _patch_external(ofd=ofd_hit, smdb=smdb_hit) as mocks:
            result = await external_all_codes("6938936716785", "gtin", ENABLED)

        fields, source, all_codes = result
        assert source == "ofd"
        assert fields["material"] == "PLA"  # OFD's value wins over SpoolmanDB's
        assert fields["nozzle_temp_min"] == 190  # gap filled from SpoolmanDB
        assert {c["code"] for c in all_codes} == {"6938936716785", "ALZMNTABS01"}
        mocks["ofd_article"].assert_not_called()
        mocks["smdb_sku"].assert_not_called()

    async def test_sibling_probe_fills_missing_fields_from_other_database(self):
        """A hit in one database probes its sibling codes against the *other*
        database to recover cross-referenced fields and codes (the enrichment
        that makes 'either database knows this product' good enough)."""
        ofd_hit = (
            {"material": "PLA", "brand": "Sunlu"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        smdb_probe = (
            {"nozzle_temp_min": 190, "nozzle_temp_max": 220},
            [
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                {"code": "6938936716786", "kind": "gtin", "is_refill": True},
            ],
        )
        with _patch_external(ofd=ofd_hit, smdb_sku=smdb_probe) as mocks:
            result = await external_all_codes("6938936716785", "gtin", ENABLED)

        fields, source, all_codes = result
        assert source == "ofd"
        assert fields["nozzle_temp_min"] == 190
        assert fields["nozzle_temp_max"] == 220
        mocks["smdb_sku"].assert_awaited_once_with("ALZMNTABS01")
        assert {c["code"] for c in all_codes} == {"6938936716785", "ALZMNTABS01", "6938936716786"}

    async def test_one_client_erroring_degrades_to_other_hit(self):
        smdb_hit = ({"material": "PLA"}, [{"code": "6938936716785", "kind": "gtin", "is_refill": False}])
        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(side_effect=RuntimeError("unreachable"))),
            patch(
                "backend.app.services.spoolmandb_community_client.lookup",
                new=AsyncMock(return_value=smdb_hit),
            ),
            patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
        ):
            result = await external_all_codes("6938936716785", "gtin", ENABLED)

        assert result is not None
        assert result[1] == "spoolmandb-community"

    async def test_both_miss_returns_none(self):
        with _patch_external():
            assert await external_all_codes("111111111117", "gtin", ENABLED) is None


class TestResolveBarcode:
    async def test_own_inventory_hit_skips_external_and_returns_all_codes(self, engine):
        """A code already stored as a SpoolCode row resolves from the local
        tables with zero external calls, returning the owning spool's fields
        and every code stored on that spool."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1, material="ASA", brand="Polymaker")
            session.add(SpoolCode(spool_id=1, code="6938936716785", kind="gtin", is_primary=True))
            session.add(SpoolCode(spool_id=1, code="ALZMNTABS01", kind="sku", is_refill=True))
            await session.commit()

            with _forbid_external():
                fields, source, all_codes = await resolve_barcode(session, "6938936716785", "gtin", ENABLED)

        assert source == "inventory"
        assert fields["material"] == "ASA"
        assert fields["brand"] == "Polymaker"
        by_code = {c["code"]: c for c in all_codes}
        assert set(by_code) == {"6938936716785", "ALZMNTABS01"}
        assert by_code["ALZMNTABS01"]["is_refill"] is True

    async def test_most_recent_code_row_wins_when_two_spools_share_a_code(self, engine):
        """The (spool_id, code) unique constraint still allows the same code on
        several spools — resolution picks the most recently registered row,
        matching SpoolmanClient.find_spool_by_barcode's tie-break."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1, brand="Old")
            await _insert_spool(session, 2, brand="New")
            session.add(
                SpoolCode(
                    spool_id=1, code="6938936716785", kind="gtin", is_primary=True, created_at=datetime(2024, 1, 1)
                )
            )
            session.add(
                SpoolCode(
                    spool_id=2, code="6938936716785", kind="gtin", is_primary=True, created_at=datetime(2024, 6, 1)
                )
            )
            await session.commit()

            with _forbid_external():
                fields, source, _ = await resolve_barcode(session, "6938936716785", "gtin", ENABLED)

        assert source == "inventory"
        assert fields["brand"] == "New"

    async def test_spoolman_client_hit_resolves_from_spoolman_not_local_tables(self, engine):
        """With a Spoolman client supplied, 'own inventory' means Spoolman's
        spools (extra.bambu_barcode) — the local tables are not consulted and
        external lookups are skipped."""
        spoolman_spool = {
            "id": 7,
            "filament": {
                "material": "PLA",
                "name": "PLA Basic",
                "vendor": {"name": "Bambu Lab"},
                "color_hex": "FF0000",
            },
            "extra": {
                "bambu_barcode": '"6938936716785"',
                "bambu_linked_codes": '[{"code": "ALZMNTABS01", "kind": "sku", "is_refill": false}]',
            },
        }
        client = AsyncMock()
        client.find_spool_by_barcode = AsyncMock(return_value=spoolman_spool)

        async with AsyncSession(engine) as session:
            with _forbid_external():
                fields, source, all_codes = await resolve_barcode(
                    session, "6938936716785", "gtin", ENABLED, spoolman_client=client
                )

        client.find_spool_by_barcode.assert_awaited_once_with("6938936716785")
        assert source == "inventory"
        assert fields["material"] == "PLA"
        assert fields["brand"] == "Bambu Lab"
        assert [c["code"] for c in all_codes] == ["ALZMNTABS01"]

    async def test_spoolman_error_falls_back_to_external(self, engine):
        client = AsyncMock()
        client.find_spool_by_barcode = AsyncMock(side_effect=RuntimeError("unreachable"))
        ofd_hit = ({"material": "PETG"}, [{"code": "12345678905", "kind": "gtin", "is_refill": False}])

        async with AsyncSession(engine) as session:
            with _patch_external(ofd=ofd_hit):
                fields, source, _ = await resolve_barcode(
                    session, "12345678905", "gtin", ENABLED, spoolman_client=client
                )

        assert source == "ofd"
        assert fields["material"] == "PETG"

    async def test_no_match_anywhere_returns_empty(self, engine):
        async with AsyncSession(engine) as session:
            with _patch_external():
                assert await resolve_barcode(session, "111111111117", "gtin", ENABLED) == ({}, None, [])

    async def test_disabled_setting_returns_empty_without_external_calls(self, engine):
        async with AsyncSession(engine) as session:
            with _forbid_external():
                assert await resolve_barcode(session, "111111111117", "gtin", DISABLED) == ({}, None, [])


class TestPersistSpoolCodes:
    async def test_persists_primary_and_siblings(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[
                    {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                    {"code": "6938936716786", "kind": "gtin", "is_refill": True},
                    {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                ],
            )
            codes = await _codes_for(session, 1)

        by_code = {c.code: c for c in codes}
        assert set(by_code) == {"6938936716785", "6938936716786", "ALZMNTABS01"}
        assert by_code["6938936716785"].is_primary is True
        assert by_code["6938936716786"].is_primary is False
        assert by_code["6938936716786"].is_refill is True
        assert by_code["ALZMNTABS01"].kind == "sku"

    async def test_primary_is_refill_flag_is_stored(self, engine):
        # A user-linked / manually-typed code carries no DB refill signal, so the
        # caller (SpoolBuddy refill toggle) supplies primary_is_refill.
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[],
                primary_is_refill=True,
            )
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].is_primary is True
        assert codes[0].is_refill is True

    async def test_is_refill_property_reflects_primary_code(self, engine):
        # The Spool.is_refill read property (drives the UI "Refill" badge) must
        # mirror the primary SpoolCode's is_refill, and be False otherwise.
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await persist_spool_codes(
                session, spool_id=1, primary_code="R", primary_kind="gtin", all_codes=[], primary_is_refill=True
            )
            await _insert_spool(session, 2)
            await persist_spool_codes(
                session, spool_id=2, primary_code="W", primary_kind="gtin", all_codes=[], primary_is_refill=False
            )
            loaded = {}
            for sid in (1, 2):
                res = await session.execute(select(Spool).options(selectinload(Spool.codes)).where(Spool.id == sid))
                loaded[sid] = res.scalar_one()
            assert loaded[1].is_refill is True
            assert loaded[2].is_refill is False

    async def test_dedupes_against_existing_rows(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            session.add(SpoolCode(spool_id=1, code="6938936716785", kind="gtin", is_primary=True))
            await session.commit()

            await persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[{"code": "6938936716785", "kind": "gtin", "is_refill": False}],
            )
            codes = await _codes_for(session, 1)

        assert len(codes) == 1

    async def test_no_siblings_still_persists_primary(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await persist_spool_codes(session, spool_id=1, primary_code="ALZMNTABS01", primary_kind="sku", all_codes=[])
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].code == "ALZMNTABS01"
        assert codes[0].kind == "sku"
        assert codes[0].is_primary is True

    async def test_second_call_with_new_siblings_adds_only_new_rows(self, engine):
        """A later scan that discovers additional sibling codes for an already-
        persisted primary must add just the new rows, not duplicate existing ones."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await persist_spool_codes(
                session, spool_id=1, primary_code="6938936716785", primary_kind="gtin", all_codes=[]
            )
            await persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[
                    {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                    {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                ],
            )
            codes = await _codes_for(session, 1)

        assert {c.code for c in codes} == {"6938936716785", "ALZMNTABS01"}

    async def test_entries_without_code_are_skipped_and_kind_defaults_to_gtin(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            await persist_spool_codes(
                session,
                spool_id=1,
                primary_code="6938936716785",
                primary_kind="gtin",
                all_codes=[{"code": "", "kind": "sku"}, {"kind": "sku"}, {"code": "6938936716786"}],
            )
            codes = await _codes_for(session, 1)

        by_code = {c.code: c for c in codes}
        assert set(by_code) == {"6938936716785", "6938936716786"}
        assert by_code["6938936716786"].kind == "gtin"
        assert by_code["6938936716786"].is_refill is False


class TestResolveCodesForBarcode:
    async def test_classifies_and_returns_external_siblings(self):
        external = (
            {"material": "PLA"},
            "ofd",
            [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}],
        )
        with patch(
            "backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=external)
        ) as mock_external:
            code, kind, all_codes = await resolve_codes_for_barcode("06938936716785", ENABLED)

        # Raw scan is canonicalized (leading zero stripped) before the lookup.
        mock_external.assert_awaited_once_with("6938936716785", "gtin", ENABLED)
        assert (code, kind) == ("6938936716785", "gtin")
        assert [c["code"] for c in all_codes] == ["ALZMNTABS01"]

    async def test_external_failure_degrades_to_primary_alone(self):
        """A cross-reference exception must not lose the scanned code — the
        spool still gets created/imported with just its primary code."""
        with patch(
            "backend.app.services.barcode_resolver.external_all_codes",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            code, kind, all_codes = await resolve_codes_for_barcode("ALZMNTABS01", ENABLED)

        assert (code, kind) == ("ALZMNTABS01", "sku")
        assert all_codes == []


class TestPersistBarcodeCodesForSpool:
    async def test_no_barcode_is_a_no_op_on_fresh_spool(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch("backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock()) as mock_external:
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode=None, settings=ENABLED)
            mock_external.assert_not_called()
            assert await _codes_for(session, 1) == []

    async def test_persists_primary_plus_cross_referenced_siblings(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            external = (
                {"material": "PLA"},
                "ofd",
                [
                    {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                    {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                ],
            )
            with patch(
                "backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=external)
            ):
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode="06938936716785", settings=ENABLED)
            codes = await _codes_for(session, 1)

        assert {c.code for c in codes} == {"6938936716785", "ALZMNTABS01"}
        primary = next(c for c in codes if c.code == "6938936716785")
        assert primary.is_primary is True

    async def test_no_external_hit_still_persists_scanned_code_alone(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch("backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=None)):
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode="ALZMNTABS01", settings=ENABLED)
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].code == "ALZMNTABS01"
        assert codes[0].kind == "sku"

    async def test_cross_reference_failure_still_persists_primary_alone(self, engine):
        """An external-lookup exception must degrade to persisting just the
        scanned code, not lose it entirely (resolve_codes_for_barcode swallows
        the exception)."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch(
                "backend.app.services.barcode_resolver.external_all_codes",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode="ALZMNTABS01", settings=ENABLED)
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].code == "ALZMNTABS01"
        assert codes[0].kind == "sku"

    async def test_barcode_change_replaces_old_bundle(self, engine):
        """Delete-then-insert: editing barcode A -> B must remove A's row AND
        every sibling discovered for A, leaving exactly one is_primary row (B).
        Without the delete, both bundles coexisted and the stale barcode kept
        resolving on scan."""
        external_a = (
            {"material": "PLA"},
            "ofd",
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch(
                "backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=external_a)
            ):
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode="06938936716785", settings=ENABLED)
            assert {c.code for c in await _codes_for(session, 1)} == {"6938936716785", "ALZMNTABS01"}

            with patch("backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=None)):
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode="012345678905", settings=ENABLED)

            # SQLite reuses a deleted row's rowid on the next insert, so the
            # identity map could hand back a stale cached object for that PK.
            session.expire_all()
            codes = await _codes_for(session, 1)

        assert {c.code for c in codes} == {"12345678905"}
        assert codes[0].is_primary is True

    async def test_clearing_barcode_deletes_all_rows(self, engine):
        external = ({"material": "PLA"}, "ofd", [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}])
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch(
                "backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=external)
            ):
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode="06938936716785", settings=ENABLED)
            assert len(await _codes_for(session, 1)) == 2

            await persist_barcode_codes_for_spool(session, spool_id=1, barcode=None, settings=ENABLED)
            session.expire_all()
            assert await _codes_for(session, 1) == []

    async def test_disabled_toggle_persists_primary_without_touching_external_clients(self, engine):
        """The write-path half of the toggle gate: saving a spool with a barcode
        while lookups are disabled still stores the primary SpoolCode row, but
        never reaches OFD/SpoolmanDB-Community (the real external_all_codes
        gating logic runs here — only the clients themselves are patched)."""
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with _forbid_external():
                await persist_barcode_codes_for_spool(session, spool_id=1, barcode="06938936716785", settings=DISABLED)
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].code == "6938936716785"
        assert codes[0].kind == "gtin"
        assert codes[0].is_primary is True

    async def test_primary_is_refill_reaches_the_primary_row(self, engine):
        async with AsyncSession(engine) as session:
            await _insert_spool(session, 1)
            with patch("backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=None)):
                await persist_barcode_codes_for_spool(
                    session, spool_id=1, barcode="06938936716785", settings=ENABLED, primary_is_refill=True
                )
            codes = await _codes_for(session, 1)

        assert len(codes) == 1
        assert codes[0].is_primary is True
        assert codes[0].is_refill is True
