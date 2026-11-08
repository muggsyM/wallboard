"""Display endpoints: GET /, WS /ws and loopback-only static mounts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.requests import HTTPConnection
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from .. import auth

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
WS_FORBIDDEN = 4403
# Code and pages revalidate on every load (cheap ETag/Last-Modified 304s), so after an update the
# browser never keeps running old modules from its heuristic disk cache. /media stays immutable.
REVALIDATE = {"Cache-Control": "no-cache"}

router = APIRouter()


def compute_build_id(web_dir: Path = WEB_DIR) -> str:
    """Short sha256 over the sorted relative paths and contents of every file under web/.

    Sent in each snapshot; the display reloads itself when it changes (ruling R26)."""
    h = hashlib.sha256()
    root = Path(web_dir)
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        h.update(len(rel).to_bytes(4, "big") + rel)
        data = path.read_bytes()
        h.update(len(data).to_bytes(8, "big") + data)
    return h.hexdigest()[:12]


class _RevalidatingStatic(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(REVALIDATE)
        return response


class GuardedStatic:
    """StaticFiles behind an access check (the HTTP dependencies don't cover mounts).

    Serves a valid admin session (the settings page imports display and shared modules) and,
    when `loopback` is true, also the loopback display browser (loopback peer and Host).
    """

    def __init__(self, directory: Path, *, loopback: bool) -> None:
        self._files = _RevalidatingStatic(directory=directory)
        self._loopback = loopback

    def _allowed(self, scope) -> bool:
        conn = HTTPConnection(scope)
        if self._loopback:
            client = scope.get("client")
            if client and client[0] in auth.LOOPBACK_HOSTS and auth.is_loopback_host_header(conn.headers.get("host")):
                return True
        secret = scope["app"].state.store.secrets.session_secret
        return auth.valid_session_cookie(secret, conn.cookies.get(auth.COOKIE_NAME))

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        if not self._allowed(scope):
            await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
            return
        await self._files(scope, receive, send)


def mount_static(app) -> None:
    app.mount("/static/display", GuardedStatic(WEB_DIR / "display", loopback=True), name="static-display")
    app.mount("/static/shared", GuardedStatic(WEB_DIR / "shared", loopback=True), name="static-shared")
    app.mount("/static/admin", GuardedStatic(WEB_DIR / "admin", loopback=False), name="static-admin")


@router.get("/", dependencies=[Depends(auth.require_display_viewer)])
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "display" / "index.html", headers=REVALIDATE)


def _ws_allowed(ws: WebSocket) -> bool:
    """The kiosk on the Pi (loopback peer, Host and Origin), or a logged-in admin whose
    browser Origin matches the Host it connected to (blocks other sites riding the cookie)."""
    origin = ws.headers.get("origin")
    if auth.is_loopback_request(ws) and auth.is_allowed_display_origin(origin):
        return True
    return auth.has_admin_session(ws) and auth.is_same_origin(origin, ws.headers.get("host"))


@router.websocket("/ws")
async def display_ws(ws: WebSocket) -> None:
    await ws.accept()
    if not _ws_allowed(ws):
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
    if not auth.is_loopback_request(request) and not auth.has_admin_session(request):
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
