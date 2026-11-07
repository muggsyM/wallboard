import test from "node:test";
import assert from "node:assert/strict";
import { assignLanes, barPiece, monthGrid } from "../display/lib/layout.js";
import { clampMaxEvents } from "../display/pages/month.js";
import month from "../display/pages/month.js";
import { pageOptions } from "../display/lib/page-options.js";

const allDay = (id, start, end) => ({ id, title: id, location: null, start, end, all_day: true, member_id: "m", color: "#4f8cff" });
const timed = (id, start, end) => ({ id, title: id, location: null, start, end, all_day: false, member_id: "m", color: "#4f8cff" });
// Week of Mon 16 – Sun 22 Nov 2026.
const WEEK = monthGrid("2026-11-07").slice(21, 28);

test("week fixture is Monday to Sunday", () => {
  assert.deepEqual([WEEK[0], WEEK[6]], ["2026-11-16", "2026-11-22"]);
});

test("assignLanes: overlapping bars get separate lanes, free lanes are reused", () => {
  const ski = allDay("ski", "2026-11-20", "2026-11-23"); // Fri-Sun
  const off = allDay("off", "2026-11-21", "2026-11-23"); // Sat-Sun, overlaps ski
  const trip = allDay("trip", "2026-11-16", "2026-11-18"); // Mon-Tue, before ski
  const lanes = assignLanes(WEEK, [off, ski, trip]);
  assert.equal(lanes.get(trip), 0);
  assert.equal(lanes.get(ski), 0);
  assert.equal(lanes.get(off), 1);
});

test("assignLanes: longer bar wins the lower lane when bars start the same day", () => {
  const short = allDay("a-short", "2026-11-17", "2026-11-19");
  const long = allDay("z-long", "2026-11-17", "2026-11-21");
  const lanes = assignLanes(WEEK, [short, long]);
  assert.equal(lanes.get(long), 0);
  assert.equal(lanes.get(short), 1);
});

test("assignLanes: ignores single-day, timed and out-of-week events; clips at the week start", () => {
  const single = allDay("single", "2026-11-17", "2026-11-18");
  const night = timed("night", "2026-11-17T23:00:00+01:00", "2026-11-18T01:00:00+01:00");
  const before = allDay("before", "2026-11-09", "2026-11-12");
  const across = allDay("across", "2026-11-14", "2026-11-18"); // Sat 14 - Tue 17
  const late = allDay("late", "2026-11-16", "2026-11-17"); // single day
  const lanes = assignLanes(WEEK, [single, night, before, across, late]);
  assert.deepEqual([...lanes.entries()].map(([e, l]) => [e.id, l]), [["across", 0]]);
});

test("barPiece: ski weekend Fri-Sun is one bar with rounded outer ends", () => {
  const ski = allDay("ski", "2026-11-20", "2026-11-23");
  assert.deepEqual(barPiece(ski, "2026-11-20", 4), { title: true, roundL: true, roundR: false, extL: false, extR: true });
  assert.deepEqual(barPiece(ski, "2026-11-21", 5), { title: false, roundL: false, roundR: false, extL: true, extR: true });
  assert.deepEqual(barPiece(ski, "2026-11-22", 6), { title: false, roundL: false, roundR: true, extL: true, extR: false });
});

test("barPiece: breaks at the week boundary and repeats the title on Monday", () => {
  const trip = allDay("trip", "2026-11-14", "2026-11-18"); // Sat 14 - Tue 17
  assert.deepEqual(barPiece(trip, "2026-11-15", 6), { title: false, roundL: false, roundR: false, extL: true, extR: false });
  assert.deepEqual(barPiece(trip, "2026-11-16", 0), { title: true, roundL: false, roundR: false, extL: false, extR: true });
  assert.deepEqual(barPiece(trip, "2026-11-17", 1), { title: false, roundL: false, roundR: true, extL: true, extR: false });
});

test("max_events_per_day setting: default 3, clamped to 1..6", () => {
  assert.deepEqual(month.settings, [{ key: "max_events_per_day", type: "number", labelKey: "max_events_per_day", default: 3 }]);
  assert.equal(clampMaxEvents(pageOptions(month, {}).max_events_per_day), 3);
  assert.equal(clampMaxEvents(0), 1);
  assert.equal(clampMaxEvents(9), 6);
  assert.equal(clampMaxEvents("4"), 4);
  assert.equal(clampMaxEvents(2.6), 3);
  assert.equal(clampMaxEvents("x"), 3);
  assert.equal(clampMaxEvents(undefined), 3);
});
