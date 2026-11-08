// Pure state logic for a tap tile (display/tile.js draws it). No DOM, no timers.
//
//   IDLE                                  -> initial tile state
//   optimisticState(entityId, haState)    -> HA state string to show right after a tap, or null
//                                            (spinner only); mirrors actions.py's service map
//   tileReducer(state, event)             -> next state (the same object when nothing changes)
//   tileView(tileCfg, haState, state)     -> {name, value, icon, tone: ""|"on"|"run", pending}
//
// Events:
//   {type:"tap", entityId, haState, controllable, canAct}
//                                                  ignored while pending, when not controllable, or
//                                                  when canAct is false (view-only visitor)
//   {type:"result", token, ok}                     the sendAction outcome for tap `token`
//   {type:"ha", haState}                           a store update (any; unchanged states are no-ops)
//   {type:"timeout", token}                        safety timer after an ok result
//
// Life of a tap: pending (optimistic state with "…" + spinner) -> HA reports a different state or a
// new last_changed -> idle, showing the real state. {ok:true} alone keeps it pending (the
// caller starts a ~5 s safety timer, then shows whatever HA has). {ok:false} -> idle with
// `failed` set (revert + toast). Results and timeouts for an older tap are ignored.
import { describe } from "../../shared/ha-format.js";
import { t } from "../../shared/i18n.js";

export const IDLE = Object.freeze({ pending: false, token: 0, base: null, baseChanged: null, optimistic: null, acked: false, failed: false });

const TOGGLE_DOMAINS = new Set(["light", "switch", "fan", "input_boolean"]);
const RUN_STATES = new Set(["cleaning", "returning", "starting"]);

export function optimisticState(entityId, current) {
  const domain = String(entityId).split(".")[0];
  if (TOGGLE_DOMAINS.has(domain)) return current === "on" ? "off" : current === "off" ? "on" : null;
  if (domain === "vacuum") return current === "cleaning" || current === "returning" ? "returning" : "starting";
  if (domain === "cover") return current === "open" || current === "opening" ? "closing" : "opening";
  return null; // scene, script: no visible state to predict
}

const changed = (s, haState) =>
  !haState || haState.state !== s.base || (haState.last_changed ?? null) !== s.baseChanged;

export function tileReducer(s, ev) {
  switch (ev.type) {
    case "tap":
      if (s.pending || !ev.controllable || ev.canAct === false) return s;
      return {
        pending: true,
        token: s.token + 1,
        base: ev.haState?.state ?? null,
        baseChanged: ev.haState?.last_changed ?? null,
        optimistic: optimisticState(ev.entityId, ev.haState?.state),
        acked: false,
        failed: false,
      };
    case "result":
      if (!s.pending || ev.token !== s.token) return s;
      if (ev.ok) return s.acked ? s : { ...s, acked: true };
      return { ...s, pending: false, optimistic: null, failed: true };
    case "ha":
      return s.pending && changed(s, ev.haState) ? { ...s, pending: false, optimistic: null } : s;
    case "timeout":
      return s.pending && ev.token === s.token ? { ...s, pending: false, optimistic: null } : s;
    default:
      return s;
  }
}

// Config icons may be "mdi:name" or "mdi-name"; anything else falls back to describe()'s icon.
function cfgIcon(icon) {
  const name = String(icon ?? "").replace(/^mdi[:-]/, "");
  return /^[a-z0-9-]+$/.test(name) ? `mdi-${name}` : null;
}

export function tileView(cfg, haState, s) {
  const shown = s.pending && s.optimistic ? { attributes: {}, ...haState, state: s.optimistic } : haState;
  const d = describe(cfg.entity_id, shown);
  const state = shown?.state;
  let tone = "";
  if (d.kind === "vacuum") tone = RUN_STATES.has(state) ? "run" : "";
  else if (d.kind === "script") tone = state === "on" ? "run" : "";
  else if (d.on && d.kind !== "scene") tone = "on";
  let value = d.unit ? `${d.value} ${d.unit}` : d.value;
  // The predicted state reads as in progress: "Starting…".
  if (s.pending && s.optimistic) value = `${value}…`;
  // A scene's state is its last activation time ("unknown" before the first one).
  if (d.kind === "scene" && shown && state !== "unavailable") value = t("scene");
  return { name: cfg.label || d.name, value, icon: cfgIcon(cfg.icon) || d.icon, tone, pending: s.pending };
}
