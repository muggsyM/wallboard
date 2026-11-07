// Pure helpers for the Home page (pages/home.js draws them). No DOM.
//
//   isOpen(entityId, haState)          binary_sensor door/window/garage_door/opening that is on
//   isRunning(entityId, haState)       an appliance at work: sensor "running"/"washing"/…,
//                                      vacuum cleaning, binary_sensor running on, switch on
//   statusTone(entityId, haState)      "warn" | "ok" | "active" | "" for a status row
//   attentionSummary(ids, ha)          header subtitle: "1 door or window open · 1 appliance
//                                      running", t("all_ok") when nothing needs attention,
//                                      "" when no status entities are configured
//   lightsOnCount(ids, ha)             lights (any entity) whose state is "on"
//   roomView(room, ha)                 {name, temp, unit, meta} for a room card; "–" for
//                                      missing or unavailable values
//   tileGroups(tiles)                  {lights, devices} in config order
//
// Locks: an unlocked lock (lock domain, or binary_sensor class lock that is on) warns like
// an open window; a locked one gets the calm "ok" tone (mockup: green "Locked"). Locks are
// not counted in the subtitle, which only speaks of doors and windows.
import { describe } from "../../shared/ha-format.js";
import { t, tn, getLanguage } from "../../shared/i18n.js";

export const DASH = "–";
const OPENING_CLASSES = new Set(["door", "window", "garage_door", "opening"]);
const RUNNING_STATES = new Set(["running", "washing", "drying", "rinsing", "spinning", "cleaning"]);
const MISSING = new Set(["unavailable", "unknown", "", null, undefined]);

const domainOf = (id) => String(id).split(".")[0];
const present = (s) => Boolean(s) && !MISSING.has(s.state);

export function isOpen(entityId, s) {
  return domainOf(entityId) === "binary_sensor" && present(s) && s.state === "on" && OPENING_CLASSES.has(s.attributes?.device_class);
}

export function isRunning(entityId, s) {
  if (!present(s)) return false;
  const state = String(s.state).toLowerCase();
  switch (domainOf(entityId)) {
    case "sensor":
      return RUNNING_STATES.has(state);
    case "vacuum":
      return state === "cleaning";
    case "binary_sensor":
      return s.attributes?.device_class === "running" && state === "on";
    case "switch":
      return state === "on";
    default:
      return false;
  }
}

function isUnlocked(entityId, s) {
  const d = domainOf(entityId);
  if (d === "lock") return s.state === "unlocked" ? true : s.state === "locked" ? false : null;
  if (d === "binary_sensor" && s.attributes?.device_class === "lock") return s.state === "on";
  return null; // not a lock
}

export function statusTone(entityId, s) {
  if (!present(s)) return "";
  if (isOpen(entityId, s)) return "warn";
  const unlocked = isUnlocked(entityId, s);
  if (unlocked !== null) return unlocked ? "warn" : "ok";
  return isRunning(entityId, s) ? "active" : "";
}

export function attentionSummary(ids, ha) {
  const unique = [...new Set(ids || [])];
  if (!unique.length) return "";
  const open = unique.filter((id) => isOpen(id, ha?.[id])).length;
  const running = unique.filter((id) => isRunning(id, ha?.[id])).length;
  const parts = [];
  if (open) parts.push(tn("attention_open", open));
  if (running) parts.push(tn("attention_running", running));
  if (!parts.length) return t("all_ok");
  // Each part is kept on one line (no-break spaces), so a long subtitle wraps only after "·".
  return parts.map((p) => p.replace(/ /g, " ")).join(" · ");
}

export function lightsOnCount(ids, ha) {
  return (ids || []).filter((id) => ha?.[id]?.state === "on").length;
}

// cs writes "46 %", en "46%".
const percent = (v) => (getLanguage() === "cs" ? `${v} %` : `${v}%`);

function reading(entityId, ha) {
  const s = entityId ? ha?.[entityId] : undefined;
  if (!present(s)) return null;
  return describe(entityId, s);
}

export function roomView(room, ha) {
  const temp = reading(room.temperature_entity, ha);
  const meta = [];
  if (room.humidity_entity) {
    const h = reading(room.humidity_entity, ha);
    if (!h) meta.push(DASH);
    else meta.push(h.unit === "%" ? percent(h.value) : h.unit ? `${h.value} ${h.unit}` : h.value);
  }
  const lights = lightsOnCount(room.light_entities, ha);
  if (lights) meta.push(tn("lights_on", lights));
  return { name: room.name ?? "", temp: temp ? temp.value : DASH, unit: temp ? temp.unit : "", meta: meta.join(" · ") };
}

export function tileGroups(tiles) {
  const list = tiles || [];
  return { lights: list.filter((x) => x.group === "lights"), devices: list.filter((x) => x.group === "devices") };
}
