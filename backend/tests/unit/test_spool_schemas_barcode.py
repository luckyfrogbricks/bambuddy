"""Schema validation tests for the spool `barcode` field.

Locks in the write-path canonicalization: a manually-typed barcode must
normalize to the same digits-only, leading-zeros-stripped form the
scan-to-add lookup already produces, so a later scan of the same physical
barcode matches the stored spool via the barcode resolver's own-inventory
check regardless of which UPC-A/EAN-13 form was typed or scanned.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.spool import SpoolCreate, SpoolResponse, SpoolUpdate, classify_code, normalize_barcode


class TestNormalizeBarcode:
    def test_strips_leading_zeros(self):
        assert normalize_barcode("0012345678905") == "12345678905"

    def test_strips_non_digit_characters(self):
        assert normalize_barcode("012-345-678-905") == "12345678905"

    def test_upc_a_and_ean_13_forms_match(self):
        assert normalize_barcode("012345678905") == normalize_barcode("0012345678905")

    def test_none_stays_none(self):
        assert normalize_barcode(None) is None

    def test_empty_string_becomes_none(self):
        assert normalize_barcode("") is None

    def test_whitespace_only_becomes_none(self):
        assert normalize_barcode("   ") is None

    def test_all_zeros_returns_zero(self):
        assert normalize_barcode("0000") == "0"

    def test_alphanumeric_sku_is_not_digit_stripped(self):
        """A Code 128 manufacturer SKU/article number (e.g. Polymaker's
        inventory barcode with no UPC/EAN counterpart) must survive intact —
        stripping non-digits would mangle "ALZMNTABS01" down to "1"."""
        assert normalize_barcode("ALZMNTABS01") == "ALZMNTABS01"

    def test_sku_is_trimmed_and_uppercased(self):
        assert normalize_barcode("  alzmntabs01  ") == "ALZMNTABS01"


class TestClassifyCode:
    def test_valid_upc_a_is_gtin(self):
        assert classify_code("012345678905") == ("12345678905", "gtin")

    def test_valid_ean_13_is_gtin(self):
        assert classify_code("06938936716785") == ("6938936716785", "gtin")

    def test_bad_checksum_falls_back_to_sku(self):
        # Right length (12 digits) but an invalid check digit.
        assert classify_code("099999999999") == ("99999999999", "sku")

    def test_alphanumeric_code_is_sku(self):
        """A Code 128 manufacturer SKU/article number — e.g. Polymaker's
        inventory barcode with no UPC/EAN counterpart."""
        assert classify_code("ALZMNTABS01") == ("ALZMNTABS01", "sku")

    def test_sku_is_stripped_and_uppercased(self):
        assert classify_code("  alzmntabs01  ") == ("ALZMNTABS01", "sku")

    def test_short_digit_string_below_floor_is_sku(self):
        assert classify_code("12345") == ("12345", "sku")

    def test_kind_vocabulary_matches_spool_code_kind_enum(self):
        """classify_code returns plain strings so the schema layer stays free
        of ORM imports; this test is the lock that keeps those strings and the
        SpoolCodeKind enum (which generates the DB CHECK constraint) from
        drifting apart."""
        from backend.app.models.spool_code import SpoolCodeKind

        produced_kinds = {
            classify_code("012345678905")[1],  # gtin
            classify_code("ALZMNTABS01")[1],  # sku
        }
        assert produced_kinds == {k.value for k in SpoolCodeKind}

    def test_leading_zero_upc_a_still_classifies_gtin_after_stripping(self):
        """Regression for the exact bug found in PR #1895's review: a UPC-A
        with a leading zero must classify as gtin whether you feed it the raw
        scanned value or the already-normalize_barcode'd stored value — the
        GTIN checksum is invariant to leading-zero padding (a leading zero
        always lands in a weight-agnostic position relative to the check
        digit), so re-padding the stripped canonical form and checking there
        gives the same verdict the raw value would have."""
        raw = "036000291452"  # 12-digit UPC-A, one leading zero
        stored = normalize_barcode(raw)  # what SpoolCreate/SpoolUpdate persist
        assert stored == "36000291452"  # confirms the zero really is stripped
        assert classify_code(raw) == ("36000291452", "gtin")
        assert classify_code(stored) == ("36000291452", "gtin")

    def test_classification_is_stable_across_normalize_barcode(self):
        """classify_code(x) == classify_code(normalize_barcode(x)) for any x —
        this is what guarantees a scan (raw input) and a repeat lookup of the
        stored value (already normalized) can never disagree on kind."""
        for raw in (
            "0012345678905",
            "012345678905",
            "06938936716785",
            "0000000000123456",  # heavily zero-padded, still within GTIN-14
            "ALZMNTABS01",
            "  alzmntabs01  ",
            "099999999999",
            "12345",
            "",
        ):
            stored = normalize_barcode(raw)
            assert classify_code(raw) == classify_code(stored), raw


class TestSpoolCreateBarcodeValidation:
    def test_canonicalizes_on_create(self):
        spool = SpoolCreate(material="PLA", barcode="0012345678905")
        assert spool.barcode == "12345678905"

    def test_accepts_null_barcode(self):
        spool = SpoolCreate(material="PLA", barcode=None)
        assert spool.barcode is None

    def test_blank_barcode_normalizes_to_none(self):
        spool = SpoolCreate(material="PLA", barcode="")
        assert spool.barcode is None

    def test_sku_barcode_survives_create(self):
        spool = SpoolCreate(material="PLA", barcode="ALZMNTABS01")
        assert spool.barcode == "ALZMNTABS01"

    def test_rejects_barcode_over_64_chars(self):
        """Matches Spool.barcode's VARCHAR(64) — Postgres would reject or
        truncate a longer value at the DB layer while SQLite silently accepts
        it, so reject it up front for cross-dialect parity."""
        with pytest.raises(ValidationError):
            SpoolCreate(material="PLA", barcode="A" * 65)

    def test_accepts_barcode_at_64_char_boundary(self):
        spool = SpoolCreate(material="PLA", barcode="A" * 64)
        assert spool.barcode == "A" * 64

    def test_barcode_is_refill_defaults_false(self):
        spool = SpoolCreate(material="PLA", barcode="0012345678905")
        assert spool.barcode_is_refill is False

    def test_barcode_is_refill_is_write_only(self):
        """The refill flag lives on the barcode's SpoolCode row, not on Spool —
        it must exist on SpoolCreate (the write path pops it before building
        the ORM object) and must NOT leak onto SpoolUpdate or SpoolResponse's
        input surface as a settable Spool attribute."""
        assert "barcode_is_refill" in SpoolCreate.model_fields
        assert "barcode_is_refill" not in SpoolUpdate.model_fields


class TestSpoolUpdateBarcodeValidation:
    def test_canonicalizes_on_update(self):
        update = SpoolUpdate(barcode="0012345678905")
        assert update.barcode == "12345678905"

    def test_unset_barcode_stays_unset(self):
        update = SpoolUpdate()
        assert "barcode" not in update.model_fields_set

    def test_rejects_barcode_over_64_chars(self):
        with pytest.raises(ValidationError):
            SpoolUpdate(barcode="A" * 65)


class TestSpoolResponseBarcodeUnconstrained:
    def test_legacy_over_length_barcode_does_not_500(self):
        """rgba already has this same escape hatch (#1055) — a barcode written
        past the 64-char cap (SQLite doesn't enforce VARCHAR length) must
        still read back instead of 500ing the whole inventory list."""
        response = SpoolResponse.model_validate(
            {
                "id": 1,
                "material": "PLA",
                "barcode": "A" * 100,
                "label_weight": 1000,
                "core_weight": 250,
                "weight_used": 0,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            }
        )
        assert response.barcode == "A" * 100
