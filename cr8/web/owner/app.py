"""Tailnet-only ASGI factory for crate-owner."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..common.database import migrate
from ..common.events import EventBus
from ..common.runtime import check_runtime
from ..common.security import (
    CSRFMiddleware,
    RangeLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from ..common.settings import AppSettings
from .routes import router
from .routes_admin import router as admin_router
from .routes_api import router as api_router
from .routes_assignments import router as assignments_router
from .routes_auth import router as auth_router
from .routes_collections import router as collections_router
from .routes_fragments import router as fragments_router
from .routes_library import router as library_router
from .routes_shares import router as shares_router
from .routes_upload import router as upload_router


def create_app(settings: AppSettings | None = None) -> FastAPI:
    resolved = settings or AppSettings.from_env("owner")
    if resolved.app_kind != "owner":
        raise RuntimeError("owner app requires owner settings")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        check_runtime()
        migrate(resolved.db_path)
        app.state.settings = resolved
        app.state.events = EventBus()
        yield

    app = FastAPI(
        title="cr8 owner",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.events = EventBus()
    app.add_middleware(RateLimitMiddleware, settings=resolved)
    app.add_middleware(RangeLimitMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount(
        "/static",
        StaticFiles(directory=resolved.static_root, check_dir=True),
        name="static",
    )
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(library_router)
    app.include_router(fragments_router)
    app.include_router(collections_router)
    app.include_router(api_router)
    app.include_router(admin_router)
    app.include_router(assignments_router)
    app.include_router(shares_router)
    app.include_router(upload_router)
    return app
