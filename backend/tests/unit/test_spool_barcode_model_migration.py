"""Model + migration tests for barcode support (#2648).

Covers the three DB-layer guarantees the feature stands on:

- `_migrate_add_spool_barcode` upgrades a pre-feature database (spool table
  with no barcode column) and is idempotent, with the dialect pinned so the
  test's verdict doesn't depend on the developer's DATABASE_URL (the exact
  failure mode a Postgres-based dev environment hit reviewing PR #1895).
- The new `spool_code` table arrives from create_all() with its constraints
  live: the kind CHECK (generated from SpoolCodeKind) and the
  (spool_id, code) uniqueness.
- `Spool.codes` is lazy="selectin", so `linked_codes` / `is_refill` are safe
  to read on any normally-queried instance in an async context — no per-route
  selectinload() needed, which is the whole point of the eager default.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.database import _migrate_add_spool_barcode
from backend.app.models.spool import Spool
from backend.app.models.spool_code import SpoolCode, SpoolCodeKind


@pytest.fixture(autouse=True)
def force_sqlite_dialect(monkeypatch):
    """Force the SQLite branch regardless of test env settings."""
    from backend.app.core import db_dialect

    monkeypatch.setattr(db_dialect, "is_sqlite", lambda: True)
    monkeypatch.setattr(db_dialect, "is_postgres", lambda: False)
    from backend.app.core import database as database_module

    monkeypatch.setattr(database_module, "is_sqlite", lambda: True)


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    yield eng
    await eng.dispose()


@pytest.fixture
async def migrated_engine():
    """Engine with the full current schema (what a fresh install gets)."""
    from backend.app.core.database import Base

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _spool_columns(conn) -> set[str]:
    rows = (await conn.execute(text("PRAGMA table_info(spool)"))).fetchall()
    return {r[1] for r in rows}


class TestAddSpoolBarcodeMigration:
    @pytest.mark.asyncio
    async def test_adds_column_and_index_to_pre_feature_table(self, engine):
        async with engine.begin() as conn:
            # A pre-feature spool table: only the columns the migration touches.
            await conn.execute(text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50))"))
            await conn.execute(text("INSERT INTO spool (id, material) VALUES (1, 'PLA')"))

            assert "barcode" not in await _spool_columns(conn)
            await _migrate_add_spool_barcode(conn)
            assert "barcode" in await _spool_columns(conn)

            # PRAGMA index_list rows are (seq, name, unique, origin, partial).
            index_names = {r[1] for r in (await conn.execute(text("PRAGMA index_list(spool)"))).fetchall()}
            assert "ix_spool_barcode" in index_names

            # Existing rows survive with barcode NULL.
            row = (await conn.execute(text("SELECT id, barcode FROM spool WHERE id = 1"))).one()
            assert row == (1, None)

    @pytest.mark.asyncio
    async def test_is_idempotent(self, engine):
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE spool (id INTEGER PRIMARY KEY, material VARCHAR(50))"))
            await _migrate_add_spool_barcode(conn)
            # Second run must be a no-op, not an error (the "duplicate column"
            # failure is swallowed by _safe_execute like every other migration).
            await _migrate_add_spool_barcode(conn)
            assert "barcode" in await _spool_columns(conn)


class TestSpoolCodeTableConstraints:
    @pytest.mark.asyncio
    async def test_kind_check_constraint_rejects_unknown_kind(self, migrated_engine):
        session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
        async with session_factory() as session:
            spool = Spool(material="PLA")
            session.add(spool)
            await session.commit()

            session.add(SpoolCode(spool_id=spool.id, code="12345678905", kind="bogus"))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_kind_check_constraint_accepts_every_enum_value(self, migrated_engine):
        session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
        async with session_factory() as session:
            spool = Spool(material="PLA")
            session.add(spool)
            await session.commit()

            for i, kind in enumerate(SpoolCodeKind):
                session.add(SpoolCode(spool_id=spool.id, code=f"CODE{i}", kind=kind.value))
            await session.commit()

    @pytest.mark.asyncio
    async def test_same_code_twice_on_one_spool_is_rejected(self, migrated_engine):
        session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
        async with session_factory() as session:
            spool = Spool(material="PLA")
            session.add(spool)
            await session.commit()

            session.add(SpoolCode(spool_id=spool.id, code="12345678905", kind="gtin", is_primary=True))
            await session.commit()
            session.add(SpoolCode(spool_id=spool.id, code="12345678905", kind="gtin"))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_same_code_on_two_spools_is_fine(self, migrated_engine):
        """Two physical spools of the same product share a retail barcode —
        uniqueness is per spool, not global."""
        session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
        async with session_factory() as session:
            a, b = Spool(material="PLA"), Spool(material="PLA")
            session.add_all([a, b])
            await session.commit()

            session.add_all(
                [
                    SpoolCode(spool_id=a.id, code="12345678905", kind="gtin", is_primary=True),
                    SpoolCode(spool_id=b.id, code="12345678905", kind="gtin", is_primary=True),
                ]
            )
            await session.commit()


class TestCodesEagerLoading:
    @pytest.mark.asyncio
    async def test_properties_readable_without_explicit_selectinload(self, migrated_engine):
        """The exact failure class from PR #1895's fifth review: a route that
        forgets selectinload(Spool.codes) lazily touches `codes` during
        response serialization and dies with MissingGreenlet on an async
        session. lazy="selectin" makes every plain query safe."""
        session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
        async with session_factory() as session:
            spool = Spool(material="PLA", barcode="36000291452")
            session.add(spool)
            await session.commit()
            session.add_all(
                [
                    SpoolCode(spool_id=spool.id, code="36000291452", kind="gtin", is_primary=True),
                    SpoolCode(spool_id=spool.id, code="6938936716785", kind="gtin", is_refill=True),
                ]
            )
            await session.commit()

        async with session_factory() as session:
            # A deliberately plain query — no .options() at all.
            loaded = (await session.execute(select(Spool).where(Spool.id == spool.id))).scalar_one()
            assert [c.code for c in loaded.linked_codes] == ["6938936716785"]
            assert loaded.is_refill is False  # primary code isn't the refill one

    @pytest.mark.asyncio
    async def test_is_refill_true_when_primary_code_is_refill(self, migrated_engine):
        session_factory = async_sessionmaker(migrated_engine, expire_on_commit=False)
        async with session_factory() as session:
            spool = Spool(material="PLA", barcode="6938936716785")
            session.add(spool)
            await session.commit()
            session.add(
                SpoolCode(spool_id=spool.id, code="6938936716785", kind="gtin", is_primary=True, is_refill=True)
            )
            await session.commit()

        async with session_factory() as session:
            loaded = (await session.execute(select(Spool).where(Spool.id == spool.id))).scalar_one()
            assert loaded.is_refill is True
            assert loaded.linked_codes == []
