import json
import os
import stat

import pytest
from pydantic import ValidationError

from wallboard.config import Config, ConfigStore, GoogleAccount, Tile


def test_defaults_created(data_dir):
    store = ConfigStore(data_dir)
    assert store.config.language == "cs"
    assert [p.type for p in store.config.pages] == ["today", "week", "month", "home"]
    assert len(store.secrets.session_secret) >= 32
    assert (data_dir / "config.json").exists()
    assert (data_dir / "secrets.json").exists()


def test_secrets_file_mode(data_dir):
    ConfigStore(data_dir)
    mode = os.stat(data_dir / "secrets.json").st_mode & 0o777
    assert oct(mode) == "0o600"


def test_atomic_save_roundtrip(data_dir):
    store = ConfigStore(data_dir)
    cfg = store.config.model_copy(update={"language": "en"})
    store.save_config(cfg)
    assert ConfigStore(data_dir).config.language == "en"
    assert list(data_dir.glob("*.tmp")) == []


def test_secrets_roundtrip_keeps_mode_and_session(data_dir):
    store = ConfigStore(data_dir)
    s = store.secrets.model_copy(update={"ha_token": "abc"})
    store.save_secrets(s)
    again = ConfigStore(data_dir)
    assert again.secrets.ha_token == "abc"
    assert again.secrets.session_secret == s.session_secret
    assert stat.S_IMODE(os.stat(data_dir / "secrets.json").st_mode) == 0o600
    assert list(data_dir.glob("*.tmp")) == []


def test_public_config_has_no_secrets(data_dir):
    store = ConfigStore(data_dir)
    store.save_secrets(
        store.secrets.model_copy(
            update={
                "admin_password_hash": "S3CRET",
                "session_secret": "S3CRET" * 8,
                "ha_token": "S3CRET",
                "google_client_id": "S3CRET",
                "google_client_secret": "S3CRET",
                "google_refresh_tokens": {"a1": "S3CRET"},
            }
        )
    )
    store.save_config(
        store.config.model_copy(
            update={"google_accounts": [GoogleAccount(id="a1", email="x@example.com", member_id="m")]}
        )
    )
    pub = store.public_config()
    assert "S3CRET" not in json.dumps(pub)
    assert "x@example.com" not in json.dumps(pub)
    assert pub["google_accounts"] == [{"id": "a1", "member_id": "m"}]


def test_quick_tiles_must_be_tiles():
    with pytest.raises(ValidationError):
        Config(quick_tiles=["light.x"], tiles=[])
    tiles = [Tile(entity_id=f"light.l{i}", group="lights") for i in range(5)]
    with pytest.raises(ValidationError):
        Config(tiles=tiles, quick_tiles=[t.entity_id for t in tiles])
    ok = Config(tiles=tiles, quick_tiles=["light.l0"])
    assert ok.quick_tiles == ["light.l0"]


def test_timezone_must_be_valid():
    assert Config(timezone="America/New_York").timezone == "America/New_York"
    for bad in ("Europe/Praha", "", "../etc/passwd"):
        with pytest.raises(ValidationError):
            Config(timezone=bad)


@pytest.mark.parametrize("field", ["rotation_seconds", "touch_pause_seconds"])
def test_rotation_and_pause_at_least_5(field):
    assert getattr(Config(**{field: 5}), field) == 5
    with pytest.raises(ValidationError):
        Config(**{field: 4})
