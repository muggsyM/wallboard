from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from wallboard.config import Member
from wallboard.events import (
    bucket_by_day,
    display_range,
    event_days,
    normalize_google_event,
    to_json,
)

TZ = ZoneInfo("Europe/Prague")
MEMBER = Member(id="m1", name="Anna", initial="A", color="#4f8cff")


def timed(start, end, **kw):
    item = {"id": kw.get("id", "e1"), "summary": kw.get("summary", "Ev"),
            "start": {"dateTime": start}, "end": {"dateTime": end}}
    return normalize_google_event(item, MEMBER, TZ)


def all_day(start, end, **kw):
    item = {"id": kw.get("id", "a1"), "summary": kw.get("summary", "Day"),
            "start": {"date": start}, "end": {"date": end}}
    return normalize_google_event(item, MEMBER, TZ)


def test_all_day_end_exclusive():
    ev = all_day("2026-11-20", "2026-11-23")
    assert event_days(ev, TZ) == [date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)]


def test_all_day_end_not_after_start_covers_start_day():
    ev = all_day("2026-11-20", "2026-11-20")
    assert event_days(ev, TZ) == [date(2026, 11, 20)]
    ev = all_day("2026-11-20", "2026-11-18")
    assert event_days(ev, TZ) == [date(2026, 11, 20)]


def test_overnight_timed():
    ev = timed("2026-11-07T23:00:00+01:00", "2026-11-08T01:00:00+01:00")
    assert event_days(ev, TZ) == [date(2026, 11, 7), date(2026, 11, 8)]


def test_ends_at_midnight():
    ev = timed("2026-11-07T22:00:00+01:00", "2026-11-08T00:00:00+01:00")
    assert event_days(ev, TZ) == [date(2026, 11, 7)]


def test_timed_end_before_start_covers_start_day():
    ev = timed("2026-11-07T22:00:00+01:00", "2026-11-07T21:00:00+01:00")
    assert event_days(ev, TZ) == [date(2026, 11, 7)]
    ev = timed("2026-11-07T22:00:00+01:00", "2026-11-07T22:00:00+01:00")
    assert event_days(ev, TZ) == [date(2026, 11, 7)]


def test_dst_day():
    start = datetime(2026, 10, 25, 1, 30, tzinfo=TZ)
    end = datetime(2026, 10, 25, 3, 30, tzinfo=TZ)
    ev = timed(start.isoformat(), end.isoformat())
    assert event_days(ev, TZ) == [date(2026, 10, 25)]


def test_utc_input_converted():
    ev = timed("2026-11-07T08:00:00Z", "2026-11-07T09:00:00Z")
    assert ev.start.hour == 9
    assert ev.start.utcoffset() == timedelta(hours=1)
    assert to_json(ev)["start"] == "2026-11-07T09:00:00+01:00"


def test_utc_input_crossing_local_midnight():
    ev = timed("2026-11-07T23:30:00Z", "2026-11-08T00:30:00Z")
    assert event_days(ev, TZ) == [date(2026, 11, 8)]


def test_cancelled_skipped():
    item = {"id": "x", "status": "cancelled", "summary": "No",
            "start": {"date": "2026-11-07"}, "end": {"date": "2026-11-08"}}
    assert normalize_google_event(item, MEMBER, TZ) is None


def test_malformed_returns_none():
    for item in [
        {},
        {"id": "x", "start": {"date": "2026-11-07"}},
        {"id": "x", "end": {"date": "2026-11-07"}},
        {"id": "x", "start": {"date": "garbage"}, "end": {"date": "2026-11-07"}},
        {"id": "x", "start": {"dateTime": "nope"}, "end": {"dateTime": "nope"}},
        {"id": "x", "start": {}, "end": {}},
        {"id": "x", "start": "2026-11-07", "end": "2026-11-08"},
    ]:
        assert normalize_google_event(item, MEMBER, TZ) is None


def test_title_fallback_and_fields():
    item = {"id": "x", "start": {"date": "2026-11-07"}, "end": {"date": "2026-11-08"}}
    ev = normalize_google_event(item, MEMBER, TZ)
    assert ev.title == "(bez názvu)"
    assert ev.location is None
    assert ev.member_id == "m1" and ev.color == "#4f8cff"
    assert ev.all_day is True


def test_non_string_title_and_location_coerced():
    # R12: Google (or a proxy) sending a number/object must never put non-strings on the wall.
    base = {"id": 7, "start": {"date": "2026-11-07"}, "end": {"date": "2026-11-08"}}
    ev = normalize_google_event({**base, "summary": 42, "location": 12}, MEMBER, TZ)
    assert ev.title == "42" and ev.location == "12" and ev.id == "7"
    ev = normalize_google_event({**base, "summary": {"x": 1}, "location": ["a"]}, MEMBER, TZ)
    assert isinstance(ev.title, str) and isinstance(ev.location, str)
    ev = normalize_google_event({**base, "summary": "", "location": ""}, MEMBER, TZ)
    assert ev.title == "(bez názvu)" and ev.location is None
    ev = normalize_google_event({**base, "summary": None, "location": None}, MEMBER, TZ)
    assert ev.title == "(bez názvu)" and ev.location is None


def test_to_json_shapes():
    ev = all_day("2026-11-20", "2026-11-23")
    assert to_json(ev) == {
        "id": "a1", "title": "Day", "location": None,
        "start": "2026-11-20", "end": "2026-11-23", "all_day": True,
        "member_id": "m1", "color": "#4f8cff",
    }
    ev = timed("2026-11-07T09:00:00+01:00", "2026-11-07T10:00:00+01:00")
    j = to_json(ev)
    assert j["start"] == "2026-11-07T09:00:00+01:00"
    assert j["end"] == "2026-11-07T10:00:00+01:00"
    assert j["all_day"] is False
    assert list(j) == ["id", "title", "location", "start", "end", "all_day", "member_id", "color"]


def test_sort_order():
    a = all_day("2026-11-07", "2026-11-08", id="a", summary="Zebra")
    t2 = timed("2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00", id="t2", summary="b")
    t1 = timed("2026-11-07T09:00:00+01:00", "2026-11-07T11:00:00+01:00", id="t1", summary="Z")
    t3 = timed("2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00", id="t3", summary="A")
    t4 = timed("2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00", id="t0", summary="a")
    out = bucket_by_day([t2, t1, a, t3, t4], date(2026, 11, 7), 1, TZ)
    assert [e.id for e in out[date(2026, 11, 7)]] == ["a", "t1", "t0", "t3", "t2"]


def test_bucket_window_clip_and_empty_days():
    ev = all_day("2026-11-05", "2026-11-12", id="long")
    out = bucket_by_day([ev], date(2026, 11, 7), 3, TZ)
    assert list(out) == [date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9)]
    assert all([e.id for e in v] == ["long"] for v in out.values())
    out = bucket_by_day([ev], date(2026, 12, 1), 2, TZ)
    assert out == {date(2026, 12, 1): [], date(2026, 12, 2): []}


def test_multi_day_timed_sorts_by_real_start_each_day():
    long = timed("2026-11-07T20:00:00+01:00", "2026-11-09T08:00:00+01:00", id="long")
    short = timed("2026-11-08T09:00:00+01:00", "2026-11-08T10:00:00+01:00", id="short")
    out = bucket_by_day([short, long], date(2026, 11, 7), 3, TZ)
    assert [e.id for e in out[date(2026, 11, 7)]] == ["long"]
    assert [e.id for e in out[date(2026, 11, 8)]] == ["long", "short"]
    assert [e.id for e in out[date(2026, 11, 9)]] == ["long"]


def test_display_range_nov_2026():
    # Saturday; its week starts Mon Nov 2 -> 6 weeks end Sun Dec 13 (month end: Dec 6).
    assert display_range(date(2026, 11, 7)) == (date(2026, 10, 26), date(2026, 12, 13))


def test_display_range_end_of_month():
    # Week of Mon Nov 23 -> 6 weeks end Sun Jan 3 2027, across the year.
    assert display_range(date(2026, 11, 28)) == (date(2026, 10, 26), date(2027, 1, 3))


def test_display_range_rolling_grid_wins_over_month_end():
    # Feb 2026 ends on Saturday the 28th -> Sunday Mar 1; week of Mon Feb 23 + 6 weeks = Apr 5.
    assert display_range(date(2026, 2, 28)) == (date(2026, 1, 26), date(2026, 4, 5))


def test_display_range_month_starting_monday():
    # Monday Jun 15 is its own week start -> Sun Jul 26.
    assert display_range(date(2026, 6, 15)) == (date(2026, 6, 1), date(2026, 7, 26))


def test_display_range_first_day_on_sunday():
    # Nov 1 2026 is a Sunday: start Mon Oct 26; its week is Oct 26 - Nov 1 -> 6 weeks end Dec 6,
    # equal to the Sunday after month end.
    assert display_range(date(2026, 11, 1)) == (date(2026, 10, 26), date(2026, 12, 6))


def test_display_range_covers_the_largest_rolling_grid_and_today_plus_six():
    d = date(2026, 1, 1)
    while d < date(2027, 1, 1):
        first, last = display_range(d)
        monday = d - timedelta(days=d.weekday())
        assert first <= monday and last >= monday + timedelta(days=41) >= d + timedelta(days=6)
        assert first.weekday() == 0 and last.weekday() == 6
        d += timedelta(days=1)
