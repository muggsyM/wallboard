"""Admin routes: login/logout and (for now) a read-only config endpoint."""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from .. import auth

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
LOGIN_HTML = WEB_DIR / "admin" / "login.html"
ADMIN_CSS = WEB_DIR / "admin" / "admin.css"

router = APIRouter()

NO_PASSWORD_MSG = "No admin password is set. Run <code>python -m wallboard set-password</code> on the server."
WRONG_PASSWORD_MSG = "Wrong password."


def _login_page(request: Request, next_: str | None, error: str | None, status: int = 200) -> HTMLResponse:
    store = request.app.state.store
    message = error
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
    if not auth.verify_password(password, store.secrets.admin_password_hash):
        return _login_page(request, next, WRONG_PASSWORD_MSG, status=401)
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
