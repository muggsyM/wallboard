import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from wallboard import auth
from wallboard.app import create_app
from wallboard.routes import photos_api


def jpeg(color=(200, 50, 50), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def app(data_dir):
    return create_app(data_dir)


def make_client(app, peer=("127.0.0.1", 50000), host="localhost", admin=True):
    c = TestClient(app, client=peer, base_url=f"http://{host}", headers={"X-Requested-With": "wallboard"})
    if admin:
        c.cookies.set(auth.COOKIE_NAME, auth.new_session_cookie(app.state.store.secrets.session_secret))
    return c


@pytest.fixture
def c(app):
    return make_client(app)


def new_album(c, name="Holiday") -> str:
    r = c.post("/api/admin/albums", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def upload(c, album_id, files, headers=None):
    return c.post(
        "/api/admin/photos/upload",
        data={"album_id": album_id},
        files=[("files[]", (n, b, "image/jpeg")) for n, b in files],
        headers=headers or {},
    )


def photo_ids(c):
    return {a["id"]: [p["id"] for p in a["photos"]] for a in c.get("/api/admin/photos").json()["albums"]}


def test_upload_appends_in_order(c):
    aid = new_album(c)
    r = upload(c, aid, [("a.jpg", jpeg()), ("bad.jpg", b"nope"), ("b.jpg", jpeg((1, 2, 3)))])
    assert r.status_code == 200
    res = r.json()["results"]
    assert [x["name"] for x in res] == ["a.jpg", "bad.jpg", "b.jpg"]
    assert [x["ok"] for x in res] == [True, False, True]
    assert res[1]["error"] == "Not a supported image"
    ids = photo_ids(c)[aid]
    assert len(ids) == 2
    thumb = c.get(f"/media/{ids[0]}.thumb.jpg")
    assert thumb.status_code == 200
    assert thumb.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in thumb.headers["cache-control"]


def test_upload_unknown_album_and_no_files(c):
    assert upload(c, "f" * 32, [("a.jpg", jpeg())]).status_code == 404
    aid = new_album(c)
    r = c.post("/api/admin/photos/upload", data={"album_id": aid})
    assert r.status_code == 400


def test_reorder_and_move(c):
    a1, a2 = new_album(c, "A"), new_album(c, "B")
    upload(c, a1, [("1.jpg", jpeg()), ("2.jpg", jpeg()), ("3.jpg", jpeg())])
    p1, p2, p3 = photo_ids(c)[a1]
    assert c.put(f"/api/admin/albums/{a1}/order", json={"ids": [p3, p1, p2]}).status_code == 200
    assert photo_ids(c)[a1] == [p3, p1, p2]
    assert c.put(f"/api/admin/albums/{a1}/order", json={"ids": [p3, p1]}).status_code == 400
    assert c.put(f"/api/admin/albums/{a1}/order", json={"ids": [p3, p1, "x"]}).status_code == 400
    assert c.put("/api/admin/albums/order", json={"ids": [a2, a1]}).status_code == 200
    assert list(photo_ids(c)) == [a2, a1]
    assert c.put("/api/admin/albums/order", json={"ids": [a2]}).status_code == 400
    assert c.post(f"/api/admin/photos/{p1}/move", json={"album_id": a2, "index": 99}).status_code == 200
    assert photo_ids(c) == {a2: [p1], a1: [p3, p2]}
    assert c.post(f"/api/admin/photos/{p2}/move", json={"album_id": a2, "index": -5}).status_code == 200
    assert photo_ids(c)[a2] == [p2, p1]


def test_hidden_album_excluded_from_display(c, app):
    a1, a2 = new_album(c, "Shown"), new_album(c, "Secret")
    upload(c, a1, [("1.jpg", jpeg())])
    upload(c, a2, [("2.jpg", jpeg())])
    assert c.patch(f"/api/admin/albums/{a2}", json={"hidden": True}).status_code == 200
    items = app.state.photos.display_items()
    assert [i["caption"] for i in items] == ["Shown"]
    assert set(items[0]) == {"id", "url", "bg_url", "caption", "landscape"}
    assert items[0]["landscape"] is True
    assert items[0]["url"] == f"/media/{items[0]['id']}.jpg"
    assert items[0]["bg_url"] == f"/media/{items[0]['id']}.bg.jpg"
    assert app.state.hub.snapshot()["photos"] == items


def test_failed_ingest_leaves_no_files(c, app, data_dir, monkeypatch):
    aid = new_album(c)
    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        c.post(
            "/api/admin/photos/upload",
            data={"album_id": aid},
            files=[("files[]", ("a.jpg", jpeg(), "image/jpeg"))],
        )
    monkeypatch.undo()
    assert list((data_dir / "photos").iterdir()) == []
    assert photo_ids(c)[aid] == []


def test_oversize_rejected(c, monkeypatch):
    monkeypatch.setattr(photos_api, "MAX_UPLOAD_BYTES", 100)
    aid = new_album(c)
    r = upload(c, aid, [("big.jpg", b"x" * 101)], headers={"Accept-Language": "cs-CZ,cs;q=0.9"})
    assert r.json()["results"][0] == {"name": "big.jpg", "ok": False, "error": "Soubor je příliš velký (max 40 MB)"}
    r = upload(c, aid, [("big.jpg", b"x" * 101)], headers={"Accept-Language": "en-US,cs;q=0.9"})
    assert r.json()["results"][0]["error"] == "File is too large (max 40 MB)"
    assert photo_ids(c)[aid] == []


def test_unsupported_message_language(c):
    aid = new_album(c)
    r = upload(c, aid, [("bad.jpg", b"nope")], headers={"Accept-Language": "cs"})
    assert r.json()["results"][0]["error"] == "Nepodporovaný formát obrázku"
    r = upload(c, aid, [("bad.jpg", b"nope")])
    assert r.json()["results"][0]["error"] == "Not a supported image"


def test_media_path_traversal_rejected(c, data_dir):
    (data_dir / "secrets.json").write_text("{}")
    assert c.get("/media/../secrets.json").status_code == 404
    assert c.get("/media/..%2Fsecrets.json").status_code == 404
    assert c.get("/media/secrets.json").status_code == 404
    assert c.get(f"/media/{'a' * 32}.jpg").status_code == 404


def test_media_access_control(app):
    admin = make_client(app)
    aid = new_album(admin)
    upload(admin, aid, [("a.jpg", jpeg())])
    pid = photo_ids(admin)[aid][0]
    url = f"/media/{pid}.thumb.jpg"
    assert make_client(app, peer=("192.168.1.5", 1), host="192.168.1.2", admin=False).get(url).status_code == 403
    assert make_client(app, peer=("192.168.1.5", 1), host="192.168.1.2", admin=True).get(url).status_code == 200
    assert make_client(app, admin=False).get(url).status_code == 200  # loopback display
    assert make_client(app, peer=("127.0.0.1", 1), host="evil.example", admin=False).get(url).status_code == 403


def test_zip_contains_display_files(c, app):
    a1 = new_album(c, "../Trip/2024")
    a2 = new_album(c, "..")
    upload(c, a1, [("1.jpg", jpeg()), ("2.jpg", jpeg((9, 9, 9)))])
    upload(c, a2, [("3.jpg", jpeg())])
    r = c.get("/api/admin/photos.zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names == ["Trip2024/001.jpg", "Trip2024/002.jpg", f"{a2}/001.jpg"]
    assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
    first = photo_ids(c)[a1][0]
    assert zf.read("Trip2024/001.jpg") == (app.state.photos.path(f"{first}.jpg")).read_bytes()


def test_mutation_publishes_photos(c, app):
    seen = []

    async def spy(msg):
        seen.append(msg)

    app.state.hub._broadcast = spy
    photos = lambda: [m for m in seen if m["type"] == "photos"]  # noqa: E731
    aid = new_album(c)
    assert photos()[-1] == {"type": "photos", "photos": []}
    upload(c, aid, [("a.jpg", jpeg())])
    assert len(photos()[-1]["photos"]) == 1
    pid = photos()[-1]["photos"][0]["id"]
    assert c.delete(f"/api/admin/photos/{pid}").status_code == 200
    assert photos()[-1]["photos"] == []
    assert c.delete(f"/api/admin/photos/{pid}").status_code == 404


def test_persistence_and_delete_removes_files(c, app, data_dir):
    aid = new_album(c)
    upload(c, aid, [("a.jpg", jpeg())])
    pid = photo_ids(c)[aid][0]
    app2 = create_app(data_dir)
    assert app2.state.hub.snapshot()["photos"][0]["id"] == pid
    assert c.delete(f"/api/admin/photos/{pid}").status_code == 200
    assert list((data_dir / "photos").iterdir()) == []


def test_api_requires_admin(app):
    anon = make_client(app, admin=False)
    assert anon.get("/api/admin/photos").status_code == 401
    assert anon.get("/api/admin/photos.zip").status_code == 401


def test_photos_page_requires_admin(app):
    lan = ("192.168.1.20", 50000)
    r = make_client(app, peer=lan, host="192.168.1.10:8080", admin=False).get("/photos", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/photos"
    r = make_client(app, peer=lan, host="192.168.1.10:8080").get("/photos", follow_redirects=False)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store"
    assert "/static/admin/photos.js" in r.text


# --- fix round 1 ---

from starlette.formparsers import MultiPartParser  # noqa: E402

from wallboard import body_limit  # noqa: E402
from wallboard.photos import PhotoStore, _safe_dir_name  # noqa: E402


def test_unauthenticated_upload_rejected_before_body_read(app, monkeypatch):
    reads = []
    orig = MultiPartParser.parse

    async def spy(self):
        reads.append(1)
        return await orig(self)

    monkeypatch.setattr(MultiPartParser, "parse", spy)
    anon = make_client(app, admin=False)
    r = anon.post("/api/admin/photos/upload", data={"album_id": "x"}, files=[("files[]", ("a.jpg", b"x" * 5000))])
    assert r.status_code == 401 and r.json() == {"detail": "unauthorized"}
    assert reads == []
    assert anon.delete("/api/admin/photos/abc").status_code == 401


def test_authenticated_upload_parses_body(c, monkeypatch):
    reads = []
    orig = MultiPartParser.parse

    async def spy(self):
        reads.append(1)
        return await orig(self)

    monkeypatch.setattr(MultiPartParser, "parse", spy)
    aid = new_album(c)
    assert upload(c, aid, [("a.jpg", jpeg())]).json()["results"][0]["ok"] is True
    assert reads == [1]


def test_content_length_over_cap_413(c):
    big = {"Content-Length": str(body_limit.DEFAULT_MAX_BODY + 1)}
    assert c.post("/api/admin/albums", content=b"x", headers=big).status_code == 413
    assert make_client(c.app, admin=False).post("/login", content=b"x", headers=big).status_code == 413


def test_chunked_body_over_cap_413(c, monkeypatch):
    monkeypatch.setattr(body_limit, "DEFAULT_MAX_BODY", 100)

    def gen():
        for _ in range(5):
            yield b"x" * 50

    r = c.post("/api/admin/albums", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_login_still_works_under_limit(app):
    r = make_client(app, admin=False).post("/login", data={"password": "x"}, follow_redirects=False)
    assert r.status_code != 413


def test_corrupt_photos_json_moved_aside(data_dir):
    (data_dir / "photos.json").write_text("{not json")
    (data_dir / "photos").mkdir()
    orphan = data_dir / "photos" / ("a" * 32 + ".jpg")
    orphan.write_bytes(b"x")
    app = create_app(data_dir)
    assert app.state.photos.albums() == []
    moved = list(data_dir.glob("photos.json.corrupt-*"))
    assert len(moved) == 1 and moved[0].read_text() == "{not json"
    assert not (data_dir / "photos.json").exists()
    assert orphan.exists()  # no sweep after a corrupt file


def test_startup_sweep(c, app, data_dir):
    aid = new_album(c)
    upload(c, aid, [("a.jpg", jpeg())])
    pid = photo_ids(c)[aid][0]
    d = data_dir / "photos"
    (d / "junk.jpg.tmp").write_bytes(b"x")
    (d / f"{'b' * 32}.jpg").write_bytes(b"x")
    (d / f"{'b' * 32}.thumb.jpg").write_bytes(b"x")
    PhotoStore(data_dir, app.state.hub)
    assert sorted(p.name for p in d.iterdir() if p.is_file()) == sorted(
        f"{pid}{s}" for s in (".jpg", ".bg.jpg", ".thumb.jpg")
    )
    assert sorted(p.name for p in (d / "orphans").iterdir()) == sorted(f"{'b' * 32}{s}" for s in (".jpg", ".thumb.jpg"))
    assert c.get(f"/media/orphans/{'b' * 32}.jpg").status_code == 404
    assert c.get(f"/media/{'b' * 32}.jpg").status_code == 404


@pytest.mark.parametrize(
    "name,expected",
    [
        (". .", "ID"),
        ('a:b*c?d"e<f>g|h', "abcdefgh"),
        ("CON", "CON_"),
        ("nul.txt", "nul_.txt"),
        ("com1", "com1_"),
        ("  Trip  ", "Trip"),
        ("\x00\x01", "ID"),
    ],
)
def test_zip_name_sanitizing(name, expected):
    assert _safe_dir_name(name, "ID") == expected


def test_zip_skips_missing_files_and_numbers_written(c, app):
    aid = new_album(c, "A")
    upload(c, aid, [("1.jpg", jpeg()), ("2.jpg", jpeg()), ("3.jpg", jpeg())])
    p1, p2, p3 = photo_ids(c)[aid]
    app.state.photos.path(f"{p2}.jpg").unlink()
    zf = zipfile.ZipFile(io.BytesIO(c.get("/api/admin/photos.zip").content))
    assert zf.namelist() == ["A/001.jpg", "A/002.jpg"]


def test_album_name_limits_and_single_patch_publish(c, app):
    assert c.post("/api/admin/albums", json={"name": "x" * 101}).status_code == 400
    aid = new_album(c, "x" * 100)
    seen = []

    async def spy(msg):
        seen.append(msg)

    app.state.hub._broadcast = spy
    assert c.patch(f"/api/admin/albums/{aid}", json={"name": "y" * 101}).status_code == 400
    assert c.patch(f"/api/admin/albums/{aid}", json={"name": "New", "hidden": True}).status_code == 200
    assert len(seen) == 1


def test_corrupt_mutate_restart_keeps_photo_files(c, data_dir):
    aid = new_album(c)
    upload(c, aid, [("a.jpg", jpeg()), ("b.jpg", jpeg())])
    before = sorted(p.name for p in (data_dir / "photos").iterdir())
    (data_dir / "photos.json").write_text("garbage")
    app2 = create_app(data_dir)
    c2 = make_client(app2)
    new_album(c2, "Fresh")  # writes a new photos.json
    app3 = create_app(data_dir)  # restart
    assert app3.state.photos.albums()[0].name == "Fresh"
    after = sorted(p.name for p in (data_dir / "photos").iterdir() if p.is_file())
    assert after == before
    assert not (data_dir / "photos" / "orphans").exists()


def test_unreadable_photos_json_is_not_overwritten(data_dir, monkeypatch):
    (data_dir / "photos.json").write_text("{}")
    real_replace = os.replace

    def fail(src, dst):
        if str(src).endswith("photos.json"):
            raise OSError("nope")
        return real_replace(src, dst)

    real_read = type(data_dir).read_text

    def bad_read(self, *a, **k):
        if self.name == "photos.json":
            raise PermissionError("denied")
        return real_read(self, *a, **k)

    monkeypatch.setattr(type(data_dir), "read_text", bad_read)
    monkeypatch.setattr(os, "replace", fail)
    store = PhotoStore(data_dir, create_app(data_dir).state.hub)
    with pytest.raises(OSError):
        store.create_album("x")
    monkeypatch.undo()
    assert (data_dir / "photos.json").read_text() == "{}"


def test_unreadable_photos_json_moved_aside_when_possible(data_dir, monkeypatch):
    (data_dir / "photos.json").write_text("{}")
    real_read = type(data_dir).read_text

    def bad_read(self, *a, **k):
        if self.name == "photos.json":
            raise PermissionError("denied")
        return real_read(self, *a, **k)

    monkeypatch.setattr(type(data_dir), "read_text", bad_read)
    PhotoStore(data_dir, create_app(data_dir).state.hub)
    assert len(list(data_dir.glob("photos.json.corrupt-*"))) == 1


def test_zip_snapshot_taken_before_worker(c, app, monkeypatch):
    aid = new_album(c, "A")
    upload(c, aid, [("1.jpg", jpeg())])
    import threading

    main = threading.get_ident()
    seen = {}
    store = app.state.photos
    real_snap, real_build = store.snapshot_for_zip, store.build_zip

    def snap():
        seen["snap"] = threading.get_ident()
        return real_snap()

    def build(s):
        seen["build"] = threading.get_ident()
        return real_build(s)

    monkeypatch.setattr(store, "snapshot_for_zip", snap)
    monkeypatch.setattr(store, "build_zip", build)
    assert c.get("/api/admin/photos.zip").status_code == 200
    assert seen["snap"] != seen["build"]  # snapshot on the event-loop thread, build on a worker


def test_sweep_failure_logs_not_raises(data_dir, monkeypatch, caplog):
    (data_dir / "photos").mkdir()
    (data_dir / "photos" / ("c" * 32 + ".jpg")).write_bytes(b"x")  # orphan to move
    from wallboard import photos as photos_mod

    real_replace = os.replace

    def broken(src, dst):
        if "orphans" in str(dst):
            raise OSError("read-only file system")
        real_replace(src, dst)

    monkeypatch.setattr(photos_mod.os, "replace", broken)
    app = create_app(data_dir)  # must not raise
    assert app.state.photos.albums() == []
    assert "sweep" in caplog.text


# ---- weather gallery (Today page pictures) ----


def upload_weather(c, files):
    return c.post(
        "/api/admin/weather/upload",
        files=[("files[]", (n, b, "image/jpeg")) for n, b in files],
        headers={"X-Requested-With": "wallboard"},
    )


def weather(c):
    return c.get("/api/admin/photos").json()["weather"]


def test_weather_upload_flags_and_display_topic(c, app):
    r = upload_weather(c, [("sun.jpg", jpeg()), ("bad.jpg", b"nope")])
    assert [x["ok"] for x in r.json()["results"]] == [True, False]
    [pic] = weather(c)
    assert pic["flags"] == [] and pic["thumb_url"].endswith(".thumb.jpg")
    # Not in any album, not in the slideshow; without a flag not on the display either.
    assert all(pic["id"] not in a["photos"] for a in c.get("/api/admin/photos").json()["albums"])
    assert app.state.hub.snapshot()["photos"] == []
    assert app.state.hub.snapshot()["weather_pics"] == []
    h = {"X-Requested-With": "wallboard"}
    r = c.put(f"/api/admin/weather/{pic['id']}/flags", json={"flags": ["rainy", "sunny", "rainy"]}, headers=h)
    assert r.status_code == 200 and r.json()["flags"] == ["sunny", "rainy"]
    assert app.state.hub.snapshot()["weather_pics"] == [{"id": pic["id"], "url": f"/media/{pic['id']}.jpg", "flags": ["sunny", "rainy"]}]
    assert c.put(f"/api/admin/weather/{pic['id']}/flags", json={"flags": ["tornado"]}, headers=h).status_code == 400
    assert c.put("/api/admin/weather/nope/flags", json={"flags": []}, headers=h).status_code == 404
    assert c.get(f"/media/{pic['id']}.jpg").status_code == 200


def test_weather_pictures_persist_survive_sweep_and_delete(c, app, data_dir):
    upload_weather(c, [("sun.jpg", jpeg())])
    pid = weather(c)[0]["id"]
    c.put(f"/api/admin/weather/{pid}/flags", json={"flags": ["snowy"]}, headers={"X-Requested-With": "wallboard"})
    again = create_app(data_dir)  # reload photos.json + sweep: the files stay, flags kept
    assert again.state.photos.weather_admin_view()[0]["flags"] == ["snowy"]
    assert (data_dir / "photos" / f"{pid}.jpg").is_file()
    assert c.delete(f"/api/admin/photos/{pid}").status_code == 200
    assert weather(c) == [] and not (data_dir / "photos" / f"{pid}.jpg").exists()


def test_weather_upload_requires_admin(app):
    r = make_client(app, peer=("192.168.1.5", 1), host="192.168.1.2", admin=False).post(
        "/api/admin/weather/upload", files=[("files[]", ("a.jpg", jpeg(), "image/jpeg"))], headers={"X-Requested-With": "wallboard"}
    )
    assert r.status_code in (401, 403)
