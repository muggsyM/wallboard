// Month: a rolling Monday-first grid of `weeks` weeks (4..6, default 4) starting on the
// Monday of the current week, filling the page below the header (subtitle: the month(s)
// covered). Open grid on the chalkboard (mockup variant J): each day shows a big centred
// number (today: in a red circle; the 1st of a month and the first cell add the short month
// name, except on today's circle), the Czech holiday name (pink) and up to
// `max_events_per_day` entries (default 4) as solid member-colour strips: a small time line
// above a title of up to 3 lines; all-day events on one line. Past days are dimmed, weekend
// numbers tinted. No legend and no forecast icons (the header has the current weather).
//
// Multi-day all-day events are continuous bars (lib/layout.js assignLanes + barPiece): each
// bar keeps one lane (vertical slot) across its week row, lanes come first in a cell (empty
// lanes as invisible spacers) and the holiday label and other entries follow, so pieces line
// up. A piece reaches into the grid gap towards its neighbours (the cell clips with a
// margin of half the gap); bars break at Sunday -> Monday and the Monday piece repeats the
// title. Bars are always drawn; the per-day limit and the overflow trim apply to the other
// entries (pickVisible: past timed events go first, then from the end) plus "+N more".
//
// The grid is rebuilt only when the calendar payload, language, zone or local minute
// changes (checked on every store update and every 20 s).
import { renderHeader, safeColor } from "../header.js";
import { t, getLanguage, fmtMonthRange, fmtWeekdayShort, fmtDayMonthShort } from "../../shared/i18n.js";
import { clampSetting } from "../lib/page-options.js";
import { localDate, noonOf, bucket, isPast, pickVisible, weekGrid, assignLanes, barPiece } from "../lib/layout.js";
import { readableText } from "../lib/color.js";
import { chipTime } from "./week.js";

const MINUTE_CHECK_MS = 20000;
const DEFAULT_MAX = 4;
const DEFAULT_WEEKS = 4;
export const clampMaxEvents = (v) => clampSetting(v, 1, 6, DEFAULT_MAX);
export const clampWeeks = (v) => clampSetting(v, 4, 6, DEFAULT_WEEKS);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

const isWeekend = (iso) => {
  const d = new Date(`${iso}T00:00:00Z`).getUTCDay();
  return d === 0 || d === 6;
};

function solid(node, ev) {
  const color = safeColor(ev.color);
  node.style.background = color;
  node.style.color = readableText(color);
}

function bar(ev, iso, column) {
  const p = barPiece(ev, iso, column);
  const b = el("div", "wb-me all bar", p.title ? ev.title ?? "" : " ");
  for (const k of ["roundL", "roundR", "extL", "extR"]) if (p[k]) b.classList.add(k);
  solid(b, ev);
  return b;
}

function entry(ev, iso, tz, now, today) {
  const e = el("div", "wb-me");
  solid(e, ev);
  if (ev.all_day) e.classList.add("all");
  // Earlier days are dimmed as a whole; only today's finished events fade on their own.
  else if (iso === today && isPast(ev, now, tz)) e.classList.add("past");
  const time = chipTime(ev, iso, tz);
  if (time) e.append(el("b", "", time));
  // Solid all-day entries are one nowrap line; timed titles clamp to 3 lines.
  e.append(ev.all_day ? ev.title ?? "" : el("span", "wb-me-ti", ev.title ?? ""));
  return e;
}

const overflows = (box) => box.scrollHeight > box.clientHeight + 1;

// One day cell. `lanes` = the week row's Map event -> lane.
function dayCell(d) {
  const { iso, column, first, today, events, lanes, holiday, cap, tz, now } = d;
  const c = el("div", "wb-mc");
  if (iso < today) c.classList.add("past");
  if (isWeekend(iso)) c.classList.add("we");
  if (iso === today) c.classList.add("today");

  // The number alone (today's sits in the circle); the first cell and the 1st of a month
  // add the short month name beside it ("2 lis"), but not in today's circle.
  const head = el("div", "wb-mc-n");
  head.append(el("span", "", String(Number(iso.slice(8)))));
  if ((iso.endsWith("-01") || first) && iso !== today) {
    head.append(el("small", "wb-mc-m", fmtDayMonthShort(noonOf(iso, tz)).month));
  }

  const barsHere = events.filter((ev) => lanes.has(ev));
  const others = events.filter((ev) => !lanes.has(ev));
  const laneRows = barsHere.length ? Math.max(...barsHere.map((ev) => lanes.get(ev))) + 1 : 0;
  const top = [head];
  for (let lane = 0; lane < laneRows; lane++) {
    const ev = barsHere.find((x) => lanes.get(x) === lane);
    top.push(ev ? bar(ev, iso, column) : el("div", "wb-me all gap", " "));
  }
  if (holiday) top.push(el("div", "wb-mc-hol", holiday));

  const past = (ev) => isPast(ev, now, tz);
  const fill = (capacity) => {
    const { shown, hidden } = pickVisible(others, capacity, past);
    const items = shown.map((ev) => entry(ev, iso, tz, now, today));
    if (hidden) items.push(el("div", "wb-mc-more", t("more_n", { n: hidden })));
    c.replaceChildren(...top, ...items);
  };
  let capacity = Math.max(0, cap - laneRows);
  fill(capacity);
  // Called once the grid is laid out: drop entries until the 3-line clamps fit the cell.
  const fit = () => {
    while (capacity > 0 && overflows(c)) fill(--capacity);
  };
  return { c, fit };
}

export default {
  type: "month",
  titleKey: "month",
  settings: [
    { key: "weeks", type: "number", labelKey: "weeks_shown", default: DEFAULT_WEEKS, min: 4, max: 6 },
    { key: "max_events_per_day", type: "number", labelKey: "max_events_per_day", default: DEFAULT_MAX, min: 1, max: 6 },
  ],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-page-header"></div><div class="wb-month"></div>`;
    const header = root.querySelector(".wb-page-header");
    const grid = root.querySelector(".wb-month");
    const cap = clampMaxEvents(ctx.options?.max_events_per_day);
    const weeks = clampWeeks(ctx.options?.weeks);
    grid.style.setProperty("--wb-mweeks", String(weeks));
    let last = null; // key of the last grid build
    let alive = true;

    const same = (a, b) =>
      a && b && a.tz === b.tz && a.minute === b.minute && a.calendar === b.calendar && a.lang === b.lang;

    function render(state, force = false) {
      const now = Date.now();
      const tz = state.config?.timezone || "Europe/Prague";
      const today = localDate(now, tz);
      const days = weekGrid(today, weeks);
      const subtitle = fmtMonthRange(noonOf(days[0], tz), noonOf(days[days.length - 1], tz));
      renderHeader(header, state, { titleKey: "month", subtitle });
      const key = { tz, minute: Math.floor(now / 60000), calendar: state.calendar, lang: getLanguage() };
      if (!force && same(key, last)) return;
      last = key;

      const byDay = bucket(state.calendar?.events || [], days[0], days.length, tz);
      const holidays = state.calendar?.holidays || {};
      const heads = days.slice(0, 7).map((iso) => el("div", "wb-mh", fmtWeekdayShort(noonOf(iso, tz))));
      const cells = [];
      for (let w = 0; w < weeks; w++) {
        const week = days.slice(w * 7, w * 7 + 7);
        const lanes = assignLanes(week, [...new Set(week.flatMap((iso) => byDay.get(iso)))]);
        week.forEach((iso, column) => {
          cells.push(dayCell({
            iso, column, first: w === 0 && column === 0, today, events: byDay.get(iso), lanes,
            holiday: typeof holidays[iso] === "string" ? holidays[iso] : "", cap, tz, now,
          }));
        });
      }
      grid.replaceChildren(...heads, ...cells.map((x) => x.c));
      // After layout: measuring needs the cells in place.
      for (const x of cells) x.fit();
    }

    const refit = () => alive && render(ctx.store.get(), true);
    const timer = setInterval(() => render(ctx.store.get()), MINUTE_CHECK_MS);
    // Cell height depends on the page size and the web fonts; re-measure when either changes.
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
      ro.observe(grid);
    }
    if (document.fonts && document.fonts.status !== "loaded") document.fonts.ready.then(refit);
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
