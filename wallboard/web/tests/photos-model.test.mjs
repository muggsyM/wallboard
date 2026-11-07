import test from "node:test";
import assert from "node:assert/strict";
import * as M from "../admin/photos-model.js";
import { gridDropIndex } from "../admin/reorder.js";

const tr = (key) => `<${key}>`;

test("gridDropIndex: reading order over item rects", () => {
  // Two rows of three 100x100 cells with 10 px gaps: x 0,110,220; y 0,110.
  const rects = [];
  for (const top of [0, 110]) for (const left of [0, 110, 220]) rects.push({ left, top, width: 100, height: 100 });
  assert.equal(gridDropIndex(10, 10, rects), 0); // left half of the first cell
  assert.equal(gridDropIndex(60, 10, rects), 1); // right half of the first cell
  assert.equal(gridDropIndex(150, 50, rects), 1); // left half of the second cell
  assert.equal(gridDropIndex(300, 50, rects), 3); // right of the last cell in row 1 -> start of row 2
  assert.equal(gridDropIndex(10, 105, rects), 3); // in the gap between rows -> before row 2
  assert.equal(gridDropIndex(170, 150, rects), 5);
  assert.equal(gridDropIndex(300, 150, rects), 6); // past the last cell
  assert.equal(gridDropIndex(10, 500, rects), 6); // below the grid
  assert.equal(gridDropIndex(10, -20, rects), 0); // above the grid
  assert.equal(gridDropIndex(10, 10, []), 0);
});

test("gridDropIndex: a single column behaves like a list", () => {
  const rects = [0, 50, 100].map((top) => ({ left: 0, top, width: 300, height: 40 }));
  assert.equal(gridDropIndex(250, 10, rects), 1); // right half of row 0 counts as after it
  assert.equal(gridDropIndex(10, 45, rects), 1);
  assert.equal(gridDropIndex(10, 200, rects), 3);
});

test("mergeOrder keeps hidden ids in their slots", () => {
  assert.deepEqual(M.mergeOrder(["a", "b", "c"], ["c", "a", "b"]), ["c", "a", "b"]);
  // b is hidden (pending delete): its slot stays, the visible ones fill the others in order.
  assert.deepEqual(M.mergeOrder(["a", "b", "c", "d"], ["d", "a", "c"]), ["d", "b", "a", "c"]);
  assert.deepEqual(M.mergeOrder(["x"], []), ["x"]);
});

test("reorderVisible moves among visible items and keeps hidden ones in place", () => {
  const items = ["a", "b", "c", "d"].map((id) => ({ id }));
  const hidden = new Set(["b"]);
  const ids = (arr) => arr.map((p) => p.id);
  // visible a c d; move a (0) to the end (2) -> c d a; b stays in slot 1.
  assert.deepEqual(ids(M.reorderVisible(items, 0, 2, (p) => hidden.has(p.id))), ["c", "b", "d", "a"]);
  assert.deepEqual(ids(M.reorderVisible(items, 2, 0, () => false)), ["c", "a", "b", "d"]);
  assert.deepEqual(ids(items), ["a", "b", "c", "d"]); // input untouched
});

test("cleanAlbumName trims and enforces 1..100 characters", () => {
  assert.equal(M.cleanAlbumName("  Holiday "), "Holiday");
  assert.equal(M.cleanAlbumName("   "), null);
  assert.equal(M.cleanAlbumName("x".repeat(100)), "x".repeat(100));
  assert.equal(M.cleanAlbumName("x".repeat(101)), null);
  assert.equal(M.MAX_ALBUM_NAME, 100);
});

test("uploadOutcome maps the XHR result to ok / error text", () => {
  const body = (r) => JSON.stringify({ results: [r] });
  assert.deepEqual(M.uploadOutcome(200, body({ name: "a.jpg", ok: true, error: null }), tr), { ok: true, error: null });
  assert.deepEqual(M.uploadOutcome(200, body({ name: "a.jpg", ok: false, error: "Nepodporovaný formát obrázku" }), tr), {
    ok: false,
    error: "Nepodporovaný formát obrázku",
  });
  assert.deepEqual(M.uploadOutcome(200, body({ name: "a", ok: false, error: null }), tr), { ok: false, error: "<admin_photos_upload_failed>" });
  assert.deepEqual(M.uploadOutcome(200, "not json", tr), { ok: false, error: "<admin_photos_upload_failed>" });
  assert.deepEqual(M.uploadOutcome(413, "", tr), { ok: false, error: "<admin_photos_too_large>" });
  assert.deepEqual(M.uploadOutcome(0, "", tr), { ok: false, error: "<admin_photos_network_error>" });
  assert.deepEqual(M.uploadOutcome(500, "", tr), { ok: false, error: "<admin_photos_upload_failed>" });
});

test("runPool runs every item with at most `limit` in flight, in order of start", async () => {
  let running = 0;
  let peak = 0;
  const started = [];
  const worker = async (item) => {
    started.push(item);
    running++;
    peak = Math.max(peak, running);
    await new Promise((r) => setTimeout(r, item === 1 ? 20 : 5));
    running--;
  };
  await M.runPool([1, 2, 3, 4, 5], 2, worker);
  assert.deepEqual(started, [1, 2, 3, 4, 5]);
  assert.equal(peak, 2);
  assert.equal(running, 0);
});

test("runPool keeps going after a worker throws", async () => {
  const done = [];
  await M.runPool(["a", "b", "c"], 1, async (x) => {
    if (x === "a") throw new Error("boom");
    done.push(x);
  });
  assert.deepEqual(done, ["b", "c"]);
});
