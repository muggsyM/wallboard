import base64
import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from wallboard import auth, google_oauth
from wallboard.app import create_app
from wallboard.config import CalendarRef, GoogleAccount, Member
from wallboard.google_oauth import GoogleAuthExpired, GoogleError

TOKEN_URL = "https://oauth2.googleapis.com/token"
EMAIL = "Anna@Example.com"
ACCT = google_oauth.account_id_for(EMAIL)
REFRESH = "refresh-SECRET-1"
CODE = "auth-code-SECRET"


def id_token(email=EMAIL):
    seg = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"hdr.{seg}.sig"


def token_json(refresh=REFRESH, **extra):
    d = {"access_token": "access-SECRET-1", "expires_in": 3600, "id_token": id_token()}
    if refresh:
        d["refresh_token"] = refresh
    d.update(extra)
    return d


@pytest.fixture(autouse=True)
def _reset_cache():
    google_oauth.token_cache.clear()
    yield
    google_oauth.token_cache.clear()


@pytest.fixture
def app(data_dir):
    app = create_app(data_dir)
    store = app.state.store
    store.save_secrets(
        store.secrets.model_copy(update={"google_client_id": "cid", "google_client_secret": "csecret"})
    )
    store.save_config(
        store.config.model_copy(update={"members": [Member(id="anna", name="Anna", initial="A", color="#fff")]})
    )
    return app


def client(app, peer=("127.0.0.1", 50000), host="localhost", admin=False):
    c = TestClient(app, follow_redirects=False, client=peer, base_url=f"http://{host}")
    if admin:
        c.cookies.set(auth.COOKIE_NAME, auth.new_session_cookie(app.state.store.secrets.session_secret))
    return c


def admin_client(app):
    return client(app, peer=("192.168.1.5", 50000), admin=True)


def start_state(c, member="anna"):
    r = c.post("/api/admin/google/start", json={"member_id": member})
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()["url"]).query)["state"][0]


# ---- module level ----


def test_auth_url_params():
    u = urlsplit(google_oauth.auth_url("cid", "st"))
    assert f"{u.scheme}://{u.netloc}{u.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert q["client_id"] == "cid" and q["state"] == "st"
    assert q["redirect_uri"] == "http://localhost:8080/api/google/callback"
    assert q["response_type"] == "code"
    assert q["scope"] == "https://www.googleapis.com/auth/calendar.readonly openid email"
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert q["include_granted_scopes"] == "true"


@respx.mock
async def test_exchange_code_returns_token_and_email(app):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    refresh, email = await google_oauth.exchange_code(app.state.store.secrets, CODE)
    assert (refresh, email) == (REFRESH, EMAIL)
    body = parse_qs(route.calls[0].request.content.decode())
    assert body["grant_type"] == ["authorization_code"] and body["code"] == [CODE]
    assert body["client_id"] == ["cid"] and body["client_secret"] == ["csecret"]
    assert body["redirect_uri"] == [google_oauth.REDIRECT_URI]


@respx.mock
async def test_exchange_without_refresh_token_errors(app):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json(refresh=None)))
    with pytest.raises(google_oauth.NoRefreshToken):
        await google_oauth.exchange_code(app.state.store.secrets, CODE)


@respx.mock
async def test_invalid_grant_raises_expired(app):
    s = app.state.store.secrets.model_copy(update={"google_refresh_tokens": {"a1": "r"}})
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(GoogleAuthExpired):
        await google_oauth.access_token(s, "a1")


@respx.mock
async def test_other_errors_raise_google_error(app):
    s = app.state.store.secrets.model_copy(update={"google_refresh_tokens": {"a1": "r"}})
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(GoogleError) as e:
        await google_oauth.access_token(s, "a1")
    assert not isinstance(e.value, GoogleAuthExpired)
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(GoogleError):
        await google_oauth.access_token(s, "a1")


@respx.mock
async def test_access_token_cached_and_refreshed_near_expiry(app):
    s = app.state.store.secrets.model_copy(update={"google_refresh_tokens": {"a1": "r"}})
    now = [1000.0]
    cache = google_oauth.TokenCache(now=lambda: now[0])
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "t1", "expires_in": 3600})
    )
    assert await google_oauth.access_token(s, "a1", cache=cache) == "t1"
    now[0] += 3000
    assert await google_oauth.access_token(s, "a1", cache=cache) == "t1"
    assert route.call_count == 1
    now[0] += 545  # within 60 s of expiry
    await google_oauth.access_token(s, "a1", cache=cache)
    assert route.call_count == 2


@respx.mock
async def test_access_token_cached(app):
    s = app.state.store.secrets.model_copy(update={"google_refresh_tokens": {"a1": "r"}})
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "t1", "expires_in": 3600})
    )
    await google_oauth.access_token(s, "a1")
    await google_oauth.access_token(s, "a1")
    assert route.call_count == 1


# ---- routes ----


def test_start_requires_admin_and_client_config(app):
    assert client(app).post("/api/admin/google/start", json={"member_id": "anna"}).status_code == 401
    store = app.state.store
    store.save_secrets(store.secrets.model_copy(update={"google_client_id": None}))
    r = admin_client(app).post("/api/admin/google/start", json={"member_id": "anna"})
    assert r.status_code == 400 and "Google client not configured" in r.text


def test_start_unknown_member(app):
    r = admin_client(app).post("/api/admin/google/start", json={"member_id": "nobody"})
    assert r.status_code == 404


@respx.mock
def test_exchange_stores_token_and_account(app):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    c = client(app)  # loopback, no cookie
    state = start_state(admin_client(app))
    r = c.get("/api/google/callback", params={"code": CODE, "state": state})
    assert r.status_code == 303 and r.headers["location"] == "/settings#google"
    store = app.state.store
    assert store.secrets.google_refresh_tokens == {ACCT: REFRESH}
    assert store.config.google_accounts == [GoogleAccount(id=ACCT, email=EMAIL, member_id="anna")]
    assert ACCT.startswith("g_") and len(ACCT) == 14
    assert app.state.hub.snapshot()["config"]["google_accounts"] == [{"id": ACCT, "member_id": "anna"}]


@respx.mock
def test_reconnect_updates_member_and_token(app):
    store = app.state.store
    store.save_config(
        store.config.model_copy(
            update={"members": store.config.members + [Member(id="bob", name="B", initial="B", color="#000")]}
        )
    )
    ac = admin_client(app)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    client(app).get("/api/google/callback", params={"code": CODE, "state": start_state(ac, "anna")})
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json(refresh="new-refresh")))
    client(app).get("/api/google/callback", params={"code": CODE, "state": start_state(ac, "bob")})
    assert store.secrets.google_refresh_tokens == {ACCT: "new-refresh"}
    assert [(a.id, a.member_id) for a in store.config.google_accounts] == [(ACCT, "bob")]


@respx.mock
def test_paste_url_flow(app):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    ac = admin_client(app)
    state = start_state(ac)
    url = f"http://localhost:8080/api/google/callback?state={state}&code={CODE}&scope=x"
    r = ac.post("/api/admin/google/paste", json={"url": url})
    assert r.status_code == 200
    assert app.state.store.secrets.google_refresh_tokens == {ACCT: REFRESH}
    assert app.state.store.config.google_accounts[0].member_id == "anna"


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.com/api/google/callback?code=c&state=s",
        "http://localhost.evil.com/api/google/callback?code=c&state=s",
        "http://localhost@evil.com/api/google/callback?code=c&state=s",
        "https://localhost:8080/api/google/callback?code=c&state=s",
        "http://localhost:8080/other?code=c&state=s",
        "http://localhost:99999/api/google/callback?code=c&state=s",
        "http://localhost:8080/api/google/callback?state=s",
        "not a url",
    ],
)
@respx.mock
def test_paste_rejects_bad_urls(app, url):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    r = admin_client(app).post("/api/admin/google/paste", json={"url": url})
    assert r.status_code == 400
    assert route.call_count == 0 and app.state.store.secrets.google_refresh_tokens == {}


def test_paste_requires_admin(app):
    r = client(app).post(
        "/api/admin/google/paste", json={"url": "http://localhost/api/google/callback?code=c&state=s"}
    )
    assert r.status_code == 401


@respx.mock
def test_bad_state_rejected(app):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    c = client(app)
    good = start_state(admin_client(app))
    payload, _, sig = good.partition(".")
    for bad in ["garbage", "", good[:-2] + "xx", payload + "." + "0" * len(sig), "a.b"]:
        r = c.get("/api/google/callback", params={"code": CODE, "state": bad})
        assert r.status_code == 400, bad
    assert route.call_count == 0
    assert app.state.store.secrets.google_refresh_tokens == {}


@respx.mock
def test_state_replay_rejected(app):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    c = client(app)
    state = start_state(admin_client(app))
    assert c.get("/api/google/callback", params={"code": CODE, "state": state}).status_code == 303
    assert c.get("/api/google/callback", params={"code": CODE, "state": state}).status_code == 400


@respx.mock
def test_expired_state_rejected(app):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    now = [1_000_000.0]
    app.state.oauth_states._now = lambda: now[0]
    state = start_state(admin_client(app))
    now[0] += 601
    r = client(app).get("/api/google/callback", params={"code": CODE, "state": state})
    assert r.status_code == 400 and route.call_count == 0


@respx.mock
def test_state_for_removed_member_rejected(app):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    state = start_state(admin_client(app))
    store = app.state.store
    store.save_config(store.config.model_copy(update={"members": []}))
    r = client(app).get("/api/google/callback", params={"code": CODE, "state": state})
    assert r.status_code == 400 and route.call_count == 0


@respx.mock
def test_callback_non_loopback_without_cookie_rejected(app):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    state = start_state(admin_client(app))
    for c in (client(app, peer=("192.168.1.9", 1)), client(app, host="wallboard.lan")):
        r = c.get("/api/google/callback", params={"code": CODE, "state": state})
        assert r.status_code in (401, 403)
    assert route.call_count == 0
    # a valid admin cookie from a LAN peer is accepted (state was not consumed above)
    assert admin_client(app).get("/api/google/callback", params={"code": CODE, "state": state}).status_code == 303


@respx.mock
def test_callback_user_denied(app):
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    state = start_state(admin_client(app))
    r = client(app).get("/api/google/callback", params={"error": "access_denied", "state": state})
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?google_error=access_denied#google"
    assert route.call_count == 0 and app.state.store.secrets.google_refresh_tokens == {}


@respx.mock
def test_no_refresh_token_is_400_with_message(app):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json(refresh=None)))
    state = start_state(admin_client(app))
    r = client(app).get("/api/google/callback", params={"code": CODE, "state": state})
    assert r.status_code == 400
    assert "did not return a refresh token" in r.text
    assert app.state.store.secrets.google_refresh_tokens == {}


@respx.mock
def test_google_failure_is_502(app):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(500))
    state = start_state(admin_client(app))
    assert client(app).get("/api/google/callback", params={"code": CODE, "state": state}).status_code == 502


def test_delete_account(app):
    store = app.state.store
    other = google_oauth.account_id_for("other@example.com")
    store.save_config(
        store.config.model_copy(
            update={
                "google_accounts": [
                    GoogleAccount(id="g_a", email="a@x", member_id="anna"),
                    GoogleAccount(id=other, email="o@x", member_id="anna"),
                ],
                "calendars": [
                    CalendarRef(account_id="g_a", calendar_id="c1", name="n", member_id="anna"),
                    CalendarRef(account_id=other, calendar_id="c2", name="n", member_id="anna"),
                ],
            }
        )
    )
    store.save_secrets(store.secrets.model_copy(update={"google_refresh_tokens": {"g_a": "r1", other: "r2"}}))
    google_oauth.token_cache.put("g_a", "t", 9e12)
    c = admin_client(app)
    assert c.delete("/api/admin/google/g_unknown").status_code == 404
    assert client(app).delete("/api/admin/google/g_a").status_code == 401
    assert c.delete("/api/admin/google/g_a").status_code == 200
    assert [a.id for a in store.config.google_accounts] == [other]
    assert [x.account_id for x in store.config.calendars] == [other]
    assert store.secrets.google_refresh_tokens == {other: "r2"}
    assert google_oauth.token_cache.get("g_a") is None
    assert [a["id"] for a in app.state.hub.snapshot()["config"]["google_accounts"]] == [other]


@respx.mock
def test_no_token_values_in_responses_or_logs(app, caplog):
    caplog.set_level(logging.DEBUG)
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=token_json()))
    ac = admin_client(app)
    bodies = []
    r = ac.post("/api/admin/google/start", json={"member_id": "anna"})
    bodies.append(r.text)
    state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
    r = ac.get("/api/google/callback", params={"code": CODE, "state": state})
    bodies += [r.text, str(r.headers)]
    state2 = start_state(ac)
    r = ac.post(
        "/api/admin/google/paste",
        json={"url": f"http://localhost/api/google/callback?code={CODE}&state={state2}"},
    )
    bodies.append(r.text)
    bodies.append(ac.get("/api/admin/config").text)
    bodies.append(json.dumps(app.state.hub.snapshot()))
    # The test client's own request log lines (http://localhost/...) are not server logs.
    server_logs = "\n".join(r.getMessage() for r in caplog.records if "http://localhost/" not in r.getMessage())
    blob = "\n".join(bodies) + server_logs
    for secret in (REFRESH, "access-SECRET-1", CODE, "csecret"):
        assert secret not in blob
    assert app.state.store.secrets.google_refresh_tokens == {ACCT: REFRESH}  # it did get stored


def test_access_log_redacts_code_and_state(app):
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/google/callback?code=SECRETCODE&state=SECRETSTATE&scope=x", "1.1", 303), None,
    )
    logging.getLogger("uvicorn.access").filters[-1].filter(rec)
    msg = rec.getMessage()
    assert "SECRETCODE" not in msg and "SECRETSTATE" not in msg and "scope=x" in msg


async def test_missing_client_config_is_plain_error_not_expired(app):
    s = app.state.store.secrets.model_copy(
        update={"google_refresh_tokens": {"a1": "r"}, "google_client_id": None, "google_client_secret": None}
    )
    with pytest.raises(GoogleError, match="Google client not configured") as e:
        await google_oauth.access_token(s, "a1", google_oauth.TokenCache())
    assert not isinstance(e.value, GoogleAuthExpired)


async def test_missing_refresh_token_is_expired(app):
    with pytest.raises(GoogleAuthExpired):
        await google_oauth.access_token(app.state.store.secrets, "nope", google_oauth.TokenCache())
