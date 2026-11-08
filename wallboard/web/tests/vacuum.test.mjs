import test from "node:test";
import assert from "node:assert/strict";
import { runningRoom } from "../display/vacuum.js";

test("runningRoom: the started room is lit only while that robot cleans", () => {
  const started = { entity: "vacuum.robo", index: 1, at: 0 };
  assert.equal(runningRoom(started, "vacuum.robo", "cleaning"), 1);
  assert.equal(runningRoom(started, "vacuum.robo", "docked"), null);
  assert.equal(runningRoom(started, "vacuum.robo", "returning"), null);
  assert.equal(runningRoom(started, "vacuum.other", "cleaning"), null);
  assert.equal(runningRoom(null, "vacuum.robo", "cleaning"), null);
});
