"""Integration tests for barcode persistence on the Spoolman inventory proxy.

Spoolman has no native barcode field, so create/update own the round-trip via
the spool's extra dict: the scanned/typed code under bambu_barcode, the
cross-referenced sibling bundle under bambu_linked_codes, and the refill
toggle under bambu_barcode_is_refill (all JSON-encoded, same pattern as
bambu_slicer_filament / bambu_color_name). The SpoolmanClient is mocked —
these tests pin what the routes write, not Spoolman itself; the external
OFD/SpoolmanDB-Community lookups are patched so nothing touches the network.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

SAMPLE_SPOOLMAN_SPOOL = {
    "id": 42,
    "filament": {
        "id": 7,
        "name": "PLA Basic",
        "material": "PLA",
        "color_hex": "FF0000",
        "weight": 1000,
        "vendor": {"id": 3, "name": "Bambu Lab"},
    },
    "remaining_weight": 750.0,
    "used_weight": 250.0,
    "location": "Printer1 - AMS A1",
    "comment": "test note",
    "first_used": "2024-01-01T00:00:00+00:00",
    "last_used": "2024-02-01T00:00:00+00:00",
    "registered": "2024-01-01T00:00:00+00:00",
    "archived": False,
    "price": None,
    "extra": {"tag": '"AABBCCDDEEFF0011AABBCCDDEEFF0011"'},
}

_EXTERNAL_TARGETS = (
    "backend.app.services.ofd_client.lookup",
    "backend.app.services.ofd_client.lookup_article",
    "backend.app.services.spoolmandb_community_client.lookup",
    "backend.app.services.spoolmandb_community_client.lookup_sku",
)


def _patch_external(ofd_result=None):
    return (
        patch("backend.app.services.ofd_client.lookup", new=AsyncMock(return_value=ofd_result)),
        patch("backend.app.services.ofd_client.lookup_article", new=AsyncMock(return_value=None)),
        patch("backend.app.services.spoolmandb_community_client.lookup", new=AsyncMock(return_value=None)),
        patch("backend.app.services.spoolmandb_community_client.lookup_sku", new=AsyncMock(return_value=None)),
    )


def _forbid_external():
    boom = AssertionError("external barcode lookup must not be called")
    return tuple(patch(target, new=AsyncMock(side_effect=boom)) for target in _EXTERNAL_TARGETS)


@pytest.fixture
async def spoolman_settings(db_session):
    """Create Spoolman settings in the database (enabled with URL)."""
    from backend.app.models.settings import Settings

    enabled_setting = Settings(key="spoolman_enabled", value="true")
    url_setting = Settings(key="spoolman_url", value="http://localhost:7912")
    db_session.add(enabled_setting)
    db_session.add(url_setting)
    await db_session.commit()
    return {"enabled": enabled_setting, "url": url_setting}


@pytest.fixture
def mock_spoolman_client():
    """Mock the Spoolman client with a sample spool."""
    mock_client = MagicMock()
    mock_client.base_url = "http://localhost:7912"
    mock_client.health_check = AsyncMock(return_value=True)
    mock_client.get_all_spools = AsyncMock(return_value=[SAMPLE_SPOOLMAN_SPOOL])
    mock_client.get_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.create_spool = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.update_spool_full = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.merge_spool_extra = AsyncMock(return_value=SAMPLE_SPOOLMAN_SPOOL)
    mock_client.find_or_create_filament = AsyncMock(return_value=7)
    mock_client.find_or_create_vendor = AsyncMock(return_value=3)
    mock_client.patch_filament = AsyncMock(return_value={"id": 7})
    mock_client.is_filament_shared = AsyncMock(return_value=False)
    mock_client.ensure_extra_field = AsyncMock(return_value=True)
    mock_client.get_distinct_locations = AsyncMock(return_value=[])

    with (
        patch(
            "backend.app.api.routes.spoolman_inventory.get_spoolman_client",
            AsyncMock(return_value=mock_client),
        ),
        patch(
            "backend.app.api.routes.spoolman_inventory.init_spoolman_client",
            AsyncMock(return_value=mock_client),
        ),
    ):
        yield mock_client


def _merged_extra(mock_client) -> dict:
    mock_client.merge_spool_extra.assert_called_once()
    return mock_client.merge_spool_extra.call_args.args[1]


class TestCreateWritesBarcodeExtras:
    async def test_create_writes_barcode_linked_codes_and_refill_flag(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """A create with a barcode registers all three extra fields and writes
        the scanned code, the cross-referenced sibling bundle, and the refill
        toggle to spool.extra."""
        ofd_hit = (
            {"material": "PLA"},
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ],
        )
        payload = {
            "material": "PLA",
            "label_weight": 1000,
            "weight_used": 0,
            "barcode": "6938936716785",
            "barcode_is_refill": True,
        }
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit)
        with p1, p2, p3, p4:
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        for field in ("bambu_barcode", "bambu_linked_codes", "bambu_barcode_is_refill"):
            mock_spoolman_client.ensure_extra_field.assert_any_call(field)
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_barcode"]) == "6938936716785"
        assert json.loads(extra_patch["bambu_barcode_is_refill"]) is True
        assert json.loads(extra_patch["bambu_linked_codes"]) == [
            {"code": "6938936716785", "kind": "gtin", "is_refill": False},
            {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
        ]

    async def test_create_without_external_hit_writes_barcode_and_default_refill(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "barcode": "6938936716785"}
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_barcode"]) == "6938936716785"
        assert json.loads(extra_patch["bambu_barcode_is_refill"]) is False
        # No cross-reference hit → no linked-codes entry at all on create.
        assert "bambu_linked_codes" not in extra_patch

    async def test_create_canonicalizes_leading_zero_barcode(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Spoolman-mode create must canonicalize exactly like the local-DB
        SpoolCreate: lookups always search the zero-stripped form, so storing
        a raw leading-zero EAN-13 in extra.bambu_barcode would make a repeat
        scan of this spool's own barcode never match it."""
        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "barcode": "06938936716785"}
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_barcode"]) == "6938936716785"

    async def test_create_with_lookup_disabled_still_writes_barcode_without_external_calls(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client, db_session
    ):
        """The barcode_lookup_enabled toggle gates Spoolman-mode writes too —
        saving must not download anything, but the scanned code still lands in
        extra.bambu_barcode."""
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="barcode_lookup_enabled", value="false"))
        await db_session.commit()

        payload = {"material": "PLA", "label_weight": 1000, "weight_used": 0, "barcode": "6938936716785"}
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            response = await async_client.post("/api/v1/spoolman/inventory/spools", json=payload)

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_barcode"]) == "6938936716785"
        assert "bambu_linked_codes" not in extra_patch


class TestUpdateResetsThenSetsBarcodeExtras:
    async def test_barcode_change_replaces_linked_codes_and_clears_refill_flag(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Reset-then-set: a barcode change must replace the linked-code bundle
        with the NEW barcode's cross-reference and clear the refill flag (the
        edit form has no refill toggle) — never leave the previous barcode's
        stale values behind. Mirrors persist_barcode_codes_for_spool's
        delete-then-insert in local mode."""
        ofd_hit_b = ({"material": "PETG"}, [{"code": "12345678905", "kind": "gtin", "is_refill": False}])
        p1, p2, p3, p4 = _patch_external(ofd_result=ofd_hit_b)
        with p1, p2, p3, p4:
            response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"barcode": "12345678905"})

        assert response.status_code == 200
        for field in ("bambu_barcode", "bambu_linked_codes", "bambu_barcode_is_refill"):
            mock_spoolman_client.ensure_extra_field.assert_any_call(field)
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_barcode"]) == "12345678905"
        assert json.loads(extra_patch["bambu_barcode_is_refill"]) is False
        assert json.loads(extra_patch["bambu_linked_codes"]) == [
            {"code": "12345678905", "kind": "gtin", "is_refill": False}
        ]

    async def test_barcode_change_without_external_hit_writes_empty_linked_codes(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """Unlike create (which just omits the key), update always writes
        bambu_linked_codes — an empty list when nothing cross-references — so a
        previous barcode's bundle can never survive the change."""
        p1, p2, p3, p4 = _patch_external()
        with p1, p2, p3, p4:
            response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"barcode": "12345678905"})

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_barcode"]) == "12345678905"
        assert json.loads(extra_patch["bambu_linked_codes"]) == []

    async def test_clearing_barcode_writes_empty_values_without_external_calls(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """An explicit empty-string barcode clears all three stored values, and
        clearing must not trigger any cross-reference lookup."""
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            response = await async_client.patch("/api/v1/spoolman/inventory/spools/42", json={"barcode": ""})

        assert response.status_code == 200
        extra_patch = _merged_extra(mock_spoolman_client)
        assert json.loads(extra_patch["bambu_barcode"]) == ""
        assert json.loads(extra_patch["bambu_linked_codes"]) == []
        assert json.loads(extra_patch["bambu_barcode_is_refill"]) is False

    async def test_omitting_barcode_skips_barcode_extra_write(
        self, async_client: AsyncClient, spoolman_settings, mock_spoolman_client
    ):
        """When barcode is absent from the PATCH body, the route must not touch
        the barcode extra fields at all (preserves any existing scanned value)."""
        p1, p2, p3, p4 = _forbid_external()
        with p1, p2, p3, p4:
            response = await async_client.patch(
                "/api/v1/spoolman/inventory/spools/42", json={"note": "no barcode here"}
            )

        assert response.status_code == 200
        barcode_calls = [
            c
            for c in mock_spoolman_client.ensure_extra_field.call_args_list
            if c.args and c.args[0] in ("bambu_barcode", "bambu_linked_codes", "bambu_barcode_is_refill")
        ]
        assert barcode_calls == []
