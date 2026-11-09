// Today ("C · Family planner"): the large header (clock, long date, weather, today's
// high/low and the next-rain hint, who's home), a weather picture, today's
// timeline, cards for the next 3 days and the quick tiles row.
// Look: mockups/portrait-v1.html screen 1.
//
// - Weather picture (mockup "prani", Dnes – počasí): a picture from the owner's weather
//   gallery (state.weather_pics) whose flag matches the current condition (header.js
//   currentWeather; lib/weather-pic.js maps HA conditions to flags with fallbacks). No text
//   on it: the header already shows the weather. A random matching picture per visit, kept
//   while it still matches; a new one when the condition changes. No matching picture (or
//   one that fails to load): a large weather icon in the frame. The frame always keeps its
//   size (425 x 931 px on the 1080 x 1920 board; the owner prepares pictures for it).
// - Today list (mockup "prani"): the "Today" label, then one row per event in the style of
//   the next-day cards: a member-colour dot and the text. All-day events first, then timed
//   ones in start order ("10:30 Title", location on a second line); finished events faded;
//   the red "now" line sits after the last finished event. When it overflows, pickVisible
//   hides finished timed events first, then trims from the end, plus "+N more".
// - Next 3 days: weekday, forecast icon and high, up to 2 events (colour dot), "+N more".
//   Tapping a card opens the day window with all its events (lib/day-window.js).
// - Quick tiles: config.quick_tiles mapped to config.tiles, drawn by tile.js, in a panel
//   of QUICK_SLOTS slots at the bottom; unused slots show as empty dashed tiles.
//
// The timeline and cards are rebuilt only when the calendar/weather payload, members,
// language, zone or local minute changes (checked on every store update and every 20 s);
// HA deltas only touch the header and the tiles.
import { renderHeader, safeColor, currentWeather } from "../header.js";
import { t, getLanguage, fmtWeekdayShort } from "../../shared/i18n.js";
import { WEATHER_ICONS } from "../../shared/ha-format.js";
import { pickPicture } from "../lib/weather-pic.js";
import { localDate, addDays, noonOf, bucket, isPast, pickVisible } from "../lib/layout.js";
import { renderTile } from "../tile.js";
import { chipTime } from "./week.js";
import { createDayWindow } from "../lib/day-window.js";

const MINUTE_CHECK_MS = 20000;
const RAIN_MIN_PERCENT = 50;
const NEXT_DAYS = 3;
const CARD_EVENTS = 2;
const QUICK_SLOTS = 4;

const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// The first day today..today+6 whose forecast rain probability is >= 50 %, as a short hint
// ("rain today" / "rain tomorrow" / "rain Thursday"); "" when there is none.
export function rainHint(daily, todayISO) {
  const last = addDays(todayISO, 6);
  const days = (Array.isArray(daily) ? daily : [])
    .filter((d) => typeof d?.date === "string" && d.date >= todayISO && d.date <= last)
    .filter((d) => (num(d.precip_probability) ?? 0) >= RAIN_MIN_PERCENT)
    .map((d) => d.date)
    .sort();
  if (!days.length) return "";
  const iso = days[0];
  if (iso === todayISO) return t("rain_today");
  if (iso === addDays(todayISO, 1)) return t("rain_tomorrow");
  const weekday = new Date(`${iso}T00:00:00Z`).getUTCDay() || 7; // ISO: Monday 1 .. Sunday 7
  return t("rain_on", { day: t(`on_day_${weekday}`) });
}

// Header line under the current weather: today's high/low and the rain hint.
export function todaySubtitle(weather, todayISO) {
  const d = (weather?.daily || []).find((x) => x?.date === todayISO);
  const high = num(d?.high);
  const low = num(d?.low);
  let temps = "";
  if (high !== null && low !== null) temps = t("high_low", { high: Math.round(high), low: Math.round(low) });
  else if (high !== null) temps = `${Math.round(high)}°`;
  return [temps, rainHint(weather?.daily, todayISO)].filter(Boolean).join(" · ");
}


// Where the "now" line goes among today's timed events (start order): right after the last
// finished one, 0 when none has finished. An earlier event still running stays above it.
export function nowLineIndex(timed, now, tz) {
  for (let i = timed.length - 1; i >= 0; i--) if (isPast(timed[i], now, tz)) return i + 1;
  return 0;
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// One row of the today list: member-colour dot, then "10:30 Title" (all-day: the title)
// and the location on a second line.
function eventRow(ev, today, tz, now) {
  const r = el("div", "wb-ev");
  if (ev.all_day) r.classList.add("all");
  else if (isPast(ev, now, tz)) r.classList.add("past");
  const dot = el("span", "wb-ev-dot");
  dot.style.background = safeColor(ev.color);
  const ti = el("span", "wb-ev-ti");
  const title = el("span", "wb-ev-title");
  const time = chipTime(ev, today, tz);
  if (time) title.append(el("span", "wb-ev-t", time));
  title.append(ev.title ?? "");
  ti.append(title);
  if (ev.location) ti.append(el("span", "wb-ev-loc", ev.location));
  r.append(dot, ti);
  return r;
}

function nowLine() {
  const n = el("div", "wb-now");
  n.append(el("span", "wb-now-dot"));
  return n;
}

function forecast(weather, iso) {
  const d = (weather?.daily || []).find((x) => x?.date === iso);
  if (!d) return null;
  const high = num(d.high);
  return { icon: WEATHER_ICONS[d.condition] || "mdi-weather-partly-cloudy", high: high === null ? "" : `${Math.round(high)}°` };
}

function card(iso, events, weather, tz) {
  const c = el("div", "wb-card3");
  c.dataset.iso = iso;
  c.dataset.tap = ""; // the shell must not page on a tap here: it opens the day window
  const h = el("div", "wb-card3-h");
  h.append(el("span", "", capitalize(fmtWeekdayShort(noonOf(iso, tz)))));
  const wx = forecast(weather, iso);
  if (wx) {
    const w = el("span", "wb-card3-wx");
    w.append(el("i", `mdi ${wx.icon}`), wx.high);
    h.append(w);
  }
  c.append(h);
  const { shown, hidden } = pickVisible(events, CARD_EVENTS, () => false);
  for (const ev of shown) {
    const i = el("div", "wb-card3-i");
    const dot = el("span", "wb-dot");
    dot.style.background = safeColor(ev.color);
    i.append(dot, ev.title ?? "");
    c.append(i);
  }
  if (hidden) c.append(el("div", "wb-card3-more", t("more_n", { n: hidden })));
  return c;
}

const overflows = (box) => box.scrollHeight > box.clientHeight + 1;

export default {
  type: "today",
  titleKey: "today",
  settings: [],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-page-header"></div>
      <div class="wb-pt"><div class="wb-wxpic"><img decoding="async" alt="" hidden><i class="mdi" hidden></i></div>
      <div class="wb-today"><span class="wb-lbl"></span><div class="wb-tl"></div></div></div>
      <div class="wb-cards3"></div>
      <div class="wb-qt"><div class="wb-tiles"></div></div>`;
    const header = root.querySelector(".wb-page-header");
    const frame = root.querySelector(".wb-wxpic");
    const img = frame.querySelector("img");
    const icon = frame.querySelector("i");
    const box = root.querySelector(".wb-today");
    const label = root.querySelector(".wb-lbl");
    const tl = root.querySelector(".wb-tl");
    const cards = root.querySelector(".wb-cards3");
    const tilesEl = root.querySelector(".wb-tiles");
    const win = createDayWindow(root, ctx, {
      cells: ".wb-card3[data-iso]",
      within: cards,
      onSelect: (iso) => {
        for (const c of cards.children) c.classList.toggle("sel", c.dataset.iso === iso);
      },
    });
    let last = null; // key of the last timeline/cards build
    let alive = true;
    let pic = null; // the weather picture shown this visit ({id, url}) or null
    const failed = new Set(); // picture ids that failed to load this visit
    let tiles = []; // [{entityId, handle}]
    let tilesKey = null;

    function showIcon(condition) {
      img.hidden = true;
      img.removeAttribute("src");
      icon.className = `mdi ${WEATHER_ICONS[condition] || "mdi-weather-partly-cloudy"}`;
      icon.hidden = false;
    }
    img.addEventListener("error", () => {
      if (!alive || !pic) return;
      console.warn("weather picture failed to load", pic.url);
      failed.add(pic.id);
      pic = null;
      syncPicture(ctx.store.get());
    });

    // Keep the visit's picture while it matches the condition; else pick another.
    function syncPicture(state) {
      const condition = currentWeather(state)?.condition ?? "";
      const pics = (state.weather_pics || []).filter((p) => !failed.has(p?.id));
      const next = pickPicture(pics, condition, pic?.id ?? null);
      if (!next) {
        pic = null;
        showIcon(condition);
        return;
      }
      if (next.id === pic?.id) return;
      pic = next;
      icon.hidden = true;
      img.src = next.url;
      img.hidden = false;
    }

    function syncTiles(state) {
      const config = state.config;
      // Rebuild only when the tile setup changes: a reconnect snapshot brings a new config
      // object and must not destroy a pending tile (and its failure toast).
      const key = JSON.stringify([config?.quick_tiles ?? [], config?.tiles ?? []]);
      if (key !== tilesKey) {
        tilesKey = key;
        for (const x of tiles) x.handle.destroy();
        const byId = new Map((config?.tiles || []).map((tc) => [tc.entity_id, tc]));
        const cfgs = (config?.quick_tiles || []).map((id) => byId.get(id)).filter(Boolean);
        tiles = cfgs.map((cfg) => {
          const node = el("div");
          return { entityId: cfg.entity_id, node, handle: renderTile(node, cfg, state.ha?.[cfg.entity_id], () => ctx.store.sendAction(cfg.entity_id), { holdRotation: ctx.holdRotation, canAct: ctx.canAct }) };
        });
        const empty = Array.from({ length: Math.max(0, QUICK_SLOTS - tiles.length) }, () => el("div", "wb-tile wb-tile-empty"));
        tilesEl.replaceChildren(...tiles.map((x) => x.node), ...empty);
        return;
      }
      for (const x of tiles) x.handle.update(state.ha?.[x.entityId]);
    }

    function drawTimeline(events, today, tz, now, cap) {
      const { shown, hidden } = pickVisible(events, cap, (ev) => isPast(ev, now, tz));
      const allDay = shown.filter((ev) => ev.all_day);
      const timed = shown.filter((ev) => !ev.all_day);
      const rows = timed.map((ev) => eventRow(ev, today, tz, now));
      if (timed.length) rows.splice(nowLineIndex(timed, now, tz), 0, nowLine());
      rows.unshift(...allDay.map((ev) => eventRow(ev, today, tz, now)));
      if (!events.length) rows.push(el("div", "wb-empty", t("no_events")));
      if (hidden) rows.push(el("div", "wb-more", t("more_n", { n: hidden })));
      tl.replaceChildren(...rows);
    }

    const same = (a, b) =>
      a && b && a.tz === b.tz && a.minute === b.minute && a.calendar === b.calendar && a.weather === b.weather &&
      a.lang === b.lang && a.members === b.members;

    function render(state, force = false) {
      const now = Date.now();
      const tz = state.config?.timezone || "Europe/Prague";
      const today = localDate(now, tz);
      renderHeader(header, state, { subtitle: todaySubtitle(state.weather, today) });
      syncTiles(state);
      syncPicture(state);
      win.update(state);
      const members = state.config?.members;
      const key = { tz, minute: Math.floor(now / 60000), calendar: state.calendar, weather: state.weather, lang: getLanguage(), members };
      if (!force && same(key, last)) return;
      last = key;
      label.textContent = t("today");
      const days = bucket(state.calendar?.events || [], today, NEXT_DAYS + 1, tz);
      const [todayEvents, ...next] = [...days.values()];
      cards.replaceChildren(...[...days.keys()].slice(1).map((iso, i) => card(iso, next[i], state.weather, tz)));
      for (const c of cards.children) c.classList.toggle("sel", c.dataset.iso === win.iso());
      // After layout: measuring needs the timeline in place. Largest selection that fits.
      drawTimeline(todayEvents, today, tz, now, todayEvents.length);
      for (let cap = todayEvents.length - 1; cap >= 1 && overflows(box); cap--) {
        drawTimeline(todayEvents, today, tz, now, cap);
      }
    }

    const refit = () => alive && render(ctx.store.get(), true);
    const timer = setInterval(() => render(ctx.store.get()), MINUTE_CHECK_MS);
    // The timeline height depends on the page size and the web font.
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
      ro.observe(box);
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
        for (const x of tiles) x.handle.destroy();
        img.removeAttribute("src"); // release the decoded bitmap promptly (weeks-long kiosk)
        root.innerHTML = "";
      },
    };
  },
};
