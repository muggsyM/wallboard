import test from "node:test";
import assert from "node:assert/strict";
import * as M from "../admin/settings-model.js";
import { dropIndex } from "../admin/reorder.js";
import { readFileSync } from "node:fs";
import { setStrings, setLanguage, t } from "../shared/i18n.js";

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
  assert.equal(dropIndex(5, [10, 30, 50]), 0);
  assert.equal(dropIndex(31, [10, 30, 50]), 2);
  assert.equal(dropIndex(99, [10, 30, 50]), 3);
  assert.equal(dropIndex(5, []), 0);
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

test("errorText maps stable types to translations and falls back to msg", () => {
  setStrings("cs", JSON.parse(readFileSync(new URL("../shared/i18n/cs.json", import.meta.url), "utf8")));
  setLanguage("cs");
  assert.equal(M.errorText({ type: "greater_than_equal", ctx: { ge: 5 }, msg: "Input should be..." }, t), "Nejméně 5");
  assert.equal(
    M.errorText({ type: "member_has_google_account", ctx: { name: "Anna", email: "a@x.cz" }, msg: "x" }, t),
    "Anna má připojený Google účet a@x.cz – nejdřív ho odpojte",
  );
  assert.equal(M.errorText({ type: "some_new_type", msg: "server text" }, t), "server text");
  assert.equal(M.errorText({ msg: "no type" }, t), "no type");
});

test("googleBadge: expired only for expired, calm note for error", () => {
  assert.equal(M.googleBadge("ok", undefined), "ok");
  assert.equal(M.googleBadge("expired", undefined), "expired");
  assert.equal(M.googleBadge("error", undefined), "unreachable");
  assert.equal(M.googleBadge(undefined, undefined), null);
  assert.equal(M.googleBadge("error", { expired: true }), "expired"); // calendar list said 409
  assert.equal(M.googleBadge("ok", { expired: true }), "expired");
  assert.equal(M.googleBadge(false, undefined), null); // old boolean status is not trusted
});

test("isStaleError and ifMatch", () => {
  assert.equal(M.isStaleError({ status: 409, errors: [{ type: "stale_config", loc: [], msg: "x" }] }), true);
  assert.equal(M.isStaleError({ status: 409, errors: null }), false); // e.g. HA not connected
  assert.equal(M.isStaleError({ status: 422, errors: [{ type: "stale_config" }] }), false);
  assert.equal(M.isStaleError(undefined), false);
  assert.deepEqual(M.ifMatch("abc123"), { "If-Match": '"abc123"' });
  assert.deepEqual(M.ifMatch(null), { "If-Match": '""' });
});

test("errorText maps stale_config in both languages", () => {
  for (const lang of ["cs", "en"]) {
    setStrings(lang, JSON.parse(readFileSync(new URL(`../shared/i18n/${lang}.json`, import.meta.url), "utf8")));
    setLanguage(lang);
    const text = M.errorText({ type: "stale_config", loc: [], msg: "Settings were changed elsewhere; reload." }, t);
    assert.notEqual(text, "Settings were changed elsewhere; reload.");
    assert.ok(text.length > 10);
  }
});

test("onlyServerOwnedChanged: Google accounts and shuffle yes, anything else no", () => {
  const base = {
    language: "cs",
    google_accounts: [{ id: "g1", email: "a@x", member_id: "a" }],
    calendars: [{ account_id: "g1", calendar_id: "c", name: "C", member_id: "a", enabled: true }],
    pages: [{ type: "today", enabled: true, seconds: null, options: {} }],
    photos_shuffle: false,
  };
  const copy = () => structuredClone(base);
  assert.equal(M.onlyServerOwnedChanged(base, copy()), true);
  assert.equal(M.onlyServerOwnedChanged(base, { ...copy(), photos_shuffle: true }), true);
  assert.equal(M.onlyServerOwnedChanged(base, { ...copy(), google_accounts: [...base.google_accounts, { id: "g2", email: "b@x", member_id: "a" }] }), true);
  // disconnect drops that account's calendars with it
  assert.equal(M.onlyServerOwnedChanged(base, { ...copy(), google_accounts: [], calendars: [] }), true);
  // the incident: a page added elsewhere
  assert.equal(M.onlyServerOwnedChanged(base, { ...copy(), pages: [...base.pages, { type: "photos", enabled: true, seconds: 60, options: {} }] }), false);
  assert.equal(M.onlyServerOwnedChanged(base, { ...copy(), language: "en" }), false);
  assert.equal(M.onlyServerOwnedChanged(base, { ...copy(), calendars: [] }), false); // calendar change with the account kept
  assert.equal(M.onlyServerOwnedChanged(null, copy()), false);
});
