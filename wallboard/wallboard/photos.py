"""Photo store: albums, ordering and the three JPEG files per photo, persisted in photos.json."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from .config import _atomic_write
from .hub import Hub
from .imaging import process_image

SUFFIXES = (".jpg", ".bg.jpg", ".thumb.jpg")
ZIP_SPOOL_BYTES = 10 * 1024 * 1024


@dataclass
class Album:
    id: str
    name: str
    hidden: bool = False
    photo_ids: list[str] = field(default_factory=list)


def _safe_dir_name(name: str, fallback: str) -> str:
    """Album name usable as a zip folder: no separators, '..' or control characters."""
    cleaned = "".join(ch for ch in name if ch >= " " and ch != "\x7f" and ch not in "/\\")
    cleaned = cleaned.replace("..", "").strip().strip(".")
    return cleaned or fallback


class PhotoStore:
    def __init__(self, data_dir: Path, hub: Hub) -> None:
        self._dir = Path(data_dir) / "photos"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._json = Path(data_dir) / "photos.json"
        self._hub = hub
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._albums: list[Album] = []
        self._landscape: dict[str, bool] = {}
        self._load()

    # persistence

    def _load(self) -> None:
        try:
            raw = json.loads(self._json.read_text(encoding="utf-8"))
            self._albums = [
                Album(a["id"], a["name"], bool(a.get("hidden", False)), list(a.get("photo_ids", [])))
                for a in raw.get("albums", [])
            ]
            self._landscape = {k: bool(v) for k, v in raw.get("landscape", {}).items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._albums, self._landscape = [], {}

    def _save(self) -> None:
        doc = {
            "albums": [
                {"id": a.id, "name": a.name, "hidden": a.hidden, "photo_ids": a.photo_ids} for a in self._albums
            ],
            "landscape": self._landscape,
        }
        _atomic_write(self._json, json.dumps(doc, indent=2), 0o644)

    def _commit(self) -> None:
        self._save()
        self.publish()

    def publish(self) -> None:
        """Update the hub's photos topic and broadcast it (the broadcast needs a running event loop)."""
        photos = self.display_items()
        self._hub.set_topic("photos", photos)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._hub.publish({"type": "photos", "photos": photos}))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start(self) -> None:
        self._hub.set_topic("photos", self.display_items())

    # lookups

    def path(self, name: str) -> Path:
        return self._dir / name

    def _album(self, album_id: str) -> Album:
        for a in self._albums:
            if a.id == album_id:
                return a
        raise KeyError(album_id)

    def _find_photo(self, photo_id: str) -> Album:
        for a in self._albums:
            if photo_id in a.photo_ids:
                return a
        raise KeyError(photo_id)

    def albums(self) -> list[Album]:
        return [Album(a.id, a.name, a.hidden, list(a.photo_ids)) for a in self._albums]

    # album mutations

    def create_album(self, name: str) -> Album:
        album = Album(uuid.uuid4().hex, name)
        self._albums.append(album)
        self._commit()
        return Album(album.id, album.name, album.hidden, [])

    def rename_album(self, album_id: str, name: str) -> None:
        self._album(album_id).name = name
        self._commit()

    def set_hidden(self, album_id: str, hidden: bool) -> None:
        self._album(album_id).hidden = hidden
        self._commit()

    def reorder_albums(self, ids: list[str]) -> None:
        by_id = {a.id: a for a in self._albums}
        if len(ids) != len(by_id) or set(ids) != set(by_id):
            raise ValueError("ids must be a permutation of the current albums")
        self._albums = [by_id[i] for i in ids]
        self._commit()

    def reorder_photos(self, album_id: str, ids: list[str]) -> None:
        album = self._album(album_id)
        if len(ids) != len(album.photo_ids) or set(ids) != set(album.photo_ids):
            raise ValueError("ids must be a permutation of the album's photos")
        album.photo_ids = list(ids)
        self._commit()

    def move_photo(self, photo_id: str, album_id: str, index: int) -> None:
        src = self._find_photo(photo_id)
        dst = self._album(album_id)
        src.photo_ids.remove(photo_id)
        dst.photo_ids.insert(max(0, min(index, len(dst.photo_ids))), photo_id)
        self._commit()

    # photos

    def _write_files(self, photo_id: str, blobs: list[bytes]) -> None:
        names = [photo_id + s for s in SUFFIXES]
        try:
            for name, blob in zip(names, blobs):
                fd = os.open(self._dir / (name + ".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                with os.fdopen(fd, "wb") as f:
                    f.write(blob)
                    f.flush()
                    os.fsync(f.fileno())
            for name in names:
                os.replace(self._dir / (name + ".tmp"), self._dir / name)
        except BaseException:
            self._remove_files(photo_id)
            raise

    def _remove_files(self, photo_id: str) -> None:
        for s in SUFFIXES:
            (self._dir / (photo_id + s)).unlink(missing_ok=True)
            (self._dir / (photo_id + s + ".tmp")).unlink(missing_ok=True)

    async def ingest(self, data: bytes, album_id: str) -> str:
        """Process one upload and append it to the album. Raises KeyError, UnsupportedImage."""
        async with self._lock:
            album = self._album(album_id)
            processed = await run_in_threadpool(process_image, data)
            photo_id = uuid.uuid4().hex
            self._write_files(photo_id, [processed.display, processed.background, processed.thumb])
            album.photo_ids.append(photo_id)
            self._landscape[photo_id] = processed.landscape
            try:
                self._commit()
            except BaseException:
                album.photo_ids.remove(photo_id)
                del self._landscape[photo_id]
                self._remove_files(photo_id)
                raise
            return photo_id

    def delete(self, photo_id: str) -> None:
        album = self._find_photo(photo_id)
        album.photo_ids.remove(photo_id)
        self._landscape.pop(photo_id, None)
        self._commit()
        self._remove_files(photo_id)

    # views

    def display_items(self) -> list[dict[str, Any]]:
        return [
            {
                "id": pid,
                "url": f"/media/{pid}.jpg",
                "bg_url": f"/media/{pid}.bg.jpg",
                "caption": a.name,
                "landscape": self._landscape.get(pid, False),
            }
            for a in self._albums
            if not a.hidden
            for pid in a.photo_ids
        ]

    def admin_view(self) -> list[dict[str, Any]]:
        return [
            {
                "id": a.id,
                "name": a.name,
                "hidden": a.hidden,
                "photos": [{"id": p, "thumb_url": f"/media/{p}.thumb.jpg"} for p in a.photo_ids],
            }
            for a in self._albums
        ]

    def zip_stream(self) -> tempfile.SpooledTemporaryFile:
        """Zip of display JPEGs as '{album}/{NNN}.jpg' in a spooled temp file, rewound to the start."""
        out = tempfile.SpooledTemporaryFile(max_size=ZIP_SPOOL_BYTES)
        try:
            used: set[str] = set()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for a in self._albums:
                    folder = _safe_dir_name(a.name, a.id)
                    if folder in used:
                        folder = f"{folder} ({a.id[:8]})"
                    used.add(folder)
                    for n, pid in enumerate(a.photo_ids, 1):
                        src = self._dir / f"{pid}.jpg"
                        if src.is_file():
                            zf.write(src, f"{folder}/{n:03d}.jpg")
            out.seek(0)
            return out
        except BaseException:
            out.close()
            raise
