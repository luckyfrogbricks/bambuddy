"""Unit tests for the ColorManager — family counting and the
adjacency-guaranteed circular partition behind the kiosk ColorFilter.
See services/color_manager.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes.inventory import barcode_catalog_hues
from backend.app.services import catalog_browse
from backend.app.services.color_manager import (
    CHROMATIC_WHEEL,
    family_counts,
    fixed_groups,
    partition,
)


def _counts(**over):
    counts = dict.fromkeys(list(CHROMATIC_WHEEL) + ["black", "gray", "white"], 0)
    counts.update(over)
    return counts


def _fams(groups):
    return [g.families for g in groups]


class TestFamilyCounts:
    def test_set_membership_counts_both_families(self):
        # A black-red dual-color counts toward black AND red.
        velvet = {"rgba": "000000FF", "hexes": ["C12E1F"]}
        counts = family_counts([velvet])
        assert counts["black"] == 1
        assert counts["red"] == 1

    def test_multicolor_pulls_rainbows_out(self):
        rainbow = {"rgba": "C12E1FFF", "hexes": ["FEC600", "00AE42", "0056B8"]}
        plain = {"rgba": "FF9016FF", "hexes": None}
        counts = family_counts([rainbow, plain], multicolor=True)
        assert counts["multicolor"] == 1
        # The rainbow no longer inflates every family it touches...
        assert counts["red"] == 0 and counts["green"] == 0
        assert counts["orange"] == 1
        # ...but without the flag it does.
        counts2 = family_counts([rainbow, plain])
        assert counts2["red"] == 1 and counts2["green"] == 1 and "multicolor" not in counts2


class TestPartition:
    def test_spec_example_purple_pink_merge(self):
        # brown 25, green 25, blue 25, purple 6, pink 6 at X=4 → purple·pink merge.
        counts = _counts(brown=25, green=25, blue=25, purple=6, pink=6)
        groups = partition(counts, 4)
        assert _fams(groups) == [["brown"], ["green"], ["blue"], ["purple", "pink"]]
        assert groups[3].count == 12

    def test_circular_wheel_allows_pink_red_merge(self):
        # Small pink + small red on opposite string ends but wheel-adjacent.
        counts = _counts(red=5, orange=100, yellow=100, blue=100, pink=5)
        groups = partition(counts, 4)
        assert ["pink", "red"] in _fams(groups)

    def test_no_skip_merges_ever(self):
        counts = _counts(red=10, orange=1, brown=1, yellow=10, green=1, blue=10, purple=1, pink=1)
        for k in range(2, 8):
            for g in partition(counts, k):
                fams = g.families
                idx = [CHROMATIC_WHEEL.index(f) for f in fams]
                # Contiguous arc on the circle: consecutive indices mod wheel length.
                for a, b in zip(idx, idx[1:], strict=False):
                    assert (b - a) % len(CHROMATIC_WHEEL) == 1

    def test_achromatics_never_merge_into_hues(self):
        counts = _counts(purple=5, pink=5, black=50, gray=50, white=50)
        for k in (2, 3, 4):
            for g in partition(counts, k):
                achromatic = {"black", "gray", "white"} & set(g.families)
                if achromatic:
                    assert set(g.families) <= {"black", "gray", "white"}

    def test_grayscale_reservation(self):
        counts = _counts(red=10, blue=10, black=5, gray=5, white=5)
        groups = partition(counts, 3, grayscale=True)
        gs = [g for g in groups if g.kind == "grayscale"]
        assert len(gs) == 1
        assert gs[0].families == ["black", "gray", "white"]
        assert gs[0].count == 15

    def test_earth_tones_reservation_pins_brown(self):
        counts = _counts(red=10, orange=10, brown=3, yellow=10)
        groups = partition(counts, 3, earth_tones=True)
        earth = [g for g in groups if g.kind == "earth-tones"]
        assert len(earth) == 1 and earth[0].families == ["brown"]
        # Brown appears nowhere else.
        assert all("brown" not in g.families for g in groups if g.kind != "earth-tones")

    def test_multicolor_reservation(self):
        counts = _counts(red=10, blue=10)
        counts["multicolor"] = 4
        groups = partition(counts, 3, multicolor=True)
        mc = [g for g in groups if g.kind == "multicolor"]
        assert len(mc) == 1 and mc[0].count == 4

    def test_empty_families_dropped_in_adaptive(self):
        counts = _counts(orange=10, blue=10)
        groups = partition(counts, 5)
        assert _fams(groups) == [["orange"], ["blue"]]


class TestFixedGroups:
    def test_all_families_zeros_included(self):
        groups = fixed_groups(_counts(orange=3))
        assert len(groups) == 11
        by_family = {g.families[0]: g.count for g in groups}
        assert by_family["orange"] == 3
        assert by_family["pink"] == 0  # present for dimming, never hidden


def _settings_row(key, value):
    row = MagicMock()
    row.key = key
    row.value = value
    return row


def _db(settings_rows=()):
    db = AsyncMock()
    settings_result = MagicMock()
    settings_result.scalars.return_value.all.return_value = list(settings_rows)
    db.execute = AsyncMock(return_value=settings_result)
    return db


def _variant(**over):
    v = {
        "rgba": "FF9016FF",
        "hexes": None,
        "brand": "Bambu Lab",
        "manufacturer": "Bambu Lab",
        "material": "PLA",
        "subtype": "PLA Basic",
        "color_name": "Pumpkin Orange",
        "label_weight": 1000,
        "nozzle_temp_min": None,
        "nozzle_temp_max": None,
        "eans": [],
        "eans_refill": [],
        "codes": [],
    }
    v.update(over)
    return v


class TestHuesRoute:
    @pytest.mark.asyncio
    async def test_fixed_mode_counts(self):
        catalog_browse._index_cache = None
        filaments = [_variant(), _variant(color_name="Black", rgba="000000FF")]
        with patch.object(
            catalog_browse.spoolmandb_community_client, "get_filaments", AsyncMock(return_value=filaments)
        ):
            r = await barcode_catalog_hues(
                brand=None,
                material=None,
                line=None,
                count=None,
                grayscale=False,
                earth_tones=False,
                multicolor=False,
                db=_db(),
                _=None,
            )
        assert r.enabled is True
        by_family = {g.families[0]: g.count for g in r.groups}
        assert by_family["orange"] == 1 and by_family["black"] == 1 and by_family["pink"] == 0

    @pytest.mark.asyncio
    async def test_disabled_setting_gates(self):
        r = await barcode_catalog_hues(
            brand=None,
            material=None,
            line=None,
            count=None,
            grayscale=False,
            earth_tones=False,
            multicolor=False,
            db=_db(settings_rows=[_settings_row("barcode_lookup_enabled", "false")]),
            _=None,
        )
        assert r.enabled is False and r.groups == []
