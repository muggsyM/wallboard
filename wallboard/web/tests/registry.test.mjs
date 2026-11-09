import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PAGES, getPage } from "../display/pages/registry.js";

const cs = JSON.parse(readFileSync(new URL("../shared/i18n/cs.json", import.meta.url), "utf8"));

test("registry knows every page type, keyed by the module's own type", () => {
  assert.deepEqual(Object.keys(PAGES), ["today", "week", "month", "home", "photos", "tasks"]);
  for (const [type, mod] of Object.entries(PAGES)) {
    assert.equal(mod.type, type);
    assert.equal(typeof mod.mount, "function");
    assert.ok(cs[mod.titleKey], `${type}: titleKey ${mod.titleKey} has a translation`);
    for (const s of mod.settings) assert.ok(cs[s.labelKey], `${type}: label ${s.labelKey} has a translation`);
  }
});

test("getPage: known types only", () => {
  assert.equal(getPage("photos"), PAGES.photos);
  assert.equal(getPage("nope"), null);
  assert.equal(getPage("toString"), null);
});
