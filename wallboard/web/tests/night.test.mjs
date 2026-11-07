import test from "node:test";
import assert from "node:assert/strict";
import { isNight } from "../display/lib/night.js";

const TZ = "Europe/Prague";
// 2026-11-07 is CET (UTC+1): local HH:MM = UTC + 1 h.
const at = (hhmm) => new Date(`2026-11-07T${hhmm}:00+01:00`).getTime();
const N = { enabled: true, start: "23:00", end: "06:00" };

test("disabled is never night", () => {
  assert.equal(isNight(at("02:00"), { ...N, enabled: false }, TZ), false);
  assert.equal(isNight(at("02:00"), null, TZ), false);
});

test("wraps midnight: [23:00, 06:00)", () => {
  assert.equal(isNight(at("22:59"), N, TZ), false);
  assert.equal(isNight(at("23:00"), N, TZ), true);
  assert.equal(isNight(at("23:59"), N, TZ), true);
  assert.equal(isNight(at("00:00"), N, TZ), true);
  assert.equal(isNight(at("05:59"), N, TZ), true);
  assert.equal(isNight(at("06:00"), N, TZ), false);
  assert.equal(isNight(at("12:00"), N, TZ), false);
});

test("same-day window: [13:00, 15:30)", () => {
  const n = { enabled: true, start: "13:00", end: "15:30" };
  assert.equal(isNight(at("12:59"), n, TZ), false);
  assert.equal(isNight(at("13:00"), n, TZ), true);
  assert.equal(isNight(at("15:29"), n, TZ), true);
  assert.equal(isNight(at("15:30"), n, TZ), false);
});

test("uses the configured timezone, not the host's", () => {
  // 23:30 in Prague is 22:30 UTC and 17:30 in New York.
  const t = at("23:30");
  assert.equal(isNight(t, N, "Europe/Prague"), true);
  assert.equal(isNight(t, N, "UTC"), false);
  assert.equal(isNight(t, N, "America/New_York"), false);
});

test("accepts a Date", () => {
  assert.equal(isNight(new Date(at("01:00")), N, TZ), true);
});

test("empty or malformed window is never night", () => {
  assert.equal(isNight(at("01:00"), { enabled: true, start: "06:00", end: "06:00" }, TZ), false);
  assert.equal(isNight(at("01:00"), { enabled: true, start: "late", end: "06:00" }, TZ), false);
  assert.equal(isNight(at("01:00"), { enabled: true, start: "25:00", end: "06:00" }, TZ), false);
});

test("unknown timezone falls back to Europe/Prague", () => {
  assert.equal(isNight(at("23:30"), N, "Not/AZone"), true);
});
