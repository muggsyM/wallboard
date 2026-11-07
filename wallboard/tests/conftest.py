from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_dev_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's exported WALLBOARD_DEV_CALENDAR must not change the tests."""
    monkeypatch.delenv("WALLBOARD_DEV_CALENDAR", raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(scope="session")
def heic_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    from PIL import Image

    import pillow_heif

    pillow_heif.register_heif_opener()
    path = tmp_path_factory.mktemp("fixtures") / "sample.heic"
    try:
        Image.new("RGB", (64, 48), (200, 80, 40)).save(path, format="HEIF")
    except Exception as exc:  # encoder missing in this pillow-heif build
        pytest.skip(f"HEIF encoding unavailable in this pillow-heif build: {exc}")
    return path.read_bytes()
