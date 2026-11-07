import test from "node:test";
import assert from "node:assert/strict";
import { setLanguage, setTimezone } from "../shared/i18n.js";
import { weekSubtitle } from "../display/pages/week.js";

const TZ = "Europe/Prague";

test("week subtitle in cs", () => {
  setTimezone(TZ);
  setLanguage("cs");
  assert.equal(weekSubtitle("2026-11-07", "2026-11-13", TZ), "7.–13. 11.");
  assert.equal(weekSubtitle("2026-11-28", "2026-12-04", TZ), "28. 11.–4. 12.");
});

test("week subtitle in en", () => {
  setTimezone(TZ);
  setLanguage("en");
  assert.equal(weekSubtitle("2026-11-07", "2026-11-13", TZ), "7–13/11");
  assert.equal(weekSubtitle("2026-11-28", "2026-12-04", TZ), "28/11–4/12");
  setLanguage("cs");
});
