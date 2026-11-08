// The Home page's vacuum card (mockup "vysavac-den" variant V1): the robot's state and
// battery, one big button per configured room (config.vacuum.rooms) and "whole home" /
// "dock" buttons.
//
//   const card = renderVacuum(el, vacuumCfg, ha, ctx)   ctx: {store, holdRotation, canAct}
//   card.update(ha)    on every store update with state.ha
//   card.destroy()
//
// A room button sends the "room:<i>" command (server: Roborock segment cleaning), the
// others "start" / "dock" (store.sendAction(entity, command)). While the robot cleans after
// a room tap that room's button is lit and a second tap sends it to the dock. Taps show a
// short toast; a failed action shows action_failed. View-only viewers (canAct() false) see
// the card but taps do nothing. Which room was started is remembered for the page session
// only (module level, so a page change keeps it) and forgotten once the robot stops.
import { t } from "../shared/i18n.js";
import { describe } from "../shared/ha-format.js";
import { classifyGesture } from "./lib/gesture.js";
import { showToast } from "./toast.js";
import { PENDING_MAX_MS } from "./tile.js";

const BUSY = new Set(["cleaning", "returning"]);
const START_GRACE_MS = 60000; // HA may still say "docked" for a while after a start
let startedRoom = null; // {entity, index, at} of the last room started from this display

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

// Which room button is lit: the started room while the robot is cleaning, else none.
export function runningRoom(started, entityId, state) {
  if (!started || started.entity !== entityId || state !== "cleaning") return null;
  return started.index;
}

export function renderVacuum(root, cfg, ha, ctx) {
  const canAct = ctx.canAct ?? (() => true);
  root.className = "wb-vac";
  const head = el("div", "wb-vac-h");
  const icon = el("i", "mdi mdi-robot-vacuum");
  const who = el("div", "wb-vac-who");
  const name = el("div", "wb-vac-nm");
  const status = el("small");
  who.append(name, status);
  const battery = el("span", "wb-vac-bat");
  head.append(icon, who, battery);

  const rooms = el("div", "wb-vac-rooms");
  const buttons = cfg.rooms.map((room, index) => {
    const b = el("div", "wb-vac-room");
    b.dataset.tap = "";
    b.dataset.cmd = `room:${index}`;
    b.append(el("i", `mdi ${room.icon || "mdi-floor-plan"}`), el("span", "wb-vac-rn", room.name), el("small"));
    rooms.append(b);
    return b;
  });
  const acts = el("div", "wb-vac-acts");
  const all = el("div", "wb-vac-act", t("vacuum_all"));
  all.dataset.tap = "";
  all.dataset.cmd = "start";
  all.prepend(el("i", "mdi mdi-play"));
  const dock = el("div", "wb-vac-act", t("vacuum_dock"));
  dock.dataset.tap = "";
  dock.dataset.cmd = "dock";
  dock.prepend(el("i", "mdi mdi-home-import-outline"));
  acts.append(all, dock);
  root.replaceChildren(head, rooms, acts);

  let current = ha;
  let pending = false;
  let alive = true;

  function draw() {
    const s = current?.[cfg.entity_id];
    const d = describe(cfg.entity_id, s);
    setText(name, s?.attributes?.friendly_name || t("vacuum"));
    const lit = runningRoom(startedRoom, cfg.entity_id, s?.state);
    setText(status, lit !== null && cfg.rooms[lit] ? `${d.value} · ${cfg.rooms[lit].name}` : d.value);
    // Stopped (after the start had time to show up in HA): forget the room.
    if (startedRoom && s?.state && !BUSY.has(s.state) && Date.now() - startedRoom.at > START_GRACE_MS) startedRoom = null;
    const bat = cfg.battery_entity ? current?.[cfg.battery_entity]?.state : null;
    const pct = bat !== null && bat !== undefined && Number.isFinite(Number(bat)) ? `${Math.round(Number(bat))} %` : "";
    setText(battery, pct);
    battery.hidden = !pct;
    buttons.forEach((b, i) => {
      b.classList.toggle("run", i === lit);
      setText(b.lastElementChild, i === lit ? t("vacuum_stop") : "");
    });
    root.classList.toggle("view-only", !canAct());
    root.classList.toggle("pending", pending);
  }

  function run(command, button) {
    if (pending || !canAct()) return;
    const s = current?.[cfg.entity_id];
    const lit = runningRoom(startedRoom, cfg.entity_id, s?.state);
    let cmd = command;
    if (cmd.startsWith("room:") && Number(cmd.slice(5)) === lit) cmd = "dock"; // second tap stops it
    pending = true;
    button.classList.add("pressed");
    ctx.holdRotation?.(PENDING_MAX_MS);
    draw();
    ctx.store
      .sendAction(cfg.entity_id, cmd)
      .then((r) => {
        if (!r?.ok) throw r;
        if (cmd.startsWith("room:")) {
          const index = Number(cmd.slice(5));
          startedRoom = { entity: cfg.entity_id, index, at: Date.now() };
          showToast(t("vacuum_started", { room: cfg.rooms[index].name }));
        } else if (cmd === "start") {
          startedRoom = null;
          showToast(t("vacuum_all_started"));
        } else {
          startedRoom = null;
          showToast(t("vacuum_docking"));
        }
      })
      .catch(() => alive && showToast(t("action_failed")))
      .finally(() => {
        pending = false;
        button.classList.remove("pressed");
        if (alive) draw();
      });
  }

  // Taps, classified like the shell does (a swipe over the card still pages).
  let press = null;
  const onDown = (e) => {
    if (!e.isPrimary) return;
    press = { x: e.clientX, y: e.clientY, t: Date.now(), target: e.target };
  };
  const onUp = (e) => {
    if (!e.isPrimary || !press) return;
    const p = press;
    press = null;
    if (classifyGesture({ dx: e.clientX - p.x, dy: e.clientY - p.y, dt: Date.now() - p.t }) !== "tap") return;
    const b = p.target.closest?.("[data-cmd]");
    if (b && root.contains(b)) run(b.dataset.cmd, b);
  };
  const onCancel = () => (press = null);
  root.addEventListener("pointerdown", onDown);
  root.addEventListener("pointerup", onUp);
  root.addEventListener("pointercancel", onCancel);
  draw();

  return {
    update(next) {
      current = next;
      draw();
    },
    destroy() {
      alive = false;
      root.removeEventListener("pointerdown", onDown);
      root.removeEventListener("pointerup", onUp);
      root.removeEventListener("pointercancel", onCancel);
    },
  };
}
