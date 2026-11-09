from datetime import date, datetime
from zoneinfo import ZoneInfo

from test_hub import LAN_HDRS, LocalClient, _anon
from wallboard.app import create_app
from wallboard.config import Config, Member, Todo
from wallboard.todos import toggled

PRAGUE = ZoneInfo("Europe/Prague")
NOW = datetime(2026, 11, 9, 23, 30, tzinfo=ZoneInfo("UTC"))  # 10 Nov 00:30 in Prague


def cfg(*todos):
    return Config(members=[Member(id="m", name="Máma", initial="M", color="#9e89c4")], todos=list(todos))


def test_toggle_ticks_with_the_local_date_and_unticks():
    c = toggled(cfg(Todo(id="a", member_id="m", text="Ponožky na lyže")), "a", NOW)
    assert c.todos[0].done == date(2026, 11, 10)
    assert toggled(c, "a", NOW).todos[0].done is None


def test_toggle_unknown_todo_is_none():
    assert toggled(cfg(), "nope", NOW) is None


def test_toggle_prunes_items_ticked_over_30_days_ago():
    c = cfg(
        Todo(id="a", member_id="m", text="a"),
        Todo(id="old", member_id="m", text="old", done=date(2026, 10, 10)),
        Todo(id="keep", member_id="m", text="keep", done=date(2026, 10, 11)),
    )
    assert [t.id for t in toggled(c, "a", NOW).todos] == ["a", "keep"]


def test_display_tap_toggles_and_publishes_config(data_dir):
    app = create_app(data_dir)
    app.state.store.save_config(cfg(Todo(id="a", member_id="m", text="Gymnathon")))
    with LocalClient(app, client=("127.0.0.1", 50000)) as c, c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert ws.receive_json()["type"] == "viewer"
        ws.send_json({"type": "action", "id": "x1", "entity_id": "todo.a", "command": "toggle"})
        msgs = [ws.receive_json(), ws.receive_json()]
        config_msg = next(m for m in msgs if m["type"] == "config")
        assert config_msg["config"]["todos"][0]["done"] is not None
        assert {"type": "action_result", "id": "x1", "ok": True} in msgs
        ws.send_json({"type": "action", "id": "x2", "entity_id": "todo.zzz", "command": "toggle"})
        assert ws.receive_json() == {"type": "action_result", "id": "x2", "ok": False, "error": "unknown todo"}
        ws.send_json({"type": "action", "id": "x3", "entity_id": "todo.a"})
        assert ws.receive_json() == {"type": "action_result", "id": "x3", "ok": False, "error": "unsupported command"}
    assert app.state.store.config.todos[0].done is not None


# ---- add / delete from the display (on-screen keyboard, long press) ----

import pytest  # noqa: E402

from wallboard.todos import TodoError, added, deleted  # noqa: E402


def test_added_appends_an_open_todo_with_clean_text():
    c = added(cfg(), {"member_id": "m", "text": "  Sehnat   ponožky\n na lyže ", "due": "2026-11-15"})
    [t] = c.todos
    assert (t.member_id, t.text, t.due, t.done) == ("m", "Sehnat ponožky na lyže", date(2026, 11, 15), None)
    assert t.id.startswith("t") and len(t.id) == 13
    assert added(c, {"member_id": "m", "text": "x", "due": None}).todos[1].due is None


@pytest.mark.parametrize(
    "data,error",
    [
        ({"member_id": "ghost", "text": "x"}, "unknown member"),
        ({"member_id": "m", "text": "   "}, "empty text"),
        ({"member_id": "m", "text": "x" * 201}, "invalid todo"),
        ({"member_id": "m", "text": "x", "due": "zítra"}, "invalid todo"),
        ("nope", "bad data"),
    ],
)
def test_added_rejects_bad_input(data, error):
    with pytest.raises(TodoError, match=error):
        added(cfg(), data)


def test_added_refuses_beyond_the_limit():
    full = cfg(*[Todo(id=f"x{i}", member_id="m", text="t") for i in range(300)])
    with pytest.raises(TodoError, match="too many"):
        added(full, {"member_id": "m", "text": "one more"})


def test_deleted():
    c = cfg(Todo(id="a", member_id="m", text="a"), Todo(id="b", member_id="m", text="b"))
    assert [t.id for t in deleted(c, "a").todos] == ["b"]
    assert deleted(c, "zzz") is None


def test_display_add_and_delete_over_the_socket(data_dir):
    app = create_app(data_dir)
    app.state.store.save_config(cfg())
    with LocalClient(app, client=("127.0.0.1", 50000)) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json(), ws.receive_json()
        ws.send_json({"type": "action", "id": "x1", "entity_id": "todo.new", "command": "add",
                      "data": {"member_id": "m", "text": "Píchnout ouška", "due": None}})
        msgs = [ws.receive_json(), ws.receive_json()]
        assert {"type": "action_result", "id": "x1", "ok": True} in msgs
        [todo] = app.state.store.config.todos
        assert todo.text == "Píchnout ouška"
        ws.send_json({"type": "action", "id": "x2", "entity_id": "todo.new", "command": "add", "data": {"member_id": "m", "text": ""}})
        assert ws.receive_json() == {"type": "action_result", "id": "x2", "ok": False, "error": "empty text"}
        ws.send_json({"type": "action", "id": "x3", "entity_id": f"todo.{todo.id}", "command": "delete"})
        msgs = [ws.receive_json(), ws.receive_json()]
        assert {"type": "action_result", "id": "x3", "ok": True} in msgs
    assert app.state.store.config.todos == []


def test_view_only_viewer_cannot_add(data_dir):
    app = create_app(data_dir)
    app.state.store.save_config(cfg().model_copy(update={"public_view": True}))
    with _anon(app) as c, c.websocket_connect("/ws", headers=LAN_HDRS) as ws:
        ws.receive_json()
        assert ws.receive_json() == {"type": "viewer", "can_act": False}
        ws.send_json({"type": "action", "id": "v1", "entity_id": "todo.new", "command": "add", "data": {"member_id": "m", "text": "x"}})
        assert ws.receive_json()["error"] == "view_only"
    assert app.state.store.config.todos == []
