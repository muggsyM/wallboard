// The Month page's day detail window (tap a day): what it shows, and its idle auto-close.
// Pure: no DOM. Time labels use the i18n formatters (current language and zone).
//
//   dayDetail(events, iso, tz, holidays, weather)
//     -> {iso, allDay, timed: [{ev, time}], count, holiday, weather}
//        allDay:  the day's all-day events (multi-day ones too), identical single-day
//                 titles of several members merged (layout.js mergeSameAllDay)
//        timed:   the day's timed events by start, each with its time label:
//                 "HH:MM" (starts and ends this day), "HH:MM–" (starts this day, ends
//                 later), "–HH:MM" (started earlier, ends this day), "" (runs through it)
//        count:   allDay.length + timed.length (a merged entry counts once)
//        holiday: the day's holiday name or ""
//        weather: {condition, high, low} from weather.daily for that date (high/low
//                 rounded, null when missing), or null
//   createIdleTimer({ms, onExpire, setTimeout, clearTimeout})
//     -> {start(), poke(), stop(), running()}: onExpire() once `ms` pass without a poke().
import { bucket, localDate, mergeSameAllDay } from "./layout.js";
import { fmtTime } from "../../shared/i18n.js";

const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Math.round(Number(v)));

function weatherOn(weather, iso) {
  const d = (weather?.daily || []).find((x) => x?.date === iso);
  if (!d) return null;
  return { condition: d.condition ?? null, high: num(d.high), low: num(d.low) };
}

function timeLabel(ev, iso, startDay, endDay) {
  const start = fmtTime(new Date(ev.start));
  const end = fmtTime(new Date(ev.end));
  if (startDay === iso && endDay === iso) return start;
  if (startDay === iso) return `${start}–`;
  if (endDay === iso) return `–${end}`;
  return "";
}

export function dayDetail(events, iso, tz, holidays, weather) {
  const day = bucket(events || [], iso, 1, tz).get(iso);
  const allDay = mergeSameAllDay(day.filter((ev) => ev.all_day));
  const timed = day
    .filter((ev) => !ev.all_day)
    .map((ev) => {
      // Same day rule as layout.js daysOf(): an event ending at midnight stays on its day.
      const startDay = localDate(Date.parse(ev.start), tz);
      const endDay = localDate(Math.max(Date.parse(ev.start), Date.parse(ev.end) - 1), tz);
      return { ev, time: timeLabel(ev, iso, startDay, endDay) };
    });
  const holiday = typeof holidays?.[iso] === "string" ? holidays[iso] : "";
  return { iso, allDay, timed, count: allDay.length + timed.length, holiday, weather: weatherOn(weather, iso) };
}

export function createIdleTimer({
  ms,
  onExpire,
  setTimeout: later = globalThis.setTimeout.bind(globalThis),
  clearTimeout: cancel = globalThis.clearTimeout.bind(globalThis),
}) {
  let timer = null;
  const stop = () => {
    if (timer !== null) cancel(timer);
    timer = null;
  };
  const start = () => {
    stop();
    timer = later(() => {
      timer = null;
      onExpire();
    }, ms);
  };
  return { start, poke: () => timer !== null && start(), stop, running: () => timer !== null };
}
