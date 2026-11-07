import test from "node:test";
import assert from "node:assert/strict";
import { calendarStale } from "../display/header.js";

test("calendar is stale only when accounts exist and none is ok", () => {
  assert.equal(calendarStale({ google: {} }), false); // no accounts: nothing to be stale
  assert.equal(calendarStale({}), false);
  assert.equal(calendarStale(undefined), false);
  assert.equal(calendarStale({ google: { a: "ok", b: "expired" } }), false);
  assert.equal(calendarStale({ google: { a: "error" } }), true);
  assert.equal(calendarStale({ google: { a: "expired", b: "error" } }), true);
});
