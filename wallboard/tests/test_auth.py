import asyncio
import time

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from wallboard import auth
from wallboard.__main__ import main, set_password
from wallboard.app import create_app
from wallboard.config import ConfigStore


def make_client(data_dir, password="hunter2", client=("testclient", 50000)):
    app = create_app(data_dir)
    if password is not None:
        store = app.state.store
        store.save_secrets(store.secrets.model_copy(update={"admin_password_hash": auth.hash_password(password)}))
    return app, TestClient(
        app, follow_redirects=False, client=client, base_url="http://localhost", headers={"X-Requested-With": "wallboard"}
    )


def test_hash_verify():
    stored = auth.hash_password("s3cret")
    assert stored.startswith("scrypt$16384$8$1$")
    assert len(stored.split("$")) == 6
    assert auth.verify_password("s3cret", stored)
    assert not auth.verify_password("wrong", stored)
    assert auth.hash_password("s3cret") != stored  # random salt


@pytest.mark.parametrize("bad", [None, "", "garbage", "scrypt$x$8$1$aa$bb", "scrypt$16384$8$1$!!$!!", "md5$1$2$3$4$5"])
def test_verify_malformed_returns_false(bad):
    assert auth.verify_password("pw", bad) is False


def test_login_sets_cookie(data_dir):
    _, c = make_client(data_dir)
    r = c.post("/login", data={"password": "hunter2"})
    assert r.status_code == 303
    assert r.headers["location"] == "/settings"
    sc = r.headers["set-cookie"]
    assert "wb_session=" in sc
    assert "HttpOnly" in sc and "samesite=strict" in sc.lower()
    assert "Max-Age=2592000" in sc and "Path=/" in sc
    assert "secure" not in sc.lower()

    _, c2 = make_client(data_dir)
    r = c2.post("/login", data={"password": "nope"})
    assert r.status_code == 401
    assert "Wrong password" in r.text
    assert "wb_session" not in r.headers.get("set-cookie", "")


def test_login_next_open_redirect_blocked(data_dir):
    _, c = make_client(data_dir)
    r = c.post("/login?next=//evil.com", data={"password": "hunter2"})
    assert r.headers["location"] == "/settings"
    r = c.post("/login?next=/photos", data={"password": "hunter2"})
    assert r.headers["location"] == "/photos"


def test_no_password_set_refuses_login(data_dir):
    _, c = make_client(data_dir, password=None)
    assert "python -m wallboard set-password" in c.get("/login").text
    r = c.post("/login", data={"password": ""})
    assert r.status_code == 401
    assert "wb_session" not in r.headers.get("set-cookie", "")


def test_login_page_served(data_dir):
    _, c = make_client(data_dir)
    r = c.get("/login")
    assert r.status_code == 200 and "<form" in r.text


def test_tampered_or_expired_cookie_rejected(data_dir):
    app, c = make_client(data_dir)
    secret = app.state.store.secrets.session_secret
    good = auth.make_session_cookie(secret, time.time() + 100)
    c.cookies.set("wb_session", good)
    assert c.get("/api/admin/config").status_code == 200

    expiry, sig = good.split(".")
    for bad in (
        f"{int(expiry) + 1000}.{sig}",
        f"{expiry}.{'0' * len(sig)}",
        auth.make_session_cookie("other-secret", time.time() + 100),
        auth.make_session_cookie(secret, time.time() - 10),
        "garbage",
        "a.b",
    ):
        c.cookies.clear()
        c.cookies.set("wb_session", bad)
        assert c.get("/api/admin/config").status_code == 401, bad


def test_admin_api_requires_cookie(data_dir):
    _, c = make_client(data_dir)
    r = c.get("/api/admin/config")
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthorized"}
    assert c.post("/login", data={"password": "hunter2"}).status_code == 303
    r = c.get("/api/admin/config")
    assert r.status_code == 200
    assert r.json()["config"]["language"] == "cs"
    assert "session_secret" not in r.text and "admin_password_hash" not in r.text


def test_require_admin_page_redirects(data_dir):
    app, c = make_client(data_dir)
    app.add_api_route("/_page", lambda: {"ok": True}, dependencies=[Depends(auth.require_admin)])
    r = c.get("/_page")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/_page"


def test_logout_clears_cookie(data_dir):
    _, c = make_client(data_dir)
    c.post("/login", data={"password": "hunter2"})
    r = c.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "Max-Age=0" in r.headers["set-cookie"]
    assert c.get("/api/admin/config").status_code == 401


@pytest.mark.parametrize(
    "addr,status",
    [(("192.168.1.20", 5000), 403), (("127.0.0.1", 5000), 200), (("::1", 5000), 200)],
)
def test_require_loopback(data_dir, addr, status):
    app, c = make_client(data_dir, client=addr)
    app.add_api_route("/_lb", lambda: {"ok": True}, dependencies=[Depends(auth.require_loopback)])
    assert c.get("/_lb").status_code == status


def test_set_password_cli(data_dir):
    answers = iter(["pw-one-long", "pw-one-long"])
    set_password(data_dir, getpass_fn=lambda prompt: next(answers))
    stored = ConfigStore(data_dir).secrets.admin_password_hash
    assert auth.verify_password("pw-one-long", stored)


@pytest.mark.parametrize("answers", [["abcdefgh", "abcdefgi"], ["", ""], ["short", "short"]])
def test_set_password_cli_refuses_mismatch_and_empty(data_dir, answers):
    it = iter(answers)
    with pytest.raises(SystemExit):
        set_password(data_dir, getpass_fn=lambda prompt: next(it))
    assert ConfigStore(data_dir).secrets.admin_password_hash is None


def test_main_dispatches_set_password(data_dir, monkeypatch):
    answers = iter(["x1-long-pw", "x1-long-pw"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    main(["set-password", "--data", str(data_dir)])
    assert ConfigStore(data_dir).secrets.admin_password_hash


def test_store_sees_out_of_process_secrets_change(data_dir):
    first = ConfigStore(data_dir)
    other = ConfigStore(data_dir)  # simulates the CLI process
    other.save_secrets(other.secrets.model_copy(update={"admin_password_hash": "scrypt$new"}))
    assert first.secrets.admin_password_hash == "scrypt$new"
    other.save_config(other.config.model_copy(update={"rotation_seconds": 99}))
    assert first.config.rotation_seconds == 99


def test_login_after_out_of_process_password_set(data_dir):
    _, c = make_client(data_dir, password=None)
    assert "set-password" in c.get("/login").text
    answers = iter(["fresh-password", "fresh-password"])
    set_password(data_dir, getpass_fn=lambda prompt: next(answers))
    assert c.post("/login", data={"password": "fresh-password"}).status_code == 303


def test_password_change_invalidates_sessions(data_dir):
    _, c = make_client(data_dir)
    c.post("/login", data={"password": "hunter2"})
    assert c.get("/api/admin/config").status_code == 200
    answers = iter(["another-password", "another-password"])
    set_password(data_dir, getpass_fn=lambda prompt: next(answers))
    assert c.get("/api/admin/config").status_code == 401


def test_short_session_secret_rejected():
    from wallboard.config import Secrets

    with pytest.raises(ValueError):
        Secrets(session_secret="")


def test_login_rate_limited(data_dir):
    app, c = make_client(data_dir)
    now = [1000.0]
    app.state.login_limiter = auth.LoginLimiter(now=lambda: now[0])
    for _ in range(5):
        assert c.post("/login", data={"password": "bad"}).status_code == 401
    r = c.post("/login", data={"password": "hunter2"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "30"
    assert "wb_session" not in r.headers.get("set-cookie", "")
    now[0] += 31
    assert c.post("/login", data={"password": "hunter2"}).status_code == 303
    for _ in range(4):  # count was reset by the success
        assert c.post("/login", data={"password": "bad"}).status_code == 401


def test_login_limiter_doubles_and_caps():
    now = [0.0]
    lim = auth.LoginLimiter(now=lambda: now[0])
    waits = []
    for _ in range(8):
        for _ in range(5):
            lim.begin("a")
        waits.append(lim.retry_after("a"))
        now[0] += waits[-1]
    assert waits == [30, 60, 120, 240, 480, 900, 900, 900]
    assert lim.retry_after("b") == 0


def test_limiter_begin_admits_only_max_without_resolution():
    lim = auth.LoginLimiter(now=lambda: 0.0)
    admitted = [lim.begin("a") == 0 for _ in range(10)]
    assert admitted == [True] * 5 + [False] * 5


def test_limiter_exponent_clamped():
    now = [0.0]
    lim = auth.LoginLimiter(now=lambda: now[0])
    lim._state["a"] = [4, 0.0, 10_000, 0.0]
    assert lim.begin("a") == 0
    assert lim.retry_after("a") == 900


def test_limiter_prunes_only_dead_entries():
    now = [0.0]
    lim = auth.LoginLimiter(now=lambda: now[0])
    lim.begin("old")
    now[0] = 10_000
    lim.begin("new")
    assert "old" not in lim._state and "new" in lim._state


def test_limiter_full_table_of_live_lockouts_fails_closed():
    now = [100.0]
    lim = auth.LoginLimiter(now=lambda: now[0])
    lim.MAX_ENTRIES = 4
    for i in range(4):
        lim._state[f"ip{i}"] = [0, 160.0, 1, 100.0]
    assert lim.begin("fresh") > 0
    assert "fresh" not in lim._state
    assert all(lim._state[f"ip{i}"][1] == 160.0 for i in range(4))
    assert lim.begin("ip0") == 60  # existing lockout still enforced


def test_limiter_global_budget_across_ips():
    now = [0.0]
    lim = auth.LoginLimiter(now=lambda: now[0])
    for i in range(30):
        assert lim.begin(f"ip{i}") == 0
        now[0] += 1
    assert lim.begin("another") == 870  # now=30; oldest (t=0) ages out at 900
    assert "another" not in lim._state
    now[0] = 900
    assert lim.begin("another") == 0


def test_limiter_success_releases_global_slot():
    lim = auth.LoginLimiter(now=lambda: 0.0)
    for i in range(30):
        lim.begin(f"ip{i}")
    lim.success("ip5")
    assert lim.begin("x") == 0


async def test_concurrent_wrong_logins_run_at_most_five_verifications(data_dir, monkeypatch):
    import httpx

    app, _ = make_client(data_dir)
    calls = []
    real = auth.verify_password

    def spy(pw, stored):
        calls.append(pw)
        return real(pw, stored)

    monkeypatch.setattr(auth, "verify_password", spy)
    transport = httpx.ASGITransport(app=app, client=("10.0.0.9", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        rs = await asyncio.gather(*[ac.post("/login", data={"password": "bad"}) for _ in range(12)])
    assert len(calls) <= 5
    assert sorted(r.status_code for r in rs).count(401) <= 5
    assert any(r.status_code == 429 for r in rs)
