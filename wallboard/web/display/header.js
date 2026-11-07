// Shared page header: big clock, date or page title, current weather, who's home and
// the red offline dot. Pages call renderHeader() from mount() and update().
//
//   renderHeader(el, state, {titleKey, subtitle})
//     no titleKey -> "today" layout: larger clock, long date, subtitle under the weather
//     titleKey    -> smaller clock, bold title with the subtitle beside it
//
// Clocks are any element with data-clock="time" | "date" (the night overlay too). The
// shell calls refreshClocks() from its 1 s tick: no minute timer to drift when NTP steps
// the Pi's clock after boot, and unchanged text is skipped, so it is cheap.
import { t, fmtTime, fmtLongDate } from "../shared/i18n.js";
import { WEATHER_ICONS } from "../shared/ha-format.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// Member colours come from config; only plain hex colours reach the style attribute.
export const safeColor = (c) => (/^#[0-9a-f]{3,8}$/i.test(String(c ?? "")) ? c : "#8c6cf0");
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

const clockText = (kind, now) => (kind === "date" ? capitalize(fmtLongDate(now)) : fmtTime(now));

export function refreshClocks(root = document) {
  const now = new Date();
  for (const el of root.querySelectorAll("[data-clock]")) {
    const text = clockText(el.dataset.clock, now);
    if (el.textContent !== text) el.textContent = text;
  }
}

export function isOffline(state) {
  return !state.connected || state.status?.ha === false;
}

// Spec §7: calendar data may be old when Google accounts exist but none fetched ("ok")
// recently. Shown as a small dimmed sync icon; the page itself is not dimmed for Google.
export function calendarStale(status) {
  const accounts = Object.values(status?.google || {});
  return accounts.length > 0 && !accounts.includes("ok");
}

function weatherHtml(weather) {
  const cur = weather?.current;
  if (!cur || !cur.condition) return "";
  const icon = WEATHER_ICONS[cur.condition] || "mdi-weather-partly-cloudy";
  const temp = Number.isFinite(Number(cur.temperature)) && cur.temperature !== null ? `${Math.round(Number(cur.temperature))}°` : "";
  return `<i class="mdi ${icon}"></i><span class="wb-tmp">${temp}</span>`;
}

function peopleHtml(config, ha) {
  const members = config?.members || [];
  if (!members.length) return "";
  const avatars = members.map((m) => {
    let cls = "";
    if (m.person_entity) cls = ha?.[m.person_entity]?.state === "home" ? " home" : " away";
    return `<span class="wb-avatar${cls}" style="background:${safeColor(m.color)}" title="${esc(m.name)}">${esc(m.initial)}</span>`;
  });
  return `<div class="wb-people">${avatars.join("")}</div>`;
}

export function renderHeader(el, state, { titleKey, subtitle } = {}) {
  const now = new Date();
  const sub = subtitle ? `<span class="wb-sub">${esc(subtitle)}</span>` : "";
  const left = titleKey
    ? `<div class="wb-clock" data-clock="time">${clockText("time", now)}</div>
       <div class="wb-title"><b>${esc(t(titleKey))}</b>${sub}</div>`
    : `<div class="wb-clock" data-clock="time">${clockText("time", now)}</div>
       <div class="wb-date" data-clock="date">${clockText("date", now)}</div>`;
  const offline = isOffline(state) ? `<span class="wb-offline" title="${esc(t("offline"))}"></span>` : "";
  const stale = calendarStale(state.status)
    ? `<i class="mdi mdi-sync-off wb-cal-stale" title="${esc(t("calendar_stale"))}"></i>`
    : "";
  const right = `<div class="wb-wx">${stale}${offline}${weatherHtml(state.weather)}</div>
    ${titleKey || !subtitle ? "" : `<div class="wb-sub wb-sub-right">${esc(subtitle)}</div>`}
    ${peopleHtml(state.config, state.ha)}`;
  const html = `<div class="wb-h-left">${left}</div><div class="wb-h-right">${right}</div>`;
  // Skip DOM work when nothing changed (HA deltas arrive every few seconds).
  if (el._wbHeader !== html) {
    el._wbHeader = html;
    el.classList.add("wb-header");
    el.classList.toggle("wb-header-today", !titleKey);
    el.innerHTML = html;
  }
}
