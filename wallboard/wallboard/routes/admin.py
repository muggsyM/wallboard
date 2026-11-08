"""Admin routes: login/logout, the settings and photos pages, the settings API, HA test/entities and Google sign-in."""

from __future__ import annotations

import html
import secrets as _secrets
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import auth, google_oauth
from ..actions import CONTROLLABLE_DOMAINS
from ..config import Config, GoogleAccount, Secrets, config_revision
from ..ha_client import HAError, probe_connection, wanted_entities_for

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
LOGIN_HTML = WEB_DIR / "admin" / "login.html"
SETTINGS_HTML = WEB_DIR / "admin" / "settings.html"
PHOTOS_HTML = WEB_DIR / "admin" / "photos.html"
ADMIN_CSS = WEB_DIR / "admin" / "admin.css"
MIN_PASSWORD = 8
HA_TEST_TIMEOUT = 5.0

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
    return FileResponse(ADMIN_CSS, media_type="text/css", headers={"Cache-Control": "no-cache"})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None) -> HTMLResponse:
    return _login_page(request, next, None)


@router.post("/login")
async def login(request: Request, password: str = Form(""), next: str | None = None) -> Response:
    store = request.app.state.store
    limiter = request.app.state.login_limiter
    ip = request.client.host if request.client else "unknown"
    wait, token = limiter.begin(ip)  # synchronous reservation, before any await
    if wait:
        resp = _login_page(request, next, f"Too many attempts. Try again in {wait} seconds.", status=429)
        resp.headers["Retry-After"] = str(wait)
        return resp
    ok = await run_in_threadpool(auth.verify_password, password, store.secrets.admin_password_hash)
    if not ok:
        return _login_page(request, next, WRONG_PASSWORD_MSG, status=401)
    limiter.success(ip, token)
    resp = RedirectResponse(auth.safe_next(next), status_code=303)
    _set_session_cookie(resp, store.secrets.session_secret)
    return resp


def _set_session_cookie(resp: Response, secret: str) -> None:
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.new_session_cookie(secret),
        max_age=auth.SESSION_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=False,  # plain HTTP on the LAN
    )


@router.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return resp


@router.get("/settings", dependencies=[Depends(auth.require_admin)], include_in_schema=False)
async def settings_page() -> FileResponse:
    return FileResponse(SETTINGS_HTML, media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/photos", dependencies=[Depends(auth.require_admin)], include_in_schema=False)
async def photos_page() -> FileResponse:
    return FileResponse(PHOTOS_HTML, media_type="text/html", headers={"Cache-Control": "no-store"})


# ---- settings API ----
#
# Stale-write rule: every handler reads store.config / store.secrets right before
# model_copy + save, with no await in between; nothing read earlier is written back.


def _secrets_set(s: Secrets) -> dict[str, bool]:
    return {
        "ha_token": bool(s.ha_token),
        "google_client": bool(s.google_client_id and s.google_client_secret),
        "admin_password": bool(s.admin_password_hash),
    }


def _admin_view(app) -> dict[str, Any]:
    """What the settings page needs. Includes Google account emails (admin only); never secrets.
    `revision` belongs to exactly this `config` (one read of store.config)."""
    store = app.state.store
    cfg = store.config
    return {
        "config": cfg.model_dump(mode="json"),
        "revision": config_revision(cfg),
        "secrets_set": _secrets_set(store.secrets),
        "status": app.state.hub.snapshot()["status"],
        "controllable_domains": sorted(CONTROLLABLE_DOMAINS),
    }


def _error(loc: list, msg: str, type_: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """One 422 entry. `type` is stable (the page maps it to a translated text); `ctx` holds
    the text's placeholders. Neither ever carries the submitted value."""
    err: dict[str, Any] = {"loc": loc, "msg": msg, "type": type_}
    if ctx:
        err["ctx"] = ctx
    return err


def _invalid(errors: list[dict[str, Any]]) -> JSONResponse:
    """422 with field paths relative to the body (["members", 0, "color"]), shown next to fields."""
    return JSONResponse({"detail": errors}, status_code=422)


def _pydantic_errors(exc: ValidationError) -> list[dict[str, Any]]:
    errors = []
    for e in exc.errors(include_url=False, include_input=False):
        # Keep only plain placeholder values ({"ge": 5}); drop exception objects and the like.
        ctx = {k: v for k, v in (e.get("ctx") or {}).items() if isinstance(v, (str, int, float)) and k != "input"}
        errors.append(_error(list(e["loc"]), e["msg"], e["type"], ctx or None))
    return errors


M = TypeVar("M", bound=BaseModel)


async def _read_object(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return _invalid([_error([], "body must be JSON", "json_invalid")])
    if not isinstance(payload, dict):
        return _invalid([_error([], "body must be a JSON object", "dict_type")])
    return payload


async def _parse(request: Request, model: type[M]) -> M | JSONResponse:
    payload = await _read_object(request)
    if isinstance(payload, JSONResponse):
        return payload
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        return _invalid(_pydantic_errors(e))


@router.get("/api/admin/config", dependencies=[Depends(auth.require_admin)])
async def get_config(request: Request) -> dict:
    return _admin_view(request.app)


def _reconcile(old: Config, new: Config) -> tuple[Config, list[dict[str, Any]]]:
    """Cross-field rules for a config coming from the settings page.

    - `google_accounts` is server-owned (the Google routes add and remove accounts); `new`
      already carries the stored list.
    - A member that still owns a connected Google account cannot be deleted: 422, disconnect
      it first. This keeps account, refresh token and calendars consistent without a hidden
      side effect inside a config save.
    - Calendars of a deleted member are dropped (cascade); any other calendar must reference
      a connected account and an existing member.
    """
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, m in enumerate(new.members):
        if m.id in seen:
            errors.append(_error(["members", i, "id"], "duplicate member id", "duplicate_member_id"))
        seen.add(m.id)
    old_names = {m.id: m.name for m in old.members}
    deleted = set(old_names) - seen
    for acct in new.google_accounts:
        if acct.member_id not in seen:
            name = old_names.get(acct.member_id, acct.member_id)
            errors.append(
                _error(
                    ["members"],
                    f"{name} has the Google account {acct.email} connected; disconnect it before removing {name}",
                    "member_has_google_account",
                    {"name": name, "email": acct.email},
                )
            )
    accounts = {a.id for a in new.google_accounts}
    calendars = []
    for i, cal in enumerate(new.calendars):
        if cal.account_id not in accounts:
            errors.append(_error(["calendars", i, "account_id"], "unknown Google account", "unknown_account"))
        elif cal.member_id in seen:
            calendars.append(cal)
        elif cal.member_id not in deleted:
            errors.append(_error(["calendars", i, "member_id"], "unknown member", "unknown_member"))
    return new.model_copy(update={"calendars": calendars}), errors


def _ha_inputs(cfg: Config) -> tuple:
    # timezone: forecast dates are computed in it, so a change re-fetches the forecast.
    return (cfg.ha_url, cfg.weather_entity, cfg.timezone, wanted_entities_for(cfg))


def _calendar_inputs(cfg: Config) -> tuple:
    return (cfg.calendars, cfg.members, cfg.timezone, cfg.language)


def _if_match(request: Request) -> str | None:
    """The revision from `If-Match: "<revision>"` (quotes optional); None when absent."""
    raw = request.headers.get("if-match", "").strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    raw = raw.strip('"').strip()
    return raw or None


def _stale() -> JSONResponse:
    return JSONResponse(
        {"detail": [_error([], "Settings were changed elsewhere; reload.", "stale_config")]}, status_code=409
    )


@router.put("/api/admin/config", dependencies=[Depends(auth.require_admin)])
async def put_config(request: Request) -> Any:
    """Save the whole config from the settings page.

    Optimistic concurrency: the request must carry `If-Match: "<revision>"` with the revision
    the page got from GET (or from its last successful PUT). The body stays the plain Config.
    A missing or outdated revision gets 409 `stale_config` and nothing is saved, so a page
    opened before another change (another device, a Google sign-in, the photos shuffle switch)
    cannot overwrite it. The response carries the new revision.
    """
    payload = await _read_object(request)
    if isinstance(payload, JSONResponse):
        return payload
    app = request.app
    store = app.state.store
    old = store.config  # no await from here until save_config / save_secrets
    if _if_match(request) != config_revision(old):
        return _stale()
    # Server-owned fields: the Google routes own the accounts, the photos page owns shuffle.
    payload["google_accounts"] = [a.model_dump() for a in old.google_accounts]
    payload["photos_shuffle"] = old.photos_shuffle
    try:
        new = Config.model_validate(payload)
    except ValidationError as e:
        return _invalid(_pydantic_errors(e))
    new, errors = _reconcile(old, new)
    if errors:
        return _invalid(errors)
    if not _same_origin(old.ha_url, new.ha_url):
        # Ruling R21: the stored token was entered for the old server; never send it to a
        # new one (a stolen cookie could otherwise point ha_url at its own host). The page
        # saves config before secrets, so a token typed together with the new URL survives.
        secrets = store.secrets
        if secrets.ha_token:
            store.save_secrets(secrets.model_copy(update={"ha_token": None}))
    store.save_config(new)
    await app.state.hub.publish({"type": "config", "config": store.public_config()})
    if _ha_inputs(old) != _ha_inputs(new):
        await app.state.ha.restart()
    if _calendar_inputs(old) != _calendar_inputs(new):
        app.state.gcal.refresh_now()  # language also changes holiday names
    return _admin_view(app)


class ShuffleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shuffle: StrictBool


@router.put("/api/admin/photos/shuffle", dependencies=[Depends(auth.require_admin)])
async def put_photos_shuffle(request: Request) -> Any:
    """The photos page's shuffle switch; `photos_shuffle` is not written by PUT /config."""
    body = await _parse(request, ShuffleBody)
    if isinstance(body, JSONResponse):
        return body
    store = request.app.state.store
    store.save_config(store.config.model_copy(update={"photos_shuffle": body.shuffle}))
    await request.app.state.hub.publish({"type": "config", "config": store.public_config()})
    return {"shuffle": store.config.photos_shuffle}


class SecretsBody(BaseModel):
    """Write-only secrets; None or "" keeps the stored value."""

    model_config = ConfigDict(extra="forbid")
    ha_token: str | None = Field(None, max_length=4096)
    google_client_id: str | None = Field(None, max_length=1024)
    google_client_secret: str | None = Field(None, max_length=1024)
    admin_password: str | None = Field(None, max_length=1024)
    current_password: str | None = Field(None, max_length=1024)


@router.put("/api/admin/secrets", dependencies=[Depends(auth.require_admin)])
async def put_secrets(request: Request) -> Any:
    body = await _parse(request, SecretsBody)
    if isinstance(body, JSONResponse):
        return body
    app = request.app
    store = app.state.store
    new_hash: str | None = None
    if body.admin_password:
        errors = []
        if len(body.admin_password) < MIN_PASSWORD:
            errors.append(
                _error(
                    ["admin_password"], f"at least {MIN_PASSWORD} characters", "password_too_short", {"min": MIN_PASSWORD}
                )
            )
        if not body.current_password:
            errors.append(
                _error(["current_password"], "required to change the password", "current_password_required")
            )
        if errors:
            return _invalid(errors)
        # The current password stops a stolen cookie from taking the account over;
        # throttled like the login form.
        limiter = app.state.login_limiter
        ip = request.client.host if request.client else "unknown"
        wait, token = limiter.begin(ip)  # synchronous reservation, before any await
        if wait:
            return JSONResponse(
                {"detail": f"Too many attempts. Try again in {wait} seconds."},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        ok = await run_in_threadpool(auth.verify_password, body.current_password, store.secrets.admin_password_hash)
        if not ok:
            return JSONResponse(
                {"detail": [_error(["current_password"], "wrong password", "wrong_password")]}, status_code=403
            )
        limiter.success(ip, token)
        new_hash = await run_in_threadpool(auth.hash_password, body.admin_password)

    current = store.secrets  # re-read after the awaits above; no await until save_secrets
    update: dict[str, Any] = {}
    for field in ("ha_token", "google_client_id", "google_client_secret"):
        value = (getattr(body, field) or "").strip()
        if value and value != getattr(current, field):
            update[field] = value
    if new_hash:
        # A new session secret logs out every other session; this response re-issues a cookie.
        update["admin_password_hash"] = new_hash
        update["session_secret"] = _secrets.token_urlsafe(32)
    if update:
        store.save_secrets(current.model_copy(update=update))

    resp = JSONResponse({"secrets_set": _secrets_set(store.secrets)})
    if new_hash:
        _set_session_cookie(resp, store.secrets.session_secret)
    if "google_client_id" in update or "google_client_secret" in update:
        google_oauth.token_cache.clear()
        app.state.gcal.refresh_now()
    if "ha_token" in update:
        await app.state.ha.restart()
    return resp


class HATestBody(BaseModel):
    url: str = Field(max_length=2048)
    token: str | None = Field(None, max_length=4096)


_BAD_HA_URL = "URL must start with http:// or https://"


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str | None) -> tuple | None:
    """(scheme, host, port) of an http(s) URL; None for no URL. Unparseable URLs are their own origin."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    try:
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
    except ValueError:
        return ("invalid", url.strip())
    return (parts.scheme.lower(), (parts.hostname or "").lower(), port)


def _same_origin(a: str | None, b: str | None) -> bool:
    return _origin(a) == _origin(b)


def _same_url(a: str, b: str | None) -> bool:
    if not b or not _same_origin(a, b):
        return False
    return urlsplit(a.strip()).path.rstrip("/") == urlsplit(b.strip()).path.rstrip("/")


@router.post("/api/admin/ha/test", dependencies=[Depends(auth.require_admin)])
async def ha_test(request: Request) -> Any:
    body = await _parse(request, HATestBody)
    if isinstance(body, JSONResponse):
        return body
    url = body.url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=400, detail=_BAD_HA_URL)
    store = request.app.state.store
    token = (body.token or "").strip()
    if not token:
        # The stored token only ever goes to the stored address: a stolen cookie must not be
        # able to make the server send it to a host of the caller's choosing.
        if _same_url(url, store.config.ha_url):
            token = store.secrets.ha_token or ""
        else:
            return {"ok": False, "error": "enter the access token to test a new address", "version": None}
    if not token:
        return {"ok": False, "error": "no access token", "version": None}
    try:
        return await probe_connection(url, token, timeout=HA_TEST_TIMEOUT)
    except ValueError:
        raise HTTPException(status_code=400, detail=_BAD_HA_URL) from None


@router.get("/api/admin/ha/entities", dependencies=[Depends(auth.require_admin)])
async def ha_entities(request: Request) -> list[dict]:
    ha = request.app.state.ha
    try:
        if not ha.connected:
            raise HAError("not connected")
        return await ha.list_entities()
    except HAError as e:
        if not ha.connected:
            raise HTTPException(status_code=409, detail="home assistant not connected") from None
        raise HTTPException(status_code=502, detail=str(e)) from None


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
    # Re-read right before writing; nothing is held across the HTTP call above. The member
    # may have been deleted while the exchange ran: then keep nothing (no orphan account).
    if member_id not in {m.id for m in store.config.members}:
        raise HTTPException(status_code=409, detail="The family member was removed during sign-in; nothing was saved")
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
