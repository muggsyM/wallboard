import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  setStrings, setLanguage, getLanguage, loadLanguage, t, setTimezone,
  fmtTime, fmtLongDate, fmtWeekdayShort, fmtMonthYear, fmtShortDayMonth, fmtNumber,
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
