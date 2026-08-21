"""Barcode resolution + persistence: the one engine every barcode path shares.

A scanned/typed code resolves through one chain — the user's own inventory
first (instant, offline, exact), then the Open Filament Database, then
SpoolmanDB-Community — and persists through one funnel that stores the
primary code plus every cross-referenced sibling (other package-size GTINs,
the refill-pack GTIN, the manufacturer SKU) as ``SpoolCode`` rows.

Living in the services layer (rather than inside ``routes/inventory.py``)
is deliberate: the web inventory routes, the SpoolBuddy scan endpoint, CSV
import, and Spoolman-mode writes all need these functions, and during PR
#1895's review the cross-route private imports and per-path reimplementations
this replaces were a recurring source of drift bugs (scan and save
classifying the same code differently, paths missing the lookup toggle).

The ``barcode_lookup_enabled`` setting gates every external call in exactly
one place — ``external_all_codes`` — which every read *and* write path
funnels through. With the toggle off, saving a spool that carries a barcode
must not download anything: on a first-ever offline instance the OFD/tarball
timeouts would otherwise block that save for minutes under the refresh lock.

Callers supply the settings map and (when Spoolman mode is active) the
SpoolmanClient — loading those is request-plumbing that stays in the routes
layer (``_load_settings_map`` / ``_ensure_spoolman_client``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_code import SpoolCode
from backend.app.schemas.spool import classify_code
from backend.app.services import ofd_client, spoolmandb_community_client

if TYPE_CHECKING:
    from backend.app.services.spoolman import SpoolmanClient

logger = logging.getLogger(__name__)

# The fixed field set a lookup can prefill on the add-spool form, whatever
# source it resolved from. Shared by the resolver, the SpoolBuddy catalog
# search, and the lookup endpoints.
BARCODE_FIELD_KEYS = (
    "material",
    "brand",
    "subtype",
    "color_name",
    "rgba",
    "label_weight",
    "nozzle_temp_min",
    "nozzle_temp_max",
)


def barcode_lookup_enabled(settings: dict[str, str]) -> bool:
    """Whether external (OFD / SpoolmanDB-Community) lookups are allowed."""
    return settings.get("barcode_lookup_enabled", "true") == "true"


async def external_all_codes(code: str, kind: str, settings: dict[str, str]) -> tuple[dict, str, list[dict]] | None:
    """Cross-reference OFD and SpoolmanDB-Community for `code`, merging both hits.

    Returns (fields, source, all_codes) where `source` is whichever database
    resolved first, `fields` prefers that source's values but fills any gaps
    (e.g. missing nozzle temps) from the other, and `all_codes` is the union
    of every sibling code (other package-size GTINs, the refill GTIN, the
    SKU/article number) discovered across both databases. If only one
    database resolves `code` directly, its sibling codes are also probed
    against the *other* database to recover cross-referenced fields/codes.

    Returns None without any network/cache activity when the
    ``barcode_lookup_enabled`` setting is off — this is THE gate, sitting in
    the one function every external-lookup path funnels through.
    """
    if not barcode_lookup_enabled(settings):
        return None

    async def _ofd_lookup(c: str, k: str) -> tuple[dict, list[dict]] | None:
        return await (ofd_client.lookup(c) if k == "gtin" else ofd_client.lookup_article(c))

    async def _smdb_lookup(c: str, k: str) -> tuple[dict, list[dict]] | None:
        return await (
            spoolmandb_community_client.lookup(c) if k == "gtin" else spoolmandb_community_client.lookup_sku(c)
        )

    try:
        ofd_hit = await _ofd_lookup(code, kind)
    except Exception:
        logger.warning("OFD lookup failed for %s", code, exc_info=True)
        ofd_hit = None
    try:
        smdb_hit = await _smdb_lookup(code, kind)
    except Exception:
        logger.warning("SpoolmanDB-Community lookup failed for %s", code, exc_info=True)
        smdb_hit = None

    if not ofd_hit and not smdb_hit:
        return None

    fields: dict = {}
    all_codes: list[dict] = []
    source: str | None = None

    def _merge(hit: tuple[dict, list[dict]], src_name: str) -> None:
        nonlocal source
        hit_fields, hit_codes = hit
        for key, value in hit_fields.items():
            if value is not None and fields.get(key) is None:
                fields[key] = value
        for entry in hit_codes:
            if not any(existing["code"] == entry["code"] for existing in all_codes):
                all_codes.append(entry)
        if source is None:
            source = src_name

    if ofd_hit:
        _merge(ofd_hit, "ofd")
    if smdb_hit:
        _merge(smdb_hit, "spoolmandb-community")

    tried = {code}
    for entry in list(all_codes):
        if ofd_hit and smdb_hit:
            break
        sibling_code = entry["code"]
        if sibling_code in tried:
            continue
        tried.add(sibling_code)
        if not ofd_hit:
            try:
                probe = await _ofd_lookup(sibling_code, entry["kind"])
            except Exception:
                probe = None
            if probe:
                _merge(probe, "ofd")
                ofd_hit = probe
        if not smdb_hit:
            try:
                probe = await _smdb_lookup(sibling_code, entry["kind"])
            except Exception:
                probe = None
            if probe:
                _merge(probe, "spoolmandb-community")
                smdb_hit = probe

    return fields, source, all_codes


async def resolve_barcode(
    db: AsyncSession,
    code: str,
    kind: str,
    settings: dict[str, str],
    spoolman_client: SpoolmanClient | None = None,
) -> tuple[dict, str | None, list[dict]]:
    """Resolve a classified code (see `classify_code`): the user's own inventory
    first, then OFD, then SpoolmanDB-Community, cross-referencing between the
    two external databases along the way.

    When Spoolman mode is active (caller passes its client), "the user's own
    inventory" means Spoolman's spools (barcode stored under
    extra.bambu_barcode — see SpoolmanClient.find_spool_by_barcode) since
    that's where the visible inventory actually lives; otherwise it means the
    local ``Spool``/``SpoolCode`` tables. Falls back to OFD, then
    SpoolmanDB-Community, if barcode_lookup_enabled — OFD stays first since
    it's purpose-built for barcode lookups; SpoolmanDB-Community's coverage
    is far sparser in barcodes but broader in brands, so it's a secondary
    fallback, not a replacement.

    Returns (fields, source, all_codes). ``source`` is "inventory", "ofd",
    "spoolmandb-community", or None (no match). ``all_codes`` is every code
    (GTIN or SKU) discovered alongside `code` — siblings to persist/display
    (own-inventory hits include every code already stored on that spool).
    """
    if spoolman_client:
        try:
            spool = await spoolman_client.find_spool_by_barcode(code)
        except Exception:
            logger.warning("Spoolman barcode lookup failed for %s", code, exc_info=True)
            spool = None
        if spool:
            # Lazy one-way exception to routes-never-imported-by-services:
            # _map_spoolman_spool is the single shared Spoolman-dict -> fields
            # mapping and lives with the Spoolman routes; importing it lazily
            # here avoids duplicating that mapping while keeping module import
            # graphs acyclic.
            from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool

            mapped = _map_spoolman_spool(spool)
            fields = {key: mapped.get(key) for key in BARCODE_FIELD_KEYS}
            return fields, "inventory", mapped.get("linked_codes") or []
    else:
        # Match on `code` alone, not `kind` — a canonical code string
        # identifies one product regardless of how it was classified at
        # write time (`kind` is only used to route external-DB lookups and
        # as row metadata).
        result = await db.execute(
            select(SpoolCode).where(SpoolCode.code == code).order_by(SpoolCode.created_at.desc()).limit(1)
        )
        hit = result.scalars().first()
        if hit:
            spool_result = await db.execute(select(Spool).where(Spool.id == hit.spool_id))
            existing = spool_result.scalars().first()
            if existing:
                fields = {key: getattr(existing, key) for key in BARCODE_FIELD_KEYS}
                all_codes = [{"code": c.code, "kind": c.kind, "is_refill": c.is_refill} for c in existing.codes]
                return fields, "inventory", all_codes

    external = await external_all_codes(code, kind, settings)
    if external is None:
        return {}, None, []
    return external


async def persist_spool_codes(
    db: AsyncSession,
    spool_id: int,
    primary_code: str,
    primary_kind: str,
    all_codes: list[dict],
    primary_is_refill: bool = False,
) -> None:
    """Store `primary_code` plus every sibling in `all_codes` against `spool_id`,
    deduped on (spool_id, code). The scanned/typed code is always `is_primary`.

    `primary_is_refill` records whether the primary code is the no-spool refill
    variant — the databases mark this via eans_refill/spool_refill, but a
    user-linked/manually-typed code has no such signal, so the caller supplies
    it (e.g. the SpoolBuddy refill toggle)."""
    existing_result = await db.execute(select(SpoolCode.code).where(SpoolCode.spool_id == spool_id))
    existing_codes = {row[0] for row in existing_result.all()}

    to_insert: dict[str, dict] = {
        primary_code: {"kind": primary_kind, "is_refill": primary_is_refill, "is_primary": True}
    }
    for entry in all_codes:
        code_val = entry.get("code")
        if not code_val or code_val == primary_code:
            continue
        to_insert.setdefault(
            code_val,
            {"kind": entry.get("kind") or "gtin", "is_refill": bool(entry.get("is_refill")), "is_primary": False},
        )

    for code_val, meta in to_insert.items():
        if code_val in existing_codes:
            continue
        db.add(SpoolCode(spool_id=spool_id, code=code_val, **meta))
    await db.commit()


async def resolve_codes_for_barcode(barcode: str, settings: dict[str, str]) -> tuple[str, str, list[dict]]:
    """Classify `barcode` and cross-reference it externally, returning
    `(canonical_code, kind, sibling_codes)`. Swallows lookup failures — a spool
    still gets created/imported with just its primary code if the external
    databases are unreachable. `external_all_codes` reads the 24h-cached
    in-memory index, not the network, so batch callers should resolve once per
    unique barcode rather than once per row/spool, but a repeat call for the
    same barcode is cheap either way."""
    code, kind = classify_code(barcode)
    try:
        external = await external_all_codes(code, kind, settings)
    except Exception:
        logger.warning("Cross-reference lookup failed while resolving codes for barcode %s", barcode, exc_info=True)
        external = None
    all_codes = external[2] if external else []
    return code, kind, all_codes


async def persist_barcode_codes_for_spool(
    db: AsyncSession,
    spool_id: int,
    barcode: str | None,
    settings: dict[str, str],
    primary_is_refill: bool = False,
) -> None:
    """Replace every SpoolCode row for `spool_id` with the set cross-referenced
    from `barcode` — or with nothing, if `barcode` is unset. Delete-then-insert
    (mirrors Spoolman mode's reset-then-set of bambu_linked_codes in
    _resolve_linked_codes_json) instead of only ever inserting: without this,
    editing a barcode A -> B left A's rows in place alongside B's, so both
    still resolved on scan, and clearing a barcode entirely left every
    previously-discovered sibling code still matching. Safe to call
    unconditionally on create/bulk-create too — a fresh spool has no rows to
    delete."""
    await db.execute(delete(SpoolCode).where(SpoolCode.spool_id == spool_id))
    if not barcode:
        await db.commit()
        return
    code, kind, all_codes = await resolve_codes_for_barcode(barcode, settings)
    await persist_spool_codes(db, spool_id, code, kind, all_codes, primary_is_refill=primary_is_refill)
