"""Calendar event model and local-day bucketing."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Member

NO_TITLE = "(bez názvu)"


@dataclass
class Event:
    id: str
    title: str
    location: str | None
    start: datetime | date
    end: datetime | date
    all_day: bool
    member_id: str
    color: str


def _parse_when(value: object, tz: ZoneInfo) -> tuple[datetime | date, bool] | None:
    """Parse a Google start/end object into (value, all_day), or None."""
    if not isinstance(value, dict):
        return None
    try:
        if value.get("date"):
            return date.fromisoformat(value["date"]), True
        if value.get("dateTime"):
            dt = datetime.fromisoformat(value["dateTime"])
            if dt.tzinfo is None:
                return dt.replace(tzinfo=tz), False
            return dt.astimezone(tz), False
    except (ValueError, TypeError):
        return None
    return None


def normalize_google_event(item: dict, member: Member, tz: ZoneInfo) -> Event | None:
    """Convert a Google Calendar item into an Event; None if cancelled or malformed."""
    if not isinstance(item, dict) or item.get("status") == "cancelled":
        return None
    start = _parse_when(item.get("start"), tz)
    end = _parse_when(item.get("end"), tz)
    if start is None or end is None or start[1] != end[1]:
        return None
    return Event(
        id=str(item.get("id", "")),
        title=item.get("summary") or NO_TITLE,
        location=item.get("location") or None,
        start=start[0],
        end=end[0],
        all_day=start[1],
        member_id=member.id,
        color=member.color,
    )


def event_days(ev: Event, tz: ZoneInfo) -> list[date]:
    """Every local date the event covers (all-day end exclusive)."""
    if ev.all_day:
        first, last = ev.start, ev.end - timedelta(days=1)
    else:
        start = ev.start.astimezone(tz)
        end = ev.end.astimezone(tz)
        first, last = start.date(), (end - timedelta(microseconds=1)).date()
    if last < first:
        last = first
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _sort_key(ev: Event) -> tuple:
    stamp = 0.0 if ev.all_day else ev.start.timestamp()
    return (not ev.all_day, stamp, ev.title.casefold(), ev.id)


def bucket_by_day(
    events: list[Event], first: date, days: int, tz: ZoneInfo
) -> dict[date, list[Event]]:
    """Assign events to each local day of [first, first+days); every day is a key."""
    out: dict[date, list[Event]] = {first + timedelta(days=i): [] for i in range(days)}
    for ev in events:
        for day in event_days(ev, tz):
            if day in out:
                out[day].append(ev)
    for evs in out.values():
        evs.sort(key=_sort_key)
    return out


def display_range(today: date) -> tuple[date, date]:
    """Inclusive (Monday on/before the 1st, max(Sunday on/after month end, today+6))."""
    month_first = today.replace(day=1)
    first = month_first - timedelta(days=month_first.weekday())
    month_last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    last = month_last + timedelta(days=6 - month_last.weekday())
    return first, max(last, today + timedelta(days=6))


def to_json(ev: Event) -> dict:
    """EventJSON: all-day as dates (end exclusive), timed as ISO-8601 with offset."""
    return {
        "id": ev.id,
        "title": ev.title,
        "location": ev.location,
        "start": ev.start.isoformat(),
        "end": ev.end.isoformat(),
        "all_day": ev.all_day,
        "member_id": ev.member_id,
        "color": ev.color,
    }
