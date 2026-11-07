"""Photo store: albums, ordering and the three JPEG files per photo, persisted in photos.json."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from .config import _atomic_write
from .hub import Hub
from .imaging import process_image

SUFFIXES = (".jpg", ".bg.jpg", ".thumb.jpg")
ZIP_SPOOL_BYTES = 10 * 1024 * 1024
log = logging.getLogger(__name__)
_RESERVED = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", re.IGNORECASE)
_PHOTO_FILE = re.compile(r"([0-9a-f]{32})(\.bg|\.thumb)?\.jpg")


@dataclass
class Album:
    id: str
    name: str
    hidden: bool = False
    photo_ids: list[str] = field(default_factory=list)


def _safe_dir_name(name: str, fallback: str) -> str:
    """Album name usable as a zip folder on any OS; falls back to the album id."""
    cleaned = "".join(ch for ch in name if ch >= " " and ch != "\x7f" and ch not in '/\\:*?"<>|')
    cleaned = cleaned.replace("..", "").strip(" .")
    if not cleaned:
        return fallback
    if _RESERVED.fullmatch(cleaned):
        stem, dot, ext = cleaned.partition(".")
        cleaned = f"{stem}_{dot}{ext}"
    return cleaned


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
        self._read_only = False
        self._load()

    # persistence

    def _load(self) -> None:
        self._albums, self._landscape = [], {}
        try:
            text = self._json.read_text(encoding="utf-8")
            raw = json.loads(text)
            albums = [
                Album(a["id"], a["name"], bool(a.get("hidden", False)), list(a.get("photo_ids", [])))
                for a in raw.get("albums", [])
            ]
            landscape = {k: bool(v) for k, v in raw.get("landscape", {}).items()}
        except FileNotFoundError:
            self._sweep()
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._set_aside()
            self._sweep()
            return
        self._albums, self._landscape = albums, landscape
        self._sweep()

    def _set_aside(self) -> None:
        """Move an unreadable or corrupt photos.json aside and start empty; if that fails, refuse to write."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        aside = self._json.with_name(f"{self._json.name}.corrupt-{stamp}")
        try:
            os.replace(self._json, aside)
        except OSError:
            log.exception("%s is unreadable and could not be moved aside; photo store is read-only", self._json)
            self._read_only = True
            return
        log.error("%s is corrupt; moved to %s and starting empty", self._json, aside)

    def _sweep(self) -> None:
        """Remove leftover *.tmp files; move unreferenced photo files to photos/orphans/ (never delete)."""
        if self._read_only:
            return
        known = {p for a in self._albums for p in a.photo_ids}
        quarantine = not any(self._json.parent.glob(self._json.name + ".corrupt-*"))
        moved = 0
        for f in list(self._dir.iterdir()):
            if not f.is_file():
                continue
            if f.name.endswith(".tmp"):
                f.unlink(missing_ok=True)
                continue
            m = _PHOTO_FILE.fullmatch(f.name)
            if quarantine and m and m.group(1) not in known:
                orphans = self._dir / "orphans"
                orphans.mkdir(exist_ok=True)
                os.replace(f, orphans / f.name)
                moved += 1
        if moved:
            log.warning("moved %d unreferenced photo file(s) to %s", moved, self._dir / "orphans")

    def _save(self) -> None:
        if self._read_only:
            raise OSError("photos.json is unreadable; refusing to overwrite it")
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

    def update_album(self, album_id: str, name: str | None = None, hidden: bool | None = None) -> None:
        album = self._album(album_id)
        if name is not None:
            album.name = name
        if hidden is not None:
            album.hidden = hidden
        self._commit()

    def rename_album(self, album_id: str, name: str) -> None:
        self.update_album(album_id, name=name)

    def set_hidden(self, album_id: str, hidden: bool) -> None:
        self.update_album(album_id, hidden=hidden)

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

    def _process_and_write(self, data: bytes, photo_id: str):
        processed = process_image(data)
        self._write_files(photo_id, [processed.display, processed.background, processed.thumb])
        return processed

    async def ingest(self, data: bytes, album_id: str) -> str:
        """Process one upload and append it to the album. Raises KeyError, UnsupportedImage."""
        async with self._lock:
            album = self._album(album_id)
            photo_id = uuid.uuid4().hex
            processed = await run_in_threadpool(self._process_and_write, data, photo_id)
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

    def snapshot_for_zip(self) -> list[tuple[str, str, list[str]]]:
        """Copy album names/ids/photo lists; call on the event loop so the worker sees a stable view."""
        return [(a.name, a.id, list(a.photo_ids)) for a in self._albums]

    def build_zip(self, snapshot: list[tuple[str, str, list[str]]]) -> tempfile.SpooledTemporaryFile:
        out = tempfile.SpooledTemporaryFile(max_size=ZIP_SPOOL_BYTES, dir=self._dir.parent)
        try:
            used: set[str] = set()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for name, album_id, pids in snapshot:
                    folder = _safe_dir_name(name, album_id)
                    if folder.lower() in used:
                        folder = f"{folder} ({album_id[:8]})"
                    used.add(folder.lower())
                    n = 0
                    for pid in pids:
                        try:
                            zf.write(self._dir / f"{pid}.jpg", f"{folder}/{n + 1:03d}.jpg")
                        except FileNotFoundError:
                            continue
                        n += 1
            out.seek(0)
            return out
        except BaseException:
            out.close()
            raise
