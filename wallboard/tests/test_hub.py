import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from wallboard.app import create_app
from wallboard.hub import Hub


class LocalClient(TestClient):
    """TestClient whose HTTP and WebSocket requests carry Host: localhost (WS hard-codes testserver)."""

    def __init__(self, app, **kw):
        super().__init__(app, base_url="http://localhost", **kw)

    def websocket_connect(self, url, subprotocols=None, **kw):
        kw["headers"] = {"host": "localhost", **kw.get("headers", {})}
        return super().websocket_connect(url, subprotocols, **kw)


@pytest.fixture
def app(data_dir):
    return create_app(data_dir)


@pytest.fixture
def client(app):
    with LocalClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


def test_snapshot_defaults():
    snap = Hub().snapshot()
    assert snap == {
        "type": "snapshot", "build": None, "config": None, "ha": {}, "calendar": None,
        "weather": None, "photos": [], "status": {"ha": False, "google": {}},
    }


def test_snapshot_carries_build_id(app, client):
    from wallboard.routes.display import compute_build_id

    build = app.state.hub.snapshot()["build"]
    assert isinstance(build, str) and len(build) == 12
    assert build == compute_build_id()  # stable: same files, same id
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["build"] == build


def test_build_id_changes_with_web_files(tmp_path):
    from wallboard.routes.display import compute_build_id

    (tmp_path / "display").mkdir()
    (tmp_path / "display" / "a.js").write_text("one")
    (tmp_path / "b.css").write_text("x")
    first = compute_build_id(tmp_path)
    assert compute_build_id(tmp_path) == first
    (tmp_path / "display" / "a.js").write_text("two")
    assert compute_build_id(tmp_path) != first
    (tmp_path / "display" / "a.js").write_text("one")
    assert compute_build_id(tmp_path) == first
    (tmp_path / "display" / "a.js").rename(tmp_path / "display" / "c.js")  # rename counts too
    assert compute_build_id(tmp_path) != first


def test_snapshot_on_connect(app, client):
    app.state.hub.set_topic("config", {"language": "en"})
    app.state.hub.set_topic("ha", {"light.a": {"state": "on", "attributes": {}, "last_changed": "x"}})
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "snapshot"
    assert msg["config"] == {"language": "en"}
    assert msg["ha"]["light.a"]["state"] == "on"


def test_config_topic_set_at_startup(app):
    assert app.state.hub.snapshot()["config"] == app.state.store.public_config()


def test_ha_delta_updates_snapshot(app, client):
    state = {"state": "on", "attributes": {}, "last_changed": "t"}
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.portal.call(app.state.hub.publish, {"type": "ha", "entity_id": "light.a", "state": state})
        assert ws.receive_json() == {"type": "ha", "entity_id": "light.a", "state": state}
        with client.websocket_connect("/ws") as ws2:
            assert ws2.receive_json()["ha"]["light.a"] == state
        # state null deletes
        client.portal.call(app.state.hub.publish, {"type": "ha", "entity_id": "light.a", "state": None})
        assert ws.receive_json()["state"] is None
    assert "light.a" not in app.state.hub.snapshot()["ha"]


async def test_publish_replaces_topics():
    hub = Hub()
    await hub.publish({"type": "calendar", "calendar": {"events": []}})
    await hub.publish({"type": "weather", "weather": {"current": {}}})
    await hub.publish({"type": "photos", "photos": [{"id": "1"}]})
    await hub.publish({"type": "config", "config": {"language": "cs"}})
    snap = hub.snapshot()
    assert snap["calendar"] == {"events": []}
    assert snap["weather"] == {"current": {}}
    assert snap["photos"] == [{"id": "1"}]
    assert snap["config"] == {"language": "cs"}


async def test_set_status_merges_and_broadcasts():
    hub = Hub()
    sent = []

    class FakeWS:
        async def send_json(self, m):
            sent.append(m)

    hub._sockets.add(FakeWS())
    await hub.set_status("ha", True)
    await hub.set_status("google", {"acc1": True})
    assert sent[-1] == {"type": "status", "status": {"ha": True, "google": {"acc1": True}}}
    assert hub.snapshot()["status"] == {"ha": True, "google": {"acc1": True}}


async def test_unknown_topic_rejected():
    hub = Hub()
    with pytest.raises(ValueError):
        hub.set_topic("bogus", 1)
    with pytest.raises(ValueError):
        await hub.publish({"type": "bogus"})


def test_ws_rejects_non_loopback(app):
    c = LocalClient(app, client=("192.168.1.20", 50000))
    with c.websocket_connect("/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4403


def test_action_routed(app, client):
    calls = []

    async def on_action(entity_id, action_id):
        calls.append((entity_id, action_id))
        return {"type": "action_result", "id": "1", "ok": True, "error": None}

    app.state.hub.on_action = on_action
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "action", "id": "1", "entity_id": "light.a"})
        assert ws.receive_json() == {"type": "action_result", "id": "1", "ok": True, "error": None}
    assert calls == [("light.a", "1")]


def test_action_without_handler(app, client):
    app.state.hub.on_action = None
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "action", "id": "7", "entity_id": "light.a"})
        assert ws.receive_json() == {"type": "action_result", "id": "7", "ok": False, "error": "not available"}


def test_malformed_messages_ignored(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("not json")
        ws.send_json([1, 2])
        ws.send_json({"type": "nope"})
        ws.send_json({"type": "action", "id": 5})
        ws.send_json({"type": "action", "id": "9", "entity_id": "light.a"})
        assert ws.receive_json()["id"] == "9"


async def test_dead_socket_dropped():
    hub = Hub()

    class Dead:
        async def send_json(self, m):
            raise RuntimeError("gone")

    dead = Dead()
    hub._sockets.add(dead)
    await hub.publish({"type": "ha", "entity_id": "a.b", "state": None})
    assert dead not in hub._sockets


async def test_stuck_socket_dropped_after_timeout(monkeypatch):
    import asyncio

    from wallboard import hub as hub_mod

    monkeypatch.setattr(hub_mod, "SEND_TIMEOUT", 0.05)
    hub = Hub()

    class Stuck:
        closed = False

        async def send_json(self, m):
            await asyncio.Event().wait()  # never completes

        async def close(self):
            Stuck.closed = True

    class Live:
        def __init__(self):
            self.got = []

        async def send_json(self, m):
            self.got.append(m)

    stuck, live = Stuck(), Live()
    hub._sockets.update({stuck, live})
    await asyncio.wait_for(hub.publish({"type": "ha", "entity_id": "a.b", "state": None}), 2)
    assert stuck not in hub._sockets and live in hub._sockets
    assert live.got == [{"type": "ha", "entity_id": "a.b", "state": None}]
    await asyncio.sleep(0)  # the background close runs
    assert Stuck.closed  # so the display reconnects for a fresh snapshot


def test_send_timeout_is_5s():
    from wallboard import hub as hub_mod

    assert hub_mod.SEND_TIMEOUT == 5


def test_index_loopback_only(app):
    ok = LocalClient(app, client=("127.0.0.1", 1)).get("/")
    assert ok.status_code == 200
    assert LocalClient(app, client=("::1", 1)).get("/").status_code == 200
    assert LocalClient(app, client=("192.168.1.20", 1)).get("/").status_code == 403


def test_static_loopback_only(app):
    ok = LocalClient(app, client=("127.0.0.1", 1)).get("/static/display/index.html")
    assert ok.status_code == 200
    bad = LocalClient(app, client=("192.168.1.20", 1))
    assert bad.get("/static/display/index.html").status_code == 403
    assert bad.get("/static/shared/.gitkeep").status_code == 403
    assert bad.get("/static/shared/missing.js").status_code == 403


def test_fonts_and_icons_are_local(app):
    """Ruling R27: no page depends on the network for fonts or icons."""
    import re
    from wallboard.routes.display import WEB_DIR

    for page in [WEB_DIR / "display" / "index.html", *sorted((WEB_DIR / "admin").glob("*.html"))]:
        html = page.read_text(encoding="utf-8")
        assert not re.search(r"""(href|src)=["']?(https?:)?//""", html), page.name
    c = LocalClient(app, client=("127.0.0.1", 1))
    index = c.get("/").text
    for url in re.findall(r'href="(/static/shared/vendor/[^"]+)"', index):
        r = c.get(url)
        assert r.status_code == 200, url
        css = r.text
        base = url.rsplit("/", 1)[0]
        for ref in set(re.findall(r"url\(\"?([^\")?#]+)", css)):
            if ref.endswith(".woff2"):
                parts = (base + "/" + ref.removeprefix("./")).split("/")
                while ".." in parts:  # resolve ../
                    i = parts.index("..")
                    del parts[i - 1 : i + 1]
                assert c.get("/".join(parts)).status_code == 200, ref
    assert len(re.findall(r'href="/static/shared/vendor/', index)) == 2


def test_ws_rejects_foreign_origin(app):
    with LocalClient(app, client=("127.0.0.1", 1)) as c:
        with c.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
    assert exc.value.code == 4403


def test_ws_accepts_loopback_origin(app):
    with LocalClient(app, client=("127.0.0.1", 1)) as c:
        with c.websocket_connect("/ws", headers={"origin": "http://localhost:8080"}) as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_ws_rejects_evil_host(app):
    with LocalClient(app, client=("127.0.0.1", 1)) as c:
        with c.websocket_connect("/ws", headers={"host": "evil.example"}) as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
    assert exc.value.code == 4403


def test_http_rejects_evil_host(app):
    c = LocalClient(app, client=("127.0.0.1", 1))
    evil = {"host": "evil.example"}
    assert c.get("/", headers=evil).status_code == 403
    assert c.get("/static/display/index.html", headers=evil).status_code == 403
    assert c.get("/", headers={"host": "localhost:8080"}).status_code == 200


def test_host_and_origin_helpers():
    from wallboard.auth import is_allowed_display_origin as o
    from wallboard.auth import is_loopback_host_header as h

    assert all(h(x) for x in ["localhost", "localhost:8080", "127.0.0.1:8080", "[::1]", "[::1]:8080"])
    bad_hosts = [None, "", "evil.example", "localhost.evil.com", "localhost:abc", "::1", "127.0.0.1.evil"]
    assert not any(h(x) for x in bad_hosts)
    assert all(o(x) for x in [None, "http://localhost:8080", "https://127.0.0.1", "http://[::1]:80"])
    bad_origins = ["http://evil.example", "null", "file://localhost", "http://localhost.evil.com"]
    assert not any(o(x) for x in bad_origins)


def test_binary_frame_ignored(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00\x01")
        ws.send_json({"type": "action", "id": "3", "entity_id": "light.a"})
        assert ws.receive_json()["id"] == "3"
