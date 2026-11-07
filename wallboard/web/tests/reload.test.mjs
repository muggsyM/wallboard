import test from "node:test";
import assert from "node:assert/strict";
import { trackBuild, nightlyReloadMinute, crossedMinute } from "../display/lib/reload.js";

test("first snapshot only remembers the build", () => {
  assert.deepEqual(trackBuild(null, "abc"), { known: "abc", reload: false });
});

test("same build: no reload", () => {
  assert.deepEqual(trackBuild("abc", "abc"), { known: "abc", reload: false });
});

test("different build: reload, keep the old known build until the reload happens", () => {
  assert.deepEqual(trackBuild("abc", "def"), { known: "abc", reload: true });
});

test("a snapshot without a build never reloads", () => {
  assert.deepEqual(trackBuild("abc", undefined), { known: "abc", reload: false });
  assert.deepEqual(trackBuild(null, null), { known: null, reload: false });
});

test("nightly reload at night-mode start, else 03:30", () => {
  assert.equal(nightlyReloadMinute({ enabled: true, start: "23:00", end: "06:00" }), 23 * 60);
  assert.equal(nightlyReloadMinute({ enabled: false, start: "23:00", end: "06:00" }), 3 * 60 + 30);
  assert.equal(nightlyReloadMinute({ enabled: true, start: "bad", end: "06:00" }), 3 * 60 + 30);
  assert.equal(nightlyReloadMinute(null), 3 * 60 + 30);
});

test("crossedMinute: fires once when local time reaches the target", () => {
  assert.equal(crossedMinute(null, 210, 210), false); // first tick after load never fires
  assert.equal(crossedMinute(209, 209, 210), false);
  assert.equal(crossedMinute(209, 210, 210), true);
  assert.equal(crossedMinute(210, 210, 210), false); // already past: no repeat
  assert.equal(crossedMinute(200, 215, 210), true); // a stalled tick still fires
});

test("crossedMinute: wraps midnight", () => {
  assert.equal(crossedMinute(1439, 0, 0), true);
  assert.equal(crossedMinute(1439, 5, 3), true);
  assert.equal(crossedMinute(1439, 0, 23 * 60), false);
  assert.equal(crossedMinute(1380, 1379, 210), false); // clock stepped back a minute (NTP)
});
