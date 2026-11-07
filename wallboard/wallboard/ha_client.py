"""Home Assistant WebSocket client: auth, subscribe_entities, call_service, forecasts, reconnect."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import time
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import websockets

from .config import Config, ConfigStore
from .hub import Hub

log = logging.getLogger(__name__)
# websockets logs frames at DEBUG, which would include the auth message and its token.
_ws_log = logging.getLogger("wallboard.ha_client.websockets")
_ws_log.propagate = False
_ws_log.setLevel(logging.CRITICAL + 1)
_ws_log.addHandler(logging.NullHandler())


class HAError(Exception):
    """A Home Assistant request failed (HA error, timeout, or no connection)."""


def filter_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that may carry a credential (same rule as custom_components/dakboard)."""
    out = {}
    for key, value in attrs.items():
        k = key.lower()
        if "token" in k or "picture" in k or "password" in k or "secret" in k:
            continue
        if isinstance(value, str) and "token=" in value.lower():
            continue
        out[key] = value
    return out


def _iso(ts: Any) -> str | None:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return None


def _ws_url(ha_url: str) -> str:
    parts = urlsplit(ha_url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme)
    if scheme is None or not parts.netloc:
        raise ValueError("ha_url must be an http(s) URL")
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + "/api/websocket", "", ""))


def forecast_date(value: object, tz: ZoneInfo) -> str:
    """Local date of a forecast entry's datetime in tz.

    Integrations often send daily forecasts as local midnight in UTC
    ("2026-11-06T23:00:00+00:00" = 7 Nov in Prague), so a datetime is converted to tz
    before taking its date; a naive one is taken as local, a bare date is kept.
    """
    text = str(value).replace("Z", "+00:00")
    if len(text) == 10:
        return date.fromisoformat(text).isoformat()
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz)
    return dt.date().isoformat()


def wanted_entities_for(cfg: Config) -> set[str]:
    """Every entity the display needs from HA for this config."""
    wanted: set[str] = set()
    for room in cfg.rooms:
        wanted.add(room.temperature_entity)
        if room.humidity_entity:
            wanted.add(room.humidity_entity)
        wanted.update(room.light_entities)
    wanted.update(t.entity_id for t in cfg.tiles)
    wanted.update(cfg.status_entities)
    wanted.update(cfg.quick_tiles)
    wanted.update(m.person_entity for m in cfg.members if m.person_entity)
    if cfg.weather_entity:
        wanted.add(cfg.weather_entity)
    return wanted


async def probe_connection(url: str, token: str, timeout: float = 5.0) -> dict[str, Any]:
    """One-off connect + auth handshake for the settings page's Test button.

    Returns {ok, error, version}. `url` must be http(s) (ValueError otherwise). Error strings
    are fixed texts or exception class names, never anything that could echo the token.
    """
    ws_url = _ws_url(url)
    version: str | None = None

    async def handshake() -> None:
        nonlocal version
        async with websockets.connect(ws_url, open_timeout=timeout, close_timeout=1.0, logger=_ws_log) as ws:
            first = json.loads(await ws.recv())
            if first.get("type") != "auth_required":
                raise HAError("not a Home Assistant WebSocket API")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            reply = json.loads(await ws.recv())
            if reply.get("type") != "auth_ok":
                raise HAError("authentication rejected (check the token)")
            v = reply.get("ha_version") or first.get("ha_version")
            version = v if isinstance(v, str) else None

    try:
        await asyncio.wait_for(handshake(), timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timed out", "version": None}
    except HAError as exc:
        return {"ok": False, "error": str(exc), "version": None}
    except Exception as exc:  # refused, DNS, bad handshake, malformed reply; never str(exc)
        return {"ok": False, "error": f"cannot connect ({type(exc).__name__})", "version": None}
    return {"ok": True, "error": None, "version": version}


class HAClient:
    filter_attributes = staticmethod(filter_attributes)

    def __init__(
        self,
        store: ConfigStore,
        hub: Hub,
        *,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        call_timeout: float = 5.0,
        connect_timeout: float = 10.0,
        close_timeout: float = 1.0,
        forecast_interval: float = 1800.0,
    ) -> None:
        self._store = store
        self._hub = hub
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._call_timeout = call_timeout
        self._connect_timeout = connect_timeout
        self._close_timeout = close_timeout
        self._forecast_interval = forecast_interval
        self._task: asyncio.Task | None = None
        self._ws: Any = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._raw: dict[str, dict[str, Any]] = {}  # entity_id -> {"s", "a", "lc"} (unfiltered)
        self._initial = False
        self._wanted: set[str] = set()
        self._delay = backoff_base
        self._forecast_task: asyncio.Task | None = None
        self._entities: tuple[float, Any, list[dict[str, str]]] | None = None  # (at, ws, items)
        self.connected = False

    # ---- config -------------------------------------------------------------------------

    def wanted_entities(self) -> set[str]:
        return wanted_entities_for(self._store.config)

    # ---- lifecycle ----------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ha-client")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.connected = False
        await self._hub.set_status("ha", False)

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _run(self) -> None:
        self._delay = self._backoff_base
        while True:
            url = self._store.config.ha_url
            token = self._store.secrets.ha_token
            if not url or not token:
                await self._hub.set_status("ha", False)
                await asyncio.Event().wait()  # idle until restart() cancels us
            try:
                await self._session(_ws_url(url), token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never include the token or payloads in the log
                log.warning("home assistant connection failed: %s", type(exc).__name__)
            await self._hub.set_status("ha", False)
            await asyncio.sleep(self._delay)
            self._delay = min(self._delay * 2, self._backoff_max)

    async def _session(self, ws_url: str, token: str) -> None:
        """One connection. Resets the backoff as soon as authentication succeeds, so an
        abnormal drop later in the session (which raises) still retries quickly."""
        try:
            async with websockets.connect(
                ws_url, open_timeout=self._connect_timeout, close_timeout=self._close_timeout, max_size=None,
                logger=_ws_log,
            ) as ws:
                self._ws = ws
                await asyncio.wait_for(self._authenticate(ws, token), self._connect_timeout)
                self._delay = self._backoff_base
                sub_id = await self._subscribe(ws)
                async for raw in ws:
                    await self._dispatch(json.loads(raw), sub_id)
        finally:
            self._ws = None
            self.connected = False
            if self._forecast_task is not None:
                self._forecast_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._forecast_task
                self._forecast_task = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(HAError("connection lost"))
            self._pending.clear()

    async def _authenticate(self, ws, token: str) -> None:
        first = json.loads(await ws.recv())
        if first.get("type") != "auth_required":
            raise HAError("unexpected greeting")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        reply = json.loads(await ws.recv())
        if reply.get("type") != "auth_ok":
            raise HAError("authentication rejected")

    async def _subscribe(self, ws) -> int | None:
        """Send subscribe_entities; the initial `a` event completes the subscription."""
        self._raw = {}
        self._initial = True
        wanted = self._wanted = self.wanted_entities()
        if not wanted:
            await self._reconcile_initial()
            return None
        sub_id = next(self._ids)
        await ws.send(json.dumps({"id": sub_id, "type": "subscribe_entities", "entity_ids": sorted(wanted)}))
        return sub_id

    # ---- incoming messages --------------------------------------------------------------

    async def _dispatch(self, msg: dict, sub_id: int | None) -> None:
        mid = msg.get("id")
        kind = msg.get("type")
        if kind == "result" and mid in self._pending:
            fut = self._pending.pop(mid)
            if not fut.done():
                if msg.get("success"):
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(HAError((msg.get("error") or {}).get("message", "request failed")))
        elif kind == "result" and mid == sub_id:
            if not msg.get("success"):
                raise HAError("subscribe_entities rejected")
        elif kind == "event" and mid == sub_id:
            await self._apply_event(msg.get("event") or {})

    async def _apply_event(self, ev: dict) -> None:
        wanted = self._wanted
        added = {k: v for k, v in (ev.get("a") or {}).items() if k in wanted}
        if self._initial:
            # An initial event is the full picture: sync the hub with exactly what's present.
            self._initial = False
            self._raw = {}
            for eid, st in added.items():
                self._raw[eid] = {"s": st.get("s"), "a": dict(st.get("a") or {}), "lc": st.get("lc")}
            await self._reconcile_initial()
            return
        for eid, st in added.items():
            self._raw[eid] = {"s": st.get("s"), "a": dict(st.get("a") or {}), "lc": st.get("lc")}
            await self._publish_entity(eid)
        for eid, diff in (ev.get("c") or {}).items():
            cur = self._raw.get(eid)
            if cur is None:
                continue
            plus = diff.get("+") or {}
            if "s" in plus:
                cur["s"] = plus["s"]
            if plus.get("a"):
                cur["a"].update(plus["a"])
            if "lc" in plus:
                cur["lc"] = plus["lc"]
            for key in (diff.get("-") or {}).get("a", []):
                cur["a"].pop(key, None)
            await self._publish_entity(eid)
        for eid in ev.get("r") or []:
            if self._raw.pop(eid, None) is not None:
                await self._hub.publish({"type": "ha", "entity_id": eid, "state": None})

    async def _reconcile_initial(self) -> None:
        present = set(self._raw)
        for eid in set(self._hub.snapshot()["ha"]) - present:
            await self._hub.publish({"type": "ha", "entity_id": eid, "state": None})
        for eid in present:
            await self._publish_entity(eid)
        self.connected = True
        if self._forecast_task is None:
            self._forecast_task = asyncio.create_task(self._forecast_loop(), name="ha-forecast")
        await self._hub.set_status("ha", True)

    async def _publish_entity(self, eid: str) -> None:
        raw = self._raw[eid]
        state = {
            "state": raw["s"],
            "attributes": filter_attributes(raw["a"]),
            "last_changed": _iso(raw.get("lc")) or datetime.now(timezone.utc).isoformat(),
        }
        await self._hub.publish({"type": "ha", "entity_id": eid, "state": state})

    # ---- requests -----------------------------------------------------------------------

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        *,
        service_data: dict | None = None,
        target: dict | None = None,
        return_response: bool = False,
    ) -> dict | None:
        """Call a HA service; returns HA's result. Raises HAError on error, timeout or no connection."""
        msg: dict[str, Any] = {"type": "call_service", "domain": domain, "service": service}
        if service_data:
            msg["service_data"] = service_data
        if target is None and entity_id:
            target = {"entity_id": entity_id}
        if target:
            msg["target"] = target
        if return_response:
            msg["return_response"] = True
        return await self._request(msg)

    async def list_entities(self, max_age: float = 30.0) -> list[dict[str, str]]:
        """All HA entities as [{entity_id, name, domain}] sorted by id, for the settings pickers.

        The subscription covers only wanted entities, so this sends get_states over the live
        connection. Cached for `max_age` seconds per connection. Raises HAError when not connected.
        """
        ws = self._ws
        cached = self._entities
        if cached and cached[1] is ws and self.connected and time.monotonic() - cached[0] < max_age:
            return cached[2]
        states = await self._request({"type": "get_states"})
        items = []
        for st in states if isinstance(states, list) else []:
            eid = st.get("entity_id") if isinstance(st, dict) else None
            if not isinstance(eid, str) or "." not in eid:
                continue
            name = (st.get("attributes") or {}).get("friendly_name")
            items.append(
                {"entity_id": eid, "name": name if isinstance(name, str) else eid, "domain": eid.split(".", 1)[0]}
            )
        items.sort(key=lambda e: e["entity_id"])
        self._entities = (time.monotonic(), ws, items)
        return items

    async def _request(self, msg: dict[str, Any]) -> Any:
        """Send one command over the live connection and await its result."""
        ws = self._ws
        if ws is None or not self.connected:
            raise HAError("not connected to Home Assistant")
        mid = next(self._ids)
        msg = {"id": mid, **msg}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await ws.send(json.dumps(msg))
            return await asyncio.wait_for(fut, self._call_timeout)
        except asyncio.TimeoutError:
            raise HAError("Home Assistant did not answer in time") from None
        except HAError:
            raise
        except Exception as exc:
            raise HAError(f"request failed: {type(exc).__name__}") from None
        finally:
            self._pending.pop(mid, None)

    async def get_forecast(self) -> dict | None:
        entity = self._store.config.weather_entity
        if not entity:
            return None
        try:
            result = await self.call_service(
                "weather",
                "get_forecasts",
                service_data={"type": "daily"},
                target={"entity_id": entity},
                return_response=True,
            )
            forecast = result["response"][entity]["forecast"]
            tz = ZoneInfo(self._store.config.timezone)
            daily = [
                {
                    "date": forecast_date(d["datetime"], tz),
                    "condition": d.get("condition"),
                    "high": d.get("temperature"),
                    "low": d.get("templow"),
                    "precip_probability": d.get("precipitation_probability"),
                }
                for d in forecast
            ]
        except (HAError, KeyError, TypeError, ValueError) as exc:
            log.warning("forecast unavailable: %s", type(exc).__name__)
            return None
        cur = self._raw.get(entity) or {}
        return {
            "current": {"condition": cur.get("s"), "temperature": (cur.get("a") or {}).get("temperature")},
            "daily": daily,
        }

    async def _forecast_loop(self) -> None:
        while True:
            payload = await self.get_forecast()
            if payload is not None:
                await self._hub.publish({"type": "weather", "weather": payload})
            await asyncio.sleep(self._forecast_interval)
