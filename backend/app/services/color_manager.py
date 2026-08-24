"""ColorManager — the business-logic owner of color *reasoning*.

Two responsibilities (see the Color Kit style guide's ColorManager section):

- **Classify**: hex color in, hue family out. The classifier itself
  (``hue_bucket`` and friends) still lives in ``catalog_browse.py`` — by
  explicit agreement nothing is refactored into here yet; this module simply
  *uses* it, so the HSL thresholds keep exactly one home.
- **Partition**: family counts + a dot budget + reservations in,
  adjacency-guaranteed groups out. This is what powers the kiosk's ColorFilter
  in adaptive mode, and the per-family counts that let fixed mode dim empty
  families.

Partitioning rules (the "adjacency guarantee"):

- The chromatic families are arranged on a *circle* — red, orange, brown,
  yellow, green, blue, purple, pink, and back to red — and a group is always
  a contiguous arc of that circle (pink·red is a legal merge; a skip like
  yellow+purple can never happen). Implemented as the classic min-max
  linear-partition DP run over every rotation of the circle.
- The achromatic run (black, gray, white) partitions separately and never
  merges into hues.
- Reservations pin a group regardless of the partition:
  ``grayscale`` → one group holding the whole achromatic run;
  ``earth_tones`` → the brown family pinned alone (beige→dark brown — the
  skin-tone range the brown classifier was fitted to);
  ``multicolor`` → filaments spanning ≥3 hue families leave the per-hue pools
  entirely and live in one group (rainbow/gradient products).

A filament's family membership is a *set* (``variant_hues``): a black-red
dual-color counts toward both black and red. Membership is orthogonal to the
adjacency rule, which constrains how *dots group families*, never which
families a filament belongs to.
"""

from __future__ import annotations

from functools import cache

from pydantic import BaseModel

from backend.app.services.catalog_browse import HUE_FAMILIES, variant_hues

# The chromatic wheel, in circular order (pink wraps back to red), and the
# separate achromatic run. Together these are exactly HUE_FAMILIES.
CHROMATIC_WHEEL = ("red", "orange", "brown", "yellow", "green", "blue", "purple", "pink")
ACHROMATIC_RUN = ("black", "gray", "white")

# A filament spanning at least this many distinct hue families reads as a
# "multicolor" product (rainbow / gradient), not as any one of its colors.
MULTICOLOR_MIN_FAMILIES = 3


class HueGroup(BaseModel):
    """One dot of a ColorFilter: which families it holds and how many colors."""

    families: list[str]
    count: int
    # "hues" for a plain (possibly merged) arc; reserved groups carry their
    # own kind so the client can pick the special rendering (smooth grayscale
    # ramp, earth ramp, rainbow conic) and label.
    kind: str = "hues"  # "hues" | "grayscale" | "earth-tones" | "multicolor"


class CatalogHuesResponse(BaseModel):
    enabled: bool
    groups: list[HueGroup] = []


def family_counts(variants: list[dict], *, multicolor: bool = False) -> dict[str, int]:
    """Count how many variants show each hue family within a scope.

    Membership is a set — a dual-color variant counts toward both its
    families. With ``multicolor`` on, variants spanning
    ``MULTICOLOR_MIN_FAMILIES``+ families are pulled out into a synthetic
    "multicolor" bucket instead of inflating every family they touch.
    """
    counts: dict[str, int] = dict.fromkeys(HUE_FAMILIES, 0)
    if multicolor:
        counts["multicolor"] = 0
    for v in variants:
        hues = variant_hues(v)
        if not hues:
            continue
        if multicolor and len(hues & set(CHROMATIC_WHEEL)) >= MULTICOLOR_MIN_FAMILIES:
            counts["multicolor"] += 1
            continue
        for h in hues:
            counts[h] += 1
    return counts


def _linear_partition(weights: tuple[int, ...], k: int) -> tuple[list[list[int]], int]:
    """Split ``weights`` into ``k`` contiguous groups minimizing the largest
    group sum. Returns (groups as index lists, max group sum)."""
    n = len(weights)
    if n == 0:
        return [], 0
    if k >= n:
        return [[i] for i in range(n)], max(weights)
    prefix = [0]
    for w in weights:
        prefix.append(prefix[-1] + w)

    @cache
    def best(i: int, groups: int) -> tuple[int, tuple[int, ...]]:
        if groups == 1:
            return prefix[n] - prefix[i], ()
        best_val: int | None = None
        best_cuts: tuple[int, ...] = ()
        for j in range(i + 1, n - groups + 2):
            tail_val, tail_cuts = best(j, groups - 1)
            val = max(prefix[j] - prefix[i], tail_val)
            if best_val is None or val < best_val:
                best_val, best_cuts = val, (j, *tail_cuts)
        assert best_val is not None
        return best_val, best_cuts

    val, cuts = best(0, k)
    best.cache_clear()
    groups: list[list[int]] = []
    prev = 0
    for c in [*cuts, n]:
        groups.append(list(range(prev, c)))
        prev = c
    return groups, val


def _circular_partition(names: list[str], weights: list[int], k: int) -> tuple[list[list[str]], int]:
    """Partition a *circle* of families into ``k`` contiguous arcs minimizing
    the largest arc — the linear DP tried at every rotation, best kept. The
    winning grouping is re-rotated so output stays in wheel order starting
    from the first family."""
    n = len(names)
    if n == 0:
        return [], 0
    if k >= n:
        return [[name] for name in names], max(weights)
    best_groups: list[list[str]] | None = None
    best_val: int | None = None
    for rot in range(n):
        rotated = tuple(weights[(rot + i) % n] for i in range(n))
        groups_idx, val = _linear_partition(rotated, k)
        if best_val is None or val < best_val:
            best_val = val
            best_groups = [[names[(rot + i) % n] for i in g] for g in groups_idx]
    assert best_groups is not None and best_val is not None
    # Stable presentation: rotate the arc list so the group containing the
    # earliest wheel family comes first.
    first = min(range(len(best_groups)), key=lambda gi: names.index(best_groups[gi][0]))
    best_groups = best_groups[first:] + best_groups[:first]
    return best_groups, best_val


def partition(
    counts: dict[str, int],
    count: int,
    *,
    grayscale: bool = False,
    earth_tones: bool = False,
    multicolor: bool = False,
) -> list[HueGroup]:
    """Group hue families into exactly ``count`` dots (fewer if fewer
    non-empty families exist), honoring reservations. Output order: chromatic
    arcs in wheel order (earth tones in brown's position), then the
    achromatic groups, then multicolor."""
    reserved: list[HueGroup] = []

    chroma = [f for f in CHROMATIC_WHEEL if counts.get(f, 0) > 0]
    if earth_tones and "brown" in chroma:
        chroma.remove("brown")
        reserved.append(HueGroup(families=["brown"], count=counts["brown"], kind="earth-tones"))

    achroma = [f for f in ACHROMATIC_RUN if counts.get(f, 0) > 0]
    if grayscale and achroma:
        reserved.append(HueGroup(families=list(achroma), count=sum(counts[f] for f in achroma), kind="grayscale"))
        achroma = []

    if multicolor and counts.get("multicolor", 0) > 0:
        reserved.append(HueGroup(families=["multicolor"], count=counts["multicolor"], kind="multicolor"))

    budget = max(count - len(reserved), 0)
    chroma_groups: list[list[str]] = []
    achroma_groups: list[list[str]] = []
    if chroma and achroma:
        # Try every split of the remaining budget between the two segments,
        # keeping whichever minimizes the overall largest group. Each segment
        # always gets at least one group.
        best_val: int | None = None
        for k_a in range(1, min(len(achroma), max(budget - 1, 1)) + 1):
            k_c = max(budget - k_a, 1)
            cg, cv = _circular_partition(chroma, [counts[f] for f in chroma], k_c)
            ag, av = _linear_partition(tuple(counts[f] for f in achroma), k_a)
            val = max(cv, av)
            if best_val is None or val < best_val:
                best_val = val
                chroma_groups = cg
                achroma_groups = [[achroma[i] for i in g] for g in ag]
    elif chroma:
        chroma_groups, _ = _circular_partition(chroma, [counts[f] for f in chroma], max(budget, 1))
    elif achroma:
        ag, _ = _linear_partition(tuple(counts[f] for f in achroma), max(budget, 1))
        achroma_groups = [[achroma[i] for i in g] for g in ag]

    out: list[HueGroup] = [HueGroup(families=g, count=sum(counts[f] for f in g)) for g in chroma_groups]
    # Earth tones renders in brown's wheel position: after any arc that ends
    # left of yellow. Simplest stable placement: insert after the arc
    # containing orange (or red), else at the front.
    earth = [g for g in reserved if g.kind == "earth-tones"]
    if earth:
        pos = 0
        for i, g in enumerate(out):
            if "orange" in g.families or "red" in g.families:
                pos = i + 1
        out[pos:pos] = earth
    out.extend(HueGroup(families=g, count=sum(counts[f] for f in g)) for g in achroma_groups)
    out.extend(g for g in reserved if g.kind == "grayscale")
    out.extend(g for g in reserved if g.kind == "multicolor")
    return out


def fixed_groups(counts: dict[str, int], *, multicolor: bool = False) -> list[HueGroup]:
    """Fixed-mode rail: every family as its own group, in canonical order,
    zeros included (the client dims those, never hides them)."""
    out = [HueGroup(families=[f], count=counts.get(f, 0)) for f in HUE_FAMILIES]
    if multicolor:
        out.append(HueGroup(families=["multicolor"], count=counts.get("multicolor", 0), kind="multicolor"))
    return out
