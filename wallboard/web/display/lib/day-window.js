// The day window (mockup variant N), shared by the Month grid and the Today next-day cards:
// tap a day → a sheet over the board with that day's all-day chips, holiday, weather and
// every timed event (full title, location, who). It closes on ×, on a tap on the veil,
// after IDLE_CLOSE_MS without a touch, and with the page. While open it holds the board
// (ctx.hold: no rotation, no night mode) and follows calendar/weather updates for the same
// day. View-only viewers may open it too.
//
//   createDayWindow(root, ctx, {cells, within, onSelect})
//     cells:    selector of the tappable day elements (each with data-iso)
//     within:   only cells inside this element count (default root)
//     onSelect: called with the open day's iso, or null when the window closes
//   -> {iso(), open(iso), close(), update(state), destroy()}
//
// Taps are classified like the shell does (lib/gesture.js): a swipe on a cell still pages.
import { safeColor } from "../header.js";
import { t, tn, getLanguage, fmtLongDate } from "../../shared/i18n.js";
import { WEATHER_ICONS } from "../../shared/ha-format.js";
import { noonOf } from "./layout.js";
import { readableText } from "./color.js";
import { classifyGesture } from "./gesture.js";
import { dayDetail, createIdleTimer } from "./day-detail.js";

export const IDLE_CLOSE_MS = 30000;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function solid(node, color) {
  const c = safeColor(color);
  node.style.background = c;
  node.style.color = readableText(c);
}

const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function memberDots(members, cls) {
  const dots = el("span", cls);
  for (const m of members) {
    const i = el("i");
    i.style.background = safeColor(m.color);
    dots.append(i);
  }
  return dots;
}

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

const detailKey = (state) => ({ calendar: state.calendar, weather: state.weather, config: state.config, lang: getLanguage() });
const sameDetail = (a, b) => a.calendar === b.calendar && a.weather === b.weather && a.config === b.config && a.lang === b.lang;

export function createDayWindow(root, ctx, { cells, within = root, onSelect = () => {} }) {
  let detail = null; // {iso, overlay, sheet, release, key} while open
  const idle = createIdleTimer({ ms: IDLE_CLOSE_MS, onExpire: () => close() });

  function fill(state) {
    const tz = state.config?.timezone || "Europe/Prague";
    const info = dayDetail(state.calendar?.events || [], detail.iso, tz, state.calendar?.holidays, state.weather);
    const body = detail.sheet.querySelector(".wb-mdet-body");
    const scroll = body ? body.scrollTop : 0;
    detail.sheet.replaceChildren(...detailContent(info, tz, state.config?.members));
    detail.sheet.querySelector(".wb-mdet-body").scrollTop = scroll;
    detail.key = detailKey(state);
  }

  function open(iso) {
    close();
    const overlay = el("div", "wb-mdet");
    overlay.dataset.tap = "";
    const sheet = el("div", "wb-mdet-sheet");
    overlay.append(sheet);
    detail = { iso, overlay, sheet, release: ctx.hold?.() ?? null, key: null };
    fill(ctx.store.get());
    root.append(overlay);
    onSelect(iso);
    idle.start();
  }

  function close() {
    idle.stop();
    if (!detail) return;
    detail.release?.();
    detail.overlay.remove();
    detail = null;
    onSelect(null);
  }

  // Any touch inside the open window restarts its idle timer.
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
      if (target.closest?.(".wb-mdet-x") || !target.closest?.(".wb-mdet-sheet")) close();
      return;
    }
    const cell = target.closest?.(cells);
    if (cell && within.contains(cell) && cell.dataset.iso) open(cell.dataset.iso);
  };
  const onCancel = () => (press = null);
  const onScroll = () => detail && idle.poke();
  root.addEventListener("pointerdown", onDown);
  root.addEventListener("pointerup", onUp);
  root.addEventListener("pointercancel", onCancel);
  root.addEventListener("scroll", onScroll, true);

  return {
    iso: () => detail?.iso ?? null,
    open,
    close,
    // Live updates keep the window open on the same day.
    update(state) {
      if (detail && !sameDetail(detail.key, detailKey(state))) fill(state);
    },
    destroy() {
      close();
      root.removeEventListener("pointerdown", onDown);
      root.removeEventListener("pointerup", onUp);
      root.removeEventListener("pointercancel", onCancel);
      root.removeEventListener("scroll", onScroll, true);
    },
  };
}
