"""Test fixtures. Requires a ParadeDB/Postgres 18 server (see docker-compose / CI service).

``KB_TEST_DATABASE_URL`` points at an admin-capable database; a fresh database is created
for the session so tests never touch real data.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

ADMIN_URL = os.environ.get("KB_TEST_DATABASE_URL", "postgresql://kb:kb@localhost:5432/postgres")
TEST_DB = f"kb_test_{uuid.uuid4().hex[:8]}"

os.environ.update(
    {
        "KB_ENV": "test",
        "KB_DATABASE_URL": ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB,
        "KB_EMBEDDING_PROVIDER": "fake",
        "KB_RERANK_PROVIDER": "fake",
        "KB_LLM_PROVIDER": "fake",
        "KB_EMBEDDING_DIM": "256",
        "KB_STATIC_DIR": "/nonexistent",
        "KB_WORKER_CONCURRENCY": "2",
    }
)


@pytest.fixture(scope="session", autouse=True)
def database() -> object:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'create database "{TEST_DB}"')
    yield
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'drop database if exists "{TEST_DB}" with (force)')


@pytest.fixture(scope="session")
async def app(database: object) -> AsyncIterator[object]:
    from kb.api.app import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture(scope="session")
async def client(app: object) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:  # type: ignore[arg-type]
        yield c
