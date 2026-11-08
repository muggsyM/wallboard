#!/usr/bin/env python3
"""Tiny fake Home Assistant for previewing the DAKboard widget without a real HA.

Serves, on one port (like real HA):
  /local/...        files from ../www (config.js is replaced by dev/config.dev.js)
  /dev/...          files from ./dev (preview.html)
  /api/websocket    a WebSocket speaking enough of the HA API (auth, ping,
                    subscribe_entities) and randomly changing states every few seconds.

Usage:  python3 dev/mock_ha.py [--port 8123]
Then open http://localhost:8123/dev/preview.html
Requires: pip install websockets   (>= 13)
"""

import argparse
import asyncio
import json
import mimetypes
import random
import time
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve
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
    "sensor.house_power": {"s": "1342.7", "a": {"friendly_name": "House power", "unit_of_measurement": "W", "device_class": "power"}},
    "sensor.energy_today": {"s": "7.83", "a": {"friendly_name": "Energy today", "unit_of_measurement": "kWh", "device_class": "energy"}},
    "person.alice": {"s": "home", "a": {"friendly_name": "Alice"}},
    "person.bob": {"s": "not_home", "a": {"friendly_name": "Bob"}},
    "sensor.broken": {"s": "unavailable", "a": {"friendly_name": "Broken sensor"}},
}

subscribers = set()  # (websocket, sub_id, frozenset(entity_ids) | None)


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


async def handler(ws):
    await ws.send(json.dumps({"type": "auth_required", "ha_version": "2026.11.0"}))
    msg = json.loads(await ws.recv())
    if msg.get("type") != "auth" or msg.get("access_token") != TOKEN:
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
                subscribers.add(sub)
                mine.add(sub)
                await ws.send(json.dumps({"id": mid, "type": "result", "success": True, "result": None}))
                added = {k: compressed(v) for k, v in STATES.items() if ids is None or k in ids}
                await ws.send(json.dumps({"id": mid, "type": "event", "event": {"a": added}}))
            elif t == "get_states":
                result = [{"entity_id": k, "state": v["s"], "attributes": v["a"]} for k, v in STATES.items()]
                await ws.send(json.dumps({"id": mid, "type": "result", "success": True, "result": result}))
            else:
                await ws.send(json.dumps({"id": mid, "type": "result", "success": False,
                                          "error": {"code": "unknown_command", "message": "Unknown command."}}))
    finally:
        subscribers.difference_update(mine)


def mutate():
    """Change one or two entities; return {entity_id: diff} in compressed format."""
    changes = {}

    def num(eid, delta, digits, lo, hi):
        v = round(min(hi, max(lo, float(STATES[eid]["s"]) + random.uniform(-delta, delta))), digits)
        STATES[eid]["s"] = str(v)
        changes[eid] = {"+": {"s": str(v), "lc": time.time()}}

    pick = random.choice(["temp", "hum", "power", "door", "light", "climate", "weather"])
    num("sensor.house_power", 250, 1, 150, 4500)
    if pick == "temp":
        num("sensor.outdoor_temperature", 0.4, 1, -5, 30)
    elif pick == "hum":
        num("sensor.living_room_humidity", 3, 0, 30, 80)
    elif pick == "door":
        st = STATES["binary_sensor.front_door"]
        st["s"] = "off" if st["s"] == "on" else "on"
        changes["binary_sensor.front_door"] = {"+": {"s": st["s"]}}
    elif pick == "light":
        st = STATES["light.kitchen"]
        if st["s"] == "on":
            st["s"] = "off"
            st["a"].pop("brightness", None)
            changes["light.kitchen"] = {"+": {"s": "off"}, "-": {"a": ["brightness"]}}
        else:
            st["s"] = "on"
            st["a"]["brightness"] = random.randint(40, 255)
            changes["light.kitchen"] = {"+": {"s": "on", "a": {"brightness": st["a"]["brightness"]}}}
    elif pick == "climate":
        a = STATES["climate.living_room"]["a"]
        a["current_temperature"] = round(a["current_temperature"] + random.uniform(-0.3, 0.3), 1)
        a["hvac_action"] = "heating" if a["current_temperature"] < a["temperature"] else "idle"
        changes["climate.living_room"] = {"+": {"a": {"current_temperature": a["current_temperature"], "hvac_action": a["hvac_action"]}}}
    elif pick == "weather":
        st = STATES["weather.home"]
        st["s"] = random.choice(["sunny", "partlycloudy", "cloudy", "rainy"])
        st["a"]["temperature"] = round(st["a"]["temperature"] + random.uniform(-0.5, 0.5), 1)
        changes["weather.home"] = {"+": {"s": st["s"], "a": {"temperature": st["a"]["temperature"]}}}
    return changes


async def ticker(interval):
    while True:
        await asyncio.sleep(interval)
        changes = mutate()
        for ws, sub_id, ids in list(subscribers):
            mine = {k: v for k, v in changes.items() if ids is None or k in ids}
            if mine:
                try:
                    await ws.send(json.dumps({"id": sub_id, "type": "event", "event": {"c": mine}}))
                except Exception:
                    subscribers.discard((ws, sub_id, ids))


async def main():
    global TOKEN
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8123)
    p.add_argument("--interval", type=float, default=3.0, help="seconds between random state changes")
    p.add_argument("--token", default=TOKEN, help="access token to accept (use another value to test auth errors)")
    args = p.parse_args()
    TOKEN = args.token
    async with serve(handler, "0.0.0.0", args.port, process_request=process_request):
        print(f"Mock HA on http://localhost:{args.port}/dev/preview.html  (token: {TOKEN})")
        await ticker(args.interval)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
