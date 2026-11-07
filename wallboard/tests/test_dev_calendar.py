import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from wallboard.app import create_app
from wallboard.config import Member
from wallboard.gcal import GoogleCalendarService, expand_dev_calendar

TZ = ZoneInfo("Europe/Prague")
MEMBERS = [
    Member(id="dad", name="Dad", initial="D", color="#2bb3a0"),
    Member(id="anna", name="Anna", initial="A", color="#e0607e"),
]
SAMPLE = Path(__file__).resolve().parents[2] / "dev" / "sample_calendar.json"


def test_expand_relative_to_today():
    spec = {
        "events": [
            {"day": 0, "start": "07:00", "end": "08:00", "title": "Run", "member_id": "dad"},
            {"day": 0, "start": "23:00", "end_day": 1, "end": "01:00", "title": "Night", "member_id": "anna",
             "location": "Home"},
            {"day": 2, "all_day": True, "days": 3, "title": "Ski", "member_id": "dad"},
            {"day": 1, "all_day": True, "title": "Party", "member_id": "anna"},
            {"day": 0, "start": "09:00", "end": "10:00", "title": "Ghost", "member_id": "nobody"},
        ]
    }
    out = expand_dev_calendar(spec, date(2026, 11, 7), TZ, MEMBERS)
    assert out == [
        {"id": "dev-0", "title": "Run", "location": None, "start": "2026-11-07T07:00:00+01:00",
         "end": "2026-11-07T08:00:00+01:00", "all_day": False, "member_id": "dad", "color": "#2bb3a0"},
        {"id": "dev-1", "title": "Night", "location": "Home", "start": "2026-11-07T23:00:00+01:00",
         "end": "2026-11-08T01:00:00+01:00", "all_day": False, "member_id": "anna", "color": "#e0607e"},
        {"id": "dev-2", "title": "Ski", "location": None, "start": "2026-11-09", "end": "2026-11-12",
         "all_day": True, "member_id": "dad", "color": "#2bb3a0"},
        {"id": "dev-3", "title": "Party", "location": None, "start": "2026-11-08", "end": "2026-11-09",
         "all_day": True, "member_id": "anna", "color": "#e0607e"},
    ]


def test_expand_uses_local_offset_across_dst():
    spec = {"events": [{"day": 0, "start": "09:00", "end": "10:00", "title": "X", "member_id": "dad"}]}
    out = expand_dev_calendar(spec, date(2026, 3, 29), TZ, MEMBERS)
    assert out[0]["start"] == "2026-03-29T09:00:00+02:00"


def test_sample_file_expands():
    spec = json.loads(SAMPLE.read_text(encoding="utf-8"))
    members = [Member(id=i, name=i, initial=i[0].upper(), color="#123456") for i in spec["member_ids"]]
    out = expand_dev_calendar(spec, date(2026, 11, 7), TZ, members)
    assert len(out) == len(spec["events"])
    assert any(e["all_day"] for e in out)


async def test_service_publishes_dev_calendar(data_dir, tmp_path, caplog):
    path = tmp_path / "cal.json"
    path.write_text(json.dumps({"events": [
        {"day": 1, "start": "10:00", "end": "11:00", "title": "Swim", "member_id": "dad"},
    ]}), encoding="utf-8")
    app = create_app(data_dir)
    store, hub = app.state.store, app.state.hub
    store.save_config(store.config.model_copy(update={"members": MEMBERS}))
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    svc = GoogleCalendarService(store, hub, now=lambda: now, dev_calendar=path)
    caplog.set_level(logging.WARNING, logger="wallboard.gcal")
    await svc.start()
    await svc.stop()
    cal = hub.snapshot()["calendar"]
    assert (cal["range_start"], cal["range_end"]) == ("2026-10-26", "2026-12-06")
    assert [(e["title"], e["start"]) for e in cal["events"]] == [("Swim", "2026-11-08T10:00:00+01:00")]
    assert cal["holidays"] == {
        "2026-10-28": "Den vzniku samostatného československého státu",
        "2026-11-17": "Den boje za svobodu a demokracii",
    }
    assert sum("dev calendar" in r.getMessage() for r in caplog.records) == 1


def test_create_app_reads_env(data_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("WALLBOARD_DEV_CALENDAR", str(tmp_path / "x.json"))
    assert create_app(data_dir).state.gcal.dev_calendar == tmp_path / "x.json"
    monkeypatch.delenv("WALLBOARD_DEV_CALENDAR")
    assert create_app(data_dir).state.gcal.dev_calendar is None
