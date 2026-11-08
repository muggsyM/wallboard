import test from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../display/store.js";

// Minimal fake timers: run(ms) advances the clock and fires due callbacks in order.
function fakeTimers() {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  return {
    setTimeout: (fn, ms) => {
      const id = ++seq;
      timers.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    run(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
      }
      now = end;
    },
    pending: () => [...timers.values()].map((t) => t.at - now),
  };
}

function fakeSocketClass() {
  const sockets = [];
  class FakeWS {
    static OPEN = 1;
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.sent = [];
      sockets.push(this);
    }
    send(s) { this.sent.push(JSON.parse(s)); }
    close() { this.readyState = 3; this.onclose?.({}); }
    // test helpers
    open() { this.readyState = 1; this.onopen?.({}); }
    receive(msg) { this.onmessage?.({ data: JSON.stringify(msg) }); }
    drop() { this.readyState = 3; this.onclose?.({}); }
  }
  return { FakeWS, sockets };
}

function setup() {
  const timers = fakeTimers();
  const { FakeWS, sockets } = fakeSocketClass();
  const store = createStore({ url: "ws://localhost:8080/ws", WebSocket: FakeWS, setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout });
  return { store, timers, sockets };
}

const SNAP = {
  type: "snapshot",
  config: { language: "en", pages: [] },
  ha: { "light.kitchen": { state: "on", attributes: {} } },
  calendar: { events: [] },
  weather: { current: { condition: "sunny", temperature: 8 } },
  photos: [{ id: "a" }],
  status: { ha: true, google: {} },
};

test("initial state before any message", () => {
  const { store } = setup();
  const s = store.get();
  assert.equal(s.connected, false);
  assert.equal(s.config, null);
  assert.deepEqual(s.ha, {});
  assert.deepEqual(s.photos, []);
  assert.deepEqual(s.status, { ha: false, google: {} });
});

test("connects to the given url and tracks connected", () => {
  const { store, sockets } = setup();
  const seen = [];
  store.subscribe((s, change) => seen.push([change, s.connected]));
  store.connect();
  assert.equal(sockets[0].url, "ws://localhost:8080/ws");
  sockets[0].open();
  assert.equal(store.get().connected, true);
  sockets[0].drop();
  assert.equal(store.get().connected, false);
  assert.deepEqual(seen, [["connected", true], ["connected", false]]);
});

test("snapshot and deltas are applied", () => {
  const { store, sockets } = setup();
  const changes = [];
  store.subscribe((s, change) => changes.push(change));
  store.connect();
  const ws = sockets[0];
  ws.open();
  ws.receive(SNAP);
  let s = store.get();
  assert.equal(s.config.language, "en");
  assert.equal(s.ha["light.kitchen"].state, "on");
  assert.equal(s.weather.current.temperature, 8);
  assert.equal(s.status.ha, true);

  ws.receive({ type: "ha", entity_id: "light.kitchen", state: { state: "off", attributes: {} } });
  ws.receive({ type: "ha", entity_id: "person.dad", state: { state: "home", attributes: {} } });
  ws.receive({ type: "ha", entity_id: "person.dad", state: null });
  ws.receive({ type: "calendar", calendar: { events: [{ id: "e1" }] } });
  ws.receive({ type: "weather", weather: { current: { condition: "rainy", temperature: 3 } } });
  ws.receive({ type: "photos", photos: [] });
  ws.receive({ type: "config", config: { language: "cs", pages: [] } });
  ws.receive({ type: "status", status: { ha: false, google: { acc: true } } });
  s = store.get();
  assert.equal(s.ha["light.kitchen"].state, "off");
  assert.ok(!("person.dad" in s.ha));
  assert.equal(s.calendar.events[0].id, "e1");
  assert.equal(s.weather.current.condition, "rainy");
  assert.deepEqual(s.photos, []);
  assert.equal(s.config.language, "cs");
  assert.deepEqual(s.status, { ha: false, google: { acc: true } });
  assert.deepEqual(changes, ["connected", "snapshot", "ha", "ha", "ha", "calendar", "weather", "photos", "config", "status"]);
});

test("garbage and unknown messages are ignored", () => {
  const { store, sockets } = setup();
  let n = 0;
  store.connect();
  sockets[0].open();
  store.subscribe(() => n++);
  sockets[0].onmessage({ data: "not json" });
  sockets[0].receive({ type: "mystery" });
  sockets[0].receive([1, 2]);
  assert.equal(n, 0);
});

test("unsubscribe stops notifications", () => {
  const { store, sockets } = setup();
  let n = 0;
  const off = store.subscribe(() => n++);
  store.connect();
  sockets[0].open();
  off();
  sockets[0].receive(SNAP);
  assert.equal(n, 1);
});

test("reconnects with backoff 1, 2, 4 ... capped at 30 s, reset after open", () => {
  const { store, sockets, timers } = setup();
  store.connect();
  const delays = [];
  for (let i = 0; i < 7; i++) {
    sockets.at(-1).drop();
    delays.push(timers.pending()[0]);
    timers.run(delays.at(-1));
  }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  assert.equal(sockets.length, 8);
  sockets.at(-1).open();
  sockets.at(-1).drop();
  assert.deepEqual(timers.pending(), [1000]);
});

test("sendAction sends an action and resolves on the matching result", async () => {
  const { store, sockets } = setup();
  store.connect();
  const ws = sockets[0];
  ws.open();
  const p1 = store.sendAction("light.kitchen");
  const p2 = store.sendAction("vacuum.robot");
  const [m1, m2] = ws.sent;
  assert.equal(m1.type, "action");
  assert.equal(m1.entity_id, "light.kitchen");
  assert.equal(m2.entity_id, "vacuum.robot");
  assert.notEqual(m1.id, m2.id);
  ws.receive({ type: "action_result", id: m2.id, ok: false, error: "not allowed" });
  ws.receive({ type: "action_result", id: m1.id, ok: true, error: null });
  assert.deepEqual(await p1, { ok: true, error: null });
  assert.deepEqual(await p2, { ok: false, error: "not allowed" });
});

test("sendAction carries a vacuum command only when given", async () => {
  const { store, sockets } = setup();
  store.connect();
  const ws = sockets[0];
  ws.open();
  store.sendAction("vacuum.robot", "room:2").catch(() => {});
  store.sendAction("light.kitchen").catch(() => {});
  assert.equal(ws.sent[0].command, "room:2");
  assert.equal("command" in ws.sent[1], false);
});

test("sendAction rejects with timeout after 5 s", async () => {
  const { store, sockets, timers } = setup();
  store.connect();
  sockets[0].open();
  const p = store.sendAction("light.kitchen");
  timers.run(4999);
  let settled = false;
  p.catch(() => {}).finally(() => (settled = true));
  await Promise.resolve();
  assert.equal(settled, false);
  timers.run(1);
  await assert.rejects(p, (e) => e.ok === false && e.error === "timeout");
  // A late result for the timed-out id is ignored.
  sockets[0].receive({ type: "action_result", id: sockets[0].sent[0].id, ok: true, error: null });
});

test("sendAction rejects immediately with offline when the socket is not open", async () => {
  const { store, sockets } = setup();
  await assert.rejects(store.sendAction("light.kitchen"), (e) => e.ok === false && e.error === "offline");
  store.connect();
  await assert.rejects(store.sendAction("light.kitchen"), (e) => e.error === "offline");
  assert.equal(sockets[0].sent.length, 0);
});

test("pending actions reject with offline when the socket drops", async () => {
  const { store, sockets, timers } = setup();
  store.connect();
  sockets[0].open();
  const p = store.sendAction("light.kitchen");
  sockets[0].drop();
  await assert.rejects(p, (e) => e.error === "offline");
  assert.deepEqual(timers.pending(), [1000]); // only the reconnect timer is left
});

test("snapshot exposes the server build id", () => {
  const { store, sockets } = setup();
  store.connect();
  sockets[0].open();
  assert.equal(store.get().build, null);
  sockets[0].receive({ ...SNAP, build: "abc123" });
  assert.equal(store.get().build, "abc123");
  sockets[0].receive(SNAP);
  assert.equal(store.get().build, null);
});

test("viewer: can_act defaults to true and follows the server's viewer message", () => {
  const { store, sockets } = setup();
  assert.deepEqual(store.get().viewer, { can_act: true });
  const changes = [];
  store.subscribe((s, change) => changes.push([change, s.viewer.can_act]));
  store.connect();
  const ws = sockets[0];
  ws.open();
  ws.receive(SNAP);
  assert.equal(store.get().viewer.can_act, true); // an older server sends no viewer message
  ws.receive({ type: "viewer", can_act: false });
  assert.deepEqual(store.get().viewer, { can_act: false });
  ws.receive({ type: "viewer", can_act: true });
  assert.deepEqual(store.get().viewer, { can_act: true });
  assert.deepEqual(changes.slice(-2), [["viewer", false], ["viewer", true]]);
});

test("viewer: only an explicit false makes the display view-only", () => {
  const { store, sockets } = setup();
  store.connect();
  sockets[0].open();
  sockets[0].receive({ type: "viewer" });
  assert.equal(store.get().viewer.can_act, true);
  sockets[0].receive({ type: "viewer", can_act: false });
  assert.equal(store.get().viewer.can_act, false);
});
