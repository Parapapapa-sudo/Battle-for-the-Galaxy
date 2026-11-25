"""HTTP API (Mini App / служебные endpoint'ы)."""
import uuid
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

    from adapters.http import admin_api, miniapp_api

    app.include_router(admin_api.router)
    app.include_router(miniapp_api.router)

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

    @app.post("/payments/webhook")
    async def payments_webhook(body: dict[str, Any]) -> dict[str, str]:
        """Платёжный webhook (провайдер-агностичный, идемпотентный — ТЗ).

        Провайдер подписывает запрос на своей стороне (заголовок/подпись —
        интеграция этапа релиза, пока доверенный канал).
        """
        from app.db import session_factory, transactional
        from app.market import process_payment_webhook

        async with session_factory() as session, transactional(session):
            await process_payment_webhook(
                session,
                provider=str(body.get("provider", "mock")),
                provider_payment_id=str(body["payment_id"]),
                character_id=uuid.UUID(str(body["character_id"])),
                offer_code=str(body["offer_code"]),
                status=str(body.get("status", "paid")),
            )
        return {"status": "ok"}

    return app
