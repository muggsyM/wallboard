// "Next 7 days" (mockup "tyden" variant W2): seven rows starting today, each with weekday,
// day number and that day's forecast (☂ when rain is likely), then the day's events: all-day
// ones as small chips side by side (member-colour edge + the member's initial), below them
// the timed ones as lines (time, initial dot, title, location). Nothing is merged; past
// events are faded. Rows are as tall as their content and share the spare height; when the
// week does not fit, the fullest day is trimmed first (pickVisible: past timed events go
// first, then from the end) plus "+N more". Tapping a day opens the day window
// (lib/day-window.js).
//
// Rows are rebuilt only when the calendar/weather payload, members, language, zone or local
// minute changes (checked on every store update and every 20 s); HA deltas only touch the
// header.
import { renderHeader, safeColor } from "../header.js";
import { t, getLanguage, fmtTime, fmtShortDayMonth, fmtWeekdayShort } from "../../shared/i18n.js";
import { WEATHER_ICONS } from "../../shared/ha-format.js";
import { localDate, addDays, noonOf, bucket, daysOf, isPast, pickVisible } from "../lib/layout.js";
import { createDayWindow } from "../lib/day-window.js";

const DAYS = 7;
const MINUTE_CHECK_MS = 20000;
const RAIN_MIN_PERCENT = 50;

// cs "7.–13. 11." / "28. 11.–4. 12."; en "7–13/11" / "28/11–4/12".
export function weekSubtitle(firstISO, lastISO, tz) {
  const last = fmtShortDayMonth(noonOf(lastISO, tz));
  if (firstISO.slice(0, 7) !== lastISO.slice(0, 7)) return `${fmtShortDayMonth(noonOf(firstISO, tz))}–${last}`;
  const day = Number(firstISO.slice(8));
  return getLanguage() === "cs" ? `${day}.–${last}` : `${day}–${last}`;
}

const isWeekend = (iso) => {
  const d = new Date(`${iso}T00:00:00Z`).getUTCDay();
  return d === 0 || d === 6;
};

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function weatherFor(weather, iso) {
  const d = (weather?.daily || []).find((x) => x?.date === iso);
  if (!d) return null;
  const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Math.round(Number(v)));
  const high = num(d.high);
  const low = num(d.low);
  const temps = high !== null && low !== null ? t("high_low", { high, low }) : high !== null ? `${high}°` : "";
  const rain = (num(d.precip_probability) ?? 0) >= RAIN_MIN_PERCENT;
  return { icon: WEATHER_ICONS[d.condition] || "mdi-weather-partly-cloudy", temps, rain };
}

// Time shown on a chip: start time on the start day; on later days of a timed event the
// end time ("–01:00") on its last day and nothing in between. Also used by the Month page.
export function chipTime(ev, iso, tz) {
  if (ev.all_day) return "";
  const days = daysOf(ev, tz);
  if (iso === days[0]) return fmtTime(new Date(ev.start));
  return iso === days[days.length - 1] ? `–${fmtTime(new Date(ev.end))}` : "";
}

const overflows = (box) => box.scrollHeight > box.clientHeight + 1;

function initialDot(ev, initials) {
  const c = safeColor(ev.color);
  const d = el("span", "wb-wini", initials.get(ev.member_id) ?? "•");
  d.style.background = c;
  return d;
}

// An all-day chip: member-colour edge, initial, title.
function allDayChip(ev, initials, now, tz) {
  const c = el("span", "wb-wad");
  c.style.borderLeftColor = safeColor(ev.color);
  if (isPast(ev, now, tz)) c.classList.add("past");
  c.append(initialDot(ev, initials), el("span", "wb-wad-ti", ev.title ?? ""));
  return c;
}

// A timed line: time, initial, title and location.
function timedLine(ev, iso, tz, now, initials) {
  const r = el("div", "wb-wtl");
  if (isPast(ev, now, tz)) r.classList.add("past");
  r.append(el("span", "wb-wtl-t", chipTime(ev, iso, tz)), initialDot(ev, initials));
  const ti = el("span", "wb-wtl-ti", ev.title ?? "");
  if (ev.location) ti.append(el("span", "wb-wtl-loc", ev.location));
  r.append(ti);
  return r;
}

// Fill a day's event box with at most `cap` of its events (all when cap >= length).
function fillDay(box, d, cap, tz, now, initials) {
  const { shown, hidden } = cap >= d.events.length
    ? { shown: d.events, hidden: 0 }
    : pickVisible(d.events, cap, (ev) => isPast(ev, now, tz));
  const parts = [];
  const alls = shown.filter((ev) => ev.all_day);
  if (alls.length) {
    const chips = el("div", "wb-wads");
    chips.append(...alls.map((ev) => allDayChip(ev, initials, now, tz)));
    parts.push(chips);
  }
  parts.push(...shown.filter((ev) => !ev.all_day).map((ev) => timedLine(ev, d.iso, tz, now, initials)));
  if (hidden) parts.push(el("div", "wb-more", t("more_n", { n: hidden })));
  if (!d.events.length) parts.push(el("div", "wb-empty", t("no_events")));
  box.replaceChildren(...parts);
  d.cap = Math.min(cap, d.events.length);
}

function row(iso, today, events, weather, tz) {
  const r = el("div", "wb-wrow");
  r.dataset.iso = iso;
  r.dataset.tap = ""; // a tap opens the day window instead of paging
  if (iso === today) r.classList.add("today");
  if (isWeekend(iso)) r.classList.add("we");
  const wd = el("div", "wb-wd");
  wd.append(el("div", "wb-wd-n", iso === today ? t("today") : fmtWeekdayShort(noonOf(iso, tz))));
  wd.append(el("div", "wb-wd-d", String(Number(iso.slice(8)))));
  const wx = weatherFor(weather, iso);
  if (wx) {
    const w = el("div", "wb-wd-w");
    w.append(el("i", `mdi ${wx.icon}`), el("span", "", wx.temps));
    if (wx.rain) w.append(el("i", "mdi mdi-umbrella wb-wd-rain"));
    wd.append(w);
  }
  const box = el("div", "wb-wev");
  r.append(wd, box);
  return { r, box, events, iso, cap: events.length };
}

export default {
  type: "week",
  titleKey: "this_week",
  settings: [],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-page-header"></div><div class="wb-week"></div>`;
    const header = root.querySelector(".wb-page-header");
    const list = root.querySelector(".wb-week");
    let last = null; // key of the last row build
    let alive = true;
    const win = createDayWindow(root, ctx, {
      cells: ".wb-wrow[data-iso]",
      within: list,
      onSelect: (iso) => {
        for (const r of list.children) r.classList.toggle("sel", r.dataset.iso === iso);
      },
    });

    const same = (a, b) =>
      a && b && a.tz === b.tz && a.minute === b.minute && a.calendar === b.calendar && a.weather === b.weather &&
      a.lang === b.lang && a.members === b.members;

    function render(state, force = false) {
      const now = Date.now();
      const tz = state.config?.timezone || "Europe/Prague";
      const today = localDate(now, tz);
      renderHeader(header, state, { titleKey: "this_week", subtitle: weekSubtitle(today, addDays(today, DAYS - 1), tz) });
      win.update(state);
      const members = state.config?.members;
      const key = { tz, minute: Math.floor(now / 60000), calendar: state.calendar, weather: state.weather, lang: getLanguage(), members };
      if (!force && same(key, last)) return;
      last = key;
      const days = bucket(state.calendar?.events || [], today, DAYS, tz);
      const initials = new Map((members || []).map((m) => [m.id, m.initial]));
      const rows = [...days].map(([iso, evs]) => row(iso, today, evs, state.weather, tz));
      for (const x of rows) fillDay(x.box, x, x.events.length, tz, now, initials);
      list.replaceChildren(...rows.map((x) => x.r));
      for (const x of rows) x.r.classList.toggle("sel", x.iso === win.iso());
      // After layout: while the week overflows, take one entry from the fullest day.
      while (overflows(list)) {
        const fullest = rows.reduce((a, b) => (b.cap > a.cap ? b : a));
        if (fullest.cap <= 1) break;
        fillDay(fullest.box, fullest, fullest.cap - 1, tz, now, initials);
      }
    }

    const refit = () => alive && render(ctx.store.get(), true);
    const timer = setInterval(() => render(ctx.store.get()), MINUTE_CHECK_MS);
    // Row height depends on the page size and the web font; re-measure when either changes.
    let ro = null;
    let lastHeight = 0;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(([entry]) => {
        const h = Math.round(entry.contentRect.height);
        if (h !== lastHeight) {
          const first = lastHeight === 0;
          lastHeight = h;
          if (!first) refit();
        }
      });
      ro.observe(list);
    }
    if (document.fonts && document.fonts.status !== "loaded") document.fonts.ready.then(refit);
    document.fonts?.addEventListener?.("loadingdone", refit);

    render(ctx.store.get());
    return {
      update: (state) => render(state),
      destroy() {
        alive = false;
        win.destroy();
        clearInterval(timer);
        ro?.disconnect();
        document.fonts?.removeEventListener?.("loadingdone", refit);
        root.innerHTML = "";
      },
    };
  },
};
