// Home Assistant entity formatting, moved from www/dakboard/index.html.
// describe(entityId, state) -> {name, value, unit, icon, on, kind}. No DOM access.
// All user-visible words go through t(); maps below hold i18n keys, not text.
import { t, fmtNumber, fmtTime } from "./i18n.js";

// device_class -> [on key, off key]
export const BINARY_LABELS = {
  door: ["open", "closed"], window: ["open", "closed"], opening: ["open", "closed"], garage_door: ["open", "closed"],
  motion: ["detected", "clear"], occupancy: ["detected", "clear"], presence: ["home_state", "away"],
  moisture: ["wet", "dry"], smoke: ["detected", "clear"], gas: ["detected", "clear"], carbon_monoxide: ["detected", "clear"],
  vibration: ["detected", "clear"], sound: ["detected", "clear"], tamper: ["tampered", "ok"],
  lock: ["unlocked", "locked"], battery: ["low", "normal"], connectivity: ["connected", "disconnected"],
  plug: ["plugged_in", "unplugged"], problem: ["problem", "ok"], safety: ["unsafe", "safe"], running: ["running", "idle"],
};

// device_class -> [on icon, off icon]
export const BINARY_ICONS = {
  door: ["mdi-door-open", "mdi-door-closed"], window: ["mdi-window-open", "mdi-window-closed"],
  opening: ["mdi-square-outline", "mdi-square"], garage_door: ["mdi-garage-open", "mdi-garage"],
  motion: ["mdi-motion-sensor", "mdi-motion-sensor-off"], occupancy: ["mdi-home-account", "mdi-home-outline"],
  presence: ["mdi-home", "mdi-home-outline"], moisture: ["mdi-water", "mdi-water-off"],
  smoke: ["mdi-smoke-detector-alert", "mdi-smoke-detector"], lock: ["mdi-lock-open-variant", "mdi-lock"],
  battery: ["mdi-battery-alert", "mdi-battery"], connectivity: ["mdi-lan-connect", "mdi-lan-disconnect"],
  problem: ["mdi-alert-circle", "mdi-check-circle"],
};

export const SENSOR_ICONS = {
  temperature: "mdi-thermometer", humidity: "mdi-water-percent", pressure: "mdi-gauge", atmospheric_pressure: "mdi-gauge",
  power: "mdi-flash", energy: "mdi-lightning-bolt", battery: "mdi-battery", illuminance: "mdi-brightness-5",
  carbon_dioxide: "mdi-molecule-co2", pm25: "mdi-air-filter", pm10: "mdi-air-filter", voltage: "mdi-sine-wave",
  current: "mdi-current-ac", wind_speed: "mdi-weather-windy", precipitation: "mdi-weather-rainy", gas: "mdi-meter-gas",
  water: "mdi-water", monetary: "mdi-cash", timestamp: "mdi-clock-outline",
};

// HA weather condition -> mdi class. Text lives under the key weather_<condition with _ for ->.
export const WEATHER_ICONS = {
  "clear-night": "mdi-weather-night", cloudy: "mdi-weather-cloudy", exceptional: "mdi-weather-cloudy-alert",
  fog: "mdi-weather-fog", hail: "mdi-weather-hail", lightning: "mdi-weather-lightning",
  "lightning-rainy": "mdi-weather-lightning-rainy", partlycloudy: "mdi-weather-partly-cloudy",
  pouring: "mdi-weather-pouring", rainy: "mdi-weather-rainy", snowy: "mdi-weather-snowy",
  "snowy-rainy": "mdi-weather-snowy-rainy", sunny: "mdi-weather-sunny", windy: "mdi-weather-windy",
  "windy-variant": "mdi-weather-windy-variant",
};

// domain -> [on icon, off icon]
export const DOMAIN_ICONS = {
  light: ["mdi-lightbulb-on", "mdi-lightbulb-outline"], switch: ["mdi-toggle-switch", "mdi-toggle-switch-off-outline"],
  input_boolean: ["mdi-toggle-switch", "mdi-toggle-switch-off-outline"], fan: ["mdi-fan", "mdi-fan-off"],
  lock: ["mdi-lock-open-variant", "mdi-lock"], cover: ["mdi-window-shutter-open", "mdi-window-shutter"],
  person: ["mdi-account", "mdi-account-outline"], device_tracker: ["mdi-account", "mdi-account-outline"],
  climate: ["mdi-thermostat", "mdi-thermostat"], media_player: ["mdi-speaker-play", "mdi-speaker"],
  sun: ["mdi-white-balance-sunny", "mdi-weather-night"], vacuum: ["mdi-robot-vacuum", "mdi-robot-vacuum"],
  alarm_control_panel: ["mdi-shield-alert", "mdi-shield-check"],
  scene: ["mdi-palette", "mdi-palette"], script: ["mdi-script-text", "mdi-script-text-outline"],
};

const KINDS = {
  binary_sensor: "binary", sensor: "sensor", weather: "weather",
  light: "toggle", switch: "toggle", fan: "toggle", input_boolean: "toggle",
  vacuum: "vacuum", cover: "cover", lock: "lock", person: "person", device_tracker: "person",
  scene: "scene", script: "script",
};

// Raw HA state -> translation key, for states whose wording we translate.
const STATE_KEYS = new Set([
  "on", "off", "open", "closed", "opening", "closing", "locked", "unlocked", "docked", "cleaning", "returning",
  "starting", "idle", "paused", "error", "playing",
]);

const ON_STATES = new Set(["on", "open", "unlocked", "cleaning", "returning", "home", "playing"]);
const MISSING = new Set(["unavailable", "unknown", "", null, undefined]);

const cap = (s) => String(s).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const mdi = (name) => (name ? String(name).replace(/^mdi:/, "mdi-") : "");
const stateText = (s) => (STATE_KEYS.has(s) ? t(s) : cap(s));

export function describe(entityId, state) {
  const [domain, objectId = ""] = String(entityId).split(".");
  const a = state?.attributes || {};
  const s = state?.state;
  const kind = KINDS[domain] || "other";
  const out = {
    name: a.friendly_name || objectId.replace(/_/g, " "),
    value: "", unit: "", icon: "", on: false, kind,
  };
  const explicit = mdi(a.icon);

  if (!state || MISSING.has(s)) {
    out.value = t(s === "unknown" ? "unknown" : "unavailable");
    out.icon = explicit || (DOMAIN_ICONS[domain] || [])[1] || "mdi-help-circle-outline";
    return out;
  }

  out.on = ON_STATES.has(s);

  switch (kind) {
    case "binary": {
      const labels = BINARY_LABELS[a.device_class] || ["on", "off"];
      const icons = BINARY_ICONS[a.device_class] || ["mdi-checkbox-marked-circle", "mdi-checkbox-blank-circle-outline"];
      out.on = s === "on";
      out.value = t(labels[out.on ? 0 : 1]);
      out.icon = icons[out.on ? 0 : 1];
      break;
    }
    case "toggle":
    case "script":
      out.value = stateText(s);
      if (s === "on" && kind === "script") out.value = t("running");
      if (s === "off" && kind === "script") out.value = t("idle");
      break;
    case "scene":
      out.on = false;
      break;
    case "person":
      out.value = s === "home" ? t("home_state") : s === "not_home" ? t("away") : s;
      break;
    case "weather": {
      const temp = fmtNumber(a.temperature);
      const key = `weather_${s.replace(/-/g, "_")}`;
      out.value = temp ?? (WEATHER_ICONS[s] ? t(key) : cap(s));
      out.unit = temp != null ? a.temperature_unit ?? "°" : "";
      out.icon = WEATHER_ICONS[s] || "mdi-weather-partly-cloudy";
      break;
    }
    default: {
      // sensor, vacuum, cover, lock, other
      const num = fmtNumber(s);
      out.value = num ?? stateText(s);
      out.unit = num != null ? a.unit_of_measurement ?? "" : "";
      if (kind === "sensor" && a.device_class === "timestamp") {
        const d = new Date(s);
        if (!isNaN(d)) {
          out.value = fmtTime(d);
          out.unit = "";
        }
      }
    }
  }

  if (!out.icon) {
    const pair = DOMAIN_ICONS[domain];
    out.icon = (pair && pair[out.on ? 0 : 1]) || SENSOR_ICONS[a.device_class] || "mdi-eye";
  }
  // The entity's own icon attribute wins.
  if (explicit) out.icon = explicit;
  return out;
}
