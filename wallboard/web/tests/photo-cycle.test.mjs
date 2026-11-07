import test from "node:test";
import assert from "node:assert/strict";
import { PhotoCycle } from "../display/lib/photo-cycle.js";

const items = (...ids) => ids.map((id) => ({ id, url: `/media/${id}.jpg`, bg_url: `/media/${id}.bg.jpg` }));
const take = (c, n) => Array.from({ length: n }, () => c.next()?.id ?? null);

test("empty list gives null", () => {
  const c = new PhotoCycle();
  assert.equal(c.next(), null);
  c.setItems([], false);
  assert.equal(c.next(), null);
});

test("ordered: follows the user order and wraps", () => {
  const c = new PhotoCycle();
  c.setItems(items("a", "b", "c"), false);
  assert.deepEqual(take(c, 7), ["a", "b", "c", "a", "b", "c", "a"]);
});

test("ordered: a new list continues after the last shown photo", () => {
  const c = new PhotoCycle();
  c.setItems(items("a", "b", "c"), false);
  take(c, 2); // a, b
  c.setItems(items("a", "x", "b", "c"), false);
  assert.deepEqual(take(c, 3), ["c", "a", "x"]);
});

test("ordered: unchanged list does not reset the position", () => {
  const c = new PhotoCycle();
  c.setItems(items("a", "b", "c"), false);
  take(c, 1);
  c.setItems(items("a", "b", "c"), false);
  assert.equal(c.next().id, "b");
});

test("shuffle: every photo once per round, no repeats", () => {
  const c = new PhotoCycle(Math.random);
  const ids = ["a", "b", "c", "d", "e"];
  c.setItems(items(...ids), true);
  for (let round = 0; round < 4; round++) {
    assert.deepEqual(take(c, 5).sort(), ids);
  }
});

test("shuffle: a new round does not start with the photo just shown", () => {
  // With two photos an unguarded reshuffle repeats at a round boundary half the time.
  const c = new PhotoCycle(Math.random);
  c.setItems(items("a", "b"), true);
  let last = null;
  for (let i = 0; i < 200; i++) {
    const id = c.next().id;
    assert.notEqual(id, last);
    last = id;
  }
});

test("shuffle: list change keeps already shown photos out of the current round", () => {
  const c = new PhotoCycle(Math.random);
  c.setItems(items("a", "b", "c", "d"), true);
  const first = take(c, 2);
  c.setItems(items("a", "b", "c", "d", "e"), true);
  const rest = take(c, 3);
  assert.deepEqual([...first, ...rest].sort(), ["a", "b", "c", "d", "e"]);
});

test("removed photos are never returned", () => {
  const c = new PhotoCycle(Math.random);
  c.setItems(items("a", "b", "c"), true);
  c.setItems(items("b"), true);
  assert.deepEqual(take(c, 3), ["b", "b", "b"]);
});

test("returns the item object", () => {
  const c = new PhotoCycle();
  c.setItems(items("a"), false);
  assert.equal(c.next().bg_url, "/media/a.bg.jpg");
});
