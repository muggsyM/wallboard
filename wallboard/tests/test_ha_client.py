import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wallboard.app import create_app
from wallboard.config import Config, ConfigStore, Member, Room, Tile
from zoneinfo import ZoneInfo

from wallboard.ha_client import HAClient, HAError, filter_attributes, forecast_date
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


async def test_bad_token_sets_status_false_and_retries(env, caplog):
    caplog.set_level("DEBUG")
    env.store.save_secrets(env.store.secrets.model_copy(update={"ha_token": "wrong"}))
    await env.client.start()
    await wait_for(lambda: env.mock.auth_attempts >= 2)  # it keeps retrying on its own
    assert status(env.hub) is False and not env.client.connected
    assert ha_snapshot(env.hub) == {}
    env.store.save_secrets(env.store.secrets.model_copy(update={"ha_token": "dev-token"}))
    await env.client.restart()
    await wait_for(lambda: status(env.hub) is True)
    assert set(ha_snapshot(env.hub)) == WANTED
    # (the in-process mock server's own websockets logging is test-side, not the client's)
    client_logs = "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records if r.name != "websockets.server")
    assert "wrong" not in client_logs and "dev-token" not in client_logs


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


async def test_backoff_resets_after_abnormal_drops(env):
    client = HAClient(env.store, env.hub, backoff_base=0.1, backoff_max=5.0, call_timeout=1.0)
    await client.start()
    try:
        loop = asyncio.get_running_loop()
        for _ in range(4):
            await wait_for(lambda: client.connected and env.mock.subscribers)
            dropped = loop.time()
            for ws, _sid, _ids in list(env.mock.subscribers):
                ws.transport.abort()  # no close frame: ConnectionClosedError on the client
            await wait_for(lambda: not client.connected)
            await wait_for(lambda: client.connected and env.mock.subscribers, timeout=1.0)
            assert loop.time() - dropped < 0.35  # about backoff_base, not doubling
    finally:
        await client.stop()


async def test_state_for_new_entity_is_published(env):
    env.store.save_config(env.store.config.model_copy(update={"status_entities": ["binary_sensor.front_door", "sensor.new_one"]}))
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    assert "sensor.new_one" not in ha_snapshot(env.hub)
    await env.mock.set_state("sensor.new_one", "5")  # not in HA yet: mock sends an add event
    await wait_for(lambda: "sensor.new_one" in ha_snapshot(env.hub))
    assert ha_snapshot(env.hub)["sensor.new_one"]["state"] == "5"


async def test_removal_event_deletes_entity(env):
    await env.client.start()
    await wait_for(lambda: status(env.hub) is True)
    await env.mock.remove_state("light.hallway")
    await wait_for(lambda: "light.hallway" not in ha_snapshot(env.hub))
    assert "light.kitchen" in ha_snapshot(env.hub)


async def test_pending_call_fails_fast_on_disconnect(env):
    client = HAClient(env.store, env.hub, call_timeout=3.0, backoff_base=0.05)
    await client.start()
    await wait_for(lambda: client.connected)

    async def never(ws, m):
        await asyncio.sleep(0.5)

    env.mock._call_service = never
    call = asyncio.create_task(client.call_service("light", "toggle", "light.kitchen"))
    await asyncio.sleep(0.1)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for ws, _sid, _ids in list(env.mock.subscribers):
        ws.transport.abort()
    with pytest.raises(HAError):
        await call
    assert loop.time() - t0 < 1.0
    await client.stop()


PRAGUE = ZoneInfo("Europe/Prague")


def test_forecast_date_local_midnight_in_utc():
    # 7 Nov 00:00 in Prague, sent as UTC
    assert forecast_date("2026-11-06T23:00:00+00:00", PRAGUE) == "2026-11-07"


def test_forecast_date_noon_utc():
    assert forecast_date("2026-11-07T12:00:00+00:00", PRAGUE) == "2026-11-07"
    assert forecast_date("2026-11-07T23:30:00Z", PRAGUE) == "2026-11-08"


def test_forecast_date_bare_date_kept():
    assert forecast_date("2026-11-07", PRAGUE) == "2026-11-07"


def test_forecast_date_naive_datetime_is_local():
    assert forecast_date("2026-11-07T00:00:00", PRAGUE) == "2026-11-07"


async def test_forecast_uses_config_timezone(env, monkeypatch):
    monkeypatch.setattr(mock_ha, "_forecast", lambda: [
        {"datetime": "2026-11-06T23:00:00+00:00", "condition": "sunny", "temperature": 10, "templow": 2},
    ])
    await env.client.start()
    await wait_for(lambda: env.client.connected)
    payload = await env.client.get_forecast()
    assert payload["daily"][0]["date"] == "2026-11-07"


def test_mock_has_coffee_not_boiler():
    # The owner put the boiler out of scope; the dev mock shows a coffee machine instead.
    assert mock_ha.STATES["switch.coffee"]["a"]["friendly_name"] == "Coffee machine"
    assert "switch.boiler" not in mock_ha.STATES


class FlakyStore:
    """Wraps a ConfigStore; the first `fails` reads of `config` raise (a corrupt file)."""

    def __init__(self, store, fails=1):
        self._store = store
        self.fails = fails

    @property
    def config(self):
        if self.fails > 0:
            self.fails -= 1
            raise ValueError("config.json is corrupt")
        return self._store.config

    @property
    def secrets(self):
        return self._store.secrets


async def test_ha_task_survives_corrupt_config(env, caplog):
    client = HAClient(FlakyStore(env.store), env.hub, backoff_base=0.05, backoff_max=0.2, connect_timeout=1.0)
    await client.start()
    try:
        await wait_for(lambda: client.connected)  # retried after the failed read, then connected
        assert "ValueError" in caplog.text
    finally:
        await client.stop()
