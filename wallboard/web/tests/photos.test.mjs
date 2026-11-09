import test from "node:test";
import assert from "node:assert/strict";
import photos, { clampPhotoSeconds } from "../display/pages/photos.js";
import { pageOptions } from "../display/lib/page-options.js";
import { createSlideshow } from "../display/lib/slideshow.js";

test("photos page: type, title and settings", () => {
  assert.equal(photos.type, "photos");
  assert.equal(photos.titleKey, "photos");
  assert.deepEqual(photos.settings, [
    { key: "seconds_per_photo", type: "number", labelKey: "seconds_per_photo", default: 10, min: 4, max: 120 },
    { key: "show_caption", type: "boolean", labelKey: "show_caption", default: true },
  ]);
  assert.deepEqual(pageOptions(photos, {}), { seconds_per_photo: 10, show_caption: true });
});

test("seconds_per_photo is clamped to 4..120 (default 10)", () => {
  assert.equal(clampPhotoSeconds(1), 4);
  assert.equal(clampPhotoSeconds(999), 120);
  assert.equal(clampPhotoSeconds("15"), 15);
  assert.equal(clampPhotoSeconds(6.6), 7);
  assert.equal(clampPhotoSeconds(undefined), 10);
  assert.equal(clampPhotoSeconds("x"), 10);
});

// ---- slideshow sequencing (lib/slideshow.js) ---------------------------------------------

function harness({ items = ["a", "b", "c"], fail = [], dwellMs = 1000 } = {}) {
  const h = { list: items.map((id) => ({ id })), shown: [], loads: [], timers: new Map(), seq: 0, pos: -1 };
  h.next = () => {
    if (!h.list.length) return null;
    h.pos = (h.pos + 1) % h.list.length;
    return h.list[h.pos];
  };
  // Loads stay pending until resolved by the test: h.resolve(id).
  h.load = (item) =>
    new Promise((resolve) => {
      h.loads.push({ id: item.id, resolve: () => resolve(!fail.includes(item.id)) });
    });
  h.resolve = async (id) => {
    const i = h.loads.findLastIndex((l) => l.id === id); // the newest (older ones were superseded)
    assert.ok(i >= 0, `load of ${id} pending`);
    h.loads.splice(i, 1)[0].resolve();
    await new Promise((r) => setImmediate(r));
  };
  h.fire = async () => {
    const entries = [...h.timers.entries()];
    assert.equal(entries.length, 1, "one timer pending");
    const [id, fn] = entries[0];
    h.timers.delete(id);
    fn();
    await new Promise((r) => setImmediate(r));
  };
  h.s = createSlideshow({
    next: h.next,
    load: h.load,
    show: (item) => h.shown.push(item ? item.id : null),
    dwellMs,
    setTimeout: (fn) => {
      const id = ++h.seq;
      h.timers.set(id, fn);
      return id;
    },
    clearTimeout: (id) => h.timers.delete(id),
  });
  return h;
}

test("slideshow: shows a photo only once loaded, preloads the next one at once", async () => {
  const h = harness();
  h.s.start();
  assert.deepEqual(h.shown, []); // nothing (not even the empty hint) before the first load
  await h.resolve("a");
  assert.deepEqual(h.shown, ["a"]);
  assert.deepEqual(h.loads.map((l) => l.id), ["b"]); // next one already preloading
  await h.resolve("b");
  assert.deepEqual(h.shown, ["a"]); // waits for the dwell time
  await h.fire();
  assert.deepEqual(h.shown, ["a", "b"]);
  assert.equal(h.s.currentId(), "b");
  assert.equal(h.s.pendingId(), "c");
});

test("slideshow: a slow load is shown as soon as it arrives after the dwell time", async () => {
  const h = harness();
  h.s.start();
  await h.resolve("a");
  await h.fire(); // "b" not loaded yet
  assert.deepEqual(h.shown, ["a"]);
  assert.equal(h.timers.size, 0);
  await h.resolve("b");
  assert.deepEqual(h.shown, ["a", "b"]);
  assert.equal(h.timers.size, 1);
});

test("slideshow: a broken photo is skipped after a dwell period, never in a tight loop", async () => {
  const h = harness({ fail: ["b"] });
  h.s.start();
  await h.resolve("a");
  await h.resolve("b"); // fails
  await h.fire();
  assert.deepEqual(h.shown, ["a"]);
  assert.equal(h.loads.length, 0); // no immediate retry
  await h.fire(); // next attempt after a dwell period
  await h.resolve("c");
  assert.deepEqual(h.shown, ["a", "c"]);
});

test("slideshow: a single photo stays without reloading", async () => {
  const h = harness({ items: ["a"] });
  h.s.start();
  await h.resolve("a");
  await h.fire();
  await h.fire();
  assert.deepEqual(h.shown, ["a"]);
  assert.equal(h.loads.length, 0);
});

test("slideshow: no photos shows the empty state once; photos arriving start the show", async () => {
  const h = harness({ items: [] });
  h.s.start();
  assert.deepEqual(h.shown, [null]);
  assert.equal(h.s.isEmpty(), true);
  assert.equal(h.timers.size, 0);
  h.list = [{ id: "x" }];
  h.s.skip();
  await h.resolve("x");
  assert.deepEqual(h.shown, [null, "x"]);
  assert.equal(h.s.isEmpty(), false);
});

test("slideshow: the last photo deleted -> empty state", async () => {
  const h = harness({ items: ["a"] });
  h.s.start();
  await h.resolve("a");
  h.list = [];
  assert.equal(h.s.prune(() => false), true);
  h.s.skip();
  assert.deepEqual(h.shown, ["a", null]);
});

// Show a, b, c (each loaded, then the dwell timer fires).
async function showABC(h) {
  h.s.start();
  await h.resolve("a");
  await h.resolve("b");
  await h.fire();
  await h.resolve("c");
  await h.fire();
  assert.deepEqual(h.shown, ["a", "b", "c"]);
}

test("slideshow: back() returns to earlier photos, forward replays them before new ones", async () => {
  const h = harness({ items: ["a", "b", "c", "d"] });
  await showABC(h);
  await h.resolve("d"); // preloaded next
  assert.equal(h.s.back(), true);
  await h.resolve("b");
  assert.deepEqual(h.shown, ["a", "b", "c", "b"]);
  assert.equal(h.s.back(), true);
  await h.resolve("a");
  assert.equal(h.s.back(), false); // nothing before "a"
  assert.equal(h.s.pendingId(), "b"); // forward = the remembered "b"
  await h.resolve("b");
  h.s.skip();
  await h.resolve("c");
  await h.fire();
  assert.equal(h.s.pendingId(), "d"); // after the history: the photo next() picked before
  await h.resolve("d");
  await h.fire();
  assert.deepEqual(h.shown, ["a", "b", "c", "b", "a", "b", "c", "d"]);
});

test("slideshow: back() restarts the dwell timer", async () => {
  const h = harness();
  await showABC(h);
  h.s.back();
  assert.equal(h.timers.size, 0);
  await h.resolve("b");
  assert.equal(h.timers.size, 1);
});

test("slideshow: a manual skip shows the preloaded photo at once", async () => {
  const h = harness();
  h.s.start();
  await h.resolve("a");
  await h.resolve("b");
  h.s.skip();
  assert.deepEqual(h.shown, ["a", "b"]);
  assert.equal(h.timers.size, 1);
});

test("slideshow: a deleted photo leaves the history", async () => {
  const h = harness({ items: ["a", "b", "c", "d"] });
  await showABC(h);
  assert.equal(h.s.prune((id) => id !== "b"), false); // neither shown nor upcoming
  h.s.back();
  await h.resolve("a");
  assert.deepEqual(h.shown, ["a", "b", "c", "a"]);
});

test("slideshow: deleting the shown photo moves on", async () => {
  const h = harness({ items: ["a", "b", "c", "d"] });
  await showABC(h);
  h.s.back();
  await h.resolve("b");
  h.list = h.list.filter((p) => p.id !== "b");
  assert.equal(h.s.prune((id) => id !== "b"), true);
  h.s.skip();
  await h.resolve("c");
  assert.deepEqual(h.shown, ["a", "b", "c", "b", "c"]);
});

test("slideshow: stop() clears the timer and ignores late loads", async () => {
  const h = harness();
  h.s.start();
  await h.resolve("a");
  h.s.stop();
  assert.equal(h.timers.size, 0);
  await h.resolve("b");
  assert.deepEqual(h.shown, ["a"]);
  assert.equal(h.timers.size, 0);
});
