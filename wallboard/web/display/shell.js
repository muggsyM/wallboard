// Display shell: wires the store, i18n, the photo sequence, page rotation, gestures,
// page dots, offline dimming and night mode. Pages are modules from pages/registry.js
// following the contract { type, titleKey, settings, mount(el, ctx) -> {update, destroy} }.
//
// Dev-only URL hash (for screenshots and debugging; the kiosk URL has no hash, so
// production behaviour is unchanged):
//   #page=<type>   start on the first page of that type
//   #norotate      never auto-advance (swipe/tap still work)
//   combine with "&", e.g. http://localhost:8080/#page=home&norotate
import {
  t, setLanguage, loadLanguage, setTimezone,
  fmtTime, fmtLongDate, fmtWeekdayShort, fmtMonthYear, fmtShortDayMonth, fmtNumber,
} from "../shared/i18n.js";
import { createStore } from "./store.js";
import { createPhoto } from "./photo.js";
import { refreshClocks, isOffline } from "./header.js";
import { getPage } from "./pages/registry.js";
import { Rotator } from "./lib/rotation.js";
import { isNight, localMinutes } from "./lib/night.js";
import { trackBuild, nightlyReloadMinute, crossedMinute } from "./lib/reload.js";
import { classifyGesture } from "./lib/gesture.js";
import { pageOptions } from "./lib/page-options.js";

const PAGE_FADE_MS = 600; // matches .wb-page transition in display.css
// Config validation already requires these minimums (rotation >= 5 s, touch pause >= 15 s);
// clamp again so a pending tile (5 s action timeout + 5 s safety) is never paged away.
const MIN_SECONDS = 5;
const MIN_PAUSE_SECONDS = 15;
const atLeastMin = (v, fallback, min = MIN_SECONDS) => Math.max(min, Number(v) > 0 ? Number(v) : fallback);
const pauseSeconds = (config) => atLeastMin(config?.touch_pause_seconds, 120, MIN_PAUSE_SECONDS);

function parseDevHash(hash) {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return { page: params.get("page"), norotate: params.has("norotate") };
}
const DEV = parseDevHash(location.hash);

// Opened from another device (a logged-in admin's laptop or phone), not the Pi's kiosk:
// display.css then keeps the board at portrait 9:16 and shows the cursor.
if (!["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) {
  document.documentElement.classList.add("remote");
}

const fmt = { fmtTime, fmtLongDate, fmtWeekdayShort, fmtMonthYear, fmtShortDayMonth, fmtNumber };
const store = createStore({ url: `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws` });
const photo = createPhoto();

const app = document.getElementById("app");
const stage = document.getElementById("stage");
const dots = document.getElementById("dots");
const nightEl = document.getElementById("night");

let rotator = null;
let current = null; // {el, inst}
let configKey = null;
let applyGen = 0;
let devPageApplied = false;
let wakeUntil = 0;
let nightOn = false;
let gesture = null;
let loadedBuild = null; // server build id this page was loaded with
let lastMinute = null; // local minute of day at the previous tick (nightly reload)
let reloading = false;

// Reload only when the server answers, so a reload never leaves a blank error page on the
// wall (e.g. the server is mid-restart). A failed check is simply tried again next time.
async function safeReload(reason) {
  if (reloading) return;
  reloading = true;
  try {
    const r = await fetch("/healthz", { cache: "no-store" });
    if (r.ok) {
      console.info(`wallboard: reloading (${reason})`);
      location.reload();
      return;
    }
  } catch {
    // server unreachable: stay on the current page
  }
  reloading = false;
}

// Page types from config that the registry knows; unknown ones are skipped with a warning.
function knownPages(config) {
  return (config.pages || [])
    .filter((p) => {
      if (getPage(p.type)) return true;
      console.warn(`wallboard: unknown page type "${p.type}" skipped`);
      return false;
    })
    .map((p) => (p.seconds > 0 ? { ...p, seconds: atLeastMin(p.seconds) } : p));
}

async function applyConfig(config) {
  const gen = ++applyGen;
  const lang = config.language || "cs";
  setTimezone(config.timezone);
  try {
    await loadLanguage(lang);
  } catch (err) {
    console.warn("wallboard: language load failed", err);
  }
  if (gen !== applyGen) return; // a newer config arrived meanwhile
  setLanguage(lang);
  document.documentElement.lang = lang;

  const keepType = rotator?.current()?.type;
  rotator = new Rotator({
    pages: knownPages(config),
    defaultSeconds: atLeastMin(config.rotation_seconds, 30),
    pauseSeconds: pauseSeconds(config),
    now: Date.now(),
  });
  if (!devPageApplied && DEV.page) {
    devPageApplied = true;
    rotator.goTo(DEV.page);
  } else if (keepType) {
    rotator.goTo(keepType);
  }
  mountCurrent(false);
  refreshClocks();
}

// ctx.holdRotation(ms): pages call it when a tile action starts, so rotation cannot page
// away while the tile is pending (a touch already pauses for >= 15 s; this is the guarantee).
function holdRotation(ms) {
  rotator?.touch(Date.now(), ms);
}

// ctx.canAct(): false for a view-only visitor (the server's "viewer" message); tiles then
// ignore taps. Asked live, so it follows the message that arrives just after the snapshot.
const canAct = () => store.get().viewer?.can_act !== false;

function mountCurrent(animate) {
  const page = rotator?.current();
  const mod = page && getPage(page.type);
  const old = current;
  current = null;
  if (mod) {
    const el = document.createElement("section");
    el.className = "wb-page";
    el.dataset.page = page.type;
    if (animate) el.classList.add("entering");
    stage.appendChild(el);
    let inst;
    try {
      // ctx.options: the module's settings defaults overlaid with this page's config options.
      inst = mod.mount(el, { store, t, fmt, photo, options: pageOptions(mod, page), holdRotation, canAct });
    } catch (err) {
      console.error(`wallboard: page "${page.type}" failed to mount`, err);
      inst = { update() {}, destroy() {} };
    }
    current = { el, inst };
    // Two frames so the browser paints opacity 0 before the transition starts.
    if (animate) requestAnimationFrame(() => requestAnimationFrame(() => el.classList.remove("entering")));
  }
  if (old) {
    const drop = () => {
      try {
        old.inst.destroy();
      } catch (err) {
        console.error("wallboard: page destroy failed", err);
      }
      old.el.remove();
    };
    if (animate) {
      old.el.classList.add("leaving");
      setTimeout(drop, PAGE_FADE_MS);
    } else {
      drop();
    }
  }
  renderDots();
}

function renderDots() {
  const n = rotator?.pages.length ?? 0;
  const html = n > 1 ? Array.from({ length: n }, (_, i) => `<span${i === rotator.index ? ' class="on"' : ""}></span>`).join("") : "";
  if (dots.innerHTML !== html) dots.innerHTML = html;
}

function go(direction, now) {
  if (!rotator || rotator.pages.length < 2) return;
  if (direction === "prev") rotator.prev(now);
  else rotator.next(now);
  mountCurrent(true);
}

function setNight(on) {
  if (on === nightOn) return;
  nightOn = on;
  if (on) refreshClocks(nightEl);
  app.classList.toggle("night", on);
}

// Every second: clocks (header + night; skipped when the text is unchanged), night mode,
// rotation. Clocks follow the wall clock, so an NTP step after boot is picked up at once.
function tick() {
  const now = Date.now();
  refreshClocks();
  const config = store.get().config;
  if (!config || !rotator) return;
  // Nightly safety reload so long-running renderer leaks don't accumulate.
  const minute = localMinutes(now, config.timezone);
  if (crossedMinute(lastMinute, minute, nightlyReloadMinute(config.night))) safeReload("nightly");
  lastMinute = minute;
  setNight(now >= wakeUntil && isNight(now, config.night, config.timezone));
  if (nightOn) return;
  if (!DEV.norotate && rotator.tick(now)) go("next", now);
}

store.subscribe((state, change) => {
  if (change === "snapshot") {
    // A different build after a reconnect means the server was updated: load the new code.
    const b = trackBuild(loadedBuild, state.build);
    loadedBuild = b.known;
    if (b.reload) safeReload("server updated");
  }
  if (change === "snapshot" || change === "config") {
    const key = JSON.stringify(state.config);
    if (state.config && key !== configKey) {
      configKey = key;
      applyConfig(state.config);
    }
  }
  if (change === "snapshot" || change === "config" || change === "photos") photo.sync(state);
  stage.classList.toggle("stale", isOffline(state));
  if (current) {
    try {
      current.inst.update(state);
    } catch (err) {
      console.error("wallboard: page update failed", err);
    }
  }
});

// Gestures: any touch pauses rotation (and wakes night mode) for touch_pause_seconds;
// a horizontal swipe changes page; a tap outside [data-tap] goes to the next page.
app.addEventListener("pointerdown", (e) => {
  if (!e.isPrimary) return;
  const now = Date.now();
  const pauseMs = pauseSeconds(store.get().config) * 1000;
  wakeUntil = now + pauseMs;
  rotator?.touch(now);
  if (nightOn) {
    // The touch that wakes the screen does nothing else.
    setNight(false);
    gesture = null;
    return;
  }
  gesture = { x: e.clientX, y: e.clientY, t: now, target: e.target };
});
app.addEventListener("pointerup", (e) => {
  if (!e.isPrimary || !gesture) return;
  const g = gesture;
  gesture = null;
  const now = Date.now();
  const kind = classifyGesture({ dx: e.clientX - g.x, dy: e.clientY - g.y, dt: now - g.t });
  if (kind === "next" || kind === "prev") go(kind, now);
  else if (kind === "tap" && !g.target.closest?.("[data-tap]")) go("next", now);
});
app.addEventListener("pointercancel", () => (gesture = null));
app.addEventListener("contextmenu", (e) => e.preventDefault());

setInterval(tick, 1000);
store.connect();
