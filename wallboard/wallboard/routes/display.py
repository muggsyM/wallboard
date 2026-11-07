"""Display endpoints: GET /, WS /ws and loopback-only static mounts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.datastructures import Headers
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
        host = Headers(scope=scope).get("host") if scope["type"] == "http" else None
        if (
            scope["type"] != "http"
            or not client
            or client[0] not in auth.LOOPBACK_HOSTS
            or not auth.is_loopback_host_header(host)
        ):
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
    if (
        not ws.client
        or ws.client.host not in auth.LOOPBACK_HOSTS
        or not auth.is_loopback_host_header(ws.headers.get("host"))
        or not auth.is_allowed_display_origin(ws.headers.get("origin"))
    ):
        await ws.close(code=WS_FORBIDDEN)
        return
    hub = ws.app.state.hub
    await hub.connect(ws)
    try:
        while True:
            frame = await ws.receive()
            if frame["type"] == "websocket.disconnect":
                break
            try:
                msg = json.loads(frame["text"])
            except (KeyError, TypeError, ValueError):
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


_MEDIA_RE = re.compile(r"[0-9a-f]{32}(\.bg|\.thumb)?\.jpg")


@router.get("/media/{file}")
async def media(file: str, request: Request) -> FileResponse:
    client = request.client
    loopback = (
        client is not None
        and client.host in auth.LOOPBACK_HOSTS
        and auth.is_loopback_host_header(request.headers.get("host"))
    )
    if not loopback and not auth.valid_session_cookie(
        request.app.state.store.secrets.session_secret, request.cookies.get(auth.COOKIE_NAME)
    ):
        raise HTTPException(status_code=403, detail="forbidden")
    if not _MEDIA_RE.fullmatch(file):
        raise HTTPException(status_code=404, detail="not found")
    path = request.app.state.photos.path(file)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=31536000, immutable"},
    )
