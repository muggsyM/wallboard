"""Hub: latest state per topic plus broadcast to connected display sockets."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from typing import Any

TOPICS = ("config", "ha", "calendar", "weather", "photos", "status")


class Hub:
    def __init__(self) -> None:
        self._sockets: set[Any] = set()
        self._topics: dict[str, Any] = {
            "config": None,
            "ha": {},
            "calendar": None,
            "weather": None,
            "photos": [],
            "status": {"ha": False, "google": {}},
        }
        # (entity_id, action_id) -> action_result message
        self.on_action: Callable[[str, str], Awaitable[dict]] | None = None

    def snapshot(self) -> dict:
        return {"type": "snapshot", **copy.deepcopy(self._topics)}

    def set_topic(self, name: str, value: Any) -> None:
        """Replace a topic's stored value without broadcasting."""
        if name not in self._topics:
            raise ValueError(f"unknown topic: {name}")
        self._topics[name] = value

    async def connect(self, ws) -> None:
        """Register an already-accepted socket and send it the current snapshot."""
        self._sockets.add(ws)
        try:
            await ws.send_json(self.snapshot())
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
        results = await asyncio.gather(*(ws.send_json(msg) for ws in sockets), return_exceptions=True)
        for ws, result in zip(sockets, results):
            if isinstance(result, BaseException):
                self._sockets.discard(ws)
