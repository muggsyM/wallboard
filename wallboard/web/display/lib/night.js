// Night mode window check. Pure: no DOM. `now` is ms or a Date; `night` is the config
// {enabled, start: "HH:MM", end: "HH:MM"}; `tz` is an IANA zone. The window is
// [start, end) in local time of `tz` and wraps midnight when end <= start.

const FALLBACK_TZ = "Europe/Prague";
const formatters = new Map();

function formatter(tz) {
  let f = formatters.get(tz);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: tz });
    } catch {
      f = formatter(FALLBACK_TZ);
    }
    formatters.set(tz, f);
  }
  return f;
}

export function parseHHMM(s) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(s ?? ""));
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  return h < 24 && min < 60 ? h * 60 + min : null;
}

export function localMinutes(now, tz) {
  const parts = formatter(tz || FALLBACK_TZ).formatToParts(new Date(now));
  const get = (type) => Number(parts.find((p) => p.type === type).value);
  return get("hour") * 60 + get("minute");
}

export function isNight(now, night, tz) {
  if (!night?.enabled) return false;
  const start = parseHHMM(night.start);
  const end = parseHHMM(night.end);
  if (start === null || end === null || start === end) return false;
  const m = localMinutes(now, tz);
  return start < end ? m >= start && m < end : m >= start || m < end;
}
