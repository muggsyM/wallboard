import test from "node:test";
import assert from "node:assert/strict";
import { pageOptions } from "../display/lib/page-options.js";

const mod = {
  settings: [
    { key: "max_events_per_day", type: "number", labelKey: "x", default: 3 },
    { key: "show_legend", type: "boolean", labelKey: "y", default: true },
  ],
};

test("defaults when the page ref has no options", () => {
  assert.deepEqual(pageOptions(mod, { type: "month" }), { max_events_per_day: 3, show_legend: true });
  assert.deepEqual(pageOptions(mod, { type: "month", options: {} }), { max_events_per_day: 3, show_legend: true });
});

test("config options override defaults; extra keys are kept", () => {
  assert.deepEqual(pageOptions(mod, { options: { max_events_per_day: 5, show_legend: false, extra: 1 } }), {
    max_events_per_day: 5,
    show_legend: false,
    extra: 1,
  });
});

test("module without settings, missing page ref", () => {
  assert.deepEqual(pageOptions({}, { options: { a: 1 } }), { a: 1 });
  assert.deepEqual(pageOptions({ settings: [] }, null), {});
});
