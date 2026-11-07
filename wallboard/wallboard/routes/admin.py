"""Admin routes: login/logout, a read-only config endpoint and Google sign-in."""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import auth, google_oauth
from ..config import GoogleAccount

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
LOGIN_HTML = WEB_DIR / "admin" / "login.html"
ADMIN_CSS = WEB_DIR / "admin" / "admin.css"

router = APIRouter()

NO_PASSWORD_MSG = "No admin password is set. Run <code>python -m wallboard set-password</code> on the server."
WRONG_PASSWORD_MSG = "Wrong password."


def _login_page(request: Request, next_: str | None, error: str | None, status: int = 200) -> HTMLResponse:
    store = request.app.state.store
    message = html.escape(error) if error else None
    if not store.secrets.admin_password_hash:
        message = NO_PASSWORD_MSG
    page = LOGIN_HTML.read_text(encoding="utf-8")
    page = page.replace("{{message}}", f'<p class="error" role="alert">{message}</p>' if message else "")
    page = page.replace("{{action}}", html.escape(f"/login?next={quote(auth.safe_next(next_), safe='/')}"))
    return HTMLResponse(page, status_code=status)


@router.get("/admin-static/admin.css", include_in_schema=False)
async def admin_css() -> FileResponse:
    return FileResponse(ADMIN_CSS, media_type="text/css")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    return _login_page(request, next, None)


@router.post("/login")
async def login(request: Request, password: str = Form(""), next: str | None = None) -> Response:
    store = request.app.state.store
    limiter = request.app.state.login_limiter
    ip = request.client.host if request.client else "unknown"
    wait = limiter.begin(ip)  # synchronous reservation, before any await
    if wait:
        resp = _login_page(request, next, f"Too many attempts. Try again in {wait} seconds.", status=429)
        resp.headers["Retry-After"] = str(wait)
        return resp
    ok = await run_in_threadpool(auth.verify_password, password, store.secrets.admin_password_hash)
    if not ok:
        return _login_page(request, next, WRONG_PASSWORD_MSG, status=401)
    limiter.success(ip)
    resp = RedirectResponse(auth.safe_next(next), status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.new_session_cookie(store.secrets.session_secret),
        max_age=auth.SESSION_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=False,  # plain HTTP on the LAN
    )
    return resp


@router.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return resp


@router.get("/api/admin/config", dependencies=[Depends(auth.require_admin)])
async def get_config(request: Request) -> dict:
    return request.app.state.store.config.model_dump(mode="json")


# ---- Google sign-in ----


class StartBody(BaseModel):
    member_id: str


class PasteBody(BaseModel):
    url: str


def _allow_callback(request: Request) -> None:
    """Callback is open to the loopback browser on the wallboard itself, or to a logged-in admin."""
    peer = request.client.host if request.client else None
    if peer in auth.LOOPBACK_HOSTS and auth.is_loopback_host_header(request.headers.get("host")):
        return
    if auth.valid_session_cookie(request.app.state.store.secrets.session_secret, request.cookies.get(auth.COOKIE_NAME)):
        return
    raise HTTPException(status_code=403, detail="forbidden")


def _consume_state(request: Request, state: str) -> str:
    app = request.app
    try:
        return app.state.oauth_states.consume(state, {m.id for m in app.state.store.config.members})
    except google_oauth.InvalidState:
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in state; start again") from None


async def _connect(request: Request, code: str, state: str) -> None:
    """Validate state, exchange the code, then store token + account (read-modify-write after the await)."""
    app = request.app
    store = app.state.store
    member_id = _consume_state(request, state)
    try:
        refresh, email = await google_oauth.exchange_code(store.secrets, code)
    except google_oauth.NoRefreshToken as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except google_oauth.GoogleError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
    account_id = google_oauth.account_id_for(email)
    # Re-read right before writing; nothing is held across the HTTP call above.
    store.save_secrets(
        store.secrets.model_copy(
            update={"google_refresh_tokens": {**store.secrets.google_refresh_tokens, account_id: refresh}}
        )
    )
    cfg = store.config
    accounts = [a for a in cfg.google_accounts if a.id != account_id]
    accounts.append(GoogleAccount(id=account_id, email=email, member_id=member_id))
    store.save_config(cfg.model_copy(update={"google_accounts": accounts}))
    google_oauth.token_cache.invalidate(account_id)
    await app.state.hub.publish({"type": "config", "config": store.public_config()})
    app.state.gcal.refresh_now()


@router.post("/api/admin/google/start", dependencies=[Depends(auth.require_admin)])
async def google_start(request: Request, body: StartBody) -> dict:
    store = request.app.state.store
    secrets = store.secrets
    if not secrets.google_client_id or not secrets.google_client_secret:
        raise HTTPException(status_code=400, detail="Google client not configured")
    if body.member_id not in {m.id for m in store.config.members}:
        raise HTTPException(status_code=404, detail="unknown member")
    state = request.app.state.oauth_states.issue(body.member_id)
    return {"url": google_oauth.auth_url(secrets.google_client_id, state)}


@router.get("/api/google/callback", dependencies=[Depends(_allow_callback)])
async def google_callback(
    request: Request, state: str = "", code: str | None = None, error: str | None = None
) -> Response:
    if error is not None:
        _consume_state(request, state)
        return RedirectResponse(f"/settings?google_error={quote(error[:64], safe='')}#google", status_code=303)
    if not code:
        raise HTTPException(status_code=400, detail="missing code")
    await _connect(request, code, state)
    return RedirectResponse("/settings#google", status_code=303)


@router.post("/api/admin/google/paste", dependencies=[Depends(auth.require_admin)])
async def google_paste(request: Request, body: PasteBody) -> dict:
    bad = HTTPException(status_code=400, detail="Paste the full http://localhost:8080/api/google/callback?... address")
    try:
        parts = urlsplit(body.url.strip())
        parts.port  # raises ValueError when > 65535
    except ValueError:
        raise bad from None
    if parts.scheme != "http" or parts.hostname != "localhost" or parts.path != "/api/google/callback":
        raise bad
    if parts.username is not None or parts.password is not None:
        raise bad
    q = parse_qs(parts.query)
    code, state = q.get("code", [""])[0], q.get("state", [""])[0]
    if not code or not state:
        raise bad
    await _connect(request, code, state)
    return {"ok": True}


@router.delete("/api/admin/google/{account_id}", dependencies=[Depends(auth.require_admin)])
async def google_delete(request: Request, account_id: str) -> dict:
    store = request.app.state.store
    cfg = store.config
    if account_id not in {a.id for a in cfg.google_accounts}:
        raise HTTPException(status_code=404, detail="unknown account")
    store.save_config(
        cfg.model_copy(
            update={
                "google_accounts": [a for a in cfg.google_accounts if a.id != account_id],
                "calendars": [c for c in cfg.calendars if c.account_id != account_id],
            }
        )
    )
    secrets = store.secrets
    tokens = {k: v for k, v in secrets.google_refresh_tokens.items() if k != account_id}
    store.save_secrets(secrets.model_copy(update={"google_refresh_tokens": tokens}))
    google_oauth.token_cache.invalidate(account_id)
    await request.app.state.hub.publish({"type": "config", "config": store.public_config()})
    request.app.state.gcal.refresh_now()
    return {"ok": True}


@router.get("/api/admin/google/{account_id}/calendars", dependencies=[Depends(auth.require_admin)])
async def google_calendars(request: Request, account_id: str) -> list[dict]:
    if account_id not in {a.id for a in request.app.state.store.config.google_accounts}:
        raise HTTPException(status_code=404, detail="unknown account")
    try:
        return await request.app.state.gcal.list_calendars(account_id)
    except google_oauth.GoogleAuthExpired:
        raise HTTPException(status_code=409, detail="sign-in expired") from None
    except google_oauth.GoogleError as e:
        raise HTTPException(status_code=502, detail=str(e)) from None
