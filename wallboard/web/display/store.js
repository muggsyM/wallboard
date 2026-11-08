// Display data store fed by WS /ws: holds the latest snapshot plus deltas, reconnects
// with backoff 1, 2, 4 ... 30 s, and sends tile actions.
//
//   store.get()            -> {build, config, ha, calendar, weather, photos, status, viewer, connected}
//                             (build: the server's web/ hash from the last snapshot, or null;
//                             viewer: {can_act}, from the "viewer" message the server sends
//                             right after each snapshot; false for a view-only visitor. It
//                             defaults to true for servers that never send it.)
//   store.subscribe(fn)    -> unsubscribe; fn(state, change) where change is the message
//                             type ("snapshot", "ha", "config", ...) or "connected"
//   store.sendAction(id)   -> Promise<{ok, error}>; rejects {ok:false, error:"timeout"} after
//                             5 s, {ok:false, error:"offline"} when the socket is not open
//
// Message formats: globals.md "Data model". Dependencies are injectable for node tests.

const BACKOFF_MIN_MS = 1000;
const BACKOFF_MAX_MS = 30000;
const ACTION_TIMEOUT_MS = 5000;
const DELTA_TYPES = new Set(["calendar", "weather", "photos", "config", "status"]);

export function createStore({
  url,
  WebSocket: WS = globalThis.WebSocket,
  setTimeout: later = globalThis.setTimeout.bind(globalThis),
  clearTimeout: cancel = globalThis.clearTimeout.bind(globalThis),
} = {}) {
  const state = {
    build: null,
    config: null,
    ha: {},
    calendar: null,
    weather: null,
    photos: [],
    status: { ha: false, google: {} },
    viewer: { can_act: true },
    connected: false,
  };
  const listeners = new Set();
  const pending = new Map(); // action id -> {resolve, reject, timer}
  let ws = null;
  let backoff = BACKOFF_MIN_MS;
  let reconnectTimer = null;
  let seq = 0;

  function emit(change) {
    for (const fn of [...listeners]) {
      try {
        fn(state, change);
      } catch (err) {
        console.error("store listener failed", err);
      }
    }
  }

  function apply(msg) {
    switch (msg.type) {
      case "snapshot":
        state.build = msg.build ?? null;
        state.config = msg.config ?? null;
        state.ha = msg.ha ?? {};
        state.calendar = msg.calendar ?? null;
        state.weather = msg.weather ?? null;
        state.photos = msg.photos ?? [];
        state.status = msg.status ?? { ha: false, google: {} };
        return true;
      case "ha":
        if (msg.state) state.ha[msg.entity_id] = msg.state;
        else delete state.ha[msg.entity_id];
        return true;
      case "viewer":
        state.viewer = { can_act: msg.can_act !== false };
        return true;
      case "action_result": {
        const p = pending.get(msg.id);
        if (p) {
          pending.delete(msg.id);
          cancel(p.timer);
          p.resolve({ ok: Boolean(msg.ok), error: msg.error ?? null });
        }
        return false;
      }
      default:
        if (!DELTA_TYPES.has(msg.type)) return false;
        state[msg.type] = msg[msg.type] ?? (msg.type === "photos" ? [] : null);
        return true;
    }
  }

  function failPending(error) {
    for (const [id, p] of pending) {
      pending.delete(id);
      cancel(p.timer);
      p.reject({ ok: false, error });
    }
  }

  function connect() {
    reconnectTimer = null;
    const sock = new WS(url);
    ws = sock;
    sock.onopen = () => {
      backoff = BACKOFF_MIN_MS;
      state.connected = true;
      emit("connected");
    };
    sock.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg && typeof msg === "object" && !Array.isArray(msg) && apply(msg)) emit(msg.type);
    };
    sock.onclose = () => {
      if (ws !== sock) return;
      ws = null;
      failPending("offline");
      const wasConnected = state.connected;
      state.connected = false;
      if (wasConnected) emit("connected");
      reconnectTimer = later(connect, backoff);
      backoff = Math.min(backoff * 2, BACKOFF_MAX_MS);
    };
    // onerror is always followed by onclose; nothing to do here.
    sock.onerror = () => {};
  }

  function sendAction(entityId) {
    if (!ws || ws.readyState !== WS.OPEN) return Promise.reject({ ok: false, error: "offline" });
    const id = `a${++seq}-${Date.now().toString(36)}`;
    return new Promise((resolve, reject) => {
      const timer = later(() => {
        pending.delete(id);
        reject({ ok: false, error: "timeout" });
      }, ACTION_TIMEOUT_MS);
      pending.set(id, { resolve, reject, timer });
      ws.send(JSON.stringify({ type: "action", id, entity_id: entityId }));
    });
  }

  return {
    connect() {
      if (!ws && !reconnectTimer) connect();
    },
    get: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    sendAction,
  };
}
