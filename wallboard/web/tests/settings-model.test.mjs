import test from "node:test";
import assert from "node:assert/strict";
import * as M from "../admin/settings-model.js";

const cfg = () => ({
  ha_url: "",
  weather_entity: " ",
  members: [{ id: "a", name: "A", initial: "A", color: "#000000", person_entity: "" }],
  google_accounts: [{ id: "g1", email: "a@x", member_id: "a" }],
  calendars: [],
  rooms: [{ name: "R", temperature_entity: "sensor.t", humidity_entity: "", light_entities: ["light.a", " "] }],
  tiles: [
    { entity_id: "light.a", label: "", icon: "", group: "lights", controllable: true },
    { entity_id: "vacuum.v", label: "Robot", icon: null, group: "devices", controllable: true },
  ],
  status_entities: ["binary_sensor.door", ""],
  quick_tiles: ["light.a"],
});
const DOMAINS = ["light", "switch", "vacuum", "scene"];

test("moveItem and dropIndex", () => {
  assert.deepEqual(M.moveItem(["a", "b", "c", "d"], 0, 2), ["b", "c", "a", "d"]);
  assert.deepEqual(M.moveItem(["a", "b", "c", "d"], 3, 0), ["d", "a", "b", "c"]);
  assert.deepEqual(M.moveItem(["a", "b"], 0, 9), ["b", "a"]);
  assert.equal(M.dropIndex(5, [10, 30, 50]), 0);
  assert.equal(M.dropIndex(31, [10, 30, 50]), 2);
  assert.equal(M.dropIndex(99, [10, 30, 50]), 3);
  assert.equal(M.dropIndex(5, []), 0);
});

test("errorTarget falls back to the nearest field", () => {
  const keys = new Set(["members", "members.0.color", "quick_tiles"]);
  const has = (k) => keys.has(k);
  assert.equal(M.errorTarget(["members", 0, "color"], has), "members.0.color");
  assert.equal(M.errorTarget(["members", 1, "name"], has), "members");
  assert.equal(M.errorTarget(["quick_tiles"], has), "quick_tiles");
  assert.equal(M.errorTarget([], has), null);
  assert.equal(M.errorTarget(["nope", 1], has), null);
});

test("newId uses getRandomValues", () => {
  const fake = { getRandomValues: (a) => a.fill(171) };
  assert.equal(M.newId("m", fake), "mabababab");
  assert.match(M.newId("m"), /^m[0-9a-f]{8}$/);
});

test("quick tiles: limit of 4, removal and rename follow tiles", () => {
  const c = cfg();
  c.tiles.push(...["light.b", "light.c", "light.d"].map((e) => M.newTile(e, DOMAINS)));
  assert.equal(M.setQuick(c, "light.b", true), true);
  assert.equal(M.setQuick(c, "light.c", true), true);
  assert.equal(M.setQuick(c, "light.d", true), true);
  assert.equal(M.setQuick(c, "vacuum.v", true), false);
  assert.deepEqual(c.quick_tiles, ["light.a", "light.b", "light.c", "light.d"]);
  M.setQuick(c, "light.b", false);
  assert.deepEqual(c.quick_tiles, ["light.a", "light.c", "light.d"]);
  M.renameTile(c, 0, "sensor.x", DOMAINS);
  assert.equal(c.tiles[0].controllable, false);
  assert.deepEqual(c.quick_tiles, ["sensor.x", "light.c", "light.d"]);
  M.removeTile(c, 0);
  assert.deepEqual(c.quick_tiles, ["light.c", "light.d"]);
});

test("newTile guesses group and controllable", () => {
  assert.deepEqual(M.newTile("switch.fan", DOMAINS), { entity_id: "switch.fan", label: null, icon: null, group: "lights", controllable: true });
  assert.equal(M.newTile("sensor.power", DOMAINS).controllable, false);
  assert.equal(M.newTile("vacuum.v", DOMAINS).group, "devices");
});

test("setCalendar adds, remaps and removes one ref per calendar", () => {
  const c = cfg();
  const acct = c.google_accounts[0];
  const cal = { id: "fam", name: "Family" };
  M.setCalendar(c, acct, cal, "a");
  assert.deepEqual(c.calendars, [{ account_id: "g1", calendar_id: "fam", name: "Family", member_id: "a", enabled: true }]);
  M.setCalendar(c, acct, cal, "b");
  assert.equal(c.calendars.length, 1);
  assert.equal(M.calendarRef(c, "g1", "fam").member_id, "b");
  M.setCalendar(c, acct, cal, null);
  assert.deepEqual(c.calendars, []);
  assert.equal(M.calendarRef(c, "g1", "fam"), null);
});

test("cleanConfig turns blank optional fields into null and drops empty list entries", () => {
  const c = cfg();
  const out = M.cleanConfig(c);
  assert.equal(out.ha_url, null);
  assert.equal(out.weather_entity, null);
  assert.equal(out.members[0].person_entity, null);
  assert.equal(out.rooms[0].humidity_entity, null);
  assert.deepEqual(out.rooms[0].light_entities, ["light.a"]);
  assert.equal(out.tiles[0].label, null);
  assert.equal(out.tiles[1].label, "Robot");
  assert.deepEqual(out.status_entities, ["binary_sensor.door"]);
  assert.equal(c.ha_url, ""); // input untouched
});

test("isLocalHost", () => {
  assert.ok(M.isLocalHost("localhost") && M.isLocalHost("127.0.0.1"));
  assert.ok(!M.isLocalHost("192.168.1.10") && !M.isLocalHost("wallboard.local"));
});
