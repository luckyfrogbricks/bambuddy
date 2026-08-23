"""Tap-first filament catalog browsing for the SpoolBuddy kiosk.

Powers the "Browse Catalog" flow (brand → material → product line → color)
that replaces on-screen-keyboard typing with taps. The data source is the
cached SpoolmanDB-Community catalog — the one source that is a *catalog*
(complete brand/material/color coverage with a hex for every color) rather
than a barcode index; OFD stays a lookup/search source only. Rows returned at
the color (leaf) level are ``CatalogSearchRow``s — exactly what the kiosk's
"Find This Filament" picker already consumes — so a browsed pick flows through
the same confirm/create/code-linking path as a Find pick.

Normalization applied on top of the raw variants (upstream files are messy):

- **Material families**: raw ``material`` strings ("PLA Silk", "PETG-CF",
  "PA6-GF") collapse to a small family set (PLA, PETG, ABS, …) via
  longest-prefix match, so the material screen is a handful of big tiles.
- **Line merging**: a product line is the variant ``subtype`` (or the raw
  material when there is none). Lines that differ only in punctuation or a
  redundant material prefix ("PETG-CF" vs "PETG CF" vs "CF") merge into one;
  a bare unnamed line additionally merges into the brand's "<family> Basic"
  line when one exists (several manufacturer files list the same colors both
  ways). Colors are de-duplicated by name within the merged line, preferring
  the variant that carries barcodes/SKUs.
- **Hue buckets**: every color maps to one of a small set of hue families
  (red/orange/…/neutral) from its hex, so "the roll in your hand is orange"
  is a single tap at any browse depth. Color grids come back hue-sorted —
  neutrals by lightness first, then the color wheel — which is how you find
  a color you can see.

Ranking is "yours first": brands (and hue-filtered results) that match the
user's own inventory sort ahead of the rest, so the shop's usual suspects are
always the first tiles.

Route-layer concerns (auth, settings gating, resolving the Spoolman client)
stay in ``routes/inventory.py``; this module owns the browse tree itself.
"""

from __future__ import annotations

import colorsys
import logging
import re
from collections import Counter

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.schemas.spool import LinkedCode
from backend.app.services import spoolmandb_community_client
from backend.app.services.barcode_resolver import BARCODE_FIELD_KEYS
from backend.app.services.catalog_search import CatalogSearchRow
from backend.app.services.spoolman import SpoolmanClient

logger = logging.getLogger(__name__)

# Longest/most-specific first so "PETG" doesn't land in "PET" and "PCTG"
# doesn't land in "PC". Aliases collapse marketing spellings into one family.
_MATERIAL_FAMILIES = (
    "NYLON",
    "PETG",
    "PCTG",
    "PEEK",
    "PEKK",
    "HIPS",
    "PPS",
    "PPA",
    "PVA",
    "PVB",
    "PLA",
    "PET",
    "ABS",
    "ASA",
    "TPU",
    "TPE",
    "PA",
    "PC",
    "PP",
)
_FAMILY_ALIASES = {"NYLON": "PA", "TPE": "TPU"}
OTHER_FAMILY = "Other"

# Hue families the kiosk's color filter offers. "neutral" is the
# black/gray/white bucket (low saturation or extreme lightness).
HUE_FAMILIES = ("red", "orange", "yellow", "green", "blue", "purple", "pink", "neutral")

# Below this saturation (or outside these lightness bounds) a color reads as
# black/gray/white regardless of its nominal hue angle.
_NEUTRAL_MAX_SATURATION = 0.15
_NEUTRAL_MIN_LIGHTNESS = 0.08
_NEUTRAL_MAX_LIGHTNESS = 0.95

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}")


def material_family(material: str | None) -> str:
    """Collapse a raw material string to its family tile ("PLA Silk" → "PLA")."""
    canonical = (material or "").strip().upper()
    if not canonical:
        return OTHER_FAMILY
    for family in _MATERIAL_FAMILIES:
        if canonical.startswith(family):
            return _FAMILY_ALIASES.get(family, family)
    return OTHER_FAMILY


def _hls_for(rgba: str | None) -> tuple[float, float, float] | None:
    """(hue, lightness, saturation) in [0,1] for a hex/RGBA string, or None."""
    if not rgba or not _HEX_RE.match(rgba):
        return None
    r = int(rgba[0:2], 16) / 255
    g = int(rgba[2:4], 16) / 255
    b = int(rgba[4:6], 16) / 255
    return colorsys.rgb_to_hls(r, g, b)


def hue_bucket(rgba: str | None) -> str | None:
    """Map a hex color to its hue family, or None for unparseable input."""
    hls = _hls_for(rgba)
    if hls is None:
        return None
    h, lightness, s = hls
    if s < _NEUTRAL_MAX_SATURATION or lightness < _NEUTRAL_MIN_LIGHTNESS or lightness > _NEUTRAL_MAX_LIGHTNESS:
        return "neutral"
    deg = h * 360
    if deg < 15 or deg >= 345:
        return "red"
    if deg < 45:
        return "orange"
    if deg < 75:
        return "yellow"
    if deg < 165:
        return "green"
    if deg < 250:
        return "blue"
    if deg < 300:
        return "purple"
    return "pink"


def variant_hues(variant: dict) -> set[str]:
    """Every hue family a variant shows — its main hex plus any multi-color hexes."""
    hues: set[str] = set()
    bucket = hue_bucket(variant.get("rgba"))
    if bucket:
        hues.add(bucket)
    hexes = variant.get("hexes")
    if isinstance(hexes, list):
        for h in hexes:
            if isinstance(h, str):
                bucket = hue_bucket(h.lstrip("#"))
                if bucket:
                    hues.add(bucket)
    return hues


def hue_sort_key(rgba: str | None) -> tuple[int, float]:
    """Sort colors the way an eye scans a palette: unparseable last is not
    needed (they sort as neutrals-first group), neutrals ordered dark→light,
    then everything else around the color wheel."""
    hls = _hls_for(rgba)
    if hls is None:
        return (0, 0.0)
    h, lightness, s = hls
    if s < _NEUTRAL_MAX_SATURATION:
        return (0, lightness)
    return (1, h)


def _line_key(fam: str, raw_line: str) -> str:
    """Canonical merge key for a product line within (brand, family):
    punctuation-insensitive and ignoring the redundant family word wherever it
    sits, so "PETG-CF", "PETG CF", and "CF" are one line — as are "Silk PLA"
    and "Silk"."""
    key = re.sub(r"[\s\-_/+]+", " ", raw_line.upper()).strip()
    key = re.sub(rf"\b{re.escape(fam.upper())}\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


class _LineNode:
    __slots__ = ("labels", "variants")

    def __init__(self) -> None:
        self.labels: Counter[str] = Counter()
        self.variants: list[dict] = []

    @property
    def label(self) -> str:
        # Most common raw label wins; ties break toward the longer (usually
        # the one still carrying the material prefix, e.g. "PETG CF" not "CF").
        best = max(self.labels.items(), key=lambda kv: (kv[1], len(kv[0])))
        return best[0]


def _dedupe_colors(variants: list[dict]) -> list[dict]:
    """One variant per color name, preferring the one that carries codes."""
    by_name: dict[str, dict] = {}
    for v in variants:
        name = (v.get("color_name") or "").strip().lower()
        key = name or f"\x00{id(v)}"  # unnamed colors never merge
        existing = by_name.get(key)
        if existing is None or (not _variant_has_codes(existing) and _variant_has_codes(v)):
            by_name[key] = v
    return list(by_name.values())


def _variant_has_codes(variant: dict) -> bool:
    return bool(variant.get("eans") or variant.get("eans_refill") or variant.get("codes"))


class _BrowseIndex:
    """brand → material family → merged line → de-duplicated color variants."""

    def __init__(self, variants: list[dict]) -> None:
        # brand display name (as published) → fam → line key → node
        self.tree: dict[str, dict[str, dict[str, _LineNode]]] = {}
        self.brand_by_lower: dict[str, str] = {}
        for v in variants:
            brand = (v.get("brand") or v.get("manufacturer") or "").strip()
            if not brand:
                continue
            self.brand_by_lower.setdefault(brand.lower(), brand)
            brand = self.brand_by_lower[brand.lower()]
            fam = material_family(v.get("material"))
            raw_line = (v.get("subtype") or v.get("material") or fam).strip() or fam
            key = _line_key(fam, raw_line)
            node = self.tree.setdefault(brand, {}).setdefault(fam, {}).setdefault(key, _LineNode())
            node.labels[raw_line] += 1
            node.variants.append(v)

        # A bare unnamed line ('' key) merges into "<family> Basic" when the
        # brand also publishes one — several upstream files list the same
        # colors under both shapes (Bambu's PLA is the flagship case).
        for fams in self.tree.values():
            for lines in fams.values():
                if "" in lines and "BASIC" in lines:
                    # Only the variants merge — the bare line's labels (raw
                    # material strings, often the more numerous side) must not
                    # outvote the explicit "<family> Basic" label.
                    lines["BASIC"].variants.extend(lines.pop("").variants)

        # De-duplicate colors once, at build time.
        for fams in self.tree.values():
            for lines in fams.values():
                for node in lines.values():
                    node.variants = sorted(_dedupe_colors(node.variants), key=lambda v: hue_sort_key(v.get("rgba")))


# Rebuilt only when the community client's payload object changes (24h TTL /
# process restart). Key is (id, len) of the cached variants list: the client
# holds that exact list in memory, so a new payload is a new object.
_index_cache: tuple[tuple[int, int], _BrowseIndex] | None = None


async def _get_index() -> _BrowseIndex:
    global _index_cache
    variants = await spoolmandb_community_client.get_filaments()
    key = (id(variants), len(variants))
    if _index_cache is None or _index_cache[0] != key:
        _index_cache = (key, _BrowseIndex(variants))
    return _index_cache[1]


class BrowseBrand(BaseModel):
    name: str
    variant_count: int
    owned: bool = False


class BrowseGroup(BaseModel):
    """A material-family or product-line tile: name + size + swatch preview."""

    name: str
    variant_count: int
    preview_rgbas: list[str] = []


class CatalogBrowseResponse(BaseModel):
    enabled: bool
    level: str  # "brands" | "materials" | "lines" | "colors"
    brands: list[BrowseBrand] = []
    groups: list[BrowseGroup] = []
    colors: list[CatalogSearchRow] = []
    # Total matches before the cap, when colors were truncated (hue filters
    # over a wide scope can match thousands).
    total: int | None = None


async def owned_brands(db: AsyncSession, spoolman_client: SpoolmanClient | None) -> set[str]:
    """Lower-cased brand names present in the user's (non-archived) inventory."""
    brands: set[str] = set()
    if spoolman_client is not None:
        try:
            from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

            for sm in await spoolman_client.get_spools():
                try:
                    mapped = _map_spoolman_spool(sm)
                except ValueError:
                    continue
                if mapped.get("brand"):
                    brands.add(str(mapped["brand"]).strip().lower())
        except Exception:
            logger.warning("Spoolman inventory unavailable for catalog-browse ranking", exc_info=True)
        return brands
    result = await db.execute(
        select(Spool.brand).where(Spool.archived_at.is_(None), Spool.brand.is_not(None)).distinct()
    )
    for brand in result.scalars().all():
        if brand and brand.strip():
            brands.add(brand.strip().lower())
    return brands


def _row_for(variant: dict) -> CatalogSearchRow:
    codes = spoolmandb_community_client.codes_for_variant(variant)
    return CatalogSearchRow(
        source="spoolmandb-community",
        codes=[LinkedCode(**c) for c in codes],
        **{k: variant.get(k) for k in BARCODE_FIELD_KEYS},
    )


def _preview_rgbas(variants: list[dict], count: int = 5) -> list[str]:
    seen: list[str] = []
    for v in variants:  # already hue-sorted at index build
        rgba = v.get("rgba")
        if rgba and rgba not in seen:
            seen.append(rgba)
        if len(seen) >= count:
            break
    return seen


async def browse_catalog(
    brand: str | None,
    material: str | None,
    line: str | None,
    hue: str | None,
    limit: int,
    owned: set[str],
) -> CatalogBrowseResponse:
    """One level of the browse tree, or hue-filtered colors across the scope.

    Scope narrows with the given params (brand → +material → +line); ``hue``
    at any scope switches the response to filtered color rows. ``owned``
    ranks the user's inventory brands first wherever brands compete.
    """
    index = await _get_index()

    scope_brands: list[str]
    if brand is not None:
        canonical_brand = index.brand_by_lower.get(brand.strip().lower())
        if canonical_brand is None:
            return CatalogBrowseResponse(enabled=True, level="colors" if hue else "materials")
        scope_brands = [canonical_brand]
    else:
        scope_brands = list(index.tree.keys())

    def lines_in_scope() -> list[tuple[str, str, _LineNode]]:
        """(brand, line label, node) for every line inside the current scope."""
        out: list[tuple[str, str, _LineNode]] = []
        for b in scope_brands:
            for fam, lines in index.tree.get(b, {}).items():
                if material is not None and fam != material:
                    continue
                for node in lines.values():
                    if line is not None and node.label.lower() != line.strip().lower():
                        continue
                    out.append((b, node.label, node))
        return out

    if hue is not None:
        matched: list[tuple[str, dict]] = [
            (b, v) for b, _label, node in lines_in_scope() for v in node.variants if hue in variant_hues(v)
        ]
        brand_size = {
            b: sum(len(n.variants) for lines in index.tree.get(b, {}).values() for n in lines.values())
            for b in scope_brands
        }
        matched.sort(
            key=lambda bv: (
                0 if bv[0].lower() in owned else 1,
                -brand_size.get(bv[0], 0),
                bv[0].lower(),
                hue_sort_key(bv[1].get("rgba")),
            )
        )
        return CatalogBrowseResponse(
            enabled=True,
            level="colors",
            colors=[_row_for(v) for _b, v in matched[:limit]],
            total=len(matched),
        )

    if brand is None:
        rows = [
            BrowseBrand(
                name=b,
                variant_count=sum(len(n.variants) for lines in index.tree[b].values() for n in lines.values()),
                owned=b.lower() in owned,
            )
            for b in index.tree
        ]
        rows.sort(key=lambda r: (not r.owned, -r.variant_count, r.name.lower()))
        return CatalogBrowseResponse(enabled=True, level="brands", brands=rows)

    if material is None:
        groups = []
        for fam, lines in index.tree.get(scope_brands[0], {}).items():
            variants = [v for n in lines.values() for v in n.variants]
            variants.sort(key=lambda v: hue_sort_key(v.get("rgba")))
            groups.append(BrowseGroup(name=fam, variant_count=len(variants), preview_rgbas=_preview_rgbas(variants)))
        groups.sort(key=lambda g: (-g.variant_count, g.name.lower()))
        return CatalogBrowseResponse(enabled=True, level="materials", groups=groups)

    if line is None:
        groups = [
            BrowseGroup(name=label, variant_count=len(node.variants), preview_rgbas=_preview_rgbas(node.variants))
            for _b, label, node in lines_in_scope()
        ]
        groups.sort(key=lambda g: (-g.variant_count, g.name.lower()))
        return CatalogBrowseResponse(enabled=True, level="lines", groups=groups)

    colors = [v for _b, _label, node in lines_in_scope() for v in node.variants]
    colors.sort(key=lambda v: hue_sort_key(v.get("rgba")))
    return CatalogBrowseResponse(
        enabled=True, level="colors", colors=[_row_for(v) for v in colors[:limit]], total=len(colors)
    )
