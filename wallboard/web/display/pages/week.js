// "Next 7 days": seven equal-height rows starting today, each with weekday, day number,
// that day's forecast and the day's events as chips (member colour on the left border,
// all-day events solid, past events faded). A row whose chips do not fit drops chips
// from the end until "+N more" fits. Look: mockups/portrait-v1.html screen 2.
//
// Re-renders only when the inputs change (calendar/weather payloads, language, today,
// which events are past), checked on every store update and once a minute.
import { renderHeader, safeColor } from "../header.js";
import { t, getLanguage, fmtTime, fmtShortDayMonth, fmtWeekdayShort } from "../../shared/i18n.js";
import { WEATHER_ICONS } from "../../shared/ha-format.js";
import { localDate, addDays, noonOf, bucket, daysOf, isPast } from "../lib/layout.js";

const DAYS = 7;
const MINUTE_CHECK_MS = 20000;

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
  return { icon: WEATHER_ICONS[d.condition] || "mdi-weather-partly-cloudy", temps };
}

// Time shown on a chip: start time on the start day; on later days of a timed event the
// end time ("–01:00") on its last day and nothing in between.
function chipTime(ev, iso, tz) {
  if (ev.all_day) return "";
  const days = daysOf(ev, tz);
  if (iso === days[0]) return fmtTime(new Date(ev.start));
  return iso === days[days.length - 1] ? `–${fmtTime(new Date(ev.end))}` : "";
}

function chip(ev, iso, tz, now) {
  const c = el("div", "wb-chip");
  const color = safeColor(ev.color);
  if (ev.all_day) {
    c.classList.add("all");
    c.style.background = color;
  } else {
    c.style.borderLeftColor = color;
  }
  if (isPast(ev, now, tz)) c.classList.add("past");
  const time = chipTime(ev, iso, tz);
  if (time) c.append(el("b", "", time));
  c.append(el("span", "wb-chip-ti", ev.title ?? ""));
  return c;
}

const overflows = (box) => box.scrollHeight > box.clientHeight + 1;

// Drop chips from the end until the remaining ones plus "+N more" fit (at least one stays).
function fitChips(box) {
  const chips = [...box.children];
  if (chips.length < 2 || !overflows(box)) return;
  let hidden = 0;
  let more = null;
  while (chips.length > 1 && (hidden === 0 || overflows(box))) {
    chips.pop().remove();
    hidden += 1;
    more?.remove();
    more = el("span", "wb-more", `· ${t("more_n", { n: hidden })}`);
    chips[chips.length - 1].append(more);
  }
}

function row(iso, today, events, weather, tz, now) {
  const r = el("div", "wb-wrow");
  if (iso === today) r.classList.add("today");
  if (isWeekend(iso)) r.classList.add("we");
  const wd = el("div", "wb-wd");
  wd.append(el("div", "wb-wd-n", iso === today ? t("today") : fmtWeekdayShort(noonOf(iso, tz))));
  wd.append(el("div", "wb-wd-d", String(Number(iso.slice(8)))));
  const wx = weatherFor(weather, iso);
  if (wx) {
    const w = el("div", "wb-wd-w");
    w.append(el("i", `mdi ${wx.icon}`), el("span", "", wx.temps));
    wd.append(w);
  }
  const chips = el("div", "wb-chips");
  if (events.length) chips.append(...events.map((ev) => chip(ev, iso, tz, now)));
  else chips.append(el("div", "wb-empty", t("no_events")));
  r.append(wd, chips);
  return { r, chips };
}

export default {
  type: "week",
  titleKey: "this_week",
  settings: [],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-page-header"></div><div class="wb-week"></div>`;
    const header = root.querySelector(".wb-page-header");
    const list = root.querySelector(".wb-week");
    let last = null; // inputs of the last render
    let alive = true;

    function inputs(state, now) {
      const tz = state.config?.timezone || "Europe/Prague";
      const today = localDate(now, tz);
      const days = bucket(state.calendar?.events || [], today, DAYS, tz);
      const past = [];
      for (const evs of days.values()) for (const ev of evs) if (isPast(ev, now, tz)) past.push(ev.id);
      return { tz, today, days, calendar: state.calendar, weather: state.weather, lang: getLanguage(), past: past.join("|") };
    }

    const same = (a, b) =>
      a && b && a.tz === b.tz && a.today === b.today && a.calendar === b.calendar && a.weather === b.weather &&
      a.lang === b.lang && a.past === b.past;

    function render(state, force = false) {
      const now = Date.now();
      const inp = inputs(state, now);
      renderHeader(header, state, { titleKey: "this_week", subtitle: weekSubtitle(inp.today, addDays(inp.today, DAYS - 1), inp.tz) });
      if (!force && same(inp, last)) return;
      last = inp;
      const rows = [...inp.days].map(([iso, evs]) => row(iso, inp.today, evs, inp.weather, inp.tz, now));
      list.replaceChildren(...rows.map((x) => x.r));
      for (const x of rows) fitChips(x.chips); // after layout: measuring needs the rows in place
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
    document.fonts?.ready.then(refit);
    document.fonts?.addEventListener?.("loadingdone", refit);

    render(ctx.store.get());
    return {
      update: (state) => render(state),
      destroy() {
        alive = false;
        clearInterval(timer);
        ro?.disconnect();
        document.fonts?.removeEventListener?.("loadingdone", refit);
        root.innerHTML = "";
      },
    };
  },
};
