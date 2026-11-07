"""Application factory. Later tasks add routers and a lifespan with background services."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .auth import LoginLimiter
from .config import ConfigStore
from .hub import Hub
from .routes import admin, display


def create_app(data_dir: Path) -> FastAPI:
    app = FastAPI(title="wallboard")
    app.state.store = ConfigStore(Path(data_dir))
    app.state.login_limiter = LoginLimiter()
    app.state.hub = Hub()
    app.state.hub.set_topic("config", app.state.store.public_config())
    app.include_router(admin.router)
    app.include_router(display.router)
    display.mount_static(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app
