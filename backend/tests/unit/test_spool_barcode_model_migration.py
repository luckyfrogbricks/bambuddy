"""Model + migration tests for the typed spool code columns (#2648).

Covers the DB-layer guarantees the feature stands on:

- `_migrate_add_spool_code_columns` upgrades a pre-feature database (spool
  table with none of the code columns) and is idempotent, with the dialect
  pinned so the test's verdict doesn't depend on the developer's
  DATABASE_URL (the exact failure mode a Postgres-based dev environment hit
  reviewing PR #1895).
- A fresh create_all() install carries the columns + their indexes.
- The TEMPORARY interim-schema teardown (`_migrate_drop_interim_spool_code_schema`,
  stripped before the upstream PR) routes the old primary code into the right
  typed column, carries the refill flag, deliberately does NOT migrate
  sibling rows (they contained other package sizes' codes — the cross-size
  contamination this schema change eliminates), and is a no-op on databases
  that never ran the interim branch.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import (
    _migrate_add_spool_code_columns,
    _migrate_drop_interim_spool_code_schema,
)

CODE_COLUMNS = ("gtin_code", "asin_code", "sku_code", "other_code", "bought_as_refill")


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    yield eng
    await eng.dispose()


async def _spool_columns(conn) -> set[str]:
    rows = (await conn.execute(text("PRAGMA table_info(spool)"))).fetchall()
    return {r[1] for r in rows}


async def _spool_indexes(conn) -> set[str]:
    return {r[1] for r in (await conn.execute(text("PRAGMA index_list(spool)"))).fetchall()}


class TestAddSpoolCodeColumnsMigration:
    async def test_adds_columns_and_indexes_to_pre_feature_table(self, engine):
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50))"))
            await conn.execute(text("INSERT INTO spool (id, material) VALUES (1, 'PLA')"))

            assert not (set(CODE_COLUMNS) & await _spool_columns(conn))
            await _migrate_add_spool_code_columns(conn)
            assert set(CODE_COLUMNS) <= await _spool_columns(conn)

            index_names = await _spool_indexes(conn)
            assert {"ix_spool_gtin_code", "ix_spool_asin_code", "ix_spool_sku_code"} <= index_names

    async def test_is_idempotent(self, engine):
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50))"))
            await _migrate_add_spool_code_columns(conn)
            await _migrate_add_spool_code_columns(conn)  # must not raise
            assert set(CODE_COLUMNS) <= await _spool_columns(conn)

    async def test_fresh_create_all_has_columns_and_indexes(self):
        from backend.app.core.database import Base

        eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        try:
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                assert set(CODE_COLUMNS) <= await _spool_columns(conn)
                index_names = await _spool_indexes(conn)
                assert {"ix_spool_gtin_code", "ix_spool_asin_code", "ix_spool_sku_code"} <= index_names
                # The interim table must NOT exist on a fresh install.
                tables = {
                    r[0]
                    for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()
                }
                assert "spool_code" not in tables
        finally:
            await eng.dispose()


class TestDropInterimSpoolCodeSchema:
    """TEMPORARY (local-only) migration — these tests are stripped together
    with the migration before the upstream PR."""

    async def _build_interim_schema(self, conn):
        await conn.execute(
            text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50), barcode VARCHAR(64))")
        )
        await conn.execute(
            text(
                "CREATE TABLE spool_code (id INTEGER PRIMARY KEY, spool_id INTEGER, code VARCHAR(64), "
                "kind VARCHAR(16), is_refill BOOLEAN, is_primary BOOLEAN)"
            )
        )
        await _migrate_add_spool_code_columns(conn)

    async def test_routes_primary_codes_and_refill_flag(self, engine):
        async with engine.begin() as conn:
            await self._build_interim_schema(conn)
            # Roll 1: scanned a GTIN, bought as refill. Its sibling rows
            # include another package's SKU that must NOT be migrated.
            await conn.execute(text("INSERT INTO spool (id, material, barcode) VALUES (1, 'PLA', '6938936716785')"))
            await conn.execute(
                text(
                    "INSERT INTO spool_code (spool_id, code, kind, is_refill, is_primary) VALUES "
                    "(1, '6938936716785', 'gtin', 1, 1), (1, 'OTHERSIZESKU', 'sku', 0, 0)"
                )
            )
            # Roll 2: scanned an alphanumeric code (stored as sku kind).
            await conn.execute(text("INSERT INTO spool (id, material, barcode) VALUES (2, 'PLA', 'ALZMNTABS01')"))
            await conn.execute(
                text(
                    "INSERT INTO spool_code (spool_id, code, kind, is_refill, is_primary) VALUES "
                    "(2, 'ALZMNTABS01', 'sku', 0, 1)"
                )
            )
            # Roll 3: no codes at all — untouched.
            await conn.execute(text("INSERT INTO spool (id, material) VALUES (3, 'PLA')"))

            await _migrate_drop_interim_spool_code_schema(conn)

            rows = (
                await conn.execute(
                    text("SELECT id, gtin_code, sku_code, other_code, bought_as_refill FROM spool ORDER BY id")
                )
            ).fetchall()
            by_id = {r[0]: r for r in rows}
            assert by_id[1][1] == "6938936716785"  # gtin routed
            assert by_id[1][2] is None  # sibling SKU deliberately NOT migrated
            assert bool(by_id[1][4]) is True  # refill flag carried
            assert by_id[2][2] == "ALZMNTABS01"  # sku-candidate routed to sku_code
            assert bool(by_id[2][4]) is False
            assert by_id[3][1] is None and by_id[3][2] is None

            # Interim schema gone.
            tables = {
                r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()
            }
            assert "spool_code" not in tables
            assert "barcode" not in await _spool_columns(conn)

    async def test_asin_barcode_routes_to_asin_code(self, engine):
        async with engine.begin() as conn:
            await self._build_interim_schema(conn)
            await conn.execute(text("INSERT INTO spool (id, material, barcode) VALUES (1, 'PLA', 'B0CJLR62MF')"))
            await conn.execute(
                text(
                    "INSERT INTO spool_code (spool_id, code, kind, is_refill, is_primary) VALUES "
                    "(1, 'B0CJLR62MF', 'sku', 0, 1)"
                )
            )
            await _migrate_drop_interim_spool_code_schema(conn)
            row = (await conn.execute(text("SELECT asin_code, sku_code FROM spool WHERE id = 1"))).fetchone()
            assert row[0] == "B0CJLR62MF"
            assert row[1] is None

    async def test_noop_without_interim_table(self, engine):
        """A database that never ran the interim branch (fresh install, or
        upstream once this migration is stripped) must pass through untouched."""
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50))"))
            await _migrate_add_spool_code_columns(conn)
            await _migrate_drop_interim_spool_code_schema(conn)  # must not raise
            assert set(CODE_COLUMNS) <= await _spool_columns(conn)
