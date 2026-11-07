"""Application factory. Later tasks add routers and a lifespan with background services."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .actions import ActionHandler
from .auth import LoginLimiter
from .body_limit import BodyLimitMiddleware
from .config import ConfigStore
from .gcal import GoogleCalendarService
from .google_oauth import OAuthStates, RedactCallbackParams
from .ha_client import HAClient
from .hub import Hub
from .photos import PhotoStore
from .routes import admin, display, photos_api


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.ha.start()
    await app.state.gcal.start()
    try:
        yield
    finally:
        await app.state.gcal.stop()
        await app.state.ha.stop()


def create_app(data_dir: Path) -> FastAPI:
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactCallbackParams) for f in access_log.filters):
        access_log.addFilter(RedactCallbackParams())
    # No interactive API docs: nothing to explore for strangers on the LAN.
    app = FastAPI(title="wallboard", lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimitMiddleware)
    app.state.store = ConfigStore(Path(data_dir))
    app.state.login_limiter = LoginLimiter()
    app.state.oauth_states = OAuthStates(lambda: app.state.store.secrets.session_secret)
    app.state.hub = Hub()
    app.state.hub.build = display.compute_build_id()
    app.state.hub.set_topic("config", app.state.store.public_config())
    app.state.photos = PhotoStore(Path(data_dir), app.state.hub)
    app.state.photos.start()
    app.state.ha = HAClient(app.state.store, app.state.hub)
    dev_calendar = os.environ.get("WALLBOARD_DEV_CALENDAR")  # dev only: sample events, no Google
    app.state.gcal = GoogleCalendarService(
        app.state.store, app.state.hub, dev_calendar=Path(dev_calendar) if dev_calendar else None
    )
    app.state.hub.on_action = ActionHandler(app.state.store, app.state.hub, app.state.ha).handle
    app.include_router(admin.router)
    app.include_router(photos_api.router)
    app.include_router(display.router)
    display.mount_static(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app
