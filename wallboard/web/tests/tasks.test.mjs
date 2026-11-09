import test from "node:test";
import assert from "node:assert/strict";
import { setStrings, setLanguage, setTimezone } from "../shared/i18n.js";
import { readFileSync } from "node:fs";
import { taskLists, dueLabel } from "../display/pages/tasks.js";

setStrings("cs", JSON.parse(readFileSync(new URL("../shared/i18n/cs.json", import.meta.url))));
setLanguage("cs");
setTimezone("Europe/Prague");

const TODAY = "2026-11-09"; // Monday
const members = [1, 2, 3, 4, 5].map((n) => ({ id: `m${n}`, name: `M${n}`, initial: "M", color: "#fff" }));
const todo = (id, member_id, extra = {}) => ({ id, member_id, text: id, due: null, done: null, ...extra });

test("taskLists: first 4 members, open sorted by due date then entered order, done today last", () => {
  const config = {
    members,
    todos: [
      todo("a", "m1"),
      todo("b", "m1", { due: "2026-12-10" }),
      todo("c", "m1", { done: TODAY }),
      todo("d", "m1", { done: "2026-11-08" }), // ticked yesterday: hidden
      todo("e", "m1", { due: "2026-11-10" }),
      todo("f", "m1"),
      todo("g", "m2"),
      todo("h", "ghost"),
    ],
  };
  const lists = taskLists(config, TODAY);
  assert.deepEqual(lists.map((l) => l.member.id), ["m1", "m2", "m3", "m4"]);
  assert.deepEqual(lists[0].open.map((x) => x.id), ["e", "b", "a", "f"]);
  assert.deepEqual(lists[0].done.map((x) => x.id), ["c"]);
  assert.equal(lists[0].count, 4);
  assert.deepEqual(lists[1].open.map((x) => x.id), ["g"]);
  assert.equal(lists[2].count, 0);
});

test("taskLists: a tap in flight shows its new state at once", () => {
  const config = { members, todos: [todo("a", "m1"), todo("b", "m1", { done: TODAY })] };
  const lists = taskLists(config, TODAY, new Map([["a", true], ["b", false]]));
  assert.deepEqual(lists[0].open.map((x) => x.id), ["b"]);
  assert.deepEqual(lists[0].done.map((x) => x.id), ["a"]);
});

test("taskLists: no config or todos is safe", () => {
  assert.deepEqual(taskLists(null, TODAY), []);
  assert.equal(taskLists({ members }, TODAY)[0].count, 0);
});

test("dueLabel: today, tomorrow, weekday within a week, date further out; soon up to tomorrow", () => {
  const tz = "Europe/Prague";
  assert.equal(dueLabel(null, TODAY, tz), null);
  assert.deepEqual(dueLabel("2026-11-09", TODAY, tz), { text: "dnes", soon: true });
  assert.deepEqual(dueLabel("2026-11-10", TODAY, tz), { text: "zítra", soon: true });
  assert.deepEqual(dueLabel("2026-11-13", TODAY, tz), { text: "pá", soon: false });
  assert.deepEqual(dueLabel("2027-01-31", TODAY, tz), { text: "31. 1.", soon: false });
  assert.deepEqual(dueLabel("2026-11-01", TODAY, tz), { text: "1. 11.", soon: true }); // overdue
});
