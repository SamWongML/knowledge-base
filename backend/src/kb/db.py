"""Async PostgreSQL access: one connection pool per process plus a tiny migration runner."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources

import structlog
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from kb.config import Settings, get_settings

log = structlog.get_logger(__name__)

_pool: AsyncConnectionPool | None = None

# Arbitrary constant used as the advisory-lock key so concurrent processes never
# race to apply migrations.
_MIGRATION_LOCK = 7_402_118_311


async def _configure(conn: AsyncConnection) -> None:
    await conn.set_autocommit(True)


def _conninfo(settings: Settings) -> str:
    return settings.database_url


async def open_pool(settings: Settings | None = None) -> AsyncConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    settings = settings or get_settings()
    _pool = AsyncConnectionPool(
        _conninfo(settings),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        kwargs={
            "row_factory": dict_row,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        },
        configure=_configure,
        open=False,
        name="kb",
    )
    await _pool.open(wait=True, timeout=30)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("database pool is not open; call open_pool() first")
    return _pool


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    """Borrow an autocommit connection. Use ``async with conn.transaction():`` for atomic work."""
    async with pool().connection() as conn:
        yield conn


def _migration_files() -> list[tuple[str, str]]:
    root = resources.files("kb") / "migrations"
    files = sorted(p for p in root.iterdir() if p.name.endswith(".sql"))
    return [(p.name, p.read_text(encoding="utf-8")) for p in files]


async def migrate(settings: Settings | None = None) -> list[str]:
    """Apply pending SQL migrations in order. Safe to call from many processes at once."""
    settings = settings or get_settings()
    applied: list[str] = []
    async with await AsyncConnection.connect(_conninfo(settings), autocommit=True) as conn:
        await conn.execute("select pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            await conn.execute(
                """
                create table if not exists schema_migrations (
                    name text primary key,
                    checksum text not null,
                    applied_at timestamptz not null default now()
                )
                """
            )
            cur = await conn.execute("select name from schema_migrations")
            done = {row[0] for row in await cur.fetchall()}
            for name, sql in _migration_files():
                if name in done:
                    continue
                sql = sql.replace("{{EMBEDDING_DIM}}", str(settings.embedding_dim))
                async with conn.transaction():
                    await conn.execute(sql)  # type: ignore[arg-type]
                    await conn.execute(
                        "insert into schema_migrations(name, checksum) values (%s, %s)",
                        (name, hashlib.sha256(sql.encode()).hexdigest()),
                    )
                applied.append(name)
                log.info("migration.applied", name=name)
        finally:
            await conn.execute("select pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return applied
