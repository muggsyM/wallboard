import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setStrings, setLanguage, setTimezone } from "../shared/i18n.js";
import { rainHint, nowLineIndex, todaySubtitle, photoAction } from "../display/pages/today.js";

const load = (n) => JSON.parse(readFileSync(new URL(`../shared/i18n/${n}.json`, import.meta.url), "utf8"));
setStrings("cs", load("cs"));
setStrings("en", load("en"));
setTimezone("Europe/Prague");

// 2026-11-07 is a Saturday.
const day = (date, p, extra = {}) => ({ date, condition: "rainy", high: 10, low: 2, precip_probability: p, ...extra });
const week = (probs) => probs.map((p, i) => day(`2026-11-${String(7 + i).padStart(2, "0")}`, p));

test("rain hint: first day with >= 50 % in today..+6", () => {
  setLanguage("en");
  assert.equal(rainHint(week([10, 20, 30, 40, 60, 80, 90]), "2026-11-07"), "rain Wednesday");
  assert.equal(rainHint(week([50, 0]), "2026-11-07"), "rain today");
  assert.equal(rainHint(week([0, 75]), "2026-11-07"), "rain tomorrow");
  setLanguage("cs");
  assert.equal(rainHint(week([0, 0, 0, 0, 0, 60]), "2026-11-07"), "déšť ve čtvrtek");
  assert.equal(rainHint(week([0, 0, 0, 0, 55]), "2026-11-07"), "déšť ve středu");
  assert.equal(rainHint(week([0, 0, 0, 0, 0, 0, 99]), "2026-11-07"), "déšť v pátek");
  assert.equal(rainHint(week([0, 0, 70]), "2026-11-07"), "déšť v pondělí");
  assert.equal(rainHint(week([99]), "2026-11-07"), "dnes déšť");
  assert.equal(rainHint(week([0, 99]), "2026-11-07"), "zítra déšť");
});

test("rain hint: nothing when no day qualifies", () => {
  setLanguage("en");
  assert.equal(rainHint(week([0, 10, 49, 49, 0, 0, 0]), "2026-11-07"), "");
  assert.equal(rainHint([], "2026-11-07"), "");
  assert.equal(rainHint(null, "2026-11-07"), "");
  assert.equal(rainHint(week([0, 0, 0, 0, 0, 0, 0, 100]), "2026-11-07"), ""); // +7 is out of range
});

test("rain hint: ignores stale days and missing or bad values; order-independent", () => {
  setLanguage("en");
  const daily = [day("2026-11-09", 80), day("2026-11-06", 100), day("2026-11-08", null), day("2026-11-07", "x")];
  assert.equal(rainHint(daily, "2026-11-07"), "rain Monday");
  assert.equal(rainHint([day("2026-11-08", "65")], "2026-11-07"), "rain tomorrow"); // numeric string
});

test("today subtitle joins high/low and the rain hint", () => {
  setLanguage("en");
  assert.equal(todaySubtitle({ current: {}, daily: week([0, 0, 0, 0, 60]) }, "2026-11-07"), "10° / 2° · rain Wednesday");
  assert.equal(todaySubtitle({ daily: week([0]) }, "2026-11-07"), "10° / 2°");
  assert.equal(todaySubtitle({ daily: [day("2026-11-08", 90)] }, "2026-11-07"), "rain tomorrow");
  assert.equal(todaySubtitle(null, "2026-11-07"), "");
});

const ev = (id, start, end) => ({ id, title: id, start, end, all_day: false, color: "#4f8cff" });
const at = (hhmm) => Date.parse(`2026-11-07T${hhmm}:00+01:00`);

test("now line sits after the last past event", () => {
  const evs = [
    ev("run", "2026-11-07T07:00:00+01:00", "2026-11-07T08:00:00+01:00"),
    ev("match", "2026-11-07T09:00:00+01:00", "2026-11-07T11:00:00+01:00"),
    ev("dentist", "2026-11-07T14:30:00+01:00", "2026-11-07T15:15:00+01:00"),
  ];
  const tz = "Europe/Prague";
  assert.equal(nowLineIndex(evs, at("06:00"), tz), 0);
  assert.equal(nowLineIndex(evs, at("07:30"), tz), 0); // run is ongoing, not past
  assert.equal(nowLineIndex(evs, at("08:00"), tz), 1); // ended exactly now = past
  assert.equal(nowLineIndex(evs, at("10:00"), tz), 1);
  assert.equal(nowLineIndex(evs, at("12:00"), tz), 2);
  assert.equal(nowLineIndex(evs, at("20:00"), tz), 3);
  assert.equal(nowLineIndex([], at("20:00"), tz), 0);
});

test("now line: a long event still running above a finished one", () => {
  const evs = [
    ev("long", "2026-11-07T09:00:00+01:00", "2026-11-07T17:00:00+01:00"),
    ev("short", "2026-11-07T10:00:00+01:00", "2026-11-07T10:30:00+01:00"),
    ev("later", "2026-11-07T18:00:00+01:00", "2026-11-07T19:00:00+01:00"),
  ];
  assert.equal(nowLineIndex(evs, at("12:00"), "Europe/Prague"), 2);
});

test("photo: keep the visit's photo (even a failed one) while it is in the list", () => {
  const ids = (...x) => new Set(x);
  assert.equal(photoAction("a", ids("a", "b")), "keep");
  assert.equal(photoAction("a", ids("b")), "pick"); // deleted: pick another
  assert.equal(photoAction("a", ids()), "hide"); // deleted, none left
  assert.equal(photoAction(null, ids("a")), "pick"); // photos arrived after mount
  assert.equal(photoAction(null, ids()), "keep"); // nothing to show, nothing to do
});
