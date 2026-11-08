import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  tn, setStrings, setLanguage, getLanguage, loadLanguage, t, setTimezone,
  fmtTime, fmtLongDate, fmtWeekdayShort, fmtMonthYear, fmtShortDayMonth, fmtNumber, fmtDayMonthShort, fmtMonthRange,
} from "../shared/i18n.js";

const load = (n) => JSON.parse(readFileSync(new URL(`../shared/i18n/${n}.json`, import.meta.url), "utf8"));
const cs = load("cs");
const en = load("en");
setStrings("cs", cs);
setStrings("en", en);
setTimezone("Europe/Prague");

const d = new Date("2026-11-07T07:42:00+01:00");

test("cs and en have identical key sets", () => {
  assert.deepEqual(Object.keys(cs).sort(), Object.keys(en).sort());
});

test("brief keys exist", () => {
  const keys = "today tomorrow this_week more_n no_events home rooms lights devices status locked unlocked open closed on off docked cleaning returning starting action_failed offline high_low all_day".split(" ");
  for (const k of keys) { assert.ok(k in en, k); assert.ok(k in cs, k); }
});

test("t interpolates and falls back", () => {
  setLanguage("cs");
  assert.equal(t("more_n", { n: 2 }), "+2 další");
  assert.equal(t("no_such_key"), "no_such_key");
  setLanguage("en");
  assert.equal(t("more_n", { n: 2 }), "+2 more");
  setStrings("xx", { a: "x" });
  setLanguage("xx");
  assert.equal(t("today"), en.today); // falls back to en
  assert.equal(t("a"), "x");
});

test("formatters in cs", () => {
  setLanguage("cs");
  assert.equal(fmtTime(d), "07:42");
  assert.equal(fmtLongDate(d), "sobota 7. listopadu");
  assert.equal(fmtWeekdayShort(d), "so");
  assert.equal(fmtMonthYear(d), "listopad 2026");
  assert.equal(fmtShortDayMonth(d), "7. 11.");
  assert.equal(fmtNumber(21.5), "21,5");
});

test("formatters in en", () => {
  setLanguage("en");
  assert.equal(fmtTime(d), "07:42");
  assert.equal(fmtLongDate(d), "Saturday 7 November");
  assert.equal(fmtWeekdayShort(d), "Sat");
  assert.equal(fmtMonthYear(d), "November 2026");
  assert.equal(fmtShortDayMonth(d), "7/11");
  assert.equal(fmtNumber(21.5), "21.5");
});

test("fmtDayMonthShort and fmtMonthRange", () => {
  const dec1 = new Date("2026-12-01T12:00:00+01:00");
  const nov2 = new Date("2026-11-02T12:00:00+01:00");
  const nov29 = new Date("2026-11-29T12:00:00+01:00");
  const dec6 = new Date("2026-12-06T12:00:00+01:00");
  const dec28 = new Date("2026-12-28T12:00:00+01:00");
  const jan31 = new Date("2027-01-31T12:00:00+01:00");
  setLanguage("cs");
  assert.deepEqual(fmtDayMonthShort(dec1), { day: "1.", month: "pro" });
  assert.equal(fmtMonthRange(nov2, nov29), "listopad 2026");
  assert.equal(fmtMonthRange(nov2, dec6), "listopad – prosinec 2026");
  assert.equal(fmtMonthRange(dec28, jan31), "prosinec 2026 – leden 2027");
  setLanguage("en");
  assert.deepEqual(fmtDayMonthShort(dec1), { day: "1", month: "Dec" });
  assert.equal(fmtMonthRange(nov2, nov29), "November 2026");
  assert.equal(fmtMonthRange(nov2, dec6), "November – December 2026");
  assert.equal(fmtMonthRange(dec28, jan31), "December 2026 – January 2027");
});

test("timezone is honoured, midnight is 00 not 24", () => {
  setLanguage("en");
  setTimezone("Asia/Tokyo");
  assert.equal(fmtTime(d), "15:42");
  setTimezone("Europe/Prague");
  assert.equal(fmtTime(new Date("2026-11-06T23:05:00Z")), "00:05");
});

test("loadLanguage uses injected fetch", async () => {
  const calls = [];
  const f = async (url) => { calls.push(String(url)); return { ok: true, json: async () => ({ today: "Heute" }) }; };
  await loadLanguage("de", f);
  assert.equal(calls.length, 1);
  assert.match(calls[0], /de\.json$/);
  setLanguage("de");
  assert.equal(t("today"), "Heute");
  setLanguage("cs");
  assert.equal(getLanguage(), "cs");
});

test("invalid timezone falls back to Europe/Prague instead of breaking fmt", (tc) => {
  const warn = tc.mock.method(console, "warn", () => {});
  setLanguage("cs");
  setTimezone("Bad/Zone");
  assert.equal(fmtTime(new Date("2026-11-06T23:05:00Z")), "00:05");
  assert.equal(warn.mock.callCount(), 1);
  setTimezone("Europe/Prague");
});

test("tn picks the plural form for the language", () => {
  setLanguage("en");
  assert.equal(tn("lights_on", 1), "1 light on");
  assert.equal(tn("lights_on", 3), "3 lights on");
  setLanguage("cs");
  assert.equal(tn("lights_on", 1), "1 světlo svítí");
  assert.equal(tn("lights_on", 4), "4 světla svítí");
  assert.equal(tn("lights_on", 5), "5 světel svítí");
  assert.equal(tn("lights_on", 0), "0 světel svítí");
  setStrings("cs", { ...cs, x_other: "{n} x" });
  assert.equal(tn("x", 2), "2 x"); // falls back to _other
  setStrings("cs", cs);
});
