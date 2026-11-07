"""Admin photo and album API."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import auth
from ..imaging import MAX_UPLOAD_BYTES, UnsupportedImage

router = APIRouter(dependencies=[Depends(auth.require_admin)])

TOO_LARGE = {"cs": "Soubor je příliš velký (max 40 MB)", "en": "File is too large (max 40 MB)"}
CZECH_IMAGE_ERRORS = {
    "Not a supported image": "Nepodporovaný formát obrázku",
    "Image is too large": "Obrázek je příliš velký",
    "Image could not be read": "Obrázek nelze přečíst",
}
ZIP_CHUNK = 1024 * 1024


def _is_czech(request: Request) -> bool:
    first = request.headers.get("accept-language", "").split(",")[0].strip().lower()
    return first.startswith("cs")


class AlbumCreate(BaseModel):
    name: str


class AlbumPatch(BaseModel):
    name: str | None = None
    hidden: bool | None = None


class IdList(BaseModel):
    ids: list[str]


class MoveBody(BaseModel):
    album_id: str
    index: int


def _store(request: Request):
    return request.app.state.photos


@router.get("/api/admin/photos")
async def list_photos(request: Request) -> dict:
    return {"albums": _store(request).admin_view()}


@router.post("/api/admin/photos/upload")
async def upload(
    request: Request,
    album_id: str = Form(...),
    files: list[UploadFile] = File(default=[], alias="files[]"),
) -> dict:
    store = _store(request)
    if not files:
        raise HTTPException(status_code=400, detail="no files")
    if all(a.id != album_id for a in store.albums()):
        raise HTTPException(status_code=404, detail="album not found")
    czech = _is_czech(request)
    results = []
    for f in files:
        name = f.filename or ""
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            results.append({"name": name, "ok": False, "error": TOO_LARGE["cs" if czech else "en"]})
            continue
        try:
            await store.ingest(data, album_id)
            results.append({"name": name, "ok": True, "error": None})
        except UnsupportedImage as exc:
            msg = str(exc)
            results.append({"name": name, "ok": False, "error": CZECH_IMAGE_ERRORS.get(msg, msg) if czech else msg})
        except KeyError:
            raise HTTPException(status_code=404, detail="album not found")
    return {"results": results}


@router.post("/api/admin/albums")
async def create_album(body: AlbumCreate, request: Request) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    a = _store(request).create_album(name)
    return {"id": a.id, "name": a.name, "hidden": a.hidden, "photo_ids": a.photo_ids}


@router.put("/api/admin/albums/order")
async def order_albums(body: IdList, request: Request) -> dict:
    try:
        _store(request).reorder_albums(body.ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.patch("/api/admin/albums/{album_id}")
async def patch_album(album_id: str, body: AlbumPatch, request: Request) -> dict:
    store = _store(request)
    name = body.name.strip() if body.name is not None else None
    if name == "":
        raise HTTPException(status_code=400, detail="name required")
    if all(a.id != album_id for a in store.albums()):
        raise HTTPException(status_code=404, detail="album not found")
    if name is not None:
        store.rename_album(album_id, name)
    if body.hidden is not None:
        store.set_hidden(album_id, body.hidden)
    return {"ok": True}


@router.put("/api/admin/albums/{album_id}/order")
async def order_photos(album_id: str, body: IdList, request: Request) -> dict:
    try:
        _store(request).reorder_photos(album_id, body.ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="album not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/api/admin/photos/{photo_id}/move")
async def move_photo(photo_id: str, body: MoveBody, request: Request) -> dict:
    try:
        _store(request).move_photo(photo_id, body.album_id, body.index)
    except KeyError:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


@router.delete("/api/admin/photos/{photo_id}")
async def delete_photo(photo_id: str, request: Request) -> dict:
    try:
        _store(request).delete(photo_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


@router.get("/api/admin/photos.zip")
async def photos_zip(request: Request) -> StreamingResponse:
    spool = await run_in_threadpool(_store(request).zip_stream)
    size = spool.seek(0, 2)
    spool.seek(0)

    def chunks() -> Iterator[bytes]:
        try:
            while block := spool.read(ZIP_CHUNK):
                yield block
        finally:
            spool.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="photos.zip"', "Content-Length": str(size)},
    )
