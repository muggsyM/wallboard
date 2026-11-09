"""Config and secrets models plus the on-disk store (atomic JSON, secrets 0600)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets as _secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

log = logging.getLogger(__name__)


class NightMode(BaseModel):
    enabled: bool = False
    start: str = "23:00"
    end: str = "06:00"


class Member(BaseModel):
    id: str
    name: str
    initial: str
    color: str  # "#4f8cff"
    person_entity: str | None = None


class GoogleAccount(BaseModel):
    id: str
    email: str
    member_id: str


class CalendarRef(BaseModel):
    account_id: str
    calendar_id: str
    name: str
    member_id: str
    enabled: bool = True


class Room(BaseModel):
    name: str
    temperature_entity: str
    humidity_entity: str | None = None
    light_entities: list[str] = []


class Tile(BaseModel):
    entity_id: str
    label: str | None = None
    icon: str | None = None
    group: Literal["lights", "devices"]
    controllable: bool = False


class VacuumRoom(BaseModel):
    """One room button on the Home page's vacuum card: cleans these map segments."""

    name: str = Field(min_length=1, max_length=40)
    icon: str | None = None  # mdi icon name, e.g. "mdi-silverware-fork-knife"
    segments: list[int] = Field(min_length=1, max_length=10)


class Vacuum(BaseModel):
    """The Home page's vacuum card (Roborock-style segment cleaning via vacuum.send_command)."""

    entity_id: str = Field(pattern=r"^vacuum\.[a-z0-9_]+$")
    battery_entity: str | None = None
    rooms: list[VacuumRoom] = Field([], max_length=6)


class Todo(BaseModel):
    """One item of a member's task list (Tasks page). `done` is the local date it was ticked:
    the display shows it struck through for the rest of that day, then hides it; ticked items
    are pruned after TODO_KEEP_DAYS (todos.py)."""

    id: str = Field(min_length=1, max_length=40)
    member_id: str
    text: str = Field(min_length=1, max_length=200)
    due: date | None = None
    done: date | None = None


class PageRef(BaseModel):
    type: str
    enabled: bool = True
    seconds: int | None = None
    options: dict[str, Any] = {}


def _default_pages() -> list[PageRef]:
    # The Photos slideshow stays longer than the global rotation_seconds by default.
    return [PageRef(type=t) for t in ("today", "week", "month", "home")] + [PageRef(type="photos", seconds=60)]


class Config(BaseModel):
    language: Literal["cs", "en"] = "cs"
    timezone: str = "Europe/Prague"
    # A tap pauses rotation for touch_pause_seconds; >= 15 s so a pending tile (5 s action
    # timeout + 5 s safety timer) is never paged away (ruling R19).
    rotation_seconds: int = Field(30, ge=5)
    touch_pause_seconds: int = Field(120, ge=15)
    night: NightMode = NightMode()
    members: list[Member] = []
    google_accounts: list[GoogleAccount] = []
    calendars: list[CalendarRef] = []
    ha_url: str | None = None
    weather_entity: str | None = None
    rooms: list[Room] = []
    tiles: list[Tile] = []
    status_entities: list[str] = []
    vacuum: Vacuum | None = None
    quick_tiles: list[str] = []  # <= 4 entity_ids, each must be in tiles
    todos: list[Todo] = Field([], max_length=300)
    pages: list[PageRef] = _default_pages()
    photos_shuffle: bool = False
    # Show the board (view only) to non-logged-in devices on the LAN; see auth.public_view_enabled.
    public_view: bool = False

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ValueError, KeyError, OSError):  # ZoneInfoNotFoundError is a KeyError
            raise PydanticCustomError("unknown_timezone", "unknown timezone: {tz}", {"tz": v}) from None
        return v

    # A field validator (not a model one) so a validation error carries loc ["quick_tiles"],
    # which the settings page shows next to the field. `tiles` is declared before
    # `quick_tiles`, so it is in info.data once it validated.
    @field_validator("quick_tiles")
    @classmethod
    def _check_quick_tiles(cls, v: list[str], info: ValidationInfo) -> list[str]:
        if len(v) > 4:
            raise PydanticCustomError("too_many_quick_tiles", "quick_tiles may hold at most {max} entries", {"max": 4})
        if "tiles" in info.data:
            known = {t.entity_id for t in info.data["tiles"]}
            missing = [e for e in v if e not in known]
            if missing:
                raise PydanticCustomError(
                    "quick_tile_not_in_tiles", "quick_tiles not present in tiles: {missing}", {"missing": ", ".join(missing)}
                )
        return v


def config_revision(cfg: Config) -> str:
    """Short stable hash of a config: the settings page sends it back on save (If-Match) so a
    save from an out-of-date page is refused instead of overwriting newer settings."""
    canonical = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class Secrets(BaseModel):
    admin_password_hash: str | None = None
    session_secret: str = Field(min_length=32)
    ha_token: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_refresh_tokens: dict[str, str] = {}  # account_id -> token


def _atomic_write(path: Path, text: str, mode: int) -> None:
    """Write `text` to `path` via `<name>.tmp` + os.replace; file created with `mode`."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _short_error(e: Exception) -> str:
    """A log-safe description: field locations and messages only, never input values (secrets)."""
    if isinstance(e, ValidationError):
        parts = []
        for err in e.errors(include_input=False, include_url=False, include_context=False)[:5]:
            loc = ".".join(str(p) for p in err.get("loc", ())) or "file"
            parts.append(f"{loc}: {err.get('msg', err.get('type'))}")
        return "; ".join(parts)
    return type(e).__name__


def _new_secrets() -> Secrets:
    return Secrets(session_secret=_secrets.token_urlsafe(32))


class ConfigStore:
    """On-disk store. `config` and `secrets` re-read their file when it changed on disk
    (e.g. the set-password CLI ran while the server is up).

    A bad file never stops the server (the wall must not go dark):
    - at startup, a corrupt or schema-invalid file is moved to `<name>.invalid-<UTC ts>` and
      replaced with defaults; an unreadable one (e.g. permission denied) is left alone and
      the store runs on in-memory defaults and refuses to write that file;
    - at runtime, a file that fails to parse (an editor writing in place) is logged once and
      the last good model is kept until the next valid write.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = self.data_dir / "config.json"
        self._secrets_path = self.data_dir / "secrets.json"
        self._config: Config | None = None
        self._secrets: Secrets | None = None
        self._config_sig: tuple[int, int] | None = None
        self._secrets_sig: tuple[int, int] | None = None
        self._config_read_only = False
        self._secrets_read_only = False

        self._config, self._config_read_only = self._startup_load(
            self._config_path, Config, Config, self.save_config, "config.json is invalid; started with default settings"
        )
        self._secrets, self._secrets_read_only = self._startup_load(
            self._secrets_path,
            Secrets,
            _new_secrets,
            self.save_secrets,
            "secrets.json is invalid; started with a new session secret and NO admin password, Home "
            "Assistant token or Google sign-ins. Run `python -m wallboard set-password --data DIR` to "
            "set the admin password, then enter the rest again in Settings",
        )

    def _startup_load(self, path: Path, model: type, default, save, invalid_msg: str):
        """Load `path` at startup; returns (model instance, read_only). Never raises for a bad file."""
        if not path.exists():
            obj = default()
            save(obj)
            return obj, False
        sig = self._sig(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = None  # not text at all: treat as corrupt
        except OSError as e:
            log.error(
                "%s is unreadable (%s); running on in-memory defaults and refusing to write it until "
                "it is fixed and the service restarted",
                path,
                type(e).__name__,
            )
            return default(), True
        try:
            if text is None:
                raise ValueError("not UTF-8 text")
            obj = model.model_validate_json(text)
        except ValueError as e:  # JSON or validation error (pydantic's ValidationError is a ValueError)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            aside = path.with_name(f"{path.name}.invalid-{stamp}")
            try:
                os.replace(path, aside)
            except OSError as move_err:
                log.error(
                    "%s is invalid (%s) and could not be moved aside (%s); running on in-memory "
                    "defaults and refusing to write it",
                    path,
                    _short_error(e),
                    type(move_err).__name__,
                )
                return default(), True
            log.error("%s (%s); the bad file was moved to %s", invalid_msg, _short_error(e), aside.name)
            obj = default()
            save(obj)
            return obj, False
        if path == self._config_path:
            self._config_sig = sig
        else:
            self._secrets_sig = sig
        return obj, False

    @staticmethod
    def _sig(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _reload(self, path: Path, model: type, current):
        """Runtime re-read after the file changed. On failure keep `current` and log once
        (the caller records the new signature, so a bad file is not re-parsed on every access)."""
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.error("%s changed but could not be loaded (%s); keeping the last good version", path, _short_error(e))
            return current

    @property
    def config(self) -> Config:
        if not self._config_read_only:
            sig = self._sig(self._config_path)
            if sig != self._config_sig:
                self._config_sig = sig
                self._config = self._reload(self._config_path, Config, self._config)
        assert self._config is not None
        return self._config

    @property
    def secrets(self) -> Secrets:
        if not self._secrets_read_only:
            sig = self._sig(self._secrets_path)
            if sig != self._secrets_sig:
                self._secrets_sig = sig
                self._secrets = self._reload(self._secrets_path, Secrets, self._secrets)
        assert self._secrets is not None
        return self._secrets

    def save_config(self, cfg: Config) -> None:
        # Re-validate so callers using model_copy(update=...) can't persist invalid state.
        cfg = Config.model_validate(cfg.model_dump())
        if self._config_read_only:
            log.error("refusing to write %s: it was unreadable at startup", self._config_path)
            raise OSError(f"{self._config_path.name} is unreadable; refusing to overwrite it")
        _atomic_write(self._config_path, cfg.model_dump_json(indent=2), 0o644)
        self._config = cfg
        self._config_sig = self._sig(self._config_path)

    def save_secrets(self, s: Secrets) -> None:
        s = Secrets.model_validate(s.model_dump())
        if self._secrets_read_only:
            log.error("refusing to write %s: it was unreadable at startup", self._secrets_path)
            raise OSError(f"{self._secrets_path.name} is unreadable; refusing to overwrite it")
        _atomic_write(self._secrets_path, s.model_dump_json(indent=2), 0o600)
        self._secrets = s
        self._secrets_sig = self._sig(self._secrets_path)

    def public_config(self) -> dict[str, Any]:
        """Config as a JSON-able dict, safe to send to the display (no emails, no secrets)."""
        data = self.config.model_dump(mode="json")
        for acct in data["google_accounts"]:
            acct.pop("email", None)
        return data
