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
//   monthGrid(todayISO)               -> 42 iso dates, Monday-first, 6 weeks

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

export function monthGrid(todayISO) {
  const first = `${todayISO.slice(0, 8)}01`;
  const weekday = (new Date(utcOf(first)).getUTCDay() + 6) % 7; // Monday = 0
  const start = addDays(first, -weekday);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}
