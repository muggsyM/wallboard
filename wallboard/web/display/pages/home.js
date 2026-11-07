// Home: Home Assistant overview and controls. Look: mockups/portrait-v1.html screen 4.
//
// - Header: title "home" + a subtitle summarising what needs attention (open doors and
//   windows, running appliances among status_entities; "all good" when nothing does).
// - Room cards (3 columns, wrapping): name, temperature, humidity and "N lights on" from
//   light_entities; "–" for missing or unavailable sensors.
// - "Lights" and "Devices" tile groups: config.tiles by group, in config order, drawn by
//   tile.js (tap to control).
// - Status rows: one per status entity (icon, name, value from describe()). Open doors,
//   windows and garage doors and unlocked locks use the warning colour, locked locks the
//   calm green, running appliances an amber icon (lib/home-state.js statusTone).
// Empty sections are hidden; with nothing configured a calm "set up HA" hint shows.
//
// The sections are rebuilt only when rooms/tiles/status_entities (or the language) change,
// keyed on their JSON content: a reconnect snapshot brings a new config object and must
// not destroy a pending tile. HA deltas only update text and the tiles via update().
import { renderHeader } from "../header.js";
import { t, getLanguage } from "../../shared/i18n.js";
import { describe } from "../../shared/ha-format.js";
import { renderTile } from "../tile.js";
import { attentionSummary, roomView, statusTone, tileGroups } from "../lib/home-state.js";

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function roomCard(room) {
  const card = el("div", "wb-room");
  const name = el("div", "wb-room-nm");
  const temp = el("div", "wb-room-t");
  const value = el("span");
  const unit = el("span", "wb-room-u");
  temp.append(value, unit);
  const meta = el("div", "wb-room-meta");
  card.append(name, temp, meta);
  return {
    node: card,
    update(ha) {
      const v = roomView(room, ha);
      setText(name, v.name);
      setText(value, v.temp);
      setText(unit, v.unit);
      setText(meta, v.meta);
      meta.hidden = !v.meta;
    },
  };
}

function statusRow(entityId) {
  const row = el("div", "wb-srow");
  const icon = el("i");
  const name = el("span", "wb-srow-nm");
  const value = el("span", "wb-srow-v");
  row.append(icon, name, value);
  let tone = "";
  return {
    node: row,
    update(ha) {
      const s = ha?.[entityId];
      const d = describe(entityId, s);
      const cls = `mdi ${d.icon}`;
      if (icon.className !== cls) icon.className = cls;
      setText(name, d.name);
      setText(value, d.unit ? `${d.value} ${d.unit}` : d.value);
      const next = statusTone(entityId, s);
      if (next !== tone) {
        if (tone) row.classList.remove(tone);
        if (next) row.classList.add(next);
        tone = next;
      }
    },
  };
}

export default {
  type: "home",
  titleKey: "home",
  settings: [],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-page-header"></div>
      <div class="wb-rooms" hidden></div>
      <div class="wb-home-group" data-group="lights" hidden><div class="wb-lbl"></div><div class="wb-tiles"></div></div>
      <div class="wb-home-group" data-group="devices" hidden><div class="wb-lbl"></div><div class="wb-tiles"></div></div>
      <div class="wb-status" hidden></div>
      <div class="wb-panel wb-home-empty" hidden><i class="mdi mdi-home-assistant"></i><span></span></div>`;
    const header = root.querySelector(".wb-page-header");
    const roomsEl = root.querySelector(".wb-rooms");
    const groupEls = {
      lights: root.querySelector('[data-group="lights"]'),
      devices: root.querySelector('[data-group="devices"]'),
    };
    const statusEl = root.querySelector(".wb-status");
    const emptyEl = root.querySelector(".wb-home-empty");
    let key = null;
    let rooms = []; // [{node, update(ha)}]
    let rows = [];
    let tiles = []; // [{entityId, handle}]

    function build(state) {
      const config = state.config;
      for (const x of tiles) x.handle.destroy();
      rooms = (config?.rooms || []).map(roomCard);
      roomsEl.replaceChildren(...rooms.map((r) => r.node));
      roomsEl.hidden = rooms.length === 0;

      tiles = [];
      const groups = tileGroups(config?.tiles);
      for (const [group, cfgs] of Object.entries(groups)) {
        const box = groupEls[group];
        box.querySelector(".wb-lbl").textContent = t(group);
        const made = cfgs.map((cfg) => {
          const node = el("div");
          const handle = renderTile(node, cfg, state.ha?.[cfg.entity_id], () => ctx.store.sendAction(cfg.entity_id), { holdRotation: ctx.holdRotation });
          return { entityId: cfg.entity_id, node, handle };
        });
        box.querySelector(".wb-tiles").replaceChildren(...made.map((x) => x.node));
        box.hidden = made.length === 0;
        tiles.push(...made);
      }

      rows = (config?.status_entities || []).map(statusRow);
      statusEl.replaceChildren(...rows.map((r) => r.node));
      statusEl.hidden = rows.length === 0;

      emptyEl.lastElementChild.textContent = t("home_empty");
      emptyEl.hidden = !(rooms.length === 0 && tiles.length === 0 && rows.length === 0);
    }

    function render(state) {
      const config = state.config;
      renderHeader(header, state, { titleKey: "home", subtitle: attentionSummary(config?.status_entities, state.ha) });
      const next = JSON.stringify([config?.rooms ?? [], config?.tiles ?? [], config?.status_entities ?? [], getLanguage()]);
      if (next !== key) {
        key = next;
        build(state); // tiles are drawn with the current state by renderTile
      } else {
        for (const x of tiles) x.handle.update(state.ha?.[x.entityId]);
      }
      for (const r of rooms) r.update(state.ha);
      for (const r of rows) r.update(state.ha);
    }

    render(ctx.store.get());
    return {
      update: (state) => render(state),
      destroy() {
        for (const x of tiles) x.handle.destroy();
        root.innerHTML = "";
      },
    };
  },
};
