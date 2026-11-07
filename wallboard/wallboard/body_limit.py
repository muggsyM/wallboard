"""Pure-ASGI middleware, before any body read: reject unauthenticated admin writes, admin
writes without the CSRF header, and oversized bodies.

CSRF: the session cookie is SameSite=Strict; on top of that every admin write must carry
`X-Requested-With: wallboard`. A cross-site form cannot set headers and a cross-site fetch
with a custom header needs a CORS preflight this server never grants. No route is exempt
(the photo upload uses XHR/fetch, which can set the header).
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from . import auth

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_MAX_BODY = 1024 * 1024
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "wallboard"
UPLOAD_PATH = "/api/admin/photos/upload"
UPLOAD_MAX_BODY = 25 * 40 * 1024 * 1024 + 1024 * 1024


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        if method in WRITE_METHODS:
            if path.startswith("/api/admin/"):
                secret = scope["app"].state.store.secrets.session_secret
                cookie = HTTPConnection(scope).cookies.get(auth.COOKIE_NAME)
                if not auth.valid_session_cookie(secret, cookie):
                    await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)
                    return
                if Headers(scope=scope).get(CSRF_HEADER) != CSRF_VALUE:
                    await JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)(
                        scope, receive, send
                    )
                    return
            cap = UPLOAD_MAX_BODY if path == UPLOAD_PATH else DEFAULT_MAX_BODY
            length = Headers(scope=scope).get("content-length")
            if length is not None and length.isdigit() and int(length) > cap:
                await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
                return
            await self._limited(scope, receive, send, cap)
            return
        await self.app(scope, receive, send)

    async def _limited(self, scope, receive, send, cap: int) -> None:
        # Frameworks may swallow an exception raised from receive (FastAPI turns it into a 400),
        # so remember the overflow and replace whatever response the app produces with a 413.
        seen = 0
        exceeded = False
        responded = False

        async def counting_receive():
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded_send(message):
            nonlocal responded
            if exceeded:
                if not responded:
                    responded = True
                    await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _TooLarge:
            if not responded:
                await guarded_send({"type": "http.response.start"})
