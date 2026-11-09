"""Tasks page actions from the display, over the display socket:

  entity_id "todo.<id>", command "toggle"   tick / untick (a tap on the item)
  entity_id "todo.<id>", command "delete"   remove it (long press -> Smazat)
  entity_id "todo.new",  command "add", data {member_id, text, due}   the on-screen keyboard

The todos live in config.json (also edited on the Settings page); each action changes the
config, saves it and publishes it to every display like a Settings save."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .config import Config, ConfigStore, Todo

TODO_PREFIX = "todo."
TODO_KEEP_DAYS = 30  # ticked items are dropped this many days after they were ticked
TODO_MAX = 300  # Config.todos max_length
NEW_ID = "new"


class TodoError(ValueError):
    """A display todo action that cannot be applied; the message goes back as the error."""


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


def added(cfg: Config, data: object) -> Config:
    """The config with a new open todo from the display's add dialog. Raises TodoError."""
    if not isinstance(data, dict):
        raise TodoError("bad data")
    member_id, text, due = data.get("member_id"), data.get("text"), data.get("due")
    if not any(m.id == member_id for m in cfg.members):
        raise TodoError("unknown member")
    text = " ".join(str(text or "").split())  # one line, single spaces
    if not text:
        raise TodoError("empty text")
    if len(cfg.todos) >= TODO_MAX:
        raise TodoError("too many todos")
    try:
        due_date = date.fromisoformat(due) if due else None
        todo = Todo(id=f"t{uuid.uuid4().hex[:12]}", member_id=member_id, text=text, due=due_date)
    except (TypeError, ValueError, ValidationError):
        raise TodoError("invalid todo") from None
    return cfg.model_copy(update={"todos": [*cfg.todos, todo]})


def deleted(cfg: Config, todo_id: str) -> Config | None:
    """The config without that todo; None when there is no such todo."""
    if not any(t.id == todo_id for t in cfg.todos):
        return None
    return cfg.model_copy(update={"todos": [t for t in cfg.todos if t.id != todo_id]})


async def handle_todo_action(
    store: ConfigStore, hub, entity_id: str, action_id: str, command: str | None, data: object = None
) -> dict:
    """Apply a display todo action; returns the action_result message."""
    fail = lambda error: {"type": "action_result", "id": action_id, "ok": False, "error": error}  # noqa: E731
    todo_id = entity_id[len(TODO_PREFIX):]
    cfg = store.config  # no await until save_config
    if todo_id == NEW_ID and command == "add":
        try:
            new = added(cfg, data)
        except TodoError as exc:
            return fail(str(exc))
    elif command == "toggle":
        new = toggled(cfg, todo_id)
    elif command == "delete":
        new = deleted(cfg, todo_id)
    else:
        return fail("unsupported command")
    if new is None:
        return fail("unknown todo")
    store.save_config(new)
    await hub.publish({"type": "config", "config": store.public_config()})
    return {"type": "action_result", "id": action_id, "ok": True}
