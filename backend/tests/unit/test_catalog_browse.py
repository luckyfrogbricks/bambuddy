"""Unit tests for the SpoolBuddy tap-first catalog browser.

Covers the normalization the browse tree applies on top of the raw
SpoolmanDB-Community variants (material families, line merging, color
de-duplication), the hue bucketing/filtering, "yours first" ranking, and the
route-level setting gate. See services/catalog_browse.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.inventory import barcode_catalog_browse
from backend.app.services import catalog_browse
from backend.app.services.catalog_browse import (
    browse_catalog,
    hue_bucket,
    hue_sort_key,
    material_family,
    variant_hues,
)


def _variant(**over):
    v = {
        "manufacturer": "Bambu Lab",
        "brand": "Bambu Lab",
        "material": "PLA",
        "subtype": "PLA Basic",
        "color_name": "Pumpkin Orange",
        "rgba": "FF9016FF",
        "hexes": None,
        "label_weight": 1000,
        "nozzle_temp_min": 190,
        "nozzle_temp_max": 230,
        "eans": [],
        "eans_refill": [],
        "codes": ["10301"],
    }
    v.update(over)
    return v


def _patched(filaments):
    catalog_browse._index_cache = None  # a fresh mock list would collide with a cached id
    return patch.object(catalog_browse.spoolmandb_community_client, "get_filaments", AsyncMock(return_value=filaments))


class TestMaterialFamily:
    def test_prefix_collapse(self):
        assert material_family("PLA Silk") == "PLA"
        assert material_family("PLA+WOOD") == "PLA"
        assert material_family("ABS-GF") == "ABS"

    def test_longest_prefix_wins(self):
        # PETG must not land in PET, PCTG must not land in PC.
        assert material_family("PETG-CF") == "PETG"
        assert material_family("PET-CF") == "PET"
        assert material_family("PCTG") == "PCTG"

    def test_aliases(self):
        assert material_family("Nylon 12") == "PA"
        assert material_family("PA6-GF") == "PA"
        assert material_family("TPE") == "TPU"

    def test_unknown_and_empty(self):
        assert material_family("Wood") == "Other"
        assert material_family(None) == "Other"
        assert material_family("") == "Other"


class TestHueBucket:
    def test_buckets(self):
        assert hue_bucket("FF9016FF") == "orange"  # Pumpkin Orange
        assert hue_bucket("C12E1FFF") == "red"
        assert hue_bucket("FEC600FF") == "yellow"
        assert hue_bucket("00AE42FF") == "green"
        assert hue_bucket("0056B8FF") == "blue"
        assert hue_bucket("5E43B7FF") == "purple"
        assert hue_bucket("EC008CFF") == "pink"

    def test_browns(self):
        # Earth tones are their own family (they were drowning orange): dark,
        # pale, and muted warm colors all read as brown …
        assert hue_bucket("6F5034FF") == "brown"  # Cocoa Brown (dark)
        assert hue_bucket("F7E6DEFF") == "brown"  # Beige (pale)
        assert hue_bucket("AE835BFF") == "brown"  # Caramel (muted)
        assert hue_bucket("B15533FF") == "brown"  # Terracotta
        # … while vivid mid-lightness warm colors stay orange, and dark cool
        # reds (maroon, ~350°) stay red.
        assert hue_bucket("FF6A13FF") == "orange"
        assert hue_bucket("9D2235FF") == "red"

    def test_neutrals_split_black_gray_white(self):
        assert hue_bucket("000000FF") == "black"
        assert hue_bucket("3B3B3FFF") == "black"  # charcoal
        assert hue_bucket("FFFFFFFF") == "white"
        assert hue_bucket("8E9089FF") == "gray"
        assert hue_bucket("D1D3D5FF") == "gray"  # Light Gray stays gray, not white

    def test_invalid(self):
        assert hue_bucket(None) is None
        assert hue_bucket("nope") is None
        assert hue_bucket("FF90") is None

    def test_multi_color_hexes(self):
        v = _variant(rgba="000000FF", hexes=["FF9016", "#0056B8"])
        assert variant_hues(v) == {"black", "orange", "blue"}

    def test_hue_sort_neutrals_first_dark_to_light(self):
        order = sorted(["FF9016FF", "FFFFFFFF", "000000FF", "C12E1FFF"], key=hue_sort_key)
        assert order[:2] == ["000000FF", "FFFFFFFF"]


class TestBrowseTree:
    @pytest.mark.asyncio
    async def test_brands_level_yours_first(self):
        filaments = [_variant(brand="Sunlu", manufacturer="Sunlu", color_name=f"C{i}") for i in range(3)] + [
            _variant(color_name="Black")
        ]
        with _patched(filaments):
            r = await browse_catalog(None, None, None, None, 60, owned={"bambu lab"})
        assert r.level == "brands"
        # Bambu (owned) ranks above Sunlu despite fewer colors.
        assert [b.name for b in r.brands] == ["Bambu Lab", "Sunlu"]
        assert r.brands[0].owned is True and r.brands[1].owned is False

    @pytest.mark.asyncio
    async def test_materials_level_counts_and_previews(self):
        filaments = [
            _variant(color_name="A", rgba="FF9016FF"),
            _variant(color_name="B", rgba="0056B8FF"),
            _variant(material="PETG HF", subtype="HF", color_name="C"),
        ]
        with _patched(filaments):
            r = await browse_catalog("bambu lab", None, None, None, 60, owned=set())
        assert r.level == "materials"
        assert [(g.name, g.variant_count) for g in r.groups] == [("PLA", 2), ("PETG", 1)]
        assert len(r.groups[0].preview_rgbas) == 2

    @pytest.mark.asyncio
    async def test_line_merge_punctuation_and_family_word(self):
        # "PETG-CF", "PETG CF", and bare "CF" are the same line.
        filaments = [
            _variant(material="PETG", subtype="PETG-CF", color_name="Black"),
            _variant(material="PETG", subtype="PETG CF", color_name="White"),
            _variant(material="PETG", subtype="CF", color_name="Red"),
        ]
        with _patched(filaments):
            r = await browse_catalog("Bambu Lab", "PETG", None, None, 60, owned=set())
        assert r.level == "lines"
        assert len(r.groups) == 1
        assert r.groups[0].variant_count == 3

    @pytest.mark.asyncio
    async def test_bare_line_merges_into_basic(self):
        # Upstream lists the same colors both with subtype "PLA Basic" and
        # with no subtype at all — one line, de-duplicated, named "PLA Basic".
        filaments = [
            _variant(subtype="PLA Basic", color_name="Pumpkin Orange"),
            _variant(subtype=None, color_name="Pumpkin Orange", codes=[]),
            _variant(subtype=None, color_name="Jade White", rgba="FFFFFFFF", codes=[]),
        ]
        with _patched(filaments):
            r = await browse_catalog("Bambu Lab", "PLA", None, None, 60, owned=set())
        assert [(g.name, g.variant_count) for g in r.groups] == [("PLA Basic", 2)]

    @pytest.mark.asyncio
    async def test_bare_line_without_basic_stays(self):
        filaments = [_variant(subtype=None, brand="Sunlu", manufacturer="Sunlu")]
        with _patched(filaments):
            r = await browse_catalog("Sunlu", "PLA", None, None, 60, owned=set())
        assert [(g.name, g.variant_count) for g in r.groups] == [("PLA", 1)]

    @pytest.mark.asyncio
    async def test_color_dedupe_prefers_variant_with_codes(self):
        filaments = [
            _variant(codes=[], eans=[]),
            _variant(codes=["10301"], eans=["6975337031234"]),
        ]
        with _patched(filaments):
            r = await browse_catalog("Bambu Lab", "PLA", "PLA Basic", None, 60, owned=set())
        assert r.level == "colors"
        assert len(r.colors) == 1
        assert {c.code for c in r.colors[0].codes} == {"6975337031234", "10301"}

    @pytest.mark.asyncio
    async def test_colors_hue_sorted_and_row_shape(self):
        filaments = [
            _variant(color_name="Pumpkin Orange", rgba="FF9016FF"),
            _variant(color_name="Black", rgba="000000FF", codes=[]),
        ]
        with _patched(filaments):
            r = await browse_catalog("Bambu Lab", "PLA", "PLA Basic", None, 60, owned=set())
        # Neutrals first, then the wheel — and rows are CatalogSearchRow-shaped.
        assert [c.color_name for c in r.colors] == ["Black", "Pumpkin Orange"]
        row = r.colors[1]
        assert row.source == "spoolmandb-community"
        assert row.brand == "Bambu Lab" and row.rgba == "FF9016FF"
        assert [c.code for c in row.codes] == ["10301"]

    @pytest.mark.asyncio
    async def test_unknown_brand_is_empty(self):
        with _patched([_variant()]):
            r = await browse_catalog("Nope Corp", None, None, None, 60, owned=set())
        assert r.groups == [] and r.colors == [] and r.brands == []


class TestHueFilter:
    @pytest.mark.asyncio
    async def test_hue_filters_within_scope(self):
        filaments = [
            _variant(color_name="Pumpkin Orange", rgba="FF9016FF"),
            _variant(color_name="Cobalt Blue", rgba="0056B8FF"),
            _variant(brand="Sunlu", manufacturer="Sunlu", color_name="Orange", rgba="F07826FF"),
        ]
        with _patched(filaments):
            scoped = await browse_catalog("Bambu Lab", None, None, "orange", 60, owned=set())
            everywhere = await browse_catalog(None, None, None, "orange", 60, owned=set())
        assert [c.color_name for c in scoped.colors] == ["Pumpkin Orange"]
        assert scoped.total == 1
        assert {c.color_name for c in everywhere.colors} == {"Pumpkin Orange", "Orange"}

    @pytest.mark.asyncio
    async def test_hue_results_rank_owned_brands_first_and_cap(self):
        filaments = [
            _variant(brand="Sunlu", manufacturer="Sunlu", color_name=f"Orange {i}", rgba="F07826FF") for i in range(3)
        ] + [_variant(color_name="Pumpkin Orange", rgba="FF9016FF")]
        with _patched(filaments):
            r = await browse_catalog(None, None, None, "orange", 2, owned={"bambu lab"})
        # Bambu (owned, 1 color) beats Sunlu (3 colors); cap keeps total honest.
        assert r.colors[0].brand == "Bambu Lab"
        assert len(r.colors) == 2
        assert r.total == 4

    @pytest.mark.asyncio
    async def test_multi_color_variant_matches_secondary_hue(self):
        filaments = [_variant(color_name="Velvet Eclipse", rgba="000000FF", hexes=["C12E1F"])]
        with _patched(filaments):
            r = await browse_catalog("Bambu Lab", None, None, "red", 60, owned=set())
        assert [c.color_name for c in r.colors] == ["Velvet Eclipse"]


def _settings_row(key, value):
    row = MagicMock()
    row.key = key
    row.value = value
    return row


def _db(settings_rows=(), owned_brand_rows=()):
    """db.execute: 1st call → settings map, 2nd call → owned-brands query."""
    db = AsyncMock()
    settings_result = MagicMock()
    settings_result.scalars.return_value.all.return_value = list(settings_rows)
    brands_result = MagicMock()
    brands_result.scalars.return_value.all.return_value = list(owned_brand_rows)
    calls = {"n": 0}

    async def _execute(*_a, **_k):
        calls["n"] += 1
        return settings_result if calls["n"] == 1 else brands_result

    db.execute = _execute
    return db


class TestBrowseRoute:
    @pytest.mark.asyncio
    async def test_disabled_setting_gates_browse(self):
        db = _db(settings_rows=[_settings_row("barcode_lookup_enabled", "false")])
        with _patched([_variant()]):
            r = await barcode_catalog_browse(brand=None, material=None, line=None, hue=None, limit=120, db=db, _=None)
        assert r.enabled is False
        assert r.brands == []

    @pytest.mark.asyncio
    async def test_owned_brands_come_from_inventory(self):
        db = _db(owned_brand_rows=["Bambu Lab"])
        filaments = [_variant(), _variant(brand="Sunlu", manufacturer="Sunlu", color_name="Black")]
        with _patched(filaments):
            r = await barcode_catalog_browse(brand=None, material=None, line=None, hue=None, limit=120, db=db, _=None)
        assert r.enabled is True
        assert [b.name for b in r.brands] == ["Bambu Lab", "Sunlu"]
        assert r.brands[0].owned is True

    @pytest.mark.asyncio
    async def test_invalid_hue_rejected(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await barcode_catalog_browse(
                brand=None, material=None, line=None, hue="mauve", limit=120, db=AsyncMock(), _=None
            )
