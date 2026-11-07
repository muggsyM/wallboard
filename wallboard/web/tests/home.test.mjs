import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setStrings, setLanguage, t } from "../shared/i18n.js";
import { statusTone, isOpen, isRunning, attentionSummary, lightsOnCount, roomView, tileGroups } from "../display/lib/home-state.js";

const load = (n) => JSON.parse(readFileSync(new URL(`../shared/i18n/${n}.json`, import.meta.url), "utf8"));
setStrings("cs", load("cs"));
setStrings("en", load("en"));

const st = (state, attributes = {}) => ({ state, attributes, last_changed: "2026-11-07T07:00:00Z" });

test("isOpen: door/window/garage_door/opening binary sensors that are on", () => {
  for (const dc of ["door", "window", "garage_door", "opening"]) {
    assert.equal(isOpen("binary_sensor.x", st("on", { device_class: dc })), true, dc);
    assert.equal(isOpen("binary_sensor.x", st("off", { device_class: dc })), false, dc);
  }
  assert.equal(isOpen("binary_sensor.x", st("on", { device_class: "motion" })), false);
  assert.equal(isOpen("binary_sensor.x", st("on")), false);
  assert.equal(isOpen("sensor.x", st("on", { device_class: "door" })), false);
  assert.equal(isOpen("binary_sensor.x", st("unavailable", { device_class: "door" })), false);
  assert.equal(isOpen("binary_sensor.x", undefined), false);
});

test("isRunning: appliances that are working", () => {
  assert.equal(isRunning("sensor.washer", st("running")), true);
  assert.equal(isRunning("sensor.washer", st("Washing")), true);
  assert.equal(isRunning("sensor.washer", st("idle")), false);
  assert.equal(isRunning("sensor.washer_remaining", st("12", { unit_of_measurement: "min" })), false);
  assert.equal(isRunning("vacuum.robot", st("cleaning")), true);
  assert.equal(isRunning("vacuum.robot", st("docked")), false);
  assert.equal(isRunning("binary_sensor.dryer", st("on", { device_class: "running" })), true);
  assert.equal(isRunning("binary_sensor.dryer", st("off", { device_class: "running" })), false);
  assert.equal(isRunning("switch.dishwasher_plug", st("on")), true);
  assert.equal(isRunning("switch.dishwasher_plug", st("off")), false);
  assert.equal(isRunning("binary_sensor.front_door", st("on", { device_class: "door" })), false);
  assert.equal(isRunning("sensor.x", undefined), false);
});

test("statusTone: warn for open openings and unlocked locks, ok when locked, active when running", () => {
  assert.equal(statusTone("binary_sensor.w", st("on", { device_class: "window" })), "warn");
  assert.equal(statusTone("binary_sensor.g", st("on", { device_class: "garage_door" })), "warn");
  assert.equal(statusTone("binary_sensor.w", st("off", { device_class: "window" })), "");
  assert.equal(statusTone("lock.front", st("unlocked")), "warn");
  assert.equal(statusTone("lock.front", st("locked")), "ok");
  assert.equal(statusTone("binary_sensor.l", st("on", { device_class: "lock" })), "warn");
  assert.equal(statusTone("binary_sensor.l", st("off", { device_class: "lock" })), "ok");
  assert.equal(statusTone("sensor.washer", st("running")), "active");
  assert.equal(statusTone("sensor.power", st("640", { unit_of_measurement: "W" })), "");
  assert.equal(statusTone("lock.front", st("unavailable")), "");
  assert.equal(statusTone("lock.front", undefined), "");
});

test("attentionSummary: open count, running count, all good, nothing watched", () => {
  const ha = {
    "binary_sensor.kids_window": st("on", { device_class: "window" }),
    "binary_sensor.front_door": st("on", { device_class: "door" }),
    "binary_sensor.garage": st("off", { device_class: "garage_door" }),
    "sensor.washer": st("running"),
    "vacuum.robot": st("docked"),
  };
  // Each part stays on one line (no-break spaces); a wrap can only fall after "· ".
  const summary = (ids) => attentionSummary(ids, ha).replace(/\u00a0/g, " ");
  setLanguage("en");
  assert.equal(attentionSummary(["binary_sensor.kids_window", "sensor.washer"], ha), "1\u00a0door\u00a0or\u00a0window\u00a0open\u00a0· 1\u00a0appliance\u00a0running");
  assert.equal(summary(["binary_sensor.kids_window", "binary_sensor.front_door", "binary_sensor.garage"]), "2 doors or windows open");
  assert.equal(attentionSummary(["binary_sensor.garage", "vacuum.robot"], ha), t("all_ok"));
  // An entity listed twice counts once; missing entities are ignored.
  assert.equal(summary(["binary_sensor.front_door", "binary_sensor.front_door", "sensor.gone"]), "1 door or window open");
  assert.equal(attentionSummary([], ha), "");
  assert.equal(attentionSummary(undefined, ha), "");
  setLanguage("cs");
  assert.equal(summary(["binary_sensor.kids_window", "sensor.washer"]), "1 otevřené okno nebo dveře · 1 spotřebič běží");
  assert.equal(summary(["binary_sensor.kids_window", "binary_sensor.front_door"]), "2 otevřená okna nebo dveře");
  const many = Object.fromEntries([1, 2, 3, 4, 5].map((i) => [`switch.p${i}`, st("on")]));
  assert.equal(attentionSummary(Object.keys(many), many).replace(/\u00a0/g, " "), "5 spotřebičů běží");
  assert.equal(attentionSummary(["binary_sensor.garage"], ha), "Doma je vše v pořádku");
});

test("lightsOnCount: counts lights that are on, ignores missing ones", () => {
  const ha = { "light.a": st("on"), "light.b": st("off"), "light.c": st("on"), "light.d": st("unavailable") };
  assert.equal(lightsOnCount(["light.a", "light.b", "light.c", "light.d", "light.gone"], ha), 2);
  assert.equal(lightsOnCount([], ha), 0);
  assert.equal(lightsOnCount(undefined, ha), 0);
  assert.equal(lightsOnCount(["light.a"], undefined), 0);
});

test("roomView: temperature, humidity and lights line; dash for missing values", () => {
  const ha = {
    "sensor.t": st("21.46", { unit_of_measurement: "°C", device_class: "temperature" }),
    "sensor.h": st("46", { unit_of_measurement: "%", device_class: "humidity" }),
    "light.a": st("on"),
    "light.b": st("on"),
    "light.c": st("off"),
  };
  setLanguage("en");
  const room = { name: "Living", temperature_entity: "sensor.t", humidity_entity: "sensor.h", light_entities: ["light.a", "light.b", "light.c"] };
  assert.deepEqual(roomView(room, ha), { name: "Living", temp: "21.5", unit: "°C", meta: "46% · 2 lights on" });
  assert.equal(roomView({ ...room, light_entities: ["light.a"] }, ha).meta, "46% · 1 light on");
  assert.equal(roomView({ ...room, light_entities: ["light.c"] }, ha).meta, "46%");
  assert.equal(roomView({ ...room, humidity_entity: null, light_entities: [] }, ha).meta, "");
  const gone = roomView({ name: "Kids", temperature_entity: "sensor.gone", humidity_entity: "sensor.gone2", light_entities: [] }, ha);
  assert.deepEqual(gone, { name: "Kids", temp: "–", unit: "", meta: "–" });
  const unavailable = roomView({ name: "X", temperature_entity: "sensor.u" }, { "sensor.u": st("unavailable") });
  assert.equal(unavailable.temp, "–");
  setLanguage("cs");
  assert.deepEqual(roomView(room, ha), { name: "Living", temp: "21,5", unit: "°C", meta: "46 % · 2 světla svítí" });
  const five = Object.fromEntries([1, 2, 3, 4, 5].map((i) => [`light.l${i}`, st("on")]));
  assert.equal(roomView({ name: "Y", temperature_entity: "sensor.t", light_entities: Object.keys(five) }, { ...ha, ...five }).meta, "5 světel svítí");
});

test("tileGroups: lights and devices in config order", () => {
  const tiles = [
    { entity_id: "vacuum.robot", group: "devices" },
    { entity_id: "light.kitchen", group: "lights" },
    { entity_id: "scene.good_night", group: "devices" },
    { entity_id: "light.hallway", group: "lights" },
  ];
  const g = tileGroups(tiles);
  assert.deepEqual(g.lights.map((x) => x.entity_id), ["light.kitchen", "light.hallway"]);
  assert.deepEqual(g.devices.map((x) => x.entity_id), ["vacuum.robot", "scene.good_night"]);
  assert.deepEqual(tileGroups(undefined), { lights: [], devices: [] });
});
