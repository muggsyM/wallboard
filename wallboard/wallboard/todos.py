"""Tasks page: tick/untick a todo from the display (a tap sends action entity_id "todo.<id>",
command "toggle"). The todos live in config.json (edited on the Settings page); a tap changes
the config, saves it and publishes it to every display like a Settings save."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config, ConfigStore

TODO_PREFIX = "todo."
TODO_KEEP_DAYS = 30  # ticked items are dropped this many days after they were ticked


def is_todo_action(entity_id: str) -> bool:
    return entity_id.startswith(TODO_PREFIX)


def toggled(cfg: Config, todo_id: str, now: datetime | None = None) -> Config | None:
    """The config with that todo ticked (done = today in cfg.timezone) or unticked, and ticked
    items older than TODO_KEEP_DAYS pruned; None when there is no such todo."""
    today = (now or datetime.now(ZoneInfo(cfg.timezone))).astimezone(ZoneInfo(cfg.timezone)).date()
    if not any(t.id == todo_id for t in cfg.todos):
        return None
    oldest = today - timedelta(days=TODO_KEEP_DAYS)
    todos = []
    for t in cfg.todos:
        if t.id == todo_id:
            t = t.model_copy(update={"done": None if t.done else today})
        if t.done is None or t.done >= oldest:
            todos.append(t)
    return cfg.model_copy(update={"todos": todos})


async def handle_todo_action(store: ConfigStore, hub, entity_id: str, action_id: str, command: str | None) -> dict:
    """Apply a display tap on a todo; returns the action_result message."""
    fail = lambda error: {"type": "action_result", "id": action_id, "ok": False, "error": error}  # noqa: E731
    if command != "toggle":
        return fail("unsupported command")
    new = toggled(store.config, entity_id[len(TODO_PREFIX):])  # no await until save_config
    if new is None:
        return fail("unknown todo")
    store.save_config(new)
    await hub.publish({"type": "config", "config": store.public_config()})
    return {"type": "action_result", "id": action_id, "ok": True}
