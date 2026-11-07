"""Server-side allowlist for display taps: only controllable tiles, fixed per-domain service map."""

from __future__ import annotations

import logging

from .config import Config
from .ha_client import HAError

log = logging.getLogger(__name__)

TOGGLE_DOMAINS = frozenset({"light", "switch", "fan", "input_boolean"})
CONTROLLABLE_DOMAINS = TOGGLE_DOMAINS | {"vacuum", "scene", "script", "cover"}


class ActionNotAllowed(Exception):
    pass


def resolve_action(config: Config, entity_id: str, current_state: str | None) -> tuple[str, str]:
    """Return (domain, service) for a tap on entity_id, or raise ActionNotAllowed."""
    domain, dot, name = entity_id.partition(".")
    if not dot or not domain or not name:
        raise ActionNotAllowed("malformed entity id")
    if not any(t.entity_id == entity_id and t.controllable for t in config.tiles):
        raise ActionNotAllowed("entity is not a controllable tile")
    if domain in TOGGLE_DOMAINS:
        return domain, "toggle"
    if domain == "vacuum":
        return domain, "return_to_base" if current_state in {"cleaning", "returning"} else "start"
    if domain in {"scene", "script"}:
        return domain, "turn_on"
    if domain == "cover":
        return domain, "close_cover" if current_state in {"open", "opening"} else "open_cover"
    raise ActionNotAllowed("unsupported domain")


class ActionHandler:
    def __init__(self, store, hub, ha) -> None:
        self._store = store
        self._hub = hub
        self._ha = ha

    async def handle(self, entity_id: str, action_id: str) -> dict:
        """Run a tap action; always returns an action_result message and never raises."""
        error: str | None = None
        try:
            entry = self._hub.snapshot().get("ha", {}).get(entity_id)
            state = entry.get("state") if isinstance(entry, dict) else None
            domain, service = resolve_action(self._store.config, entity_id, state)
            await self._ha.call_service(domain, service, entity_id)
        except ActionNotAllowed:
            error = "not allowed"
        except HAError as exc:
            error = str(exc)
        except Exception as exc:
            log.warning("action failed: %s", type(exc).__name__)
            error = "action failed"
        return {"type": "action_result", "id": action_id, "ok": error is None, "error": error}
