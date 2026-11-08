import json
import os
import stat

import pytest
from pydantic import ValidationError

from wallboard.config import Config, ConfigStore, GoogleAccount, Tile


def test_defaults_created(data_dir):
    store = ConfigStore(data_dir)
    assert store.config.language == "cs"
    assert [p.type for p in store.config.pages] == ["today", "week", "month", "home", "photos"]
    # Only the Photos slideshow has its own (longer) time; the others use rotation_seconds.
    assert [p.seconds for p in store.config.pages] == [None, None, None, None, 60]
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


def test_rotation_at_least_5():
    assert Config(rotation_seconds=5).rotation_seconds == 5
    with pytest.raises(ValidationError):
        Config(rotation_seconds=4)


def test_touch_pause_at_least_15():
    # Worst-case tile pending: 5 s action timeout + 5 s safety timer (ruling R19).
    assert Config(touch_pause_seconds=15).touch_pause_seconds == 15
    with pytest.raises(ValidationError):
        Config(touch_pause_seconds=14)


# ---- an invalid or unreadable file never stops the server (final review, item 1) ----


def _aside(data_dir, name):
    return sorted(data_dir.glob(f"{name}.invalid-*"))


def test_corrupt_config_at_startup_moved_aside(data_dir, caplog):
    (data_dir / "config.json").write_text("{not json", encoding="utf-8")
    store = ConfigStore(data_dir)
    assert store.config == Config()
    [aside] = _aside(data_dir, "config.json")
    assert aside.read_text(encoding="utf-8") == "{not json"
    assert Config.model_validate_json((data_dir / "config.json").read_text(encoding="utf-8")) == Config()
    assert any(r.levelname == "ERROR" and "config.json" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("bad", [{"timezone": "Mars/Olympus"}, {"rotation_seconds": 1}])
def test_schema_invalid_config_at_startup_moved_aside(data_dir, bad):
    (data_dir / "config.json").write_text(json.dumps(bad), encoding="utf-8")
    store = ConfigStore(data_dir)
    assert store.config == Config()
    assert len(_aside(data_dir, "config.json")) == 1
    # a later save works normally
    store.save_config(store.config.model_copy(update={"language": "en"}))
    assert ConfigStore(data_dir).config.language == "en"


def test_corrupt_secrets_at_startup_moved_aside(data_dir, caplog):
    (data_dir / "secrets.json").write_text("\x00garbage", encoding="utf-8")
    store = ConfigStore(data_dir)
    assert len(store.secrets.session_secret) >= 32
    assert store.secrets.admin_password_hash is None
    assert len(_aside(data_dir, "secrets.json")) == 1
    assert stat.S_IMODE(os.stat(data_dir / "secrets.json").st_mode) == 0o600
    assert ConfigStore(data_dir).secrets.session_secret == store.secrets.session_secret
    assert any(r.levelname == "ERROR" and "set-password" in r.getMessage() for r in caplog.records)


def test_short_session_secret_at_startup_regenerated(data_dir, caplog):
    bad = {"session_secret": "S3CRETshort", "ha_token": ["S3CRETtoken"]}
    (data_dir / "secrets.json").write_text(json.dumps(bad), encoding="utf-8")
    store = ConfigStore(data_dir)
    assert len(store.secrets.session_secret) >= 32
    assert len(_aside(data_dir, "secrets.json")) == 1
    assert "S3CRET" not in caplog.text  # never log secret values
    # runtime: same rule
    (data_dir / "secrets.json").write_text(json.dumps(bad), encoding="utf-8")
    caplog.clear()
    store.secrets
    assert "secrets.json" in caplog.text and "S3CRET" not in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
@pytest.mark.parametrize("name", ["config.json", "secrets.json"])
def test_unreadable_file_runs_read_only(data_dir, name, caplog):
    ConfigStore(data_dir).save_config(Config(language="en"))
    path = data_dir / name
    os.chmod(path, 0)
    try:
        store = ConfigStore(data_dir)
        assert store.config is not None and len(store.secrets.session_secret) >= 32
        assert _aside(data_dir, name) == []  # never moved
        with pytest.raises(OSError):
            if name == "config.json":
                store.save_config(Config())
            else:
                store.save_secrets(store.secrets)
        assert any(r.levelname == "ERROR" and name in r.getMessage() for r in caplog.records)
    finally:
        os.chmod(path, 0o600)
    if name == "config.json":
        assert ConfigStore(data_dir).config.language == "en"  # untouched


def test_runtime_corruption_keeps_last_good_then_recovers(data_dir, caplog):
    store = ConfigStore(data_dir)
    store.save_config(Config(language="en"))
    path = data_dir / "config.json"
    path.write_text('{"language": "en", "rotation', encoding="utf-8")  # half-written by an editor
    assert store.config.language == "en"
    assert store.config.language == "en"
    errors = [r for r in caplog.records if r.levelname == "ERROR" and "config.json" in r.getMessage()]
    assert len(errors) == 1  # logged once, not re-parsed on every access
    assert _aside(data_dir, "config.json") == []  # never moved at runtime
    path.write_text(json.dumps({"language": "cs", "rotation_seconds": 60}), encoding="utf-8")
    assert store.config.rotation_seconds == 60


def test_runtime_corrupt_secrets_keep_last_good(data_dir):
    store = ConfigStore(data_dir)
    secret = store.secrets.session_secret
    (data_dir / "secrets.json").write_text("{", encoding="utf-8")
    assert store.secrets.session_secret == secret
    assert _aside(data_dir, "secrets.json") == []
