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
    return app, TestClient(app, follow_redirects=False, client=client)


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
    assert r.json()["language"] == "cs"
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
    assert 'wb_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]
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
    answers = iter(["pw-one", "pw-one"])
    set_password(data_dir, getpass_fn=lambda prompt: next(answers))
    stored = ConfigStore(data_dir).secrets.admin_password_hash
    assert auth.verify_password("pw-one", stored)


@pytest.mark.parametrize("answers", [["a", "b"], ["", ""]])
def test_set_password_cli_refuses_mismatch_and_empty(data_dir, answers):
    it = iter(answers)
    with pytest.raises(SystemExit):
        set_password(data_dir, getpass_fn=lambda prompt: next(it))
    assert ConfigStore(data_dir).secrets.admin_password_hash is None


def test_main_dispatches_set_password(data_dir, monkeypatch):
    answers = iter(["x1", "x1"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    main(["set-password", "--data", str(data_dir)])
    assert ConfigStore(data_dir).secrets.admin_password_hash
