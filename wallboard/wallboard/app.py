"""Application factory. Later tasks add routers and a lifespan with background services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .actions import ActionHandler
from .auth import LoginLimiter
from .config import ConfigStore
from .ha_client import HAClient
from .hub import Hub
from .routes import admin, display


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.ha.start()
    try:
        yield
    finally:
        await app.state.ha.stop()


def create_app(data_dir: Path) -> FastAPI:
    app = FastAPI(title="wallboard", lifespan=_lifespan)
    app.state.store = ConfigStore(Path(data_dir))
    app.state.login_limiter = LoginLimiter()
    app.state.hub = Hub()
    app.state.hub.set_topic("config", app.state.store.public_config())
    app.state.ha = HAClient(app.state.store, app.state.hub)
    app.state.hub.on_action = ActionHandler(app.state.store, app.state.hub, app.state.ha).handle
    app.include_router(admin.router)
    app.include_router(display.router)
    display.mount_static(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app
