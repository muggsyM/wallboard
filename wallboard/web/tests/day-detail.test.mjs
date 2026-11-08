import test from "node:test";
import assert from "node:assert/strict";
import { mergeSameAllDay, bucket } from "../display/lib/layout.js";
import { dayDetail, createIdleTimer } from "../display/lib/day-detail.js";
import { setLanguage, setTimezone } from "../shared/i18n.js";

setLanguage("cs");
setTimezone("Europe/Prague");
const TZ = "Europe/Prague";

const allDay = (id, title, member, start, end) => ({
  id, title, location: null, start, end, all_day: true, member_id: member, color: `#${member === "lily" ? "f49ab4" : member === "maty" ? "4fd1a1" : "7b2d8e"}`,
});
const timed = (id, title, member, start, end, location = null) => ({
  id, title, location, start, end, all_day: false, member_id: member, color: "#1a2fd6",
});

// ---- mergeSameAllDay ----

test("mergeSameAllDay: the same single-day all-day title of two members becomes one entry", () => {
  const a = allDay("a", "Školka", "lily", "2026-11-12", "2026-11-13");
  const b = allDay("b", "Školka", "maty", "2026-11-12", "2026-11-13");
  const out = mergeSameAllDay([a, b]);
  assert.equal(out.length, 1);
  assert.equal(out[0].title, "Školka");
  assert.equal(out[0].id, "a");
  assert.deepEqual(out[0].members, [{ id: "lily", color: "#f49ab4" }, { id: "maty", color: "#4fd1a1" }]);
  assert.deepEqual(out[0].events, [a, b]);
  assert.equal(out[0].all_day, true);
  assert.equal(a.members, undefined, "inputs are not mutated");
});

test("mergeSameAllDay: keeps order, position of the first event, and untouched objects", () => {
  const trip = allDay("trip", "Školka", "mom", "2026-11-11", "2026-11-14"); // multi-day: never merged
  const gym = allDay("gym", "Gymnathon", "lily", "2026-11-12", "2026-11-13");
  const s1 = allDay("s1", "Školka", "lily", "2026-11-12", "2026-11-13");
  const run = timed("run", "Školka", "maty", "2026-11-12T08:00:00+01:00", "2026-11-12T09:00:00+01:00");
  const s2 = allDay("s2", " Školka ", "maty", "2026-11-12", "2026-11-13");
  const out = mergeSameAllDay([trip, gym, s1, run, s2]);
  assert.equal(out.length, 4);
  assert.equal(out[0], trip);
  assert.equal(out[1], gym, "single entries stay the same object");
  assert.deepEqual(out[2].members.map((m) => m.id), ["lily", "maty"]);
  assert.equal(out[3], run);
});

test("mergeSameAllDay: different titles stay apart; one member's duplicate shows once", () => {
  const a = allDay("a", "Školka", "lily", "2026-11-12", "2026-11-13");
  const b = allDay("b", "Škola", "maty", "2026-11-12", "2026-11-13");
  const dup = allDay("dup", "Školka", "lily", "2026-11-12", "2026-11-13");
  const out = mergeSameAllDay([a, b, dup]);
  assert.deepEqual(out, [a, b]);
  assert.deepEqual(mergeSameAllDay([]), []);
  assert.deepEqual(mergeSameAllDay(undefined), []);
});

test("mergeSameAllDay: three members, merged entry counts once against a capacity", () => {
  const evs = ["lily", "maty", "mom"].map((m, i) => allDay(`s${i}`, "Výlet", m, "2026-11-12", "2026-11-13"));
  const out = mergeSameAllDay(bucket(evs, "2026-11-12", 1, TZ).get("2026-11-12"));
  assert.equal(out.length, 1);
  assert.equal(out[0].members.length, 3);
});

// ---- dayDetail ----

const DAY = "2026-11-12";
const EVENTS = [
  timed("late", "Rodičovské schůzky", "mom", "2026-11-12T18:00:00+01:00", "2026-11-12T19:00:00+01:00", "ZŠ Korunovační"),
  timed("early", "Fotbal", "maty", "2026-11-12T15:30:00+01:00", "2026-11-12T17:00:00+01:00"),
  allDay("s1", "Školka", "lily", "2026-11-12", "2026-11-13"),
  allDay("s2", "Školka", "maty", "2026-11-12", "2026-11-13"),
  allDay("trip", "Slovinsko", "dad", "2026-11-12", "2026-11-14"),
  timed("night", "Noční vlak", "dad", "2026-11-11T22:00:00+01:00", "2026-11-12T06:10:00+01:00"),
  timed("away", "Konference", "dad", "2026-11-12T20:00:00+01:00", "2026-11-14T12:00:00+01:00"),
  timed("other", "Jiný den", "mom", "2026-11-13T10:00:00+01:00", "2026-11-13T11:00:00+01:00"),
  timed("long", "Celý týden", "mom", "2026-11-10T10:00:00+01:00", "2026-11-15T11:00:00+01:00"),
];

test("dayDetail: all-day merged first, timed by start with time labels, count", () => {
  const d = dayDetail(EVENTS, DAY, TZ, { "2026-11-12": "Svátek" }, null);
  assert.equal(d.iso, DAY);
  // The usual event order (layout.js compare: code units, so "Š" sorts after "S").
  assert.deepEqual(d.allDay.map((e) => e.title), ["Slovinsko", "Školka"]);
  assert.deepEqual(d.allDay[1].members.map((m) => m.id), ["lily", "maty"]);
  assert.deepEqual(d.timed.map((x) => [x.ev.id, x.time]), [
    ["long", ""], // runs through the whole day
    ["night", "–06:10"], // started the day before
    ["early", "15:30"],
    ["late", "18:00"],
    ["away", "20:00–"], // ends on a later day
  ]);
  assert.equal(d.count, 7);
  assert.equal(d.holiday, "Svátek");
  assert.equal(d.weather, null);
});

test("dayDetail: weather for the date, high/low rounded or null", () => {
  const weather = { daily: [{ date: "2026-11-11", condition: "sunny", high: 5, low: 0 }, { date: DAY, condition: "cloudy", high: 9.6, low: "x" }] };
  assert.deepEqual(dayDetail([], DAY, TZ, {}, weather).weather, { condition: "cloudy", high: 10, low: null });
  assert.equal(dayDetail([], "2026-11-20", TZ, {}, weather).weather, null);
});

test("dayDetail: empty day", () => {
  const d = dayDetail(EVENTS, "2026-11-20", TZ, { "2026-11-20": 3 }, undefined);
  assert.deepEqual([d.allDay, d.timed, d.count, d.holiday], [[], [], 0, ""]);
  assert.equal(dayDetail(undefined, DAY, TZ, undefined, undefined).count, 0);
});

test("dayDetail: an event ending at midnight stays on its start day", () => {
  const ev = timed("party", "Oslava", "mom", "2026-11-12T20:00:00+01:00", "2026-11-13T00:00:00+01:00");
  assert.deepEqual(dayDetail([ev], DAY, TZ, {}, null).timed.map((x) => x.time), ["20:00"]);
  assert.equal(dayDetail([ev], "2026-11-13", TZ, {}, null).count, 0);
});

// ---- createIdleTimer ----

function fakeClock() {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  return {
    setTimeout: (fn, ms) => (timers.set(++seq, { fn, at: now + ms }), seq),
    clearTimeout: (id) => timers.delete(id),
    advance(ms) {
      now += ms;
      for (const [id, x] of [...timers]) if (x.at <= now) (timers.delete(id), x.fn());
    },
    pending: () => timers.size,
  };
}

test("createIdleTimer: expires once after ms without a poke", () => {
  const clock = fakeClock();
  let fired = 0;
  const idle = createIdleTimer({ ms: 30000, onExpire: () => fired++, ...clock });
  assert.equal(idle.running(), false);
  idle.start();
  assert.equal(idle.running(), true);
  clock.advance(29999);
  assert.equal(fired, 0);
  clock.advance(1);
  assert.equal(fired, 1);
  assert.equal(idle.running(), false);
  clock.advance(60000);
  assert.equal(fired, 1);
});

test("createIdleTimer: poke restarts the countdown; stop cancels; poke after stop does nothing", () => {
  const clock = fakeClock();
  let fired = 0;
  const idle = createIdleTimer({ ms: 30000, onExpire: () => fired++, ...clock });
  idle.start();
  clock.advance(20000);
  idle.poke();
  clock.advance(20000);
  assert.equal(fired, 0);
  clock.advance(10000);
  assert.equal(fired, 1);
  idle.start();
  idle.stop();
  assert.equal(clock.pending(), 0);
  idle.poke();
  assert.equal(clock.pending(), 0);
  clock.advance(60000);
  assert.equal(fired, 1);
});
