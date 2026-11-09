from datetime import date, datetime
from zoneinfo import ZoneInfo

from test_hub import LocalClient
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
