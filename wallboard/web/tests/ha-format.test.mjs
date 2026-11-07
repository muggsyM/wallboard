import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setStrings, setLanguage, t } from "../shared/i18n.js";
import { describe, WEATHER_ICONS } from "../shared/ha-format.js";

const load = (n) => JSON.parse(readFileSync(new URL(`../shared/i18n/${n}.json`, import.meta.url), "utf8"));
setStrings("cs", load("cs"));
setStrings("en", load("en"));
setLanguage("cs");

test("binary door off is closed", () => {
  const r = describe("binary_sensor.front_door", { state: "off", attributes: { device_class: "door" } });
  assert.equal(r.value, t("closed"));
  assert.equal(r.icon, "mdi-door-closed");
  assert.equal(r.on, false);
  assert.equal(r.kind, "binary");
  assert.equal(r.name, "front door");
});

test("binary door on is open", () => {
  const r = describe("binary_sensor.front_door", { state: "on", attributes: { device_class: "door", friendly_name: "Vchod" } });
  assert.equal(r.value, t("open"));
  assert.equal(r.icon, "mdi-door-open");
  assert.equal(r.on, true);
  assert.equal(r.name, "Vchod");
});

test("vacuum docked is not on; cleaning is", () => {
  const a = describe("vacuum.robot", { state: "docked", attributes: {} });
  assert.equal(a.on, false);
  assert.equal(a.kind, "vacuum");
  assert.equal(a.value, t("docked"));
  assert.equal(describe("vacuum.robot", { state: "cleaning", attributes: {} }).on, true);
  assert.equal(describe("vacuum.robot", { state: "returning", attributes: {} }).on, true);
});

test("light toggle", () => {
  const r = describe("light.lamp", { state: "on", attributes: { brightness: 128 } });
  assert.equal(r.kind, "toggle");
  assert.equal(r.on, true);
  assert.equal(r.value, t("on"));
  assert.equal(r.icon, "mdi-lightbulb-on");
  assert.equal(describe("switch.x", { state: "off", attributes: {} }).icon, "mdi-toggle-switch-off-outline");
});

test("lock, cover, person", () => {
  const l = describe("lock.door", { state: "unlocked", attributes: {} });
  assert.equal(l.kind, "lock"); assert.equal(l.on, true); assert.equal(l.value, t("unlocked"));
  assert.equal(describe("lock.door", { state: "locked", attributes: {} }).value, t("locked"));
  const c = describe("cover.blind", { state: "open", attributes: {} });
  assert.equal(c.kind, "cover"); assert.equal(c.on, true); assert.equal(c.value, t("open"));
  const p = describe("person.mirek", { state: "home", attributes: {} });
  assert.equal(p.kind, "person"); assert.equal(p.on, true); assert.equal(p.value, t("home_state"));
  assert.equal(describe("person.mirek", { state: "not_home", attributes: {} }).value, t("away"));
});

test("sensor numeric with unit and icon", () => {
  const r = describe("sensor.t", { state: "21.5", attributes: { device_class: "temperature", unit_of_measurement: "°C" } });
  assert.equal(r.kind, "sensor");
  assert.equal(r.value, "21,5");
  assert.equal(r.unit, "°C");
  assert.equal(r.icon, "mdi-thermometer");
  assert.equal(r.on, false);
});

test("weather", () => {
  const r = describe("weather.home", { state: "rainy", attributes: { temperature: 8 } });
  assert.equal(r.kind, "weather");
  assert.equal(r.icon, "mdi-weather-rainy");
  assert.equal(r.value, "8");
  assert.equal(r.unit, "°");
});

test("unavailable, scene, script, other, explicit icon", () => {
  assert.equal(describe("sensor.x", { state: "unavailable", attributes: {} }).value, t("unavailable"));
  assert.equal(describe("sensor.x", null).value, t("unavailable"));
  assert.equal(describe("sensor.x", { state: "unknown", attributes: {} }).value, t("unknown"));
  assert.equal(describe("scene.movie", { state: "2026-01-01T00:00:00", attributes: {} }).kind, "scene");
  assert.equal(describe("script.go", { state: "off", attributes: {} }).kind, "script");
  assert.equal(describe("climate.x", { state: "heat", attributes: {} }).kind, "other");
  assert.equal(describe("sensor.x", { state: "5", attributes: { icon: "mdi:foo" } }).icon, "mdi-foo");
});

test("WEATHER_ICONS covers all conditions and each has text keys", () => {
  const conds = "clear-night cloudy exceptional fog hail lightning lightning-rainy partlycloudy pouring rainy snowy snowy-rainy sunny windy windy-variant".split(" ");
  const cs = load("cs"), en = load("en");
  for (const c of conds) {
    assert.ok(WEATHER_ICONS[c], c);
    const key = `weather_${c.replace(/-/g, "_")}`;
    assert.ok(key in cs && key in en, key);
  }
});
