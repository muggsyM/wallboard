"""Settings API (Task 18): config/secrets read-write, HA test + entity picker, admin static, CSRF header."""

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from wallboard import auth, google_oauth
from wallboard.app import create_app
from wallboard.config import CalendarRef, GoogleAccount, Member, Tile
from wallboard.ha_client import HAClient

_spec = importlib.util.spec_from_file_location(
    "mock_ha", Path(__file__).resolve().parents[2] / "dev" / "mock_ha.py"
)
mock_ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_ha)

CSRF = {"X-Requested-With": "wallboard"}
PASSWORD = "hunter2-old"


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, msg):
        self.messages.append(json.loads(json.dumps(msg)))


class Spy:
    def __init__(self):
        self.calls = 0

    async def async_call(self, *a, **k):
        self.calls += 1

    def call(self, *a, **k):
        self.calls += 1


@pytest.fixture
def app(data_dir):
    app = create_app(data_dir)
    store = app.state.store
    store.save_secrets(store.secrets.model_copy(update={"admin_password_hash": auth.hash_password(PASSWORD)}))
    app.state.restart_spy = Spy()
    app.state.refresh_spy = Spy()
    app.state.ha.restart = app.state.restart_spy.async_call
    app.state.gcal.refresh_now = app.state.refresh_spy.call
    return app


def cookie_for(app):
    return auth.new_session_cookie(app.state.store.secrets.session_secret)


def client(app, peer=("192.168.1.5", 50000), host="192.168.1.10:8080", admin=True, csrf=True):
    c = TestClient(app, follow_redirects=False, client=peer, base_url=f"http://{host}", headers=CSRF if csrf else None)
    if admin:
        c.cookies.set(auth.COOKIE_NAME, cookie_for(app))
    return c


def full_config(app, **update):
    data = app.state.store.config.model_dump(mode="json")
    data.update(update)
    return data


def with_family(app):
    store = app.state.store
    store.save_config(
        store.config.model_copy(
            update={
                "members": [
                    Member(id="anna", name="Anna", initial="A", color="#ff0000"),
                    Member(id="bob", name="Bob", initial="B", color="#00ff00"),
                ],
                "google_accounts": [GoogleAccount(id="g_a", email="anna@gmail.com", member_id="anna")],
                "calendars": [
                    CalendarRef(account_id="g_a", calendar_id="anna@gmail.com", name="Anna", member_id="anna"),
                    CalendarRef(account_id="g_a", calendar_id="kids", name="Kids", member_id="bob"),
                ],
            }
        )
    )


# ---- brief tests --------------------------------------------------------------------------


def test_put_config_validates_and_publishes(app):
    with_family(app)
    c = client(app)
    sock = FakeSocket()
    asyncio.run(app.state.hub.connect(sock))
    sock.messages.clear()

    r = c.put("/api/admin/config", json=full_config(app, rotation_seconds=2))
    assert r.status_code == 422
    assert ["rotation_seconds"] in [e["loc"] for e in r.json()["detail"]]
    r = c.put("/api/admin/config", json=full_config(app, quick_tiles=["light.nope"]))
    assert r.status_code == 422
    assert ["quick_tiles"] in [e["loc"] for e in r.json()["detail"]]
    r = c.put("/api/admin/config", json=full_config(app, members=[{"id": "x", "name": "X", "initial": "X"}]))
    assert r.status_code == 422
    assert ["members", 0, "color"] in [e["loc"] for e in r.json()["detail"]]
    assert app.state.store.config.rotation_seconds == 30
    assert sock.messages == []

    r = c.put("/api/admin/config", json=full_config(app, language="en", rotation_seconds=45))
    assert r.status_code == 200, r.text
    assert r.json()["config"]["language"] == "en"
    assert app.state.store.config.language == "en"
    assert app.state.store.config.rotation_seconds == 45
    msgs = [m for m in sock.messages if m["type"] == "config"]
    assert len(msgs) == 1 and msgs[0]["config"]["language"] == "en"
    assert msgs[0]["config"]["google_accounts"] == [{"id": "g_a", "member_id": "anna"}]  # public: no emails
    assert app.state.refresh_spy.calls == 1  # language changes holiday names


def test_secrets_write_only(app):
    c = client(app)
    r = c.put("/api/admin/secrets", json={"ha_token": "tok-SECRET-123", "google_client_secret": "gcs-SECRET"})
    assert r.status_code == 200, r.text
    assert r.json() == {"secrets_set": {"ha_token": True, "google_client": False, "admin_password": True}}
    assert app.state.store.secrets.ha_token == "tok-SECRET-123"
    r = c.get("/api/admin/config")
    assert r.status_code == 200
    body = r.json()
    assert "tok-SECRET-123" not in r.text and "gcs-SECRET" not in r.text
    assert app.state.store.secrets.session_secret not in r.text
    assert "scrypt$" not in r.text
    assert body["secrets_set"] == {"ha_token": True, "google_client": False, "admin_password": True}
    assert set(body) == {"config", "secrets_set", "status", "controllable_domains"}
    assert "light" in body["controllable_domains"] and body["controllable_domains"] == sorted(body["controllable_domains"])
    c.put("/api/admin/secrets", json={"google_client_id": "cid"})
    assert c.get("/api/admin/config").json()["secrets_set"]["google_client"] is True


def test_empty_secret_keeps_old(app):
    c = client(app)
    assert c.put("/api/admin/secrets", json={"ha_token": "abc", "google_client_id": "cid"}).status_code == 200
    assert c.put("/api/admin/secrets", json={"ha_token": "", "google_client_id": ""}).status_code == 200
    assert c.put("/api/admin/secrets", json={}).status_code == 200
    s = app.state.store.secrets
    assert s.ha_token == "abc" and s.google_client_id == "cid"
    assert auth.verify_password(PASSWORD, s.admin_password_hash)


def test_ha_change_restarts_client(app):
    c = client(app)
    spy = app.state.restart_spy
    assert c.put("/api/admin/config", json=full_config(app, language="en")).status_code == 200
    assert spy.calls == 0  # unrelated change
    assert c.put("/api/admin/config", json=full_config(app, ha_url="http://ha.local:8123")).status_code == 200
    assert spy.calls == 1
    tiles = [Tile(entity_id="light.kitchen", group="lights").model_dump()]
    assert c.put("/api/admin/config", json=full_config(app, tiles=tiles)).status_code == 200
    assert spy.calls == 2  # wanted entity set changed
    assert c.put("/api/admin/config", json=full_config(app, weather_entity="weather.home")).status_code == 200
    assert spy.calls == 3
    assert c.put("/api/admin/secrets", json={"ha_token": "new"}).status_code == 200
    assert spy.calls == 4
    assert c.put("/api/admin/secrets", json={"ha_token": "new"}).status_code == 200
    assert spy.calls == 4  # unchanged token
    assert c.put("/api/admin/config", json=full_config(app, timezone="Europe/London")).status_code == 200
    assert spy.calls == 5  # forecast dates follow the timezone


@pytest.fixture
async def mock():
    m = mock_ha.MockHA(token="dev-token")
    port = await m.start()
    m.port = port
    yield m
    await m.stop()


def async_client(app, admin=True):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://192.168.1.10:8080", headers=CSRF)
    if admin:
        c.cookies.set(auth.COOKIE_NAME, cookie_for(app))
    return c


async def test_ha_test_endpoint(app, mock):
    url = f"http://127.0.0.1:{mock.port}"
    async with async_client(app) as c:
        r = await c.post("/api/admin/ha/test", json={"url": url, "token": "dev-token"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "error": None, "version": "2026.11.0"}

        r = await c.post("/api/admin/ha/test", json={"url": url, "token": "wrong-TOKEN-xyz"})
        assert r.status_code == 200
        assert r.json()["ok"] is False and r.json()["error"]
        assert "wrong-TOKEN-xyz" not in r.text

        # empty token -> the stored one, but only for the stored ha_url (a stolen cookie must
        # not be able to send the stored token to a server of its choice)
        store = app.state.store
        store.save_secrets(store.secrets.model_copy(update={"ha_token": "dev-token"}))
        auth_before = mock.auth_attempts
        r = await c.post("/api/admin/ha/test", json={"url": url, "token": ""})
        assert r.json()["ok"] is False and "token" in r.json()["error"]
        assert mock.auth_attempts == auth_before  # nothing was sent
        store.save_config(store.config.model_copy(update={"ha_url": url + "/"}))
        r = await c.post("/api/admin/ha/test", json={"url": url, "token": ""})
        assert r.json()["ok"] is True
        r = await c.post("/api/admin/ha/test", json={"url": url})
        assert r.json()["ok"] is True

        await mock.stop()
        r = await c.post("/api/admin/ha/test", json={"url": url, "token": "dev-token"})
        assert r.status_code == 200
        assert r.json()["ok"] is False and r.json()["error"] and r.json()["version"] is None
        assert "dev-token" not in r.text

        for bad in ("ftp://x", "file:///etc/passwd", "ws://127.0.0.1:1", "not a url", "http://"):
            r = await c.post("/api/admin/ha/test", json={"url": bad, "token": "t"})
            assert r.status_code == 400, bad


async def test_ha_test_timeout(app):
    async def silent(reader, writer):  # accepts TCP but never speaks
        await asyncio.sleep(30)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        from wallboard import ha_client

        result = await ha_client.probe_connection(f"http://127.0.0.1:{port}", "t", timeout=0.3)
        assert result["ok"] is False and result["version"] is None and result["error"]
    finally:
        server.close()


# ---- entities -----------------------------------------------------------------------------


def test_entities_409_when_disconnected(app):
    r = client(app).get("/api/admin/ha/entities")
    assert r.status_code == 409
    assert r.json() == {"detail": "home assistant not connected"}
    assert client(app, admin=False).get("/api/admin/ha/entities").status_code == 401


async def test_entities_from_connection(app, mock):
    store = app.state.store
    store.save_config(store.config.model_copy(update={"ha_url": f"http://127.0.0.1:{mock.port}"}))
    store.save_secrets(store.secrets.model_copy(update={"ha_token": "dev-token"}))
    ha = HAClient(store, app.state.hub, connect_timeout=1.0, call_timeout=1.0)
    app.state.ha = ha
    await ha.start()
    try:
        for _ in range(200):
            if ha.connected:
                break
            await asyncio.sleep(0.01)
        assert ha.connected
        async with async_client(app) as c:
            r = await c.get("/api/admin/ha/entities")
        assert r.status_code == 200
        items = r.json()
        ids = [e["entity_id"] for e in items]
        assert ids == sorted(ids) and "light.kitchen" in ids and "sensor.house_power" in ids
        kitchen = next(e for e in items if e["entity_id"] == "light.kitchen")
        assert kitchen == {"entity_id": "light.kitchen", "name": mock.states["light.kitchen"]["a"]["friendly_name"], "domain": "light"}
        # cached: an entity added in HA is not visible until the cache expires
        mock.states["light.brand_new"] = {"s": "on", "a": {}}
        assert "light.brand_new" not in [e["entity_id"] for e in await ha.list_entities()]
        assert "light.brand_new" in [e["entity_id"] for e in await ha.list_entities(max_age=0)]
    finally:
        await ha.stop()


# ---- server-owned fields and members --------------------------------------------------------


def test_client_cannot_inject_google_accounts(app):
    with_family(app)
    c = client(app)
    evil = full_config(app, google_accounts=[{"id": "g_evil", "email": "evil@x.cz", "member_id": "bob"}])
    r = c.put("/api/admin/config", json=evil)
    assert r.status_code == 200, r.text
    accts = app.state.store.config.google_accounts
    assert [(a.id, a.email, a.member_id) for a in accts] == [("g_a", "anna@gmail.com", "anna")]
    # dropping the list does not disconnect anything either
    assert c.put("/api/admin/config", json=full_config(app, google_accounts=[])).status_code == 200
    assert [a.id for a in app.state.store.config.google_accounts] == ["g_a"]
    # admin GET shows the email (the page shows which Gmail is connected)
    assert c.get("/api/admin/config").json()["config"]["google_accounts"][0]["email"] == "anna@gmail.com"


def test_calendar_needs_known_account(app):
    with_family(app)
    cals = full_config(app)["calendars"] + [
        {"account_id": "g_nope", "calendar_id": "x", "name": "X", "member_id": "anna", "enabled": True}
    ]
    r = client(app).put("/api/admin/config", json=full_config(app, calendars=cals))
    assert r.status_code == 422
    assert ["calendars", 2, "account_id"] in [e["loc"] for e in r.json()["detail"]]
    cals = [{"account_id": "g_a", "calendar_id": "x", "name": "X", "member_id": "ghost", "enabled": True}]
    r = client(app).put("/api/admin/config", json=full_config(app, calendars=cals))
    assert r.status_code == 422
    assert ["calendars", 0, "member_id"] in [e["loc"] for e in r.json()["detail"]]


def test_member_delete_cascades_calendars(app):
    with_family(app)
    c = client(app)
    data = full_config(app)
    data["members"] = [m for m in data["members"] if m["id"] != "bob"]
    r = c.put("/api/admin/config", json=data)  # client still sends bob's calendar
    assert r.status_code == 200, r.text
    cfg = app.state.store.config
    assert [m.id for m in cfg.members] == ["anna"]
    assert [c.calendar_id for c in cfg.calendars] == ["anna@gmail.com"]


def test_member_with_google_account_cannot_be_deleted(app):
    with_family(app)
    data = full_config(app)
    data["members"] = [m for m in data["members"] if m["id"] != "anna"]
    r = client(app).put("/api/admin/config", json=data)
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"] == ["members"] and "anna@gmail.com" in err["msg"]
    assert [m.id for m in app.state.store.config.members] == ["anna", "bob"]


def test_duplicate_member_ids_rejected(app):
    members = [{"id": "x", "name": "A", "initial": "A", "color": "#000000"}] * 2
    r = client(app).put("/api/admin/config", json=full_config(app, members=members))
    assert r.status_code == 422
    assert ["members", 1, "id"] in [e["loc"] for e in r.json()["detail"]]


def test_put_config_requires_admin_and_object(app):
    assert client(app, admin=False).put("/api/admin/config", json=full_config(app)).status_code == 401
    assert client(app).put("/api/admin/config", json=[1]).status_code == 422
    r = client(app).put("/api/admin/config", content=b"{nope", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


# ---- password change ----------------------------------------------------------------------


def test_password_change_requires_current_and_rotates_session(app):
    store = app.state.store
    old_cookie = cookie_for(app)
    old_secret = store.secrets.session_secret
    c = client(app)

    r = c.put("/api/admin/secrets", json={"admin_password": "brand-new-pw"})
    assert r.status_code == 422 and ["current_password"] in [e["loc"] for e in r.json()["detail"]]
    r = c.put("/api/admin/secrets", json={"admin_password": "brand-new-pw", "current_password": "wrong"})
    assert r.status_code == 403
    r = c.put("/api/admin/secrets", json={"admin_password": "short", "current_password": PASSWORD})
    assert r.status_code == 422 and ["admin_password"] in [e["loc"] for e in r.json()["detail"]]
    assert store.secrets.session_secret == old_secret
    assert auth.verify_password(PASSWORD, store.secrets.admin_password_hash)

    r = c.put("/api/admin/secrets", json={"admin_password": "brand-new-pw", "current_password": PASSWORD})
    assert r.status_code == 200, r.text
    assert "brand-new-pw" not in r.text
    assert store.secrets.session_secret != old_secret
    assert auth.verify_password("brand-new-pw", store.secrets.admin_password_hash)
    new_cookie = r.cookies.get(auth.COOKIE_NAME)
    assert new_cookie and auth.valid_session_cookie(store.secrets.session_secret, new_cookie)
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie

    stale = client(app, admin=False)
    stale.cookies.set(auth.COOKIE_NAME, old_cookie)
    assert stale.get("/api/admin/config").status_code == 401
    fresh = client(app, admin=False)
    fresh.cookies.set(auth.COOKIE_NAME, new_cookie)
    assert fresh.get("/api/admin/config").status_code == 200


def test_wrong_current_password_is_throttled(app):
    c = client(app)
    codes = [
        c.put("/api/admin/secrets", json={"admin_password": "brand-new-pw", "current_password": "x"}).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [403] * 5 and codes[5] == 429


def test_google_client_change_clears_token_cache(app):
    google_oauth.token_cache.put("g_a", "access", 9e12)
    client(app).put("/api/admin/secrets", json={"google_client_secret": "new-secret"})
    assert google_oauth.token_cache.get("g_a") is None


# ---- CSRF header --------------------------------------------------------------------------


def test_admin_writes_require_csrf_header(app):
    c = client(app, csrf=False)
    assert c.put("/api/admin/config", json=full_config(app)).status_code == 403
    assert c.put("/api/admin/secrets", json={"ha_token": "x"}).status_code == 403
    assert c.post("/api/admin/ha/test", json={"url": "http://x"}).status_code == 403
    assert c.delete("/api/admin/google/g_a").status_code == 403
    assert c.post("/api/admin/albums", json={"name": "x"}).status_code == 403
    r = c.put("/api/admin/config", json=full_config(app), headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 403
    assert app.state.store.secrets.ha_token is None
    assert c.get("/api/admin/config").status_code == 200  # reads need no header
    # unauthenticated writes still get 401 first
    assert client(app, admin=False, csrf=False).put("/api/admin/config", json={}).status_code == 401
    assert client(app).put("/api/admin/config", json=full_config(app)).status_code == 200


# ---- pages and static files ---------------------------------------------------------------


def test_settings_page_requires_admin(app):
    r = client(app, admin=False).get("/settings")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/settings"
    r = client(app).get("/settings")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "/static/admin/settings.js" in r.text


def test_static_modules_for_admin_on_lan(app):
    anon = client(app, admin=False)
    admin = client(app)
    for path in ("/static/display/pages/registry.js", "/static/shared/i18n.js", "/static/shared/i18n/en.json"):
        assert anon.get(path).status_code == 403, path
        assert admin.get(path).status_code == 200, path
    assert anon.get("/static/admin/settings.js").status_code == 403
    assert admin.get("/static/admin/settings.js").status_code == 200
    # the admin bundle is admin-only, even for the loopback display browser
    local = client(app, peer=("127.0.0.1", 1), host="localhost", admin=False)
    assert local.get("/static/admin/settings.js").status_code == 403
    assert local.get("/static/display/pages/registry.js").status_code == 200
    # a forged or stale cookie does not count
    forged = client(app, admin=False)
    forged.cookies.set(auth.COOKIE_NAME, "9999999999.deadbeef")
    assert forged.get("/static/shared/i18n.js").status_code == 403


# ---- fix round 1 --------------------------------------------------------------------------


def with_ha(app, url="http://ha.local:8123", token="tok-OLD"):
    store = app.state.store
    store.save_config(store.config.model_copy(update={"ha_url": url}))
    store.save_secrets(store.secrets.model_copy(update={"ha_token": token}))


@pytest.mark.parametrize(
    "new_url",
    ["http://evil.example:8123", "https://ha.local:8123", "http://ha.local:9999", "http://ha2.local:8123", None],
)
def test_ha_url_origin_change_clears_token(app, new_url):
    with_ha(app)
    r = client(app).put("/api/admin/config", json=full_config(app, ha_url=new_url))
    assert r.status_code == 200, r.text
    assert app.state.store.secrets.ha_token is None
    assert r.json()["secrets_set"]["ha_token"] is False


@pytest.mark.parametrize(
    "new_url", ["http://ha.local:8123/", "http://ha.local:8123/sub", "http://HA.local:8123", "http://ha.local:8123"]
)
def test_ha_url_same_origin_keeps_token(app, new_url):
    with_ha(app)
    assert client(app).put("/api/admin/config", json=full_config(app, ha_url=new_url)).status_code == 200
    assert app.state.store.secrets.ha_token == "tok-OLD"


def test_ha_url_default_port_is_same_origin(app):
    with_ha(app, url="http://ha.local")
    assert client(app).put("/api/admin/config", json=full_config(app, ha_url="http://ha.local:80")).status_code == 200
    assert app.state.store.secrets.ha_token == "tok-OLD"


def test_ha_url_first_set_clears_unbound_token(app):
    with_ha(app, url=None)
    client(app).put("/api/admin/config", json=full_config(app, ha_url="http://ha.local:8123"))
    assert app.state.store.secrets.ha_token is None


def test_url_and_new_token_in_one_save(app):
    """The page saves config first, then secrets, so a token typed with a new URL survives."""
    with_ha(app)
    c = client(app)
    assert c.put("/api/admin/config", json=full_config(app, ha_url="http://new.local:8123")).status_code == 200
    assert c.put("/api/admin/secrets", json={"ha_token": "tok-NEW"}).status_code == 200
    assert app.state.store.secrets.ha_token == "tok-NEW"
    assert app.state.store.config.ha_url == "http://new.local:8123"


async def _redirect_server(target_port):
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            f"HTTP/1.1 302 Found\r\nLocation: ws://127.0.0.1:{target_port}/api/websocket\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_probe_does_not_follow_redirects(mock):
    from wallboard import ha_client

    server, port = await _redirect_server(mock.port)
    try:
        result = await ha_client.probe_connection(f"http://127.0.0.1:{port}", "dev-token", timeout=2.0)
    finally:
        server.close()
    assert result["ok"] is False and result["error"]
    assert mock.auth_attempts == 0  # the token never reached the redirect target


async def test_client_does_not_follow_redirects(app, mock):
    server, port = await _redirect_server(mock.port)
    store = app.state.store
    store.save_config(store.config.model_copy(update={"ha_url": f"http://127.0.0.1:{port}"}))
    store.save_secrets(store.secrets.model_copy(update={"ha_token": "dev-token"}))
    ha = HAClient(store, app.state.hub, backoff_base=0.05, backoff_max=0.1, connect_timeout=1.0)
    await ha.start()
    try:
        await asyncio.sleep(0.5)
        assert not ha.connected
        assert mock.auth_attempts == 0
    finally:
        await ha.stop()
        server.close()


def test_secrets_invalid_value_not_echoed(app):
    c = client(app)
    big = "SECRETVALUE" * 500  # > 4096 chars
    r = c.put("/api/admin/secrets", json={"ha_token": big})
    assert r.status_code == 422
    assert "SECRETVALUE" not in r.text
    assert r.json()["detail"][0]["loc"] == ["ha_token"] and r.json()["detail"][0]["type"] == "string_too_long"
    r = c.put("/api/admin/secrets", json={"ha_token": 12345678})
    assert r.status_code == 422 and "12345678" not in r.text
    r = c.put("/api/admin/secrets", json={"nope": "SECRET-x"})
    assert r.status_code == 422 and "SECRET-x" not in r.text
    assert app.state.store.secrets.ha_token is None


def test_error_types_are_stable(app):
    with_family(app)
    c = client(app)
    data = full_config(app)
    data["members"] = [m for m in data["members"] if m["id"] != "anna"]
    err = c.put("/api/admin/config", json=data).json()["detail"][0]
    assert err["type"] == "member_has_google_account"
    assert err["ctx"] == {"name": "Anna", "email": "anna@gmail.com"}

    def types(r):
        return [e["type"] for e in r.json()["detail"]]

    assert types(c.put("/api/admin/config", json=full_config(app, timezone="Mars/Base"))) == ["unknown_timezone"]
    assert types(c.put("/api/admin/config", json=full_config(app, quick_tiles=["x.y"]))) == ["quick_tile_not_in_tiles"]
    tiles = [{"entity_id": f"light.l{i}", "group": "lights"} for i in range(5)]
    r = c.put("/api/admin/config", json=full_config(app, tiles=tiles, quick_tiles=[t["entity_id"] for t in tiles]))
    assert types(r) == ["too_many_quick_tiles"]
    r = c.put("/api/admin/config", json=full_config(app, rotation_seconds=1))
    assert types(r) == ["greater_than_equal"] and r.json()["detail"][0]["ctx"] == {"ge": 5}
    members = full_config(app)["members"]
    members = members + [members[1]]
    assert types(c.put("/api/admin/config", json=full_config(app, members=members))) == ["duplicate_member_id"]
    cals = [{"account_id": "g_nope", "calendar_id": "x", "name": "X", "member_id": "anna"}]
    assert types(c.put("/api/admin/config", json=full_config(app, calendars=cals))) == ["unknown_account"]
    cals = [{"account_id": "g_a", "calendar_id": "x", "name": "X", "member_id": "ghost"}]
    assert types(c.put("/api/admin/config", json=full_config(app, calendars=cals))) == ["unknown_member"]
    r = c.put("/api/admin/secrets", json={"admin_password": "short"})
    assert sorted(types(r)) == ["current_password_required", "password_too_short"]
    r = c.put("/api/admin/secrets", json={"admin_password": "long-enough-1", "current_password": "bad"})
    assert r.status_code == 403 and types(r) == ["wrong_password"]


def test_photos_shuffle_is_server_owned(app):
    c = client(app)
    assert c.put("/api/admin/config", json=full_config(app, photos_shuffle=True)).status_code == 200
    assert app.state.store.config.photos_shuffle is False
    sock = FakeSocket()
    asyncio.run(app.state.hub.connect(sock))
    sock.messages.clear()
    r = c.put("/api/admin/photos/shuffle", json={"shuffle": True})
    assert r.status_code == 200 and r.json() == {"shuffle": True}
    assert app.state.store.config.photos_shuffle is True
    assert [m["config"]["photos_shuffle"] for m in sock.messages if m["type"] == "config"] == [True]
    # a later full-config save from the settings page does not undo it
    assert c.put("/api/admin/config", json=full_config(app, photos_shuffle=False)).status_code == 200
    assert app.state.store.config.photos_shuffle is True
    assert c.put("/api/admin/photos/shuffle", json={"shuffle": "yes"}).status_code == 422
    assert client(app, csrf=False).put("/api/admin/photos/shuffle", json={"shuffle": False}).status_code == 403
    assert client(app, admin=False).put("/api/admin/photos/shuffle", json={"shuffle": False}).status_code == 401
