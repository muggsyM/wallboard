// Today ("C · Family planner"): the large header (clock, long date, weather, today's
// high/low and the next-rain hint, who's home), a framed photo with its caption, today's
// timeline, cards for the next 3 days and the quick tiles row.
// Look: mockups/portrait-v1.html screen 1.
//
// - Photo: one ctx.photo.nextPhoto() per mount (a new photo on every visit). Landscape
//   photos are contained over their own blurred bg_url, portrait ones cover the frame.
//   No photos: the frame is hidden and the timeline takes its space. A photo that fails
//   to load stays the visit's photo (frame hidden) until it is deleted or the page
//   re-mounts, so a broken file is never re-fetched or swapped mid-visit (photoAction).
// - Timeline: all-day chips beside the "Today" label, then timed events in start order
//   (member initial in the member colour, title, location line); finished events faded;
//   the red "now" line sits after the last finished event. When it overflows, pickVisible
//   hides finished timed events first, then trims from the end, plus "+N more".
// - Next 3 days: weekday, forecast icon and high, up to 2 events (colour dot), "+N more".
// - Quick tiles: config.quick_tiles mapped to config.tiles, drawn by tile.js.
//
// The timeline and cards are rebuilt only when the calendar/weather payload, members,
// language, zone or local minute changes (checked on every store update and every 20 s);
// HA deltas only touch the header and the tiles.
import { renderHeader, safeColor } from "../header.js";
import { t, getLanguage, fmtWeekdayShort } from "../../shared/i18n.js";
import { WEATHER_ICONS } from "../../shared/ha-format.js";
import { localDate, addDays, noonOf, bucket, isPast, pickVisible } from "../lib/layout.js";
import { readableText } from "../lib/color.js";
import { renderTile } from "../tile.js";
import { chipTime } from "./week.js";

const MINUTE_CHECK_MS = 20000;
const RAIN_MIN_PERCENT = 50;
const NEXT_DAYS = 3;
const CARD_EVENTS = 2;

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

// What the photo frame does on a store update, given the visit's photo id (also when it
// failed to load) and the ids currently in the photo list: "keep" | "pick" | "hide".
export function photoAction(currentId, ids) {
  if (currentId && ids.has(currentId)) return "keep";
  if (ids.size) return "pick";
  return currentId ? "hide" : "keep";
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

function solid(node, color) {
  const c = safeColor(color);
  node.style.background = c;
  node.style.color = readableText(c);
}

function allDayChip(ev) {
  const c = el("span", "wb-allday", ev.title ?? "");
  solid(c, ev.color);
  return c;
}

function timedRow(ev, today, tz, now, initials) {
  const r = el("div", "wb-ev");
  if (isPast(ev, now, tz)) r.classList.add("past");
  r.append(el("span", "wb-ev-t", chipTime(ev, today, tz)));
  const who = el("span", "wb-who", initials.get(ev.member_id) ?? "•");
  solid(who, ev.color);
  const ti = el("span", "wb-ev-ti");
  ti.append(el("span", "wb-ev-title", ev.title ?? ""));
  if (ev.location) ti.append(el("span", "wb-ev-loc", ev.location));
  r.append(who, ti);
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
      <div class="wb-pt"><div class="wb-photo" hidden><img decoding="async" alt=""><div class="wb-caption"><i class="mdi mdi-image-outline"></i><span></span></div></div>
      <div class="wb-today"><div class="wb-today-top"><span class="wb-lbl"></span><div class="wb-alldays"></div></div><div class="wb-tl"></div></div></div>
      <div class="wb-cards3"></div>
      <div class="wb-tiles" hidden></div>`;
    const header = root.querySelector(".wb-page-header");
    const frame = root.querySelector(".wb-photo");
    const img = frame.querySelector("img");
    const caption = frame.querySelector(".wb-caption");
    const box = root.querySelector(".wb-today");
    const label = root.querySelector(".wb-lbl");
    const alldays = root.querySelector(".wb-alldays");
    const tl = root.querySelector(".wb-tl");
    const cards = root.querySelector(".wb-cards3");
    const tilesEl = root.querySelector(".wb-tiles");
    let last = null; // key of the last timeline/cards build
    let alive = true;
    let photo = null;
    let tiles = []; // [{entityId, handle}]
    let tilesKey = null;

    function setPhoto(item) {
      photo = item || null;
      if (!photo) {
        frame.hidden = true;
        img.removeAttribute("src");
        return;
      }
      frame.classList.toggle("landscape", Boolean(photo.landscape));
      // JSON.stringify quotes and escapes the URL for CSS url("...").
      frame.style.backgroundImage = photo.landscape && photo.bg_url ? `url(${JSON.stringify(String(photo.bg_url))})` : "";
      caption.lastElementChild.textContent = photo.caption ?? "";
      caption.hidden = !photo.caption;
      img.src = photo.url;
      frame.hidden = false;
    }
    img.addEventListener("error", () => {
      if (!alive) return;
      console.warn("today photo failed to load", photo?.url);
      // Keep `photo` (photoAction keeps it while it is listed); only hide the frame.
      frame.hidden = true;
      img.removeAttribute("src");
    });

    // Keep the photo for the whole visit; only replace it when it is deleted.
    function syncPhoto(state) {
      const action = photoAction(photo?.id ?? null, new Set((state.photos || []).map((p) => p.id)));
      if (action === "pick") setPhoto(ctx.photo.nextPhoto());
      else if (action === "hide") setPhoto(null);
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
        tilesEl.replaceChildren(...tiles.map((x) => x.node));
        tilesEl.hidden = tiles.length === 0;
        return;
      }
      for (const x of tiles) x.handle.update(state.ha?.[x.entityId]);
    }

    function drawTimeline(events, today, tz, now, initials, cap) {
      const { shown, hidden } = pickVisible(events, cap, (ev) => isPast(ev, now, tz));
      alldays.replaceChildren(...shown.filter((ev) => ev.all_day).map(allDayChip));
      const timed = shown.filter((ev) => !ev.all_day);
      const rows = timed.map((ev) => timedRow(ev, today, tz, now, initials));
      if (timed.length) rows.splice(nowLineIndex(timed, now, tz), 0, nowLine());
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
      syncPhoto(state);
      const members = state.config?.members;
      const key = { tz, minute: Math.floor(now / 60000), calendar: state.calendar, weather: state.weather, lang: getLanguage(), members };
      if (!force && same(key, last)) return;
      last = key;
      label.textContent = t("today");
      const initials = new Map((members || []).map((m) => [m.id, m.initial]));
      const days = bucket(state.calendar?.events || [], today, NEXT_DAYS + 1, tz);
      const [todayEvents, ...next] = [...days.values()];
      cards.replaceChildren(...[...days.keys()].slice(1).map((iso, i) => card(iso, next[i], state.weather, tz)));
      // After layout: measuring needs the timeline in place. Largest selection that fits.
      drawTimeline(todayEvents, today, tz, now, initials, todayEvents.length);
      for (let cap = todayEvents.length - 1; cap >= 1 && overflows(box); cap--) {
        drawTimeline(todayEvents, today, tz, now, initials, cap);
      }
    }

    const refit = () => alive && render(ctx.store.get(), true);
    const timer = setInterval(() => render(ctx.store.get()), MINUTE_CHECK_MS);
    // The timeline height depends on the page size, the photo frame and the web font.
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
