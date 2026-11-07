import test from "node:test";
import assert from "node:assert/strict";
import { classifyGesture } from "../display/lib/gesture.js";

test("left swipe goes to the next page, right swipe to the previous", () => {
  assert.equal(classifyGesture({ dx: -60, dy: 0, dt: 200 }), "next");
  assert.equal(classifyGesture({ dx: 120, dy: 10, dt: 300 }), "prev");
});

test("swipe needs at least 60 px", () => {
  assert.equal(classifyGesture({ dx: -59, dy: 0, dt: 200 }), null);
});

test("swipe must take less than 600 ms", () => {
  assert.equal(classifyGesture({ dx: -200, dy: 0, dt: 599 }), "next");
  assert.equal(classifyGesture({ dx: -200, dy: 0, dt: 600 }), null);
});

test("swipe must be mostly horizontal", () => {
  assert.equal(classifyGesture({ dx: -100, dy: 60, dt: 200 }), "next");
  assert.equal(classifyGesture({ dx: -100, dy: 80, dt: 200 }), null);
  assert.equal(classifyGesture({ dx: 0, dy: 200, dt: 200 }), null);
});

test("small, short movement is a tap", () => {
  assert.equal(classifyGesture({ dx: 0, dy: 0, dt: 80 }), "tap");
  assert.equal(classifyGesture({ dx: 10, dy: -10, dt: 300 }), "tap");
});

test("long press or drag is neither tap nor swipe", () => {
  assert.equal(classifyGesture({ dx: 0, dy: 0, dt: 900 }), null);
  assert.equal(classifyGesture({ dx: 30, dy: 0, dt: 100 }), null);
});
