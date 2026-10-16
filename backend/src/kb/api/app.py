"""FastAPI application factory. Routers live in sibling modules and are mounted here."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kb import __version__
from kb.config import get_settings
from kb.db import close_pool, migrate, open_pool
from kb.logging import setup_logging


def create_app(*, run_migrations: bool = True) -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_json)

    # Optional sub-applications (e.g. the MCP server) contribute their own lifespans.
    extra_lifespans = []
    mcp_app = None
    try:
        from kb.mcp.server import build_mcp_app

        mcp_app = build_mcp_app()
        extra_lifespans.append(mcp_app)
    except ImportError:  # pragma: no cover - MCP extra not installed
        mcp_app = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if run_migrations:
            await migrate(settings)
        await open_pool(settings)
        from kb.events import broker

        async with AsyncExitStack() as stack:
            for sub in extra_lifespans:
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            await broker.start()
            # Dev / single-process mode: run ingestion workers inside the API process. Safe
            # with several API workers too, since jobs are claimed with SKIP LOCKED.
            from kb.ingest.worker import embedded_worker_enabled, start_embedded_worker

            stop_worker = await start_embedded_worker() if embedded_worker_enabled() else None
            try:
                yield
            finally:
                if stop_worker is not None:
                    await stop_worker()
                await broker.stop()
                await close_pool()

    app = FastAPI(
        title="Knowledge Base",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from kb.api import ask, collections, documents, search, system

    for module in (system, collections, documents, search, ask):
        app.include_router(module.router, prefix="/api")

    if mcp_app is not None:
        app.add_route("/mcp", mcp_app, include_in_schema=False)

    _mount_spa(app, Path(settings.static_dir))
    return app


def _mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built frontend with history-API fallback, if it has been built."""
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
