"""FastAPI application factory. Routers live in sibling modules and are mounted here."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import psycopg
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import PoolTimeout
from starlette.routing import Route

from kb import __version__
from kb.api.limits import BodySizeLimit
from kb.config import get_settings
from kb.db import close_pool, migrate, open_pool
from kb.logging import setup_logging

log = structlog.get_logger(__name__)


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
    app.add_middleware(BodySizeLimit, limit_for=_body_limit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from kb.api import ask, collections, documents, search, system
    from kb.api.deps import require_token

    # When KB_API_TOKEN is set every route needs it (the knowledge base is private data);
    # only /api/health stays open for container and uptime checks.
    app.include_router(system.public_router, prefix="/api")
    for module in (system, collections, documents, search, ask):
        app.include_router(module.router, prefix="/api", dependencies=[Depends(require_token)])

    if mcp_app is not None:
        # A plain Route (not a Mount) so ``/mcp`` is served without a ``/mcp/`` redirect;
        # Starlette treats a non-function endpoint as a raw ASGI app.
        app.router.routes.append(Route("/mcp", endpoint=mcp_app, include_in_schema=False))

    _add_error_handlers(app)
    _mount_spa(app, Path(settings.static_dir))
    return app


def _body_limit(path: str) -> int:
    s = get_settings()
    return (s.max_request_mb if path.endswith("/upload") else s.max_upload_mb) << 20


def _add_error_handlers(app: FastAPI) -> None:
    """Database overload/outage is a 503 the client may retry, not an opaque 500."""

    @app.exception_handler(psycopg.OperationalError)
    async def db_unavailable(_request: Request, exc: psycopg.OperationalError) -> JSONResponse:
        if isinstance(exc, PoolTimeout):
            detail = "server busy: no database connection available, retry shortly"
        elif isinstance(exc, psycopg.errors.QueryCanceled):
            detail = "database query timed out"
        else:
            detail = "database unavailable"
        log.warning("api.db_error", error_type=type(exc).__name__, error=str(exc)[:300])
        return JSONResponse({"detail": detail}, status_code=503, headers={"Retry-After": "2"})


def _mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built frontend with history-API fallback, if it has been built."""
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith(("api/", "mcp/")):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")  # never HTML for APIs
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
