// Tap tile shared by the Today quick-tiles row and the Home page.
//
//   const tile = renderTile(el, tileCfg, haState, onTap, { holdRotation, canAct } = {})
//   tile.update(haState)   call on every store update with state.ha[tileCfg.entity_id]
//   tile.destroy()         stops timers and listeners; later action results are ignored
//
// `el` is the tile element, created and placed by the caller; renderTile fills it (icon,
// name, value line) and owns its classes: wb-tile + on | run | pending | pressed.
// `tileCfg` is a config Tile {entity_id, label, icon, group, controllable}; the text comes
// from describe() with label/icon overrides. `onTap()` must start the action and return its
// promise, normally () => ctx.store.sendAction(tileCfg.entity_id). Pass the page's
// ctx.holdRotation: each accepted tap calls holdRotation(PENDING_MAX_MS) so rotation cannot
// page away while the tile is pending (5 s action timeout + 5 s safety timer).
// `canAct()` (optional, default: always true) is asked on every draw and tap; pass
// () => ctx.canAct(). When it returns false (a view-only visitor) the tile gets the
// view-only class (no pressed look) and a tap does nothing: no spinner, no toast, no action.
//
// Controllable tiles get the data-tap attribute (the shell then does not change page) and a
// pressed look. A tap shows the optimistic state with a spinner and calls onTap(); further
// taps are ignored while pending. {ok:true} stays pending until HA reports the change
// (5 s safety timer, then the actual state); {ok:false} or a rejection reverts and shows the
// action_failed toast. State logic: lib/tile-state.js. Non-controllable tiles only display.
import { t } from "../shared/i18n.js";
import { IDLE, tileReducer, tileView } from "./lib/tile-state.js";
import { classifyGesture } from "./lib/gesture.js";
import { showToast } from "./toast.js";

const SAFETY_MS = 5000;
const ACTION_TIMEOUT_MS = 5000; // store.sendAction rejects after this
export const PENDING_MAX_MS = ACTION_TIMEOUT_MS + SAFETY_MS + 1000;

function node(tag, cls) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  return n;
}

export function renderTile(el, cfg, haState, onTap, { holdRotation, canAct = () => true } = {}) {
  let s = IDLE;
  let ha = haState;
  let alive = true;
  let safety = null;
  let press = null;
  let drawn = "";

  const icon = node("i");
  const name = node("span", "wb-tile-name");
  const small = node("small");
  const spin = node("span", "wb-spin");
  const value = node("span", "wb-tile-val");
  small.append(value);
  el.className = "wb-tile";
  el.replaceChildren(icon, name, small);
  if (cfg.controllable) el.dataset.tap = "";

  function draw() {
    const v = tileView(cfg, ha, s);
    const viewOnly = Boolean(cfg.controllable) && !canAct();
    const key = JSON.stringify([v, viewOnly]);
    if (key === drawn) return;
    drawn = key;
    icon.className = `mdi ${v.icon}`;
    name.textContent = v.name;
    value.textContent = v.value;
    el.classList.toggle("on", v.tone === "on");
    el.classList.toggle("run", v.tone === "run");
    el.classList.toggle("pending", v.pending);
    el.classList.toggle("view-only", viewOnly);
    // The spinner exists only while pending, so its animation never runs otherwise.
    if (v.pending) small.prepend(spin);
    else spin.remove();
  }

  function clearSafety() {
    clearTimeout(safety);
    safety = null;
  }

  function dispatch(ev) {
    const prev = s;
    s = tileReducer(s, ev);
    if (s === prev) return;
    if (!s.pending) clearSafety();
    if (s.failed && ev.type === "result") showToast(t("action_failed"));
    draw();
  }

  function settle(token, ok) {
    if (!alive) return;
    dispatch({ type: "result", token, ok });
    // Server ok but no HA delta yet: wait up to SAFETY_MS, then quietly show the actual
    // state. No toast here (accepted ruling): HA confirmed the call, it just did not change
    // a state we can see (or the change is slow).
    if (ok && s.pending && s.token === token && !safety) {
      safety = setTimeout(() => alive && dispatch({ type: "timeout", token }), SAFETY_MS);
    }
  }

  function tap() {
    const before = s.token;
    dispatch({ type: "tap", entityId: cfg.entity_id, haState: ha, controllable: Boolean(cfg.controllable), canAct: canAct() });
    if (s.token === before) return; // ignored
    const token = s.token;
    holdRotation?.(PENDING_MAX_MS);
    Promise.resolve()
      .then(onTap)
      .then((r) => settle(token, r?.ok === true), () => settle(token, false));
  }

  const onDown = (e) => {
    if (!e.isPrimary) return;
    press = { x: e.clientX, y: e.clientY, t: Date.now() };
    if (canAct()) el.classList.add("pressed");
  };
  const onUp = (e) => {
    if (!e.isPrimary || !press) return;
    const p = press;
    press = null;
    el.classList.remove("pressed");
    if (classifyGesture({ dx: e.clientX - p.x, dy: e.clientY - p.y, dt: Date.now() - p.t }) === "tap") tap();
  };
  const onCancel = () => {
    press = null;
    el.classList.remove("pressed");
  };
  if (cfg.controllable) {
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onCancel);
    el.addEventListener("pointerleave", onCancel);
  }

  draw();
  return {
    update(next) {
      ha = next;
      dispatch({ type: "ha", haState: next });
      draw();
    },
    destroy() {
      alive = false;
      clearSafety();
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      el.removeEventListener("pointerleave", onCancel);
    },
  };
}
