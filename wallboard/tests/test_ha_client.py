import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wallboard.app import create_app
from wallboard.config import Config, ConfigStore, Member, Room, Tile
from wallboard.ha_client import HAClient, HAError, filter_attributes
from wallboard.hub import Hub

_spec = importlib.util.spec_from_file_location(
    "mock_ha", Path(__file__).resolve().parents[2] / "dev" / "mock_ha.py"
)
mock_ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_ha)

WANTED = {
    "light.kitchen",
    "light.hallway",
    "sensor.living_temperature",
    "sensor.living_humidity",
    "binary_sensor.front_door",
    "person.dad",
    "weather.home",
    "scene.good_night",
    "vacuum.robot",
}


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, msg):
        self.messages.append(json.loads(json.dumps(msg)))


async def wait_for(pred, timeout=2.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


def make_store(data_dir, port, token="dev-token"):
    store = ConfigStore(data_dir)
    store.save_config(
        Config(
            ha_url=f"http://127.0.0.1:{port}",
            weather_entity="weather.home",
            members=[Member(id="dad", name="Dad", initial="D", color="#4f8cff", person_entity="person.dad")],
            rooms=[
                Room(
                    name="Living",
                    temperature_entity="sensor.living_temperature",
                    humidity_entity="sensor.living_humidity",
                    light_entities=["light.kitchen"],
                )
            ],
            tiles=[
                Tile(entity_id="light.kitchen", group="lights", controllable=True),
                Tile(entity_id="light.hallway", group="lights", controllable=True),
                Tile(entity_id="vacuum.robot", group="devices", controllable=True),
                Tile(entity_id="scene.good_night", group="devices", controllable=True),
            ],
            status_entities=["binary_sensor.front_door"],
            quick_tiles=["scene.good_night"],
        )
    )
    secrets = store.secrets
    store.save_secrets(secrets.model_copy(update={"ha_token": token}))
    return store


@pytest.fixture
async def env(data_dir):
    mock = mock_ha.MockHA(token="dev-token")
    port = await mock.start()
    store = make_store(data_dir, port)
    hub = Hub()
    sock = FakeSocket()
    await hub.connect(sock)
    client = HAClient(store, hub, backoff_base=0.05, backoff_max=0.2, call_timeout=1.0, connect_timeout=1.0)
    yield type("Env", (), {"mock": mock, "port": port, "store": store, "hub": hub, "sock": sock, "client": client})
    await client.stop()
    await mock.stop()


def ha_snapshot(hub):
    return hub.snapshot()["ha"]


def status(hub):
    return hub.snapshot()["status"]["ha"]


async def test_subscribes_only_wanted(env):
    assert env.client.wanted_entities() == WANTED
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    snap = ha_snapshot(env.hub)
    assert set(snap) == WANTED
    assert "sensor.outdoor_temperature" not in snap
    assert env.client.connected
    st = snap["light.kitchen"]
    assert st["state"] == "on" and st["attributes"]["brightness"] == 180
    assert st["last_changed"].endswith("+00:00")


async def test_state_change_published(env):
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    env.sock.messages.clear()
    await env.mock.set_state("light.kitchen", "off", {"friendly_name": "Kitchen"})
    await wait_for(lambda: any(m.get("type") == "ha" and m["entity_id"] == "light.kitchen" for m in env.sock.messages))
    msg = next(m for m in env.sock.messages if m.get("type") == "ha")
    assert msg["state"]["state"] == "off"
    assert "brightness" not in msg["state"]["attributes"]  # removal diff applied
    assert ha_snapshot(env.hub)["light.kitchen"]["state"] == "off"


async def test_attributes_filtered(env):
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    await env.mock.set_state(
        "light.kitchen",
        "on",
        {"friendly_name": "K", "entity_picture": "/p", "access_token": "abc", "url": "x?token=1", "keep": 1},
    )
    await wait_for(lambda: ha_snapshot(env.hub)["light.kitchen"]["attributes"].get("keep") == 1)
    assert ha_snapshot(env.hub)["light.kitchen"]["attributes"] == {"friendly_name": "K", "keep": 1}
    # the initial snapshot is filtered too (person.dad has entity_picture in the mock)
    assert "entity_picture" not in ha_snapshot(env.hub)["person.dad"]["attributes"]


def test_filter_attributes_function():
    attrs = {"a": 1, "My_Password": "x", "entity_picture_local": "y", "Secret_thing": 1, "u": "A?TOKEN=1", "n": 5}
    assert filter_attributes(attrs) == {"a": 1, "n": 5}
    assert HAClient.filter_attributes(attrs) == {"a": 1, "n": 5}


async def test_bad_token_sets_status_false_and_retries(env):
    env.store.save_secrets(env.store.secrets.model_copy(update={"ha_token": "wrong"}))
    await env.client.start()
    await asyncio.sleep(0.3)  # several rejected attempts
    assert status(env.hub) is False and not env.client.connected
    assert ha_snapshot(env.hub) == {}
    env.store.save_secrets(env.store.secrets.model_copy(update={"ha_token": "dev-token"}))
    await env.client.restart()
    await wait_for(lambda: status(env.hub) is True)
    assert set(ha_snapshot(env.hub)) == WANTED


async def test_reconnect_after_drop(env):
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    await env.mock.stop()
    await wait_for(lambda: status(env.hub) is False)
    assert set(ha_snapshot(env.hub)) == WANTED  # stale states kept
    env.mock.states["light.kitchen"]["s"] = "off"
    await env.mock.start(env.port)
    await wait_for(lambda: status(env.hub) is True)
    assert ha_snapshot(env.hub)["light.kitchen"]["state"] == "off"


async def test_resubscribe_removes_unwanted_and_missing(env):
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    await env.hub.publish({"type": "ha", "entity_id": "sensor.stale_old", "state": {"state": "1", "attributes": {}, "last_changed": "x"}})
    del env.mock.states["light.hallway"]  # gone from HA
    await env.client.restart()
    await wait_for(lambda: status(env.hub) is True)
    assert set(ha_snapshot(env.hub)) == WANTED - {"light.hallway"}


async def test_call_service_toggles_and_unknown_entity_raises_HAError(env):
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    await env.client.call_service("light", "toggle", "light.hallway")
    await wait_for(lambda: ha_snapshot(env.hub)["light.hallway"]["state"] == "on")
    with pytest.raises(HAError):
        await env.client.call_service("light", "toggle", "light.nope")


async def test_call_service_not_connected_raises(env):
    with pytest.raises(HAError):
        await env.client.call_service("light", "toggle", "light.kitchen")


async def test_call_service_timeout(env):
    client = HAClient(env.store, env.hub, call_timeout=0.1, backoff_base=0.05)
    await client.start()
    await wait_for(lambda: client.connected)
    # make the mock answer too slowly for the client timeout
    orig = env.mock._call_service

    async def hang(ws, m):
        await asyncio.sleep(0.4)

    env.mock._call_service = hang
    try:
        with pytest.raises(HAError):
            await client.call_service("light", "toggle", "light.kitchen")
    finally:
        env.mock._call_service = orig
        await client.stop()


async def test_forecast_payload_shape(env):
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    await wait_for(lambda: env.hub.snapshot()["weather"] is not None)
    payload = await env.client.get_forecast()
    assert payload == env.hub.snapshot()["weather"]
    assert payload["current"] == {"condition": "partlycloudy", "temperature": 14.2}
    assert len(payload["daily"]) == 7
    for d in payload["daily"]:
        assert set(d) == {"date", "condition", "high", "low", "precip_probability"}
        assert len(d["date"]) == 10
    assert payload["daily"][0]["high"] == 14 and payload["daily"][0]["low"] == 6


async def test_no_url_or_token_idles_without_network(data_dir):
    store = ConfigStore(data_dir)
    hub = Hub()
    client = HAClient(store, hub, backoff_base=0.01)
    await client.start()
    await asyncio.sleep(0.1)
    assert not client.connected and status(hub) is False
    await client.stop()


def test_app_without_ha_url_starts_and_stops(data_dir):
    app = create_app(data_dir)
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert isinstance(app.state.ha, HAClient)
