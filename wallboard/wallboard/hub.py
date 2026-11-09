"""Hub: latest state per topic plus broadcast to connected display sockets."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from typing import Any

TOPICS = ("config", "ha", "calendar", "weather", "photos", "weather_pics", "status")
SEND_TIMEOUT = 5  # seconds; a socket that can't take a message in time is dropped


class Hub:
    def __init__(self) -> None:
        self._sockets: set[Any] = set()
        self._closing: set[asyncio.Task] = set()
        self._topics: dict[str, Any] = {
            "config": None,
            "ha": {},
            "calendar": None,
            "weather": None,
            "photos": [],
            "weather_pics": [],
            "status": {"ha": False, "google": {}},
        }
        # Server build id (hash of web/); the display reloads when it changes.
        self.build: str | None = None
        # (entity_id, action_id) -> action_result message
        # on_action(entity_id, action_id[, command]): a tile tap, or a vacuum-card button.
        self.on_action: Callable[..., Awaitable[dict]] | None = None

    def snapshot(self) -> dict:
        return {"type": "snapshot", "build": self.build, **copy.deepcopy(self._topics)}

    def set_topic(self, name: str, value: Any) -> None:
        """Replace a topic's stored value without broadcasting."""
        if name not in self._topics:
            raise ValueError(f"unknown topic: {name}")
        self._topics[name] = value

    async def connect(self, ws, after: list[dict] | None = None) -> None:
        """Register an already-accepted socket and send it the current snapshot, then the
        `after` messages (e.g. the connection's viewer role)."""
        self._sockets.add(ws)
        try:
            await ws.send_json(self.snapshot())
            for msg in after or ():
                await ws.send_json(msg)
        except Exception:
            self._sockets.discard(ws)
            raise

    def disconnect(self, ws) -> None:
        self._sockets.discard(ws)

    async def publish(self, msg: dict) -> None:
        """Update the snapshot from a known message type, then broadcast it."""
        kind = msg.get("type")
        if kind == "ha":
            if msg.get("state") is None:
                self._topics["ha"].pop(msg["entity_id"], None)
            else:
                self._topics["ha"][msg["entity_id"]] = msg["state"]
        elif kind in TOPICS:
            self.set_topic(kind, msg[kind])
        else:
            raise ValueError(f"unknown message type: {kind}")
        await self._broadcast(msg)

    async def set_status(self, key: str, value: Any) -> None:
        """Merge one producer's status (e.g. "ha", "google") and publish the full status dict."""
        self._topics["status"][key] = value
        await self._broadcast({"type": "status", "status": copy.deepcopy(self._topics["status"])})

    async def _broadcast(self, msg: dict) -> None:
        sockets = list(self._sockets)
        # A stuck peer (send buffer full, half-open TCP) must not stall every later broadcast.
        results = await asyncio.gather(
            *(asyncio.wait_for(ws.send_json(msg), SEND_TIMEOUT) for ws in sockets), return_exceptions=True
        )
        for ws, result in zip(sockets, results):
            if isinstance(result, BaseException):
                self._sockets.discard(ws)
                if isinstance(result, asyncio.TimeoutError):
                    # Close it too (best effort, in the background), so a slow but live display
                    # reconnects and gets a fresh snapshot instead of silently going stale.
                    task = asyncio.create_task(self._close_quietly(ws))
                    self._closing.add(task)
                    task.add_done_callback(self._closing.discard)

    @staticmethod
    async def _close_quietly(ws) -> None:
        close = getattr(ws, "close", None)
        if close is None:
            return
        try:
            await asyncio.wait_for(close(), SEND_TIMEOUT)
        except Exception:
            pass
