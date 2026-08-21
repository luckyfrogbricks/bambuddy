"""Integration coverage for SpoolCode persistence and barcode resolution on the
local-DB inventory endpoints — the write side of the multi-code barcode
architecture (see `persist_barcode_codes_for_spool` in
`services/barcode_resolver.py`) plus the GET /inventory/barcode/{barcode}
read path.

Every external-database call is patched so these tests never hit the
network; resolution/persistence run against the real FastAPI app and a real
SQLite DB (PR #1895's review specifically flagged MagicMock-DB tests as not
exercising the SpoolCode SQL at all).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.settings import Settings
from backend.app.models.spool_code import SpoolCode

pytestmark = pytest.mark.integration

_EXTERNAL_TARGETS = (
    "backend.app.services.ofd_client.lookup",
    "backend.app.services.ofd_client.lookup_article",
    "backend.app.services.spoolmandb_community_client.lookup",
    "backend.app.services.spoolmandb_community_client.lookup_sku",
)


def _patch_external(ofd_result=None, smdb_result=None):
    return (
        patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_result)),
        patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)),
        patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=smdb_result)),
        patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
    )


def _forbid_external():
    """Patches that fail the test outright if any external client is called —
    the strong form of 'no external activity' for the toggle-gating tests."""
    boom = AssertionError("external barcode lookup must not be called")
    return tuple(patch(target, new=AsyncMock(side_effect=boom)) for target in _EXTERNAL_TARGETS)


async def _codes_for(db_session: AsyncSession, spool_id: int) -> list[SpoolCode]:
    result = await db_session.execute(select(SpoolCode).where(SpoolCode.spool_id == spool_id))
    return list(result.scalars().all())


@pytest.fixture
async def lookup_disabled(db_session: AsyncSession):
    db_session.add(Settings(key="barcode_lookup_enabled", value="false"))
    await db_session.commit()


class TestCreateSpoolPersistsCodes:
    async def test_create_with_barcode_persists_cross_referenced_siblings(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        ofd_hit = (
            {"material": "PLA", "brand": "Sunlu"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["barcode"] == "6938936716785"

        codes = await _codes_for(db_session, body["id"])
        assert {c.code for c in codes} == {"6938936716785", "ALZMNTABS01"}
        primary = next(c for c in codes if c.code == "6938936716785")
        assert primary.is_primary is True

        # The persisted bundle surfaces in the POST response itself — the
        # route expires the pre-persist `codes` collection before building the
        # response, so the just-discovered siblings aren't reported as [].
        assert {c["code"] for c in body["linked_codes"]} == {"ALZMNTABS01"}

        # ...and through the read path.
        get_resp = await async_client.get(f"/api/v1/inventory/spools/{body['id']}")
        assert {c["code"] for c in get_resp.json()["linked_codes"]} == {"ALZMNTABS01"}

    async def test_create_without_barcode_persists_no_codes(self, async_client: AsyncClient, db_session: AsyncSession):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "label_weight": 1000},
            )

        assert resp.status_code == 200
        assert resp.json()["linked_codes"] == []
        assert await _codes_for(db_session, resp.json()["id"]) == []

    async def test_bulk_create_persists_codes_for_every_spool(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools/bulk",
                json={"spool": {"material": "PLA", "barcode": "ALZMNTABS01", "label_weight": 1000}, "quantity": 2},
            )

        assert resp.status_code == 200
        spools = resp.json()
        assert len(spools) == 2
        for spool in spools:
            codes = await _codes_for(db_session, spool["id"])
            assert len(codes) == 1
            assert codes[0].code == "ALZMNTABS01"
            assert codes[0].kind == "sku"

    async def test_bulk_create_resolves_shared_barcode_only_once(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """All spools in a batch share one barcode — the external
        cross-reference must be resolved once per batch, not once per spool."""
        with patch(
            "backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock(return_value=None)
        ) as mock_external:
            resp = await async_client.post(
                "/api/v1/inventory/spools/bulk",
                json={"spool": {"material": "PLA", "barcode": "ALZMNTABS01", "label_weight": 1000}, "quantity": 5},
            )

        assert resp.status_code == 200
        spools = resp.json()
        assert len(spools) == 5
        assert mock_external.await_count == 1
        for spool in spools:
            codes = await _codes_for(db_session, spool["id"])
            assert len(codes) == 1
            assert codes[0].code == "ALZMNTABS01"
            assert codes[0].is_primary is True


class TestUpdateSpoolPersistsCodes:
    async def test_setting_barcode_on_update_persists_codes(self, async_client: AsyncClient, db_session: AsyncSession):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "label_weight": 1000},
            )
        spool_id = create_resp.json()["id"]
        assert await _codes_for(db_session, spool_id) == []

        ofd_hit = ({"material": "PLA"}, [{"code": "6938936716785", "kind": "gtin", "is_refill": False}])
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            update_resp = await async_client.patch(
                f"/api/v1/inventory/spools/{spool_id}",
                json={"barcode": "06938936716785"},
            )

        assert update_resp.status_code == 200
        codes = await _codes_for(db_session, spool_id)
        assert {c.code for c in codes} == {"6938936716785"}

    async def test_updating_unrelated_field_does_not_reresolve_barcode(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """No barcode change → no external cross-reference call at all."""
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )
        spool_id = create_resp.json()["id"]

        with patch("backend.app.services.barcode_resolver.external_all_codes", new=AsyncMock()) as mock_external:
            update_resp = await async_client.patch(
                f"/api/v1/inventory/spools/{spool_id}",
                json={"note": "just a note update"},
            )

        assert update_resp.status_code == 200
        mock_external.assert_not_called()

    async def test_changing_barcode_replaces_old_codes_not_augments_them(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """A -> B must leave only B's bundle behind — A's row, and the row of
        any sibling code discovered for A, must be gone, not just B's rows
        added alongside them (which would leave two is_primary rows and let
        the stale barcode A keep resolving)."""
        ofd_hit_a = ({"material": "PLA"}, [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}])
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit_a)
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )
        spool_id = create_resp.json()["id"]
        codes = await _codes_for(db_session, spool_id)
        assert {c.code for c in codes} == {"6938936716785", "ALZMNTABS01"}

        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            update_resp = await async_client.patch(
                f"/api/v1/inventory/spools/{spool_id}",
                json={"barcode": "012345678905"},
            )
        assert update_resp.status_code == 200
        # The PATCH response reports B's bundle, not the previous barcode's —
        # the route expires the pre-persist `codes` collection before building
        # the response (same staleness trap as create).
        assert update_resp.json()["barcode"] == "12345678905"
        assert update_resp.json()["linked_codes"] == []

        # SQLite reuses a deleted row's rowid on the next insert (no
        # AUTOINCREMENT keyword on spool_code.id), so the old, now-replaced
        # row can come back with the exact same primary key as the new one.
        # db_session's identity map would otherwise hand back its stale
        # cached object for that PK instead of the freshly queried row.
        db_session.expire_all()
        codes = await _codes_for(db_session, spool_id)
        assert {c.code for c in codes} == {"12345678905"}
        assert codes[0].is_primary is True

        # The old barcode and its sibling no longer resolve from inventory.
        with (
            patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=None)) as mock_ofd,
            patch(
                "backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)
            ) as mock_ofd_article,
            patch(
                "backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)
            ) as mock_smdb,
            patch(
                "backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)
            ) as mock_smdb_sku,
        ):
            stale_resp = await async_client.get("/api/v1/inventory/barcode/6938936716785")
        assert stale_resp.json()["matched"] is False
        # "6938936716785" classifies as gtin, so only the gtin-side lookups
        # (not the SKU/article ones) are exercised on the external fallback.
        mock_ofd.assert_called_once()
        mock_ofd_article.assert_not_called()
        mock_smdb.assert_called_once()
        mock_smdb_sku.assert_not_called()

    async def test_clearing_barcode_removes_all_codes(self, async_client: AsyncClient, db_session: AsyncSession):
        ofd_hit = ({"material": "PLA"}, [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}])
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )
        spool_id = create_resp.json()["id"]
        assert len(await _codes_for(db_session, spool_id)) == 2

        update_resp = await async_client.patch(
            f"/api/v1/inventory/spools/{spool_id}",
            json={"barcode": None},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["barcode"] is None
        db_session.expire_all()
        assert await _codes_for(db_session, spool_id) == []


class TestWritePathToggleGating:
    """Review-5's second bug: with barcode_lookup_enabled off, saving a spool
    that carries a barcode must not download anything — but it must still
    succeed and persist the primary SpoolCode row. Every external client
    function is patched to raise if called, so any regression that re-adds a
    lookup to a write path fails loudly."""

    async def test_create_with_barcode_persists_primary_without_external_calls(
        self, async_client: AsyncClient, db_session: AsyncSession, lookup_disabled
    ):
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )

        assert resp.status_code == 200
        codes = await _codes_for(db_session, resp.json()["id"])
        assert len(codes) == 1
        assert codes[0].code == "6938936716785"
        assert codes[0].is_primary is True

    async def test_bulk_create_with_barcode_persists_primary_without_external_calls(
        self, async_client: AsyncClient, db_session: AsyncSession, lookup_disabled
    ):
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools/bulk",
                json={"spool": {"material": "PLA", "barcode": "06938936716785", "label_weight": 1000}, "quantity": 2},
            )

        assert resp.status_code == 200
        spools = resp.json()
        assert len(spools) == 2
        for spool in spools:
            codes = await _codes_for(db_session, spool["id"])
            assert len(codes) == 1
            assert codes[0].code == "6938936716785"
            assert codes[0].is_primary is True

    async def test_update_changing_barcode_persists_primary_without_external_calls(
        self, async_client: AsyncClient, db_session: AsyncSession, lookup_disabled
    ):
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "label_weight": 1000},
            )
            spool_id = create_resp.json()["id"]
            update_resp = await async_client.patch(
                f"/api/v1/inventory/spools/{spool_id}",
                json={"barcode": "ALZMNTABS01"},
            )

        assert update_resp.status_code == 200
        codes = await _codes_for(db_session, spool_id)
        assert len(codes) == 1
        assert codes[0].code == "ALZMNTABS01"
        assert codes[0].kind == "sku"
        assert codes[0].is_primary is True


class TestReadPathResolvesOwnInventoryThroughRealSql:
    """Exercises the actual resolve_barcode SQL against a real DB — the gap
    PR #1895's review flagged: driving the resolver against a MagicMock DB
    never exercises the SpoolCode query itself (nor the scan/persist
    classification agreement it depends on). This is also the exact regression
    case from the review: a UPC-A with a leading zero must resolve on repeat
    scan regardless of whether the raw or already-normalized form is scanned."""

    async def test_repeat_scan_of_leading_zero_upc_a_resolves_from_own_inventory(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "036000291452", "label_weight": 1000},
            )
        assert create_resp.status_code == 200
        assert create_resp.json()["barcode"] == "36000291452"  # stored, zero-stripped

        # Re-scan both the raw (as-scanned) and the already-stripped (as-stored)
        # forms — both must resolve from inventory with zero external calls.
        for barcode in ("036000291452", "36000291452"):
            f1, f2, f3, f4 = _forbid_external()
            with f1, f2, f3, f4:
                resp = await async_client.get(f"/api/v1/inventory/barcode/{barcode}")

            assert resp.status_code == 200
            body = resp.json()
            assert body["matched"] is True, f"barcode {barcode} failed to resolve: {body}"
            assert body["source"] == "inventory"

    async def test_repeat_scan_of_alphanumeric_sku_resolves_from_own_inventory(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "ALZMNTABS01", "label_weight": 1000},
            )
        assert create_resp.status_code == 200

        f1, f2, f3, f4 = _forbid_external()
        with f1, f2, f3, f4:
            resp = await async_client.get("/api/v1/inventory/barcode/ALZMNTABS01")

        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] is True
        assert body["source"] == "inventory"


class TestLookupBarcodeEndpoint:
    async def test_unknown_code_returns_matched_false_with_enabled_true(self, async_client: AsyncClient):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.get("/api/v1/inventory/barcode/111111111117")

        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["matched"] is False
        assert body["source"] is None
        assert body["material"] is None
        assert body["linked_codes"] == []

    async def test_disabled_setting_reports_enabled_false_and_skips_external(
        self, async_client: AsyncClient, lookup_disabled
    ):
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            resp = await async_client.get("/api/v1/inventory/barcode/111111111117")

        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["matched"] is False

    async def test_barcode_is_canonicalized_in_response(self, async_client: AsyncClient):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.get("/api/v1/inventory/barcode/0012345678905")

        assert resp.status_code == 200
        assert resp.json()["barcode"] == "12345678905"


class TestBarcodePathParamLengthLimit:
    """The GET /barcode/{barcode} path param caps at Spool.barcode's
    VARCHAR(64) — without it, an arbitrarily long path segment reaches
    classify_code and the external-lookup chain unbounded. This only takes
    effect through FastAPI's real request parsing, so it needs a real HTTP
    round-trip, not a direct call to the route function."""

    async def test_barcode_over_max_length_is_rejected(self, async_client: AsyncClient):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.get(f"/api/v1/inventory/barcode/{'1' * 65}")

        assert resp.status_code == 422

    async def test_barcode_at_max_length_is_accepted(self, async_client: AsyncClient):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.get(f"/api/v1/inventory/barcode/{'1' * 64}")

        assert resp.status_code == 200


class TestBarcodeIsRefill:
    """The write-only SpoolCreate.barcode_is_refill hint must land on the
    primary SpoolCode row and surface read-only as SpoolResponse.is_refill,
    while cross-referenced siblings keep their own per-code refill flags."""

    async def test_refill_hint_lands_on_primary_row_and_surfaces_in_response(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        ofd_hit = (
            {"material": "PLA"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "6938936716786", "kind": "gtin", "is_refill": True},
            ],
        )
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={
                    "material": "PLA",
                    "barcode": "06938936716785",
                    "barcode_is_refill": True,
                    "label_weight": 1000,
                },
            )

        assert create_resp.status_code == 200
        spool_id = create_resp.json()["id"]

        codes = await _codes_for(db_session, spool_id)
        primary = next(c for c in codes if c.is_primary)
        assert primary.code == "6938936716785"
        assert primary.is_refill is True

        get_resp = await async_client.get(f"/api/v1/inventory/spools/{spool_id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["is_refill"] is True
        # The Refill sibling keeps its own per-code flag in linked_codes.
        assert body["linked_codes"] == [{"code": "6938936716786", "kind": "gtin", "is_refill": True}]

    async def test_default_hint_leaves_is_refill_false(self, async_client: AsyncClient, db_session: AsyncSession):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )

        assert create_resp.status_code == 200
        spool_id = create_resp.json()["id"]
        get_resp = await async_client.get(f"/api/v1/inventory/spools/{spool_id}")
        assert get_resp.json()["is_refill"] is False

    async def test_bulk_create_applies_refill_hint_to_every_spool(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            resp = await async_client.post(
                "/api/v1/inventory/spools/bulk",
                json={
                    "spool": {
                        "material": "PLA",
                        "barcode": "06938936716785",
                        "barcode_is_refill": True,
                        "label_weight": 1000,
                    },
                    "quantity": 2,
                },
            )

        assert resp.status_code == 200
        for spool in resp.json():
            codes = await _codes_for(db_session, spool["id"])
            assert len(codes) == 1
            assert codes[0].is_primary is True
            assert codes[0].is_refill is True


class TestListAssignmentsWithLinkedCodes:
    """Regression for the #1895 review-5 500: SpoolAssignmentResponse is built
    by hand (model_validate) in the assignment routes, and pydantic v2 does NOT
    cascade a parent's from_attributes into nested BaseModel fields — so a
    spool that actually HAS sibling codes 500'd both POST /assignments and
    GET /assignments until LinkedCode gained its own from_attributes Config."""

    async def test_assignments_return_spool_linked_codes_shape(
        self, async_client: AsyncClient, db_session: AsyncSession, printer_factory
    ):
        ofd_hit = ({"material": "PLA"}, [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}])
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            create_resp = await async_client.post(
                "/api/v1/inventory/spools",
                json={"material": "PLA", "barcode": "06938936716785", "label_weight": 1000},
            )
        assert create_resp.status_code == 200
        spool_id = create_resp.json()["id"]

        printer = await printer_factory(name="H2D")

        mock_pm = MagicMock()
        mock_pm.get_status.return_value = None  # printer offline: no fingerprint, no MQTT
        mock_pm.get_client.return_value = None
        mock_pm.get_all_statuses.return_value = {}
        with patch("backend.app.services.printer_manager.printer_manager", mock_pm):
            assign_resp = await async_client.post(
                "/api/v1/inventory/assignments",
                json={"spool_id": spool_id, "printer_id": printer.id, "ams_id": 0, "tray_id": 0},
            )
            assert assign_resp.status_code == 200
            assert assign_resp.json()["spool"]["linked_codes"] == [
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}
            ]

            list_resp = await async_client.get("/api/v1/inventory/assignments")

        assert list_resp.status_code == 200
        assignments = list_resp.json()
        assert len(assignments) == 1
        assert assignments[0]["spool_id"] == spool_id
        assert assignments[0]["spool"]["linked_codes"] == [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}]
