import test from "node:test";
import assert from "node:assert/strict";
import { Rotator } from "../display/lib/rotation.js";

const S = 1000;
const PAGES = [{ type: "today" }, { type: "week" }, { type: "month" }, { type: "home" }];
const make = (over = {}) => new Rotator({ pages: PAGES, defaultSeconds: 30, pauseSeconds: 120, now: 0, ...over });

test("starts on the first page", () => {
  assert.equal(make().current().type, "today");
});

test("advances after defaultSeconds", () => {
  const r = make();
  assert.equal(r.tick(29 * S), false);
  assert.equal(r.tick(30 * S), true);
  assert.equal(r.next(30 * S).type, "week");
  assert.equal(r.tick(59 * S), false);
  assert.equal(r.tick(60 * S), true);
});

test("per-page seconds override", () => {
  const r = make({ pages: [{ type: "today", seconds: 10 }, { type: "week" }] });
  assert.equal(r.tick(9 * S), false);
  assert.equal(r.tick(10 * S), true);
  r.next(10 * S);
  assert.equal(r.tick(39 * S), false);
  assert.equal(r.tick(40 * S), true);
});

test("non-positive seconds fall back to the default", () => {
  const r = make({ pages: [{ type: "today", seconds: 0 }, { type: "week" }] });
  assert.equal(r.tick(29 * S), false);
  assert.equal(r.tick(30 * S), true);
});

test("disabled pages are skipped", () => {
  const r = make({ pages: [{ type: "today" }, { type: "week", enabled: false }, { type: "month" }] });
  assert.deepEqual(r.pages.map((p) => p.type), ["today", "month"]);
  assert.equal(r.next(1).type, "month");
  assert.equal(r.next(2).type, "today");
});

test("touch pauses for pauseSeconds, then the page gets its full time again", () => {
  const r = make();
  r.touch(20 * S);
  assert.equal(r.tick(30 * S), false);
  assert.equal(r.tick(139 * S), false); // paused until 140 s
  assert.equal(r.tick(140 * S), false); // resumed; timer restarts at pause end
  assert.equal(r.tick(169 * S), false);
  assert.equal(r.tick(170 * S), true);
});

test("a second touch extends the pause", () => {
  const r = make();
  r.touch(0);
  r.touch(100 * S);
  assert.equal(r.tick(200 * S), false);
  assert.equal(r.tick(250 * S), true);
});

test("next() resets the page timer", () => {
  const r = make();
  r.next(25 * S);
  assert.equal(r.tick(54 * S), false);
  assert.equal(r.tick(55 * S), true);
});

test("prev() wraps from first to last", () => {
  const r = make();
  assert.equal(r.prev(1).type, "home");
  assert.equal(r.prev(2).type, "month");
  assert.equal(r.next(3).type, "home");
  assert.equal(r.next(4).type, "today");
});

test("goTo jumps to a page type", () => {
  const r = make();
  assert.equal(r.goTo("month", 5 * S), true);
  assert.equal(r.current().type, "month");
  assert.equal(r.index, 2);
  assert.equal(r.goTo("nope", 6 * S), false);
  assert.equal(r.current().type, "month");
  assert.equal(r.tick(34 * S), false);
  assert.equal(r.tick(35 * S), true);
});

test("no pages: current is null and tick never advances", () => {
  const r = make({ pages: [{ type: "today", enabled: false }] });
  assert.equal(r.current(), null);
  assert.equal(r.next(1), null);
  assert.equal(r.prev(1), null);
  assert.equal(r.tick(1e9), false);
});

test("a single page never asks to advance", () => {
  const r = make({ pages: [{ type: "today" }] });
  assert.equal(r.tick(1e9), false);
});
