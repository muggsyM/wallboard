#!/usr/bin/env python3
"""Tiny fake Home Assistant for developing the wallboard without a real HA.

Serves /api/websocket: a WebSocket speaking enough of the HA API (auth, ping,
subscribe_entities, call_service, weather.get_forecasts) and randomly changing
states every few seconds.

Usage:  python3 dev/mock_ha.py [--port 8123] [--token dev-token]
Then set the wallboard's HA URL to http://127.0.0.1:8123 with token dev-token.
In tests:  mock = MockHA(); port = await mock.start(); ...; await mock.stop()
Requires: pip install websockets   (>= 13)
"""

import argparse
import asyncio
import copy
import json
import mimetypes
import random
import time
from datetime import date, timedelta
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve as ws_serve
from websockets.datastructures import Headers
from websockets.http11 import Response

ROOT = Path(__file__).resolve().parent.parent
WWW = ROOT / "www"
DEV = ROOT / "dev"
TOKEN = "dev-token"

STATES = {
    "weather.home": {"s": "partlycloudy", "a": {"friendly_name": "Home", "temperature": 14.2, "temperature_unit": "°C", "humidity": 71}},
    "sensor.outdoor_temperature": {"s": "13.8", "a": {"friendly_name": "Outdoor temperature", "unit_of_measurement": "°C", "device_class": "temperature"}},
    "sensor.living_room_humidity": {"s": "48", "a": {"friendly_name": "Living room humidity", "unit_of_measurement": "%", "device_class": "humidity"}},
    "climate.living_room": {"s": "heat", "a": {"friendly_name": "Living room", "current_temperature": 21.4, "temperature": 22, "hvac_action": "heating"}},
    "binary_sensor.front_door": {"s": "off", "a": {"friendly_name": "Front door", "device_class": "door"}},
    "light.kitchen": {"s": "on", "a": {"friendly_name": "Kitchen", "brightness": 180}},
    "light.hallway": {"s": "off", "a": {"friendly_name": "Hallway"}},
    "vacuum.robot": {"s": "docked", "a": {"friendly_name": "Robot", "battery_level": 92}},
    "scene.good_night": {"s": "unknown", "a": {"friendly_name": "Good night"}},
    "person.dad": {"s": "home", "a": {"friendly_name": "Dad", "entity_picture": "/api/image/serve/x/512x512"}},
    "sensor.living_temperature": {"s": "21.4", "a": {"friendly_name": "Living temperature", "unit_of_measurement": "°C", "device_class": "temperature"}},
    "sensor.living_humidity": {"s": "46", "a": {"friendly_name": "Living humidity", "unit_of_measurement": "%", "device_class": "humidity"}},
    "sensor.house_power": {"s": "1342.7", "a": {"friendly_name": "House power", "unit_of_measurement": "W", "device_class": "power"}},
    "sensor.energy_today": {"s": "7.83", "a": {"friendly_name": "Energy today", "unit_of_measurement": "kWh", "device_class": "energy"}},
    "person.alice": {"s": "home", "a": {"friendly_name": "Alice"}},
    "person.bob": {"s": "not_home", "a": {"friendly_name": "Bob"}},
    "sensor.broken": {"s": "unavailable", "a": {"friendly_name": "Broken sensor"}},
    # Home page: a second and third room, an open window, a washer, a lock, more lights.
    "sensor.kids_temperature": {"s": "20.4", "a": {"friendly_name": "Kids temperature", "unit_of_measurement": "°C", "device_class": "temperature"}},
    "sensor.kids_humidity": {"s": "51", "a": {"friendly_name": "Kids humidity", "unit_of_measurement": "%", "device_class": "humidity"}},
    "sensor.kitchen_temperature": {"s": "22.1", "a": {"friendly_name": "Kitchen temperature", "unit_of_measurement": "°C", "device_class": "temperature"}},
    "binary_sensor.window_kids": {"s": "on", "a": {"friendly_name": "Kids' window", "device_class": "window"}},
    "sensor.washer": {"s": "running", "a": {"friendly_name": "Washing machine", "icon": "mdi:washing-machine"}},
    "lock.front_door": {"s": "locked", "a": {"friendly_name": "Front door lock"}},
    "light.living": {"s": "on", "a": {"friendly_name": "Living", "brightness": 200}},
    "light.kids": {"s": "off", "a": {"friendly_name": "Kids"}},
    "switch.coffee": {"s": "on", "a": {"friendly_name": "Coffee machine"}},
}


def file_response(path: Path) -> Response:
    body = path.read_bytes()
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")])
    return Response(HTTPStatus.OK.value, "OK", headers, body)


def process_request(connection, request):
    path = request.path.split("?", 1)[0]
    if path == "/api/websocket":
        return None  # continue with the WebSocket handshake
    if path in ("/", "/dev", "/dev/"):
        return Response(302, "Found", Headers([("Location", "/dev/preview.html"), ("Content-Length", "0")]), b"")
    if path == "/local/dakboard/config.js":
        return file_response(DEV / "config.dev.js")
    for prefix, base in (("/local/", WWW), ("/dev/", DEV)):
        if path.startswith(prefix):
            target = (base / path[len(prefix):]).resolve()
            if target.is_file() and target.is_relative_to(base):
                return file_response(target)
    return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")


def compressed(state):
    return {"s": state["s"], "a": state["a"], "c": "mock", "lc": time.time()}


def _ok(mid, result=None):
    return json.dumps({"id": mid, "type": "result", "success": True, "result": result})


def _err(mid, code, message):
    return json.dumps({"id": mid, "type": "result", "success": False, "error": {"code": code, "message": message}})


def _forecast():
    start = date.today()
    conditions = ["sunny", "partlycloudy", "cloudy", "rainy", "sunny", "cloudy", "pouring"]
    return [
        {
            "datetime": f"{(start + timedelta(days=i)).isoformat()}T00:00:00+00:00",
            "condition": conditions[i],
            "temperature": 14 + i,
            "templow": 6 + i,
            "precipitation_probability": (i * 15) % 100,
        }
        for i in range(7)
    ]


class MockHA:
    """The fake HA server; start()/stop() make it usable in-process from tests."""

    def __init__(self, token: str = TOKEN, host: str = "127.0.0.1"):
        self.token = token
        self.host = host
        self.states = copy.deepcopy(STATES)
        self.subscribers = set()  # (websocket, sub_id, frozenset(entity_ids) | None)
        self.auth_attempts = 0
        self._server = None
        self._ticker = None

    async def start(self, port: int = 0, interval: float | None = None) -> int:
        """Listen (port 0 = any free port) and return the bound port."""
        self._server = await ws_serve(self._handler, self.host, port, process_request=process_request)
        if interval:
            self._ticker = asyncio.create_task(self._tick(interval))
        return next(iter(self._server.sockets)).getsockname()[1]

    async def stop(self) -> None:
        if self._ticker:
            self._ticker.cancel()
            self._ticker = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.subscribers.clear()

    async def set_state(self, entity_id: str, state: str, attrs: dict | None = None) -> None:
        """Set an entity's state (replacing its attributes if given) and push a diff."""
        if entity_id not in self.states:
            self.states[entity_id] = {"s": state, "a": dict(attrs or {})}
            await self._push_add(entity_id)
            return
        st = self.states[entity_id]
        diff = {"+": {"s": state, "lc": time.time()}}
        if attrs is not None:
            removed = [k for k in st["a"] if k not in attrs]
            diff["+"]["a"] = attrs
            if removed:
                diff["-"] = {"a": removed}
            st["a"] = dict(attrs)
        st["s"] = state
        await self._push({entity_id: diff})

    async def remove_state(self, entity_id: str) -> None:
        """Delete an entity and push a removal event."""
        self.states.pop(entity_id, None)
        for ws, sub_id, ids in list(self.subscribers):
            if ids is None or entity_id in ids:
                try:
                    await ws.send(json.dumps({"id": sub_id, "type": "event", "event": {"r": [entity_id]}}))
                except Exception:
                    self.subscribers.discard((ws, sub_id, ids))

    async def _push_add(self, entity_id):
        for ws, sub_id, ids in list(self.subscribers):
            if ids is None or entity_id in ids:
                try:
                    msg = {"id": sub_id, "type": "event", "event": {"a": {entity_id: compressed(self.states[entity_id])}}}
                    await ws.send(json.dumps(msg))
                except Exception:
                    self.subscribers.discard((ws, sub_id, ids))

    async def _push(self, changes):
        for ws, sub_id, ids in list(self.subscribers):
            mine = {k: v for k, v in changes.items() if ids is None or k in ids}
            if mine:
                try:
                    await ws.send(json.dumps({"id": sub_id, "type": "event", "event": {"c": mine}}))
                except Exception:
                    self.subscribers.discard((ws, sub_id, ids))

    async def _call_service(self, ws, m):
        mid, domain, service = m.get("id"), m.get("domain"), m.get("service")
        data = m.get("service_data") or {}
        target = m.get("target") or {}
        eid = target.get("entity_id") or data.get("entity_id")
        if eid not in self.states:
            return await ws.send(_err(mid, "not_found", f"Entity not found: {eid}"))
        if domain == "weather" and service == "get_forecasts":
            result = {"context": {"id": "mock"}}
            if m.get("return_response"):
                result["response"] = {eid: {"forecast": _forecast()}}
            return await ws.send(_ok(mid, result))
        st = self.states[eid]
        new = None
        if domain in ("light", "switch") and service in ("toggle", "turn_on", "turn_off"):
            new = {"toggle": "off" if st["s"] == "on" else "on", "turn_on": "on", "turn_off": "off"}[service]
        elif domain == "vacuum" and service == "start":
            new = "cleaning"
        elif domain == "vacuum" and service == "return_to_base":
            new = "docked"
        elif domain == "scene" and service == "turn_on":
            new = str(time.time())
        if new is None:
            return await ws.send(_err(mid, "not_found", f"Service {domain}.{service} not found."))
        await ws.send(_ok(mid, {"context": {"id": "mock"}}))
        await self.set_state(eid, new)

    async def _handler(self, ws):
        await ws.send(json.dumps({"type": "auth_required", "ha_version": "2026.11.0"}))
        msg = json.loads(await ws.recv())
        self.auth_attempts += 1
        if msg.get("type") != "auth" or msg.get("access_token") != self.token:
            await ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token or password"}))
            return
        await ws.send(json.dumps({"type": "auth_ok", "ha_version": "2026.11.0"}))
        mine = set()
        try:
            async for raw in ws:
                m = json.loads(raw)
                t, mid = m.get("type"), m.get("id")
                if t == "ping":
                    await ws.send(json.dumps({"id": mid, "type": "pong"}))
                elif t == "subscribe_entities":
                    ids = frozenset(m["entity_ids"]) if m.get("entity_ids") else None
                    sub = (ws, mid, ids)
                    self.subscribers.add(sub)
                    mine.add(sub)
                    await ws.send(_ok(mid))
                    added = {k: compressed(v) for k, v in self.states.items() if ids is None or k in ids}
                    await ws.send(json.dumps({"id": mid, "type": "event", "event": {"a": added}}))
                elif t == "call_service":
                    await self._call_service(ws, m)
                elif t == "get_states":
                    result = [{"entity_id": k, "state": v["s"], "attributes": v["a"]} for k, v in self.states.items()]
                    await ws.send(_ok(mid, result))
                else:
                    await ws.send(_err(mid, "unknown_command", "Unknown command."))
        finally:
            self.subscribers.difference_update(mine)

    def mutate(self):
        """Change one or two entities; return {entity_id: diff} in compressed format."""
        states = self.states
        changes = {}

        def num(eid, delta, digits, lo, hi):
            v = round(min(hi, max(lo, float(states[eid]["s"]) + random.uniform(-delta, delta))), digits)
            states[eid]["s"] = str(v)
            changes[eid] = {"+": {"s": str(v), "lc": time.time()}}

        pick = random.choice(["temp", "hum", "power", "door", "light", "climate", "weather"])
        num("sensor.house_power", 250, 1, 150, 4500)
        if pick == "temp":
            num("sensor.outdoor_temperature", 0.4, 1, -5, 30)
        elif pick == "hum":
            num("sensor.living_room_humidity", 3, 0, 30, 80)
        elif pick == "door":
            st = states["binary_sensor.front_door"]
            st["s"] = "off" if st["s"] == "on" else "on"
            changes["binary_sensor.front_door"] = {"+": {"s": st["s"]}}
        elif pick == "light":
            st = states["light.kitchen"]
            if st["s"] == "on":
                st["s"] = "off"
                st["a"].pop("brightness", None)
                changes["light.kitchen"] = {"+": {"s": "off"}, "-": {"a": ["brightness"]}}
            else:
                st["s"] = "on"
                st["a"]["brightness"] = random.randint(40, 255)
                changes["light.kitchen"] = {"+": {"s": "on", "a": {"brightness": st["a"]["brightness"]}}}
        elif pick == "climate":
            a = states["climate.living_room"]["a"]
            a["current_temperature"] = round(a["current_temperature"] + random.uniform(-0.3, 0.3), 1)
            a["hvac_action"] = "heating" if a["current_temperature"] < a["temperature"] else "idle"
            changes["climate.living_room"] = {"+": {"a": {"current_temperature": a["current_temperature"], "hvac_action": a["hvac_action"]}}}
        elif pick == "weather":
            st = states["weather.home"]
            st["s"] = random.choice(["sunny", "partlycloudy", "cloudy", "rainy"])
            st["a"]["temperature"] = round(st["a"]["temperature"] + random.uniform(-0.5, 0.5), 1)
            changes["weather.home"] = {"+": {"s": st["s"], "a": {"temperature": st["a"]["temperature"]}}}
        return changes

    async def _tick(self, interval):
        while True:
            await asyncio.sleep(interval)
            await self._push(self.mutate())


async def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8123)
    p.add_argument("--interval", type=float, default=3.0, help="seconds between random state changes")
    p.add_argument("--token", default=TOKEN, help="access token to accept (use another value to test auth errors)")
    args = p.parse_args()
    mock = MockHA(token=args.token, host="0.0.0.0")
    await mock.start(args.port, args.interval)
    print(f"Mock HA on ws://localhost:{args.port}/api/websocket  (token: {args.token})")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
