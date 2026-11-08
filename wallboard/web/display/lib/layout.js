// Pure calendar layout helpers for the Today, Week and Month pages. No DOM.
// Mirrors wallboard/wallboard/events.py (event_days, bucket_by_day): keep them in step.
//
// Dates are plain "YYYY-MM-DD" strings. Day arithmetic runs on UTC Date objects so DST
// can never shift a day; local dates come from Intl in the given IANA zone `tz`.
// Events are EventJSON: all-day start/end are dates with an exclusive end, timed
// start/end are ISO-8601 instants with an offset.
//
//   localDate(instant, tz)            -> iso date of the instant in tz
//   addDays(iso, n)                   -> iso date n days later (n may be negative)
//   noonOf(iso, tz)                   -> Date at local noon of iso in tz (for Intl formatting)
//   daysOf(event, tz)                 -> every local iso date the event covers
//   bucket(events, firstISO, days, tz)-> Map iso -> events, every day of the window a key
//   isPast(event, now, tz)            -> event ended at or before now
//   spanPart(event, iso)              -> "single" | "start" | "middle" | "end"
//   pickVisible(events, capacity, pastFn) -> {shown, hidden} when a day has too many events
//   weekGrid(todayISO, weeks)         -> 7*weeks iso dates from the Monday of today's week
//   assignLanes(weekDays, events)     -> Map event -> lane for multi-day all-day bars in a week row
//   barPiece(event, iso, column)      -> how one day's piece of such a bar is drawn
//   mergeSameAllDay(events)           -> one day's events with identical single-day all-day
//                                        titles of several members folded into one entry

const FALLBACK_TZ = "Europe/Prague";
const dateFormatters = new Map();
const partFormatters = new Map();

function cachedFormatter(cache, tz, options) {
  let f = cache.get(tz);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat("en-CA", { ...options, timeZone: tz || FALLBACK_TZ });
    } catch {
      f = cachedFormatter(cache, FALLBACK_TZ, options);
    }
    cache.set(tz, f);
  }
  return f;
}

const isoParts = (iso) => iso.split("-").map(Number);
const utcOf = (iso) => {
  const [y, m, d] = isoParts(iso);
  return Date.UTC(y, m - 1, d);
};

export function localDate(instant, tz) {
  // en-CA formats as YYYY-MM-DD.
  return cachedFormatter(dateFormatters, tz, { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(instant));
}

export function addDays(iso, n) {
  return new Date(utcOf(iso) + n * 86400000).toISOString().slice(0, 10);
}

// Offset of tz from UTC at `ms`, in ms.
function offsetMs(ms, tz) {
  const f = cachedFormatter(partFormatters, tz, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  });
  const p = Object.fromEntries(f.formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
  const wall = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  return wall - Math.floor(ms / 1000) * 1000;
}

export function noonOf(iso, tz) {
  const guess = utcOf(iso) + 12 * 3600000;
  return new Date(guess - offsetMs(guess, tz));
}

const dayDiff = (a, b) => Math.round((utcOf(b) - utcOf(a)) / 86400000);

function range(first, last) {
  const n = Math.max(0, dayDiff(first, last));
  return Array.from({ length: n + 1 }, (_, i) => addDays(first, i));
}

export function daysOf(event, tz) {
  let first;
  let last;
  if (event.all_day) {
    first = event.start;
    last = addDays(event.end, -1);
  } else {
    const start = Date.parse(event.start);
    const end = Date.parse(event.end);
    if (Number.isNaN(start) || Number.isNaN(end)) return [];
    first = localDate(start, tz);
    last = localDate(end - 1, tz); // end - 1 ms: an event ending at midnight stays on its day
  }
  return range(first, last < first ? first : last);
}

// Python: (not all_day, start timestamp or 0, title.casefold(), id). Plain < compares
// code units like Python's str comparison (toLowerCase stands in for casefold).
function sortKey(ev) {
  return [ev.all_day ? 0 : 1, ev.all_day ? 0 : Date.parse(ev.start), String(ev.title ?? "").toLowerCase(), String(ev.id ?? "")];
}

function compare(a, b) {
  const ka = sortKey(a);
  const kb = sortKey(b);
  for (let i = 0; i < ka.length; i++) {
    if (ka[i] < kb[i]) return -1;
    if (ka[i] > kb[i]) return 1;
  }
  return 0;
}

export function bucket(events, firstISO, days, tz) {
  const out = new Map();
  for (let i = 0; i < days; i++) out.set(addDays(firstISO, i), []);
  for (const ev of events || []) {
    for (const day of daysOf(ev, tz)) out.get(day)?.push(ev);
  }
  for (const evs of out.values()) evs.sort(compare);
  return out;
}

// All-day events end at local midnight of their exclusive end date, so they are past once
// the local date in tz reaches it (a malformed end before start still lasts one day).
export function isPast(event, now, tz) {
  const ms = new Date(now).getTime();
  if (event.all_day) {
    const days = daysOf(event, tz);
    return localDate(ms, tz) > days[days.length - 1];
  }
  const end = Date.parse(event.end);
  return !Number.isNaN(end) && end <= ms;
}

// Position of iso within a multi-day all-day event (for continuous bars). Timed and
// single-day events, and dates the event does not cover, are "single".
export function spanPart(event, iso) {
  if (!event.all_day) return "single";
  const days = daysOf(event);
  if (days.length < 2 || !days.includes(iso)) return "single";
  if (iso === days[0]) return "start";
  if (iso === days[days.length - 1]) return "end";
  return "middle";
}

// Which of a day's (sorted) events to show when only `capacity` fit (ruling R17):
// past timed events are hidden first (earliest first), all-day events are never hidden
// for being past, then events are trimmed from the end. Order is kept.
// Returns {shown, hidden}; "+N more" should show `hidden`.
export function pickVisible(events, capacity, pastFn) {
  const n = events.length;
  const cap = Math.max(0, capacity);
  if (n <= cap) return { shown: events, hidden: 0 };
  let toHide = n - cap;
  const hide = new Set();
  for (const ev of events) {
    if (toHide === 0) break;
    if (!ev.all_day && pastFn(ev)) {
      hide.add(ev);
      toHide -= 1;
    }
  }
  for (let i = n - 1; i >= 0 && toHide > 0; i--) {
    if (!hide.has(events[i])) {
      hide.add(events[i]);
      toHide -= 1;
    }
  }
  return { shown: events.filter((ev) => !hide.has(ev)), hidden: hide.size };
}

// The Month page's rolling grid: Monday of the week containing todayISO, then `weeks`
// whole weeks (the caller clamps the count).
export function weekGrid(todayISO, weeks) {
  const weekday = (new Date(utcOf(todayISO)).getUTCDay() + 6) % 7; // Monday = 0
  const start = addDays(todayISO, -weekday);
  return Array.from({ length: 7 * weeks }, (_, i) => addDays(start, i));
}

// Multi-day all-day events in a Month week row are drawn as continuous bars. Each one gets
// a lane (vertical slot, 0 = top) for the whole row, so its pieces line up from cell to cell;
// a day renders its lanes first (empty lanes as spacers) and its other entries after them.
// Greedy interval packing: bars sorted by first day in the row, longer first, then by the
// usual event order; each takes the lowest lane free over its days. Lanes restart every
// week row (bars break at Sunday -> Monday). Events that are not multi-day all-day, or do
// not touch the row, get no lane.
export function assignLanes(weekDays, events) {
  const first = weekDays[0];
  const last = weekDays[weekDays.length - 1];
  const bars = [];
  for (const ev of events || []) {
    if (!ev.all_day) continue;
    const days = daysOf(ev);
    if (days.length < 2 || days[0] > last || days[days.length - 1] < first) continue;
    const s = Math.max(0, dayDiff(first, days[0]));
    const e = Math.min(weekDays.length - 1, dayDiff(first, days[days.length - 1]));
    bars.push({ ev, s, e });
  }
  bars.sort((a, b) => a.s - b.s || b.e - b.s - (a.e - a.s) || compare(a.ev, b.ev));
  const laneEnd = []; // last occupied column per lane
  const out = new Map();
  for (const { ev, s, e } of bars) {
    let lane = laneEnd.findIndex((end) => end < s);
    if (lane < 0) lane = laneEnd.length;
    laneEnd[lane] = e;
    out.set(ev, lane);
  }
  return out;
}

// One day's piece of a multi-day all-day bar at grid `column` (0 = Monday .. 6 = Sunday):
// title on the first piece in the row (event start, or Monday for a continuation), rounded
// corners only on the event's real ends, and the piece reaches into the gap towards a
// neighbouring piece in the same row (never past the row's edges).
export function barPiece(event, iso, column) {
  const part = spanPart(event, iso);
  const continuesLeft = part === "middle" || part === "end";
  const continuesRight = part === "start" || part === "middle";
  return {
    title: !continuesLeft || column === 0,
    roundL: !continuesLeft,
    roundR: !continuesRight,
    extL: continuesLeft && column > 0,
    extR: continuesRight && column < 6,
  };
}

// One day's (sorted) events with identical single-day all-day entries folded together:
// "Školka" for two kids becomes one entry. Entries are grouped by trimmed title; a group
// with two or more members becomes a copy of its first event plus
//   members: [{id, color}] (distinct member_ids, first-seen order)
//   events:  the folded source events
// at the first event's position. A group of one member (the same event twice, e.g. from
// two calendars) keeps only its first event. Timed and multi-day events, and single
// entries, are returned as the same objects, so Maps keyed by event (lanes) still work.
export function mergeSameAllDay(events) {
  const groups = new Map(); // title -> [events]
  const isSingleAllDay = (ev) => ev.all_day && daysOf(ev).length === 1;
  for (const ev of events || []) {
    if (!isSingleAllDay(ev)) continue;
    const key = String(ev.title ?? "").trim();
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(ev);
  }
  const out = [];
  for (const ev of events || []) {
    if (!isSingleAllDay(ev)) {
      out.push(ev);
      continue;
    }
    const group = groups.get(String(ev.title ?? "").trim());
    if (group[0] !== ev) continue; // folded into the first one
    const members = [];
    for (const g of group) {
      if (!members.some((m) => m.id === g.member_id)) members.push({ id: g.member_id, color: g.color });
    }
    out.push(members.length > 1 ? { ...ev, members, events: group } : ev);
  }
  return out;
}
