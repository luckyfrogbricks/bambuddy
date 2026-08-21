"""Unit tests for the Spoolman-mode side of the barcode feature.

Covers the read mapping (`_extract_linked_codes` and the barcode/is_refill/
linked_codes outputs of `_map_spoolman_spool` in `_spoolman_helpers.py`) and
`SpoolmanClient.find_spool_by_barcode` — Spoolman has no native barcode
field, so everything round-trips through the spool's extra dict as
JSON-encoded values under bambu_barcode / bambu_linked_codes /
bambu_barcode_is_refill (see the writes in `routes/spoolman_inventory.py`).
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.api.routes._spoolman_helpers import _extract_linked_codes, _map_spoolman_spool
from backend.app.services.spoolman import SpoolmanClient

MINIMAL_SPOOL = {
    "id": 1,
    "filament": {
        "material": "PLA",
        "name": "PLA Basic",
        "color_hex": "FF0000",
        "weight": 1000.0,
        "vendor": {"name": "Bambu Lab"},
    },
    "used_weight": 250.0,
    "archived": False,
    "registered": "2024-01-01T00:00:00Z",
}


class TestExtractLinkedCodes:
    def test_excludes_primary_barcode(self):
        raw = json.dumps(
            [
                {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
            ]
        )
        result = _extract_linked_codes({"bambu_linked_codes": raw}, "6938936716785")
        assert result == [{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}]

    def test_missing_key_returns_empty(self):
        assert _extract_linked_codes({}, None) == []

    def test_malformed_json_returns_empty(self):
        assert _extract_linked_codes({"bambu_linked_codes": "not json"}, None) == []

    def test_non_list_json_returns_empty(self):
        assert _extract_linked_codes({"bambu_linked_codes": '{"code": "X"}'}, None) == []

    def test_non_dict_items_and_missing_codes_are_skipped(self):
        raw = json.dumps(["bare-string", {"kind": "sku"}, {"code": ""}, {"code": 42}, {"code": "OK", "kind": "sku"}])
        result = _extract_linked_codes({"bambu_linked_codes": raw}, None)
        assert result == [{"code": "OK", "kind": "sku", "is_refill": False}]

    def test_kind_defaults_to_gtin_and_is_refill_coerced_to_bool(self):
        raw = json.dumps([{"code": "6938936716786", "is_refill": 1}])
        result = _extract_linked_codes({"bambu_linked_codes": raw}, None)
        assert result == [{"code": "6938936716786", "kind": "gtin", "is_refill": True}]


class TestMapSpoolmanSpoolBarcode:
    def test_no_barcode_extra_reads_back_as_none(self):
        result = _map_spoolman_spool(MINIMAL_SPOOL)
        assert result["barcode"] is None
        assert result["linked_codes"] == []
        assert result["is_refill"] is False

    def test_barcode_read_from_extra(self):
        """Spoolman has no native barcode field; it's stored JSON-encoded under
        extra.bambu_barcode (same pattern as bambu_slicer_filament/bambu_color_name)."""
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_barcode": json.dumps("6938936716785")}}
        assert _map_spoolman_spool(spool)["barcode"] == "6938936716785"

    def test_linked_codes_read_from_extra_excluding_primary(self):
        """extra.bambu_linked_codes stores every cross-referenced sibling code
        (see _resolve_linked_codes_json in routes/spoolman_inventory.py) —
        the primary barcode itself must be excluded since it's already shown
        via the `barcode` field."""
        spool = {
            **MINIMAL_SPOOL,
            "extra": {
                "bambu_barcode": json.dumps("6938936716785"),
                "bambu_linked_codes": json.dumps(
                    [
                        {"code": "6938936716785", "kind": "gtin", "is_refill": False},
                        {"code": "6938936716786", "kind": "gtin", "is_refill": True},
                        {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
                    ]
                ),
            },
        }
        result = _map_spoolman_spool(spool)
        assert result["linked_codes"] == [
            {"code": "6938936716786", "kind": "gtin", "is_refill": True},
            {"code": "ALZMNTABS01", "kind": "sku", "is_refill": False},
        ]

    def test_linked_codes_tolerates_malformed_json(self):
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_linked_codes": "not json"}}
        assert _map_spoolman_spool(spool)["linked_codes"] == []

    def test_is_refill_parsed_from_json_encoded_bool(self):
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_barcode_is_refill": "true"}}
        assert _map_spoolman_spool(spool)["is_refill"] is True
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_barcode_is_refill": "false"}}
        assert _map_spoolman_spool(spool)["is_refill"] is False

    def test_is_refill_tolerates_raw_bool_and_garbage(self):
        # A raw (non-JSON-stringified) bool sneaking into extra still parses…
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_barcode_is_refill": True}}
        assert _map_spoolman_spool(spool)["is_refill"] is True
        # …and non-JSON strings fall back to a case-insensitive truthy check.
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_barcode_is_refill": "True"}}
        assert _map_spoolman_spool(spool)["is_refill"] is True
        spool = {**MINIMAL_SPOOL, "extra": {"bambu_barcode_is_refill": "garbage"}}
        assert _map_spoolman_spool(spool)["is_refill"] is False


class TestFindSpoolByBarcode:
    """SpoolmanClient.find_spool_by_barcode — 'the user's own inventory' for
    barcode resolution when Spoolman mode is active."""

    @pytest.fixture
    def client(self):
        return SpoolmanClient("http://localhost:7912")

    async def test_matches_primary_barcode_with_cached_spools(self, client):
        """Reads the JSON-encoded extra.bambu_barcode value; a supplied cache
        must suppress the API fetch."""
        cached = [
            {"id": 1, "extra": {"bambu_barcode": json.dumps("6938936716785")}},
            {"id": 2, "extra": {"bambu_barcode": json.dumps("12345678905")}},
        ]
        with patch.object(client, "get_all_spools", AsyncMock()) as mock_get:
            result = await client.find_spool_by_barcode("6938936716785", cached_spools=cached)
        assert result["id"] == 1
        mock_get.assert_not_called()

    async def test_fetches_including_archived_when_no_cache(self, client):
        """A repeat scan must resolve even if the original spool was later
        archived — matching the local-inventory lookup's behavior."""
        mock_spools = [{"id": 1, "extra": {"bambu_barcode": json.dumps("6938936716785")}}]
        with patch.object(client, "get_all_spools", AsyncMock(return_value=mock_spools)) as mock_get:
            result = await client.find_spool_by_barcode("6938936716785")
        assert result["id"] == 1
        mock_get.assert_called_once_with(allow_archived=True)

    async def test_no_match_returns_none(self, client):
        cached = [{"id": 1, "extra": {"bambu_barcode": '"999"'}}]
        assert await client.find_spool_by_barcode("6938936716785", cached_spools=cached) is None

    async def test_bare_numeric_string_still_matches(self, client):
        """Our writers always json.dumps a string, but a hand-edited extra
        field holding an unquoted digit string json-decodes to an int — it
        must coerce back to a string and match, not silently never resolve."""
        cached = [{"id": 1, "extra": {"bambu_barcode": "6938936716785"}}]
        result = await client.find_spool_by_barcode("6938936716785", cached_spools=cached)
        assert result["id"] == 1

    async def test_ignores_spools_without_extra(self, client):
        cached = [{"id": 1, "extra": {}}, {"id": 2}, {"id": 3, "extra": None}]
        assert await client.find_spool_by_barcode("6938936716785", cached_spools=cached) is None

    async def test_most_recently_registered_wins(self, client):
        """When multiple spools share a barcode, the most recently registered
        one wins."""
        cached = [
            {
                "id": 1,
                "extra": {"bambu_barcode": json.dumps("6938936716785")},
                "registered": "2024-01-01T00:00:00+00:00",
            },
            {
                "id": 2,
                "extra": {"bambu_barcode": json.dumps("6938936716785")},
                "registered": "2024-06-01T00:00:00+00:00",
            },
        ]
        result = await client.find_spool_by_barcode("6938936716785", cached_spools=cached)
        assert result["id"] == 2

    async def test_matches_linked_sibling_code(self, client):
        """A scan of a sibling GTIN/SKU (never itself scanned/typed) must still
        resolve via extra.bambu_linked_codes, so repeat scans of any
        cross-referenced code hit this spool too (see _resolve_linked_codes_json
        in routes/spoolman_inventory.py)."""
        cached = [
            {
                "id": 1,
                "extra": {
                    "bambu_barcode": json.dumps("6938936716785"),
                    "bambu_linked_codes": json.dumps([{"code": "ALZMNTABS01", "kind": "sku", "is_refill": False}]),
                },
            }
        ]
        result = await client.find_spool_by_barcode("ALZMNTABS01", cached_spools=cached)
        assert result["id"] == 1

    async def test_ignores_malformed_linked_codes(self, client):
        cached = [{"id": 1, "extra": {"bambu_linked_codes": "not json"}}]
        assert await client.find_spool_by_barcode("ALZMNTABS01", cached_spools=cached) is None

    async def test_bare_string_barcode_extra_still_matches(self, client):
        """Tolerate a bambu_barcode written without JSON encoding (manual edits
        via the Spoolman UI) — when the value isn't valid JSON the raw string
        is compared directly."""
        cached = [{"id": 1, "extra": {"bambu_barcode": "ALZMNTABS01"}}]
        result = await client.find_spool_by_barcode("ALZMNTABS01", cached_spools=cached)
        assert result["id"] == 1
