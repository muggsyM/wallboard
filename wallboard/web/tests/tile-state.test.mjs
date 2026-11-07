import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setStrings, setLanguage, t } from "../shared/i18n.js";
import { IDLE, optimisticState, tileReducer, tileView } from "../display/lib/tile-state.js";

const load = (n) => JSON.parse(readFileSync(new URL(`../shared/i18n/${n}.json`, import.meta.url), "utf8"));
setStrings("cs", load("cs"));
setStrings("en", load("en"));
setLanguage("en");

const ha = (state, extra = {}) => ({ state, attributes: { friendly_name: "Kitchen" }, last_changed: "2026-11-07T07:00:00Z", ...extra });
const tap = (s, entityId, haState, controllable = true) => tileReducer(s, { type: "tap", entityId, haState, controllable });

test("optimistic state per domain (mirrors actions.py)", () => {
  for (const d of ["light", "switch", "fan", "input_boolean"]) {
    assert.equal(optimisticState(`${d}.x`, "on"), "off");
    assert.equal(optimisticState(`${d}.x`, "off"), "on");
    assert.equal(optimisticState(`${d}.x`, "unavailable"), null);
  }
  assert.equal(optimisticState("vacuum.r", "docked"), "starting");
  assert.equal(optimisticState("vacuum.r", "idle"), "starting");
  assert.equal(optimisticState("vacuum.r", "cleaning"), "returning");
  assert.equal(optimisticState("vacuum.r", "returning"), "returning");
  assert.equal(optimisticState("cover.c", "open"), "closing");
  assert.equal(optimisticState("cover.c", "opening"), "closing");
  assert.equal(optimisticState("cover.c", "closed"), "opening");
  assert.equal(optimisticState("scene.s", "unknown"), null);
  assert.equal(optimisticState("script.s", "off"), null);
  assert.equal(optimisticState("sensor.t", "21"), null);
});

test("tap -> pending with optimistic flip; second tap ignored", () => {
  const s1 = tap(IDLE, "light.kitchen", ha("on"));
  assert.equal(s1.pending, true);
  assert.equal(s1.optimistic, "off");
  assert.equal(s1.token, 1);
  assert.equal(tap(s1, "light.kitchen", ha("on")), s1);
});

test("non-controllable tiles ignore taps", () => {
  assert.equal(tap(IDLE, "light.kitchen", ha("on"), false), IDLE);
});

test("ok result keeps pending until HA reports a change", () => {
  let s = tap(IDLE, "light.kitchen", ha("on"));
  s = tileReducer(s, { type: "result", token: s.token, ok: true });
  assert.equal(s.pending, true);
  assert.equal(s.acked, true);
  // Same state (e.g. an attribute-only delta): still pending.
  assert.equal(tileReducer(s, { type: "ha", haState: ha("on", { attributes: { brightness: 3 } }) }), s);
  const done = tileReducer(s, { type: "ha", haState: ha("off") });
  assert.equal(done.pending, false);
  assert.equal(done.optimistic, null);
  assert.equal(done.failed, false);
});

test("HA change before the result ends pending; the late result is ignored", () => {
  let s = tap(IDLE, "light.kitchen", ha("on"));
  const token = s.token;
  s = tileReducer(s, { type: "ha", haState: ha("off") });
  assert.equal(s.pending, false);
  assert.equal(tileReducer(s, { type: "result", token, ok: true }), s);
  assert.equal(tileReducer(s, { type: "result", token, ok: false }), s);
});

test("scene: a new last_changed counts as the change", () => {
  let s = tap(IDLE, "scene.good_night", ha("2026-11-07T07:00:00Z"));
  assert.equal(s.optimistic, null);
  s = tileReducer(s, { type: "result", token: s.token, ok: true });
  s = tileReducer(s, { type: "ha", haState: ha("2026-11-07T07:00:00Z", { last_changed: "2026-11-07T07:05:00Z" }) });
  assert.equal(s.pending, false);
});

test("entity removed while pending ends pending", () => {
  const s = tap(IDLE, "light.kitchen", ha("on"));
  assert.equal(tileReducer(s, { type: "ha", haState: null }).pending, false);
});

test("failure reverts and flags the toast", () => {
  const s = tap(IDLE, "light.kitchen", ha("on"));
  const f = tileReducer(s, { type: "result", token: s.token, ok: false });
  assert.equal(f.pending, false);
  assert.equal(f.optimistic, null);
  assert.equal(f.failed, true);
  // The next tap clears the flag.
  const again = tap(f, "light.kitchen", ha("on"));
  assert.equal(again.failed, false);
  assert.equal(again.token, 2);
});

test("safety timeout after ok shows the actual state", () => {
  let s = tap(IDLE, "light.kitchen", ha("on"));
  s = tileReducer(s, { type: "result", token: s.token, ok: true });
  const done = tileReducer(s, { type: "timeout", token: s.token });
  assert.equal(done.pending, false);
  assert.equal(done.failed, false);
  // A timeout for an older tap does nothing.
  const s2 = tap(done, "light.kitchen", ha("on"));
  assert.equal(tileReducer(s2, { type: "timeout", token: s.token }), s2);
});

test("results for an older tap are ignored", () => {
  let s = tap(IDLE, "light.kitchen", ha("on"));
  s = tileReducer(s, { type: "result", token: s.token, ok: false });
  s = tap(s, "light.kitchen", ha("on"));
  assert.equal(tileReducer(s, { type: "result", token: 1, ok: false }), s);
});

test("tileView: real state, labels and icon overrides", () => {
  const cfg = { entity_id: "light.kitchen", label: null, icon: null, group: "lights", controllable: true };
  const v = tileView(cfg, ha("on"), IDLE);
  assert.deepEqual(v, { name: "Kitchen", value: t("on"), icon: "mdi-lightbulb-on", tone: "on", pending: false });
  const o = tileView({ ...cfg, label: "Kuchyň", icon: "mdi:countertop" }, ha("off"), IDLE);
  assert.equal(o.name, "Kuchyň");
  assert.equal(o.icon, "mdi-countertop");
  assert.equal(o.tone, "");
  assert.equal(tileView({ ...cfg, icon: 'bad icon"' }, ha("off"), IDLE).icon, "mdi-lightbulb-outline");
});

test("tileView: optimistic display while pending", () => {
  const cfg = { entity_id: "light.kitchen", controllable: true };
  const s = tap(IDLE, "light.kitchen", ha("on"));
  const v = tileView(cfg, ha("on"), s);
  assert.equal(v.value, `${t("off")}…`);
  assert.equal(v.icon, "mdi-lightbulb-outline");
  assert.equal(v.tone, "");
  assert.equal(v.pending, true);
  const vac = { entity_id: "vacuum.robot", controllable: true };
  const sv = tap(IDLE, "vacuum.robot", ha("docked"));
  const vv = tileView(vac, ha("docked", { attributes: { friendly_name: "Robot" } }), sv);
  assert.equal(vv.value, `${t("starting")}…`);
  // A tap with no state to predict (scene) shows only the spinner and the real value.
  const ss = tap(IDLE, "scene.good_night", ha("unknown"));
  assert.equal(tileView({ entity_id: "scene.good_night", controllable: true }, ha("unknown"), ss).value, t("scene"));
  assert.equal(vv.tone, "run");
  assert.equal(tileView(vac, ha("cleaning"), IDLE).tone, "run");
  assert.equal(tileView(vac, ha("docked"), IDLE).tone, "");
});

test("tileView: scene shows a fixed label, missing entity shows unavailable", () => {
  const v = tileView({ entity_id: "scene.good_night" }, ha("2026-11-07T07:00:00Z", { attributes: { friendly_name: "Good night" } }), IDLE);
  assert.equal(v.name, "Good night");
  assert.equal(v.value, t("scene"));
  assert.equal(v.tone, "");
  const m = tileView({ entity_id: "light.gone", label: "Gone" }, undefined, IDLE);
  assert.equal(m.value, t("unavailable"));
  assert.equal(m.name, "Gone");
});
