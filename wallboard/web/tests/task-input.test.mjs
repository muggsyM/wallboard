import test from "node:test";
import assert from "node:assert/strict";
import { LAYOUTS, initialInput, applyKey, keyLabel, dueFor, cleanText } from "../display/lib/task-input.js";

const type = (keys, s = initialInput()) => keys.reduce((st, k) => applyKey(st, k), s);

test("first letter is capital, then lower case; shift is one-shot", () => {
  assert.equal(type([..."pích"]).text, "Pích");
  assert.equal(type([..."ab", "shift", ..."cd"]).text, "AbCd");
  assert.equal(type(["shift", ..."ab"]).text, "ab"); // shift toggled off at the start
});

test("space never doubles nor leads; back deletes and re-arms the capital", () => {
  assert.equal(type(["space", "a", "space", "space", "b"]).text, "A b");
  const s = type(["a", "b", "back", "back"]);
  assert.equal(s.text, "");
  assert.equal(s.shift, true);
  assert.equal(type(["back"]).text, "");
});

test("czech letters upper-case correctly; numbers layout and back", () => {
  assert.equal(type([..."ěšč"]).text, "Ěšč");
  const n = type(["num", "1", "2", "abc", "x"]);
  assert.equal(n.text, "12x");
  assert.equal(n.layout, "letters");
});

test("text stops at the limit", () => {
  assert.equal(type([..."abcdef"], { ...initialInput(), shift: false }).text, "abcdef");
  assert.equal([...applyKey({ text: "x".repeat(200), shift: false, layout: "letters" }, "y").text].length, 200);
});

test("layouts: every row fits on the 1080 px board", () => {
  for (const rows of Object.values(LAYOUTS)) for (const r of rows) assert.ok(r.length <= 11, r.join(""));
  assert.equal(keyLabel("ž", true), "Ž");
  assert.equal(keyLabel("back", false), "⌫");
});

test("due choices: today, tomorrow, this Sunday, next Sunday", () => {
  const mon = "2026-11-09";
  assert.equal(dueFor("none", mon), null);
  assert.equal(dueFor("today", mon), mon);
  assert.equal(dueFor("tomorrow", mon), "2026-11-10");
  assert.equal(dueFor("week", mon), "2026-11-15");
  assert.equal(dueFor("nextweek", mon), "2026-11-22");
  assert.equal(dueFor("week", "2026-11-15"), "2026-11-15"); // on Sunday: today
  assert.equal(dueFor("tomorrow", "2026-12-31"), "2027-01-01");
});

test("cleanText: one line, single spaces", () => {
  assert.equal(cleanText("  a  b \n c "), "a b c");
  assert.equal(cleanText(""), "");
});
