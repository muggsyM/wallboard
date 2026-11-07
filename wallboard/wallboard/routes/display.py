"""Display endpoints: GET /, WS /ws and loopback-only static mounts."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from .. import auth

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
WS_FORBIDDEN = 4403

router = APIRouter()


class LoopbackStatic:
    """StaticFiles that only serves loopback clients (the HTTP dependency doesn't cover mounts)."""

    def __init__(self, directory: Path) -> None:
        self._files = StaticFiles(directory=directory)

    async def __call__(self, scope, receive, send) -> None:
        client = scope.get("client")
        if scope["type"] != "http" or not client or client[0] not in auth.LOOPBACK_HOSTS:
            if scope["type"] == "http":
                await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
            return
        await self._files(scope, receive, send)


def mount_static(app) -> None:
    app.mount("/static/display", LoopbackStatic(WEB_DIR / "display"), name="static-display")
    app.mount("/static/shared", LoopbackStatic(WEB_DIR / "shared"), name="static-shared")


@router.get("/", dependencies=[Depends(auth.require_loopback)])
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "display" / "index.html")


@router.websocket("/ws")
async def display_ws(ws: WebSocket) -> None:
    await ws.accept()
    if not ws.client or ws.client.host not in auth.LOOPBACK_HOSTS:
        await ws.close(code=WS_FORBIDDEN)
        return
    hub = ws.app.state.hub
    await hub.connect(ws)
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if not isinstance(msg, dict) or msg.get("type") != "action":
                continue
            action_id, entity_id = msg.get("id"), msg.get("entity_id")
            if not isinstance(action_id, str) or not isinstance(entity_id, str):
                continue
            if hub.on_action is None:
                result = {"type": "action_result", "id": action_id, "ok": False, "error": "not available"}
            else:
                try:
                    result = await hub.on_action(entity_id, action_id)
                except Exception:
                    result = {"type": "action_result", "id": action_id, "ok": False, "error": "action failed"}
            await ws.send_json(result)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.disconnect(ws)
