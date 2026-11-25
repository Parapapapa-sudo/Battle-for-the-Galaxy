"""HTTP API (Mini App / служебные endpoint'ы)."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response

from app import db
from app.config import Settings
from app.logging import configure_logging


def create_app(settings: Settings) -> FastAPI:
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await db.engine.dispose()

    app = FastAPI(title="Space MMO API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    from adapters.http import admin_api, miniapp_api, miniapp_page, payments_api

    app.include_router(admin_api.router)
    app.include_router(miniapp_api.router)
    app.include_router(payments_api.router)
    app.include_router(miniapp_page.router)

    @app.get("/metrics")
    async def metrics() -> Any:
        from app.metrics import render_metrics

        payload, media_type = render_metrics()
        return Response(content=payload, media_type=media_type)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        db_ok = await db.health_check()
        status = "ok" if db_ok else "degraded"
        return {"status": status, "db": "ok" if db_ok else "error", "env": settings.app_env}

    return app
