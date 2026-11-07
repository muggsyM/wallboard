// Mirrors wallboard/tests/test_events.py (event_days, bucket_by_day) plus the Task 7 review extras.
import test from "node:test";
import assert from "node:assert/strict";
import { localDate, addDays, daysOf, bucket, isPast, spanPart, monthGrid, noonOf } from "../display/lib/layout.js";

const TZ = "Europe/Prague";
const timed = (start, end, kw = {}) => ({
  id: kw.id ?? "e1", title: kw.title ?? "Ev", location: null, start, end, all_day: false, member_id: "m1", color: "#4f8cff",
});
const allDay = (start, end, kw = {}) => ({
  id: kw.id ?? "a1", title: kw.title ?? "Day", location: null, start, end, all_day: true, member_id: "m1", color: "#4f8cff",
});

test("localDate and addDays", () => {
  assert.equal(localDate(new Date("2026-11-07T23:30:00Z"), TZ), "2026-11-08");
  assert.equal(localDate(Date.parse("2026-11-07T22:59:59Z"), TZ), "2026-11-07");
  assert.equal(addDays("2026-11-30", 1), "2026-12-01");
  assert.equal(addDays("2026-03-28", 2), "2026-03-30"); // across spring-forward
  assert.equal(addDays("2026-10-24", 2), "2026-10-26"); // across fall-back
  assert.equal(addDays("2026-01-01", -1), "2025-12-31");
});

test("all-day end exclusive", () => {
  assert.deepEqual(daysOf(allDay("2026-11-20", "2026-11-23"), TZ), ["2026-11-20", "2026-11-21", "2026-11-22"]);
});

test("all-day end not after start covers start day", () => {
  assert.deepEqual(daysOf(allDay("2026-11-20", "2026-11-20"), TZ), ["2026-11-20"]);
  assert.deepEqual(daysOf(allDay("2026-11-20", "2026-11-18"), TZ), ["2026-11-20"]);
});

test("overnight timed", () => {
  assert.deepEqual(daysOf(timed("2026-11-07T23:00:00+01:00", "2026-11-08T01:00:00+01:00"), TZ), ["2026-11-07", "2026-11-08"]);
});

test("ends at midnight", () => {
  assert.deepEqual(daysOf(timed("2026-11-07T22:00:00+01:00", "2026-11-08T00:00:00+01:00"), TZ), ["2026-11-07"]);
});

test("timed end before or at start covers start day", () => {
  assert.deepEqual(daysOf(timed("2026-11-07T22:00:00+01:00", "2026-11-07T21:00:00+01:00"), TZ), ["2026-11-07"]);
  assert.deepEqual(daysOf(timed("2026-11-07T22:00:00+01:00", "2026-11-07T22:00:00+01:00"), TZ), ["2026-11-07"]);
});

test("DST fall-back day", () => {
  assert.deepEqual(daysOf(timed("2026-10-25T01:30:00+02:00", "2026-10-25T03:30:00+01:00"), TZ), ["2026-10-25"]);
});

test("DST spring-forward day 01:30-03:30", () => {
  assert.deepEqual(daysOf(timed("2026-03-29T01:30:00+01:00", "2026-03-29T03:30:00+02:00"), TZ), ["2026-03-29"]);
});

test("UTC input crossing local midnight", () => {
  assert.deepEqual(daysOf(timed("2026-11-07T23:30:00Z", "2026-11-08T00:30:00Z"), TZ), ["2026-11-08"]);
});

test("foreign -05:00 offset lands on the next Prague day", () => {
  // 20:00-05:00 = 01:00Z = 02:00 Prague on the 8th
  assert.deepEqual(daysOf(timed("2026-11-07T20:00:00-05:00", "2026-11-07T21:00:00-05:00"), TZ), ["2026-11-08"]);
});

test("sort order: all-day, real start, title casefold, id", () => {
  const a = allDay("2026-11-07", "2026-11-08", { id: "a", title: "Zebra" });
  const t2 = timed("2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00", { id: "t2", title: "b" });
  const t1 = timed("2026-11-07T09:00:00+01:00", "2026-11-07T11:00:00+01:00", { id: "t1", title: "Z" });
  const t3 = timed("2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00", { id: "t3", title: "A" });
  const t4 = timed("2026-11-07T10:00:00+01:00", "2026-11-07T11:00:00+01:00", { id: "t0", title: "a" });
  const out = bucket([t2, t1, a, t3, t4], "2026-11-07", 1, TZ);
  assert.deepEqual(out.get("2026-11-07").map((e) => e.id), ["a", "t1", "t0", "t3", "t2"]);
});

test("sort uses the real instant, not the offset string", () => {
  // 09:30+02:00 (08:30 Prague) is earlier than 09:00+01:00.
  const x = timed("2026-11-07T09:00:00+01:00", "2026-11-07T10:00:00+01:00", { id: "x" });
  const y = timed("2026-11-07T09:30:00+02:00", "2026-11-07T10:00:00+02:00", { id: "y" });
  assert.deepEqual(bucket([x, y], "2026-11-07", 1, TZ).get("2026-11-07").map((e) => e.id), ["y", "x"]);
});

test("bucket window clip and empty days", () => {
  const ev = allDay("2026-11-05", "2026-11-12", { id: "long" });
  let out = bucket([ev], "2026-11-07", 3, TZ);
  assert.deepEqual([...out.keys()], ["2026-11-07", "2026-11-08", "2026-11-09"]);
  for (const v of out.values()) assert.deepEqual(v.map((e) => e.id), ["long"]);
  out = bucket([ev], "2026-12-01", 2, TZ);
  assert.deepEqual([...out.entries()], [["2026-12-01", []], ["2026-12-02", []]]);
});

test("multi-day timed sorts by real start each day", () => {
  const long = timed("2026-11-07T20:00:00+01:00", "2026-11-09T08:00:00+01:00", { id: "long" });
  const short = timed("2026-11-08T09:00:00+01:00", "2026-11-08T10:00:00+01:00", { id: "short" });
  const out = bucket([short, long], "2026-11-07", 3, TZ);
  assert.deepEqual(out.get("2026-11-07").map((e) => e.id), ["long"]);
  assert.deepEqual(out.get("2026-11-08").map((e) => e.id), ["long", "short"]);
  assert.deepEqual(out.get("2026-11-09").map((e) => e.id), ["long"]);
});

test("isPast: timed events end <= now", () => {
  const ev = timed("2026-11-07T07:00:00+01:00", "2026-11-07T08:00:00+01:00");
  assert.equal(isPast(ev, Date.parse("2026-11-07T07:59:59+01:00"), TZ), false);
  assert.equal(isPast(ev, Date.parse("2026-11-07T08:00:00+01:00"), TZ), true);
  assert.equal(isPast(ev, new Date("2026-11-07T09:00:00+01:00"), TZ), true);
});

test("isPast: all-day ends at local midnight of the exclusive end date", () => {
  const ev = allDay("2026-11-07", "2026-11-08");
  assert.equal(isPast(ev, Date.parse("2026-11-07T23:59:00+01:00"), TZ), false);
  assert.equal(isPast(ev, Date.parse("2026-11-08T00:00:00+01:00"), TZ), true);
  // 23:30Z on the 7th is already 00:30 on the 8th in Prague
  assert.equal(isPast(ev, Date.parse("2026-11-07T23:30:00Z"), TZ), true);
  // malformed end before start still lasts its start day
  assert.equal(isPast(allDay("2026-11-07", "2026-11-05"), Date.parse("2026-11-07T12:00:00+01:00"), TZ), false);
});

test("spanPart for the 20-22 Nov trip", () => {
  const trip = allDay("2026-11-20", "2026-11-23");
  assert.equal(spanPart(trip, "2026-11-20"), "start");
  assert.equal(spanPart(trip, "2026-11-21"), "middle");
  assert.equal(spanPart(trip, "2026-11-22"), "end");
  assert.equal(spanPart(allDay("2026-11-20", "2026-11-21"), "2026-11-20"), "single");
  assert.equal(spanPart(timed("2026-11-07T23:00:00+01:00", "2026-11-08T01:00:00+01:00"), "2026-11-07"), "single");
});

test("monthGrid is Monday-first, 6 weeks", () => {
  const g = monthGrid("2026-11-07");
  assert.equal(g.length, 42);
  assert.equal(g[0], "2026-10-26");
  assert.equal(g[41], "2026-12-06");
  // month starting on Monday starts on the 1st
  assert.equal(monthGrid("2026-06-15")[0], "2026-06-01");
});

test("noonOf is local noon of the date", () => {
  assert.equal(noonOf("2026-11-07", TZ).toISOString(), "2026-11-07T11:00:00.000Z");
  assert.equal(localDate(noonOf("2026-11-07", "Pacific/Kiritimati"), "Pacific/Kiritimati"), "2026-11-07");
});
