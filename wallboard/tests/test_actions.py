import asyncio

import pytest

from test_hub import LocalClient
from wallboard.actions import ActionHandler, ActionNotAllowed, resolve_action
from wallboard.app import create_app
from wallboard.config import Config, Tile
from wallboard.ha_client import HAError


def cfg(*entity_ids, controllable=True):
    return Config(tiles=[Tile(entity_id=e, group="devices", controllable=controllable) for e in entity_ids])


@pytest.mark.parametrize(
    "entity_id,state,expected",
    [
        ("light.a", "on", ("light", "toggle")),
        ("switch.a", None, ("switch", "toggle")),
        ("fan.a", "off", ("fan", "toggle")),
        ("input_boolean.a", "on", ("input_boolean", "toggle")),
        ("vacuum.a", "cleaning", ("vacuum", "return_to_base")),
        ("vacuum.a", "returning", ("vacuum", "return_to_base")),
        ("vacuum.a", "docked", ("vacuum", "start")),
        ("vacuum.a", None, ("vacuum", "start")),
        ("scene.a", None, ("scene", "turn_on")),
        ("script.a", "off", ("script", "turn_on")),
        ("cover.a", "open", ("cover", "close_cover")),
        ("cover.a", "opening", ("cover", "close_cover")),
        ("cover.a", "closed", ("cover", "open_cover")),
        ("cover.a", None, ("cover", "open_cover")),
    ],
)
def test_mapping(entity_id, state, expected):
    assert resolve_action(cfg(entity_id), entity_id, state) == expected


def test_not_in_tiles_rejected():
    with pytest.raises(ActionNotAllowed):
        resolve_action(cfg("light.a"), "light.b", "on")


def test_tile_not_controllable_rejected():
    with pytest.raises(ActionNotAllowed):
        resolve_action(cfg("light.a", controllable=False), "light.a", "on")


def test_unsupported_domain_rejected():
    with pytest.raises(ActionNotAllowed):
        resolve_action(cfg("climate.x"), "climate.x", "heat")


@pytest.mark.parametrize("bad", ["light", ".a", "light.", ""])
def test_malformed_entity_rejected(bad):
    with pytest.raises(ActionNotAllowed):
        resolve_action(cfg(bad, "light.a"), bad, None)


class FakeStore:
    def __init__(self, config):
        self.config = config


class FakeHub:
    def __init__(self, ha=None):
        self._ha = ha or {}

    def snapshot(self):
        return {"ha": self._ha}


class FakeHA:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    async def start(self):
        pass

    async def stop(self):
        pass

    async def call_service(self, domain, service, entity_id=None, **kw):
        self.calls.append((domain, service, entity_id))
        if self.exc:
            raise self.exc


def handler(ha, states=None):
    return ActionHandler(FakeStore(cfg("light.a", "cover.c")), FakeHub(states), ha)


def test_handle_action_calls_ha_once():
    ha = FakeHA()
    res = asyncio.run(handler(ha).handle("light.a", "id1"))
    assert res == {"type": "action_result", "id": "id1", "ok": True, "error": None}
    assert ha.calls == [("light", "toggle", "light.a")]


def test_handle_uses_hub_state():
    ha = FakeHA()
    h = handler(ha, states={"cover.c": {"state": "open", "attributes": {}, "last_changed": "x"}})
    asyncio.run(h.handle("cover.c", "i"))
    assert ha.calls == [("cover", "close_cover", "cover.c")]


def test_handle_action_reports_ha_error():
    ha = FakeHA(HAError("boom"))
    res = asyncio.run(handler(ha).handle("light.a", "i"))
    assert res == {"type": "action_result", "id": "i", "ok": False, "error": "boom"}


def test_handle_unexpected_error_is_generic():
    ha = FakeHA(RuntimeError("secret-token-xyz"))
    res = asyncio.run(handler(ha).handle("light.a", "i"))
    assert res == {"type": "action_result", "id": "i", "ok": False, "error": "action failed"}


def test_handle_not_allowed_makes_no_call():
    ha = FakeHA()
    res = asyncio.run(handler(ha).handle("light.zzz", "i"))
    assert res == {"type": "action_result", "id": "i", "ok": False, "error": "not allowed"}
    assert ha.calls == []


def test_wiring_through_display_websocket(data_dir, monkeypatch):
    ha = FakeHA()
    monkeypatch.setattr("wallboard.app.HAClient", lambda store, hub: ha)
    app = create_app(data_dir)
    app.state.store.save_config(
        Config(tiles=[Tile(entity_id="light.kitchen", group="lights", controllable=True)])
    )
    with LocalClient(app, client=("127.0.0.1", 50000)) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "action", "id": "a1", "entity_id": "light.kitchen"})
        assert ws.receive_json() == {"type": "action_result", "id": "a1", "ok": True, "error": None}
        ws.send_json({"type": "action", "id": "a2", "entity_id": "light.other"})
        assert ws.receive_json() == {"type": "action_result", "id": "a2", "ok": False, "error": "not allowed"}
    assert [x[:2] for x in ha.calls] == [("light", "toggle")]
