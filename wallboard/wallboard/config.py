"""Config and secrets models plus the on-disk store (atomic JSON, secrets 0600)."""

from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path
from typing import Any, Literal

from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator


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


class PageRef(BaseModel):
    type: str
    enabled: bool = True
    seconds: int | None = None
    options: dict[str, Any] = {}


def _default_pages() -> list[PageRef]:
    return [PageRef(type=t) for t in ("today", "week", "month", "home")]


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
    quick_tiles: list[str] = []  # <= 4 entity_ids, each must be in tiles
    pages: list[PageRef] = _default_pages()
    photos_shuffle: bool = False

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ValueError, KeyError, OSError):  # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"unknown timezone: {v!r}") from None
        return v

    @model_validator(mode="after")
    def _check_quick_tiles(self) -> Config:
        if len(self.quick_tiles) > 4:
            raise ValueError("quick_tiles may hold at most 4 entries")
        known = {t.entity_id for t in self.tiles}
        missing = [e for e in self.quick_tiles if e not in known]
        if missing:
            raise ValueError(f"quick_tiles not present in tiles: {missing}")
        return self


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


class ConfigStore:
    """On-disk store. `config` and `secrets` re-read their file when it changed on disk
    (e.g. the set-password CLI ran while the server is up)."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = self.data_dir / "config.json"
        self._secrets_path = self.data_dir / "secrets.json"
        self._config: Config | None = None
        self._secrets: Secrets | None = None
        self._config_sig: tuple[int, int] | None = None
        self._secrets_sig: tuple[int, int] | None = None

        if self._config_path.exists():
            self._reload_config()
        else:
            self.save_config(Config())

        if self._secrets_path.exists():
            self._reload_secrets()
        else:
            self.save_secrets(Secrets(session_secret=_secrets.token_urlsafe(32)))

    @staticmethod
    def _sig(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _reload_config(self) -> None:
        sig = self._sig(self._config_path)
        self._config = Config.model_validate_json(self._config_path.read_text(encoding="utf-8"))
        self._config_sig = sig

    def _reload_secrets(self) -> None:
        sig = self._sig(self._secrets_path)
        self._secrets = Secrets.model_validate_json(self._secrets_path.read_text(encoding="utf-8"))
        self._secrets_sig = sig

    @property
    def config(self) -> Config:
        if self._sig(self._config_path) != self._config_sig:
            self._reload_config()
        assert self._config is not None
        return self._config

    @property
    def secrets(self) -> Secrets:
        if self._sig(self._secrets_path) != self._secrets_sig:
            self._reload_secrets()
        assert self._secrets is not None
        return self._secrets

    def save_config(self, cfg: Config) -> None:
        # Re-validate so callers using model_copy(update=...) can't persist invalid state.
        cfg = Config.model_validate(cfg.model_dump())
        _atomic_write(self._config_path, cfg.model_dump_json(indent=2), 0o644)
        self._config = cfg
        self._config_sig = self._sig(self._config_path)

    def save_secrets(self, s: Secrets) -> None:
        s = Secrets.model_validate(s.model_dump())
        _atomic_write(self._secrets_path, s.model_dump_json(indent=2), 0o600)
        self._secrets = s
        self._secrets_sig = self._sig(self._secrets_path)

    def public_config(self) -> dict[str, Any]:
        """Config as a JSON-able dict, safe to send to the display (no emails, no secrets)."""
        data = self.config.model_dump(mode="json")
        for acct in data["google_accounts"]:
            acct.pop("email", None)
        return data
