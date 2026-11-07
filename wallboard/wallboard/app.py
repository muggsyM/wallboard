"""Application factory. Later tasks add routers and a lifespan with background services."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .config import ConfigStore


def create_app(data_dir: Path) -> FastAPI:
    app = FastAPI(title="wallboard")
    app.state.store = ConfigStore(Path(data_dir))

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app
