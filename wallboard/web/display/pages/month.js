// Month: a rolling Monday-first grid of `weeks` weeks (4..6, default 4) starting on the
// Monday of the current week, filling the page below the header (subtitle: the month(s)
// covered). Open grid on the chalkboard (mockup variant L, mesic-detail): each day starts
// with a small day number top-left (today: in a small red circle; the 1st of a month and
// the first cell add the short month name) with the Czech holiday name (pink) beside it,
// then up to `max_events_per_day` entries (default 5) as member-colour strips: timed ones a
// small time line above a title of up to 3 lines, all-day ones up to 2 lines. Every
// member's all-day entry is its own strip, even with identical titles ("Školka" twice).
// Past days are dimmed, weekend numbers tinted.
//
// Multi-day all-day events are continuous bars (lib/layout.js assignLanes + barPiece): each
// bar keeps one lane (vertical slot) across its week row, lanes come right after the day
// number (empty lanes as invisible spacers) and the other entries follow, so pieces line
// up. Bars stay one line: a 2-line title piece would be taller than its continuation pieces
// and the lanes below would no longer line up. A piece reaches into the grid gap towards its
// neighbours (the cell clips with a margin of half the gap); bars break at Sunday -> Monday
// and the Monday piece repeats the title. Bars are always drawn; the per-day limit and the
// overflow trim apply to the other entries (pickVisible: past timed events go first, then
// from the end) plus "+N more".
//
// Tapping a day (a tap, not a swipe: lib/gesture.js) opens the day window (mockup variant
// N) over the board: all-day chips, holiday, timed events with full titles, location and
// who. It closes on ×, on a tap on the veil, after IDLE_CLOSE_MS without a touch, and with
// the page. While open it holds the board (ctx.hold: no rotation, no night mode) and
// follows calendar/weather updates for the same day. View-only viewers may open it too.
//
// The grid is rebuilt only when the calendar payload, language, zone or local minute
// changes (checked on every store update and every 20 s).
import { renderHeader, safeColor } from "../header.js";
import { t, tn, getLanguage, fmtMonthRange, fmtWeekdayShort, fmtDayMonthShort, fmtLongDate } from "../../shared/i18n.js";
import { WEATHER_ICONS } from "../../shared/ha-format.js";
import { clampSetting } from "../lib/page-options.js";
import {
  localDate, noonOf, bucket, isPast, pickVisible, weekGrid, assignLanes, barPiece,
} from "../lib/layout.js";
import { readableText } from "../lib/color.js";
import { classifyGesture } from "../lib/gesture.js";
import { dayDetail, createIdleTimer } from "../lib/day-detail.js";
import { chipTime } from "./week.js";

const MINUTE_CHECK_MS = 20000;
export const IDLE_CLOSE_MS = 30000;
const DEFAULT_MAX = 5;
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
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function solid(node, color) {
  const c = safeColor(color);
  node.style.background = c;
  node.style.color = readableText(c);
}

// One coloured dot per member of a merged entry.
function memberDots(members, cls) {
  const dots = el("span", cls);
  for (const m of members) {
    const i = el("i");
    i.style.background = safeColor(m.color);
    dots.append(i);
  }
  return dots;
}

function bar(ev, iso, column) {
  const p = barPiece(ev, iso, column);
  const b = el("div", "wb-me bar", p.title ? ev.title ?? "" : "\u00a0");
  for (const k of ["roundL", "roundR", "extL", "extR"]) if (p[k]) b.classList.add(k);
  solid(b, ev.color);
  return b;
}

function entry(ev, iso, tz, now, today) {
  const e = el("div", "wb-me");
  const ti = el("span", "wb-me-ti");
  if (ev.members) {
    // Merged identical all-day entry: neutral strip, first member's colour on the left.
    e.classList.add("all", "merged");
    e.style.borderLeftColor = safeColor(ev.members[0].color);
    ti.append(memberDots(ev.members, "wb-me-dots"), ev.title ?? "");
    e.append(ti);
    return e;
  }
  solid(e, ev.color);
  if (ev.all_day) e.classList.add("all");
  // Earlier days are dimmed as a whole; only today's finished events fade on their own.
  else if (iso === today && isPast(ev, now, tz)) e.classList.add("past");
  const time = chipTime(ev, iso, tz);
  if (time) e.append(el("b", "", time));
  // Timed titles clamp to 3 lines, all-day ones to 2 (display.css).
  ti.textContent = ev.title ?? "";
  e.append(ti);
  return e;
}

const overflows = (box) => box.scrollHeight > box.clientHeight + 1;

// One day cell. `lanes` = the week row's Map event -> lane.
function dayCell(d) {
  const { iso, column, first, today, events, lanes, holiday, cap, tz, now, selected } = d;
  const c = el("div", "wb-mc");
  // A tap opens the day window; data-tap keeps the shell from paging on it.
  c.dataset.tap = "";
  c.dataset.iso = iso;
  if (iso < today) c.classList.add("past");
  if (isWeekend(iso)) c.classList.add("we");
  if (iso === today) c.classList.add("today");
  if (iso === selected) c.classList.add("sel");

  // Small number top-left (today's in the circle); the first cell and the 1st of a month
  // add the short month name ("2 lis"), but not beside today's circle; the holiday after.
  const head = el("div", "wb-mc-n");
  head.append(el("span", "wb-mc-d", String(Number(iso.slice(8)))));
  if ((iso.endsWith("-01") || first) && iso !== today) {
    head.append(el("small", "wb-mc-m", fmtDayMonthShort(noonOf(iso, tz)).month));
  }
  if (holiday) head.append(el("span", "wb-mc-hol", holiday));

  const barsHere = events.filter((ev) => lanes.has(ev));
  // Each member's all-day entry is its own strip here (the owner wants two "Školka"
  // strips, one per child); the day window still merges them (lib/day-detail.js).
  const others = events.filter((ev) => !lanes.has(ev));
  const laneRows = barsHere.length ? Math.max(...barsHere.map((ev) => lanes.get(ev))) + 1 : 0;
  const top = [head];
  for (let lane = 0; lane < laneRows; lane++) {
    const ev = barsHere.find((x) => lanes.get(x) === lane);
    top.push(ev ? bar(ev, iso, column) : el("div", "wb-me bar gap", "\u00a0"));
  }

  const past = (ev) => isPast(ev, now, tz);
  const fill = (capacity) => {
    const { shown, hidden } = pickVisible(others, capacity, past);
    const items = shown.map((ev) => entry(ev, iso, tz, now, today));
    if (hidden) items.push(el("div", "wb-mc-more", t("more_n", { n: hidden })));
    c.replaceChildren(...top, ...items);
  };
  let capacity = Math.max(0, cap - laneRows);
  fill(capacity);
  // Called once the grid is laid out: drop entries until the line clamps fit the cell.
  const fit = () => {
    while (capacity > 0 && overflows(c)) fill(--capacity);
  };
  return { c, fit };
}

// ---- day window ----

function detailSubtitle(info) {
  const sub = el("small", "wb-mdet-sub");
  const w = info.weather;
  const parts = [];
  if (w) {
    if (w.condition) sub.append(el("i", `mdi ${WEATHER_ICONS[w.condition] || "mdi-weather-partly-cloudy"}`));
    if (w.high !== null && w.low !== null) parts.push(t("high_low", { high: w.high, low: w.low }));
    else if (w.high !== null) parts.push(`${w.high}°`);
  }
  if (info.count) parts.push(tn("day_events", info.count));
  sub.append(parts.join("  ·  "));
  return sub;
}

function detailChip(ev) {
  const c = el("span", "wb-mdet-chip");
  if (ev.members) {
    c.classList.add("merged");
    c.style.borderLeftColor = safeColor(ev.members[0].color);
    c.append(memberDots(ev.members, "wb-mdet-dots"), ev.title ?? "");
  } else {
    solid(c, ev.color);
    c.textContent = ev.title ?? "";
  }
  return c;
}

function detailRow({ ev, time }, initials) {
  const r = el("div", "wb-mdet-row");
  r.append(el("span", "wb-mdet-tm", time));
  const who = el("span", "wb-mdet-who wb-initial", initials.get(ev.member_id) ?? "•");
  solid(who, ev.color);
  const ti = el("span", "wb-mdet-ti", ev.title ?? "");
  if (ev.location) ti.append(el("span", "wb-mdet-loc", ev.location));
  r.append(who, ti);
  return r;
}

// The sheet's content for `info` (dayDetail()).
function detailContent(info, tz, members) {
  const top = el("div", "wb-mdet-top");
  const h = el("h2", "", capitalize(fmtLongDate(noonOf(info.iso, tz))));
  h.append(detailSubtitle(info));
  const x = el("div", "wb-mdet-x", "×");
  x.setAttribute("role", "button");
  x.setAttribute("aria-label", t("day_close"));
  top.append(h, x);

  const body = el("div", "wb-mdet-body");
  if (info.holiday) body.append(el("div", "wb-mdet-hol", info.holiday));
  if (info.allDay.length) {
    const alls = el("div", "wb-mdet-alls");
    alls.append(...info.allDay.map(detailChip));
    body.append(alls);
  }
  if (info.timed.length) {
    const initials = new Map((members || []).map((m) => [m.id, m.initial]));
    const list = el("div", "wb-mdet-list");
    list.append(...info.timed.map((x) => detailRow(x, initials)));
    body.append(list);
  }
  if (!info.count) body.append(el("div", "wb-mdet-empty", t("day_no_events")));
  return [top, body, el("div", "wb-mdet-hint", t("day_hint"))];
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

    // Day window: {iso, overlay, sheet, release, key} while open.
    let detail = null;
    const idle = createIdleTimer({ ms: IDLE_CLOSE_MS, onExpire: () => closeDetail() });

    function fillDetail(state) {
      const tz = state.config?.timezone || "Europe/Prague";
      const info = dayDetail(state.calendar?.events || [], detail.iso, tz, state.calendar?.holidays, state.weather);
      const body = detail.sheet.querySelector(".wb-mdet-body");
      const scroll = body ? body.scrollTop : 0;
      detail.sheet.replaceChildren(...detailContent(info, tz, state.config?.members));
      detail.sheet.querySelector(".wb-mdet-body").scrollTop = scroll;
      detail.key = detailKey(state);
    }
    const detailKey = (state) => ({
      calendar: state.calendar, weather: state.weather, config: state.config, lang: getLanguage(),
    });
    const sameDetail = (a, b) =>
      a.calendar === b.calendar && a.weather === b.weather && a.config === b.config && a.lang === b.lang;

    function markSelected(iso) {
      for (const c of grid.querySelectorAll(".wb-mc.sel")) c.classList.remove("sel");
      if (iso) grid.querySelector(`.wb-mc[data-iso="${iso}"]`)?.classList.add("sel");
    }

    function openDetail(iso) {
      const overlay = el("div", "wb-mdet");
      overlay.dataset.tap = "";
      const sheet = el("div", "wb-mdet-sheet");
      overlay.append(sheet);
      detail = { iso, overlay, sheet, release: ctx.hold?.() ?? null, key: null };
      fillDetail(ctx.store.get());
      root.append(overlay);
      markSelected(iso);
      idle.start();
    }

    function closeDetail() {
      idle.stop();
      if (!detail) return;
      detail.release?.();
      detail.overlay.remove();
      detail = null;
      markSelected(null);
    }

    // Taps: classified like the shell does (a swipe on a cell still pages, the shell
    // handles it). Any touch inside the open window restarts its idle timer.
    let press = null;
    const onDown = (e) => {
      if (!e.isPrimary) return;
      press = { x: e.clientX, y: e.clientY, t: Date.now(), target: e.target };
      if (detail) idle.poke();
    };
    const onUp = (e) => {
      if (!e.isPrimary || !press) return;
      const p = press;
      press = null;
      if (classifyGesture({ dx: e.clientX - p.x, dy: e.clientY - p.y, dt: Date.now() - p.t }) !== "tap") return;
      const target = p.target;
      if (detail) {
        if (target.closest?.(".wb-mdet-x") || !target.closest?.(".wb-mdet-sheet")) closeDetail();
        return;
      }
      const cell = target.closest?.(".wb-mc[data-iso]");
      if (cell && grid.contains(cell)) openDetail(cell.dataset.iso);
    };
    const onCancel = () => (press = null);
    const onScroll = () => detail && idle.poke();
    root.addEventListener("pointerdown", onDown);
    root.addEventListener("pointerup", onUp);
    root.addEventListener("pointercancel", onCancel);
    root.addEventListener("scroll", onScroll, true);

    const same = (a, b) =>
      a && b && a.tz === b.tz && a.minute === b.minute && a.calendar === b.calendar && a.lang === b.lang;

    function render(state, force = false) {
      const now = Date.now();
      const tz = state.config?.timezone || "Europe/Prague";
      const today = localDate(now, tz);
      const days = weekGrid(today, weeks);
      const subtitle = fmtMonthRange(noonOf(days[0], tz), noonOf(days[days.length - 1], tz));
      renderHeader(header, state, { titleKey: "month", subtitle });
      // Live updates keep the window open on the same day.
      if (detail && !sameDetail(detail.key, detailKey(state))) fillDetail(state);
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
            holiday: typeof holidays[iso] === "string" ? holidays[iso] : "", cap, tz, now, selected: detail?.iso,
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
        closeDetail();
        root.removeEventListener("pointerdown", onDown);
        root.removeEventListener("pointerup", onUp);
        root.removeEventListener("pointercancel", onCancel);
        root.removeEventListener("scroll", onScroll, true);
        clearInterval(timer);
        ro?.disconnect();
        document.fonts?.removeEventListener?.("loadingdone", refit);
        root.innerHTML = "";
      },
    };
  },
};
