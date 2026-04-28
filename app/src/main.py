"""FastAPI entry point.

Lifespan:
  1. Build ISAPI camera registry.
  2. Start alternating-close background task.
  3. Start Telegram bot polling.

Auth: every request goes through `JWTAuthMiddleware`. Swagger / ReDoc
exposure is controlled by `ENABLE_DOCS` in `.env`.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.bot.bot import bot_runtime
from src.config import get_settings
from src.isapi.registry import registry
from src.routers import (
    admin_ui,
    auth,
    barrier,
    export,
    isapi_events,
    links,
    logs,
    phones,
    plates,
)
from src.scheduler.alternating_close import scheduler
from src.security import JWTAuthMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("shlakbaum")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Building camera registry…")
    registry.build()
    logger.info("Starting alternating-close scheduler…")
    await scheduler.start()
    logger.info("Starting Telegram bot…")
    await bot_runtime.start()
    try:
        yield
    finally:
        logger.info("Shutting down Telegram bot…")
        await bot_runtime.stop()
        logger.info("Shutting down scheduler…")
        await scheduler.stop()
        logger.info("Closing camera clients…")
        await registry.shutdown()


settings = get_settings()

# Docs URLs are turned off entirely when ENABLE_DOCS=false; this also stops
# /openapi.json from being served, so the schema is not leaked.
_docs_kwargs: dict = (
    {}
    if settings.enable_docs
    else {"docs_url": None, "redoc_url": None, "openapi_url": None}
)

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
    **_docs_kwargs,
)

# Auth runs before any route handler. Public paths (login, /static, camera
# push, docs when enabled) are whitelisted inside the middleware itself.
app.add_middleware(JWTAuthMiddleware)

app.mount("/static", StaticFiles(directory="static"), name="static")

# API
app.include_router(auth.router)
app.include_router(plates.router)
app.include_router(phones.router)
app.include_router(links.router)
app.include_router(logs.router)
app.include_router(export.router)
app.include_router(barrier.router)
app.include_router(isapi_events.router)

# Admin UI (HTML)
app.include_router(admin_ui.router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
