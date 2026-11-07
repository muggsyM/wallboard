// Settings page (admin only). Plain ES module, no build step.
//
// Loads GET /api/admin/config, edits a local copy of the config and sends the whole config
// back with PUT /api/admin/config on Save (secrets go separately to PUT /api/admin/secrets
// and are never prefilled). All DOM is built with h() / textContent; no innerHTML with data.
// Every write carries X-Requested-With: wallboard (the server rejects admin writes without it).

import { PAGES, getPage } from "/static/display/pages/registry.js";
import { t, setLanguage, loadLanguage } from "/static/shared/i18n.js";
import * as M from "/static/admin/settings-model.js";

const CSRF = { "X-Requested-With": "wallboard" };
const MEMBER_COLORS = ["#4f8cff", "#ff6b9d", "#ffb547", "#4cd4a0", "#b18cff", "#ff7a59"];
const LONG_PRESS_MS = 350;
const SECRET_FIELDS = ["ha_token", "google_client_id", "google_client_secret"];

const S = {
  view: null, // last GET/PUT response: {config, secrets_set, status, controllable_domains}
  config: null, // the working copy being edited
  draft: { ha_token: "", google_client_id: "", google_client_secret: "", current_password: "", admin_password: "", admin_password2: "" },
  entities: null, // [{entity_id, name, domain}] or null when HA is not connected
  calendars: {}, // account_id -> {state: "loading"|"ok"|"error", items, expired}
  haTest: null, // {ok, text}
  googleNote: null, // {ok, text}
  connecting: false, // a Google sign-in tab was opened; refresh accounts when we get focus back
  dirty: false,
};

// ---- DOM helpers ----------------------------------------------------------------------------

const PROPS = new Set(["value", "checked", "disabled", "hidden", "selected", "type", "min", "max", "maxLength", "placeholder"]);

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (PROPS.has(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const $ = (sel) => document.querySelector(sel);

function markDirty() {
  S.dirty = true;
  $("#save").disabled = false;
  setStatus(t("admin_unsaved"));
}

function setStatus(text, kind = "") {
  const el = $("#save-status");
  el.textContent = text || "";
  el.className = `save-status ${kind}`;
}

// A labelled field. `loc` is the config path the server uses in 422 errors ("members.0.color").
function field(label, control, { loc, hint, inline } = {}) {
  return h(
    "label",
    { class: inline ? "field inline" : "field", dataset: loc ? { loc } : undefined },
    inline ? [control, h("span", { text: label })] : [h("span", { text: label }), control],
    hint ? h("small", { class: "hint", text: hint }) : null,
  );
}

// Inputs bound to obj[key]. `nullable` stores null for an empty value.
function textInput(obj, key, opts = {}) {
  return h("input", {
    type: opts.type || "text",
    value: obj[key] ?? "",
    placeholder: opts.placeholder,
    list: opts.list,
    maxLength: opts.maxLength,
    autocomplete: "off",
    autocapitalize: opts.list ? "none" : undefined,
    spellcheck: opts.list ? "false" : undefined,
    oninput: (e) => {
      obj[key] = opts.nullable && e.target.value === "" ? null : e.target.value;
      markDirty();
    },
    onchange: opts.onchange,
  });
}

function numberInput(obj, key, { min, placeholder, nullable } = {}) {
  return h("input", {
    type: "number",
    inputmode: "numeric",
    min,
    placeholder,
    value: obj[key] ?? "",
    oninput: (e) => {
      const v = e.target.value;
      if (v === "") {
        if (nullable) obj[key] = null;
      } else obj[key] = Number(v);
      markDirty();
    },
  });
}

function checkbox(obj, key, { disabled, onchange } = {}) {
  return h("input", {
    type: "checkbox",
    checked: !!obj[key],
    disabled,
    onchange: (e) => {
      obj[key] = e.target.checked;
      markDirty();
      onchange?.(e);
    },
  });
}

function select(obj, key, options, onchange) {
  return h(
    "select",
    {
      onchange: (e) => {
        obj[key] = e.target.value;
        markDirty();
        onchange?.(e);
      },
    },
    options.map(([value, label]) => h("option", { value, selected: obj[key] === value, text: label })),
  );
}

// Write-only secret: a password input bound to the draft, never filled from the server.
function secretInput(key, autocomplete = "off") {
  return h("input", {
    type: "password",
    value: S.draft[key],
    autocomplete,
    oninput: (e) => {
      S.draft[key] = e.target.value;
      markDirty();
    },
  });
}

const badge = (text, kind = "") => h("span", { class: `badge ${kind}`, text });
const setBadge = (isSet) => badge(isSet ? t("admin_set") : t("admin_not_set"), isSet ? "ok" : "");

function button(label, onclick, cls = "secondary", extra = {}) {
  return h("button", { type: "button", class: cls, onclick, ...extra }, label);
}

function section(id, titleKey, ...children) {
  return h("section", { id, class: "panel" }, h("h2", { text: t(titleKey) }), ...children);
}

// A list of entity ids edited as removable chips plus an input with suggestions.
function entityList(list, loc, datalist) {
  const wrap = h("div", { class: "chips", dataset: { loc } });
  const draw = () => {
    const input = h("input", { type: "text", list: datalist, placeholder: "entity_id", autocomplete: "off", autocapitalize: "none", spellcheck: "false" });
    const add = () => {
      const v = input.value.trim();
      if (v && !list.includes(v)) {
        list.push(v);
        markDirty();
      }
      draw();
      wrap.querySelector("input").focus();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        add();
      }
    });
    wrap.replaceChildren(
      ...list.map((eid, i) =>
        h("span", { class: "chip" }, eid, h("button", { type: "button", class: "chip-x", "aria-label": t("admin_remove"), onclick: () => { list.splice(i, 1); markDirty(); draw(); } }, "×")),
      ),
      h("div", { class: "row" }, input, button(t("admin_add"), add)),
    );
  };
  draw();
  return wrap;
}

// ---- API --------------------------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, data) {
    const detail = data?.detail;
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.status = status;
    this.errors = Array.isArray(detail) ? detail : null;
  }
}

async function api(method, path, body) {
  const headers = method === "GET" ? {} : { ...CSRF };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" });
  if (res.status === 401) {
    location.href = `/login?next=${encodeURIComponent("/settings")}`;
    throw new ApiError(401, null);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

// ---- sections -----------------------------------------------------------------------------------

function renderGeneral() {
  const c = S.config;
  return section(
    "general",
    "admin_general",
    field(t("admin_language"), select(c, "language", [["cs", "Čeština"], ["en", "English"]]), { loc: "language" }),
    field(t("admin_timezone"), textInput(c, "timezone", { list: "dl-timezones" }), { loc: "timezone" }),
    h(
      "div",
      { class: "grid2" },
      field(t("admin_rotation"), numberInput(c, "rotation_seconds", { min: 5 }), { loc: "rotation_seconds" }),
      field(t("admin_touch_pause"), numberInput(c, "touch_pause_seconds", { min: 15 }), { loc: "touch_pause_seconds" }),
    ),
    h("h3", { text: t("admin_night") }),
    field(t("admin_night_enabled"), checkbox(c.night, "enabled"), { loc: "night.enabled", inline: true }),
    h(
      "div",
      { class: "grid2" },
      field(t("admin_night_start"), textInput(c.night, "start", { type: "time" }), { loc: "night.start" }),
      field(t("admin_night_end"), textInput(c.night, "end", { type: "time" }), { loc: "night.end" }),
    ),
  );
}

function renderFamily() {
  const c = S.config;
  const cards = c.members.map((m, i) =>
    h(
      "div",
      { class: "card-row", dataset: { loc: `members.${i}` } },
      h(
        "div",
        { class: "grid-member" },
        field(t("admin_member_name"), textInput(m, "name", { onchange: () => rerender("google") }), { loc: `members.${i}.name` }),
        field(t("admin_member_initial"), textInput(m, "initial", { maxLength: 2 }), { loc: `members.${i}.initial` }),
        field(t("admin_member_color"), textInput(m, "color", { type: "color" }), { loc: `members.${i}.color` }),
      ),
      field(t("admin_member_person"), textInput(m, "person_entity", { list: "dl-person", nullable: true, placeholder: "person.…" }), { loc: `members.${i}.person_entity` }),
      h("div", { class: "row end" }, button(t("admin_remove"), () => removeMember(i), "danger")),
    ),
  );
  return section(
    "family",
    "admin_family",
    h("div", { class: "stack", dataset: { loc: "members" } }, cards),
    button(t("admin_add_member"), () => {
      c.members.push({ id: M.newId("m"), name: "", initial: "", color: MEMBER_COLORS[c.members.length % MEMBER_COLORS.length], person_entity: null });
      markDirty();
      rerender("family");
      rerender("google");
      document.querySelector("#family .card-row:last-child input")?.focus();
    }),
  );
}

function removeMember(i) {
  const c = S.config;
  const m = c.members[i];
  const name = m.name || t("admin_unnamed");
  if (M.memberAccounts(c, m.id).length) {
    alert(t("admin_member_has_google", { name }));
    return;
  }
  if (!confirm(t("admin_confirm_remove_member", { name }))) return;
  c.members.splice(i, 1);
  c.calendars = c.calendars.filter((cal) => cal.member_id !== m.id); // the server cascades the same way
  markDirty();
  rerender("family");
  rerender("google");
}

function renderGoogle() {
  const c = S.config;
  const set = S.view.secrets_set;
  const savedMembers = new Set(S.view.config.members.map((m) => m.id));
  const local = M.isLocalHost(location.hostname);

  const note = S.googleNote ? h("p", { class: S.googleNote.ok ? "note ok" : "note error", role: "status", text: S.googleNote.text }) : null;

  const members = c.members.map((m) => {
    const accounts = M.memberAccounts(c, m.id);
    let connectHint = null;
    if (!set.google_client) connectHint = t("admin_google_need_client");
    else if (!savedMembers.has(m.id)) connectHint = t("admin_google_need_save");
    return h(
      "div",
      { class: "card-row" },
      h("h3", { class: "member-title" }, h("span", { class: "dot", style: `background:${safeColor(m.color)}` }), m.name || t("admin_unnamed")),
      accounts.map(renderAccount),
      h(
        "div",
        { class: "row" },
        button(t("admin_google_connect"), () => connectGoogle(m.id), "secondary", { disabled: !!connectHint }),
        connectHint ? h("small", { class: "hint", text: connectHint }) : null,
      ),
    );
  });

  const paste = local
    ? null
    : (() => {
        const input = h("input", { type: "url", placeholder: "http://localhost:8080/api/google/callback?…", autocomplete: "off" });
        return h(
          "div",
          { class: "card-row" },
          field(t("admin_paste_label"), input, { hint: t("admin_paste_hint") }),
          button(t("admin_paste_submit"), () => pasteGoogle(input)),
        );
      })();

  return section(
    "google",
    "admin_google",
    h("p", { class: "hint", text: t("admin_google_setup") }),
    field(t("admin_google_client_id"), h("div", { class: "row" }, secretInput("google_client_id"), setBadge(set.google_client)), { loc: "google_client_id" }),
    field(t("admin_google_client_secret"), secretInput("google_client_secret"), { loc: "google_client_secret", hint: t("admin_secret_hint") }),
    note,
    c.google_accounts.length ? null : h("p", { class: "muted", text: t("admin_no_accounts") }),
    h("div", { class: "stack" }, members),
    paste,
  );
}

function safeColor(c) {
  return /^#[0-9a-fA-F]{3,8}$/.test(c || "") ? c : "#888888";
}

function renderAccount(acct) {
  const c = S.config;
  const ok = S.view.status?.google?.[acct.id];
  const cal = S.calendars[acct.id];
  const expired = ok === false || cal?.expired;
  if (!cal) loadCalendars(acct.id);

  // Calendars from Google, plus any configured ones Google did not return (e.g. sign-in expired).
  const items = cal?.items ? [...cal.items] : [];
  for (const ref of c.calendars.filter((r) => r.account_id === acct.id)) {
    if (!items.some((x) => x.id === ref.calendar_id)) items.push({ id: ref.calendar_id, name: ref.name });
  }

  const rows = items.map((calendar) => {
    const ref = M.calendarRef(c, acct.id, calendar.id);
    let memberId = ref?.member_id || acct.member_id;
    const box = h("input", {
      type: "checkbox",
      checked: !!ref,
      onchange: (e) => {
        M.setCalendar(c, acct, calendar, e.target.checked ? memberId : null);
        markDirty();
      },
    });
    const who = h(
      "select",
      {
        "aria-label": t("admin_calendar_member"),
        onchange: (e) => {
          memberId = e.target.value;
          if (box.checked) {
            M.setCalendar(c, acct, calendar, memberId);
            markDirty();
          }
        },
      },
      c.members.map((m) => h("option", { value: m.id, selected: m.id === memberId, text: m.name || t("admin_unnamed") })),
    );
    return h("div", { class: "cal-row" }, h("label", { class: "inline" }, box, h("span", { text: calendar.name || calendar.id })), who);
  });

  return h(
    "div",
    { class: "account" },
    h(
      "div",
      { class: "row" },
      h("strong", { class: "email", text: acct.email }),
      expired ? badge(t("admin_google_expired"), "error") : ok === true ? badge(t("admin_google_ok"), "ok") : null,
      button(t("admin_google_disconnect"), () => disconnectGoogle(acct), "danger small"),
    ),
    cal?.state === "loading" ? h("p", { class: "muted", text: t("admin_calendars_loading") }) : null,
    cal?.state === "error" && !cal.expired ? h("p", { class: "note error", text: t("admin_error", { msg: cal.error }) }) : null,
    rows.length ? h("div", { class: "cals" }, rows) : null,
  );
}

async function loadCalendars(accountId) {
  S.calendars[accountId] = { state: "loading" };
  try {
    const items = await api("GET", `/api/admin/google/${encodeURIComponent(accountId)}/calendars`);
    S.calendars[accountId] = { state: "ok", items };
  } catch (e) {
    S.calendars[accountId] = { state: "error", items: null, expired: e.status === 409, error: e.message };
  }
  rerender("google");
}

async function connectGoogle(memberId) {
  // Open the tab inside the click (popup blockers), then point it at Google once we have the URL.
  const win = window.open("about:blank", "_blank");
  try {
    const { url } = await api("POST", "/api/admin/google/start", { member_id: memberId });
    S.connecting = true;
    if (win) {
      win.opener = null;
      win.location.href = url;
    } else location.assign(url);
  } catch (e) {
    win?.close();
    S.googleNote = { ok: false, text: t("admin_error", { msg: e.message }) };
    rerender("google");
  }
}

// After the Google routes change accounts, take the server's accounts (and calendars for a
// removed account) without discarding other unsaved edits.
async function refreshGoogle() {
  const view = await api("GET", "/api/admin/config");
  S.view = { ...S.view, secrets_set: view.secrets_set, status: view.status, config: view.config };
  S.config.google_accounts = view.config.google_accounts;
  const ids = new Set(view.config.google_accounts.map((a) => a.id));
  S.config.calendars = S.config.calendars.filter((c) => ids.has(c.account_id));
  for (const id of Object.keys(S.calendars)) if (!ids.has(id)) delete S.calendars[id];
}

async function pasteGoogle(input) {
  try {
    await api("POST", "/api/admin/google/paste", { url: input.value.trim() });
    await refreshGoogle();
    S.googleNote = { ok: true, text: t("admin_google_connected") };
  } catch (e) {
    S.googleNote = { ok: false, text: t("admin_error", { msg: e.message }) };
  }
  rerender("google");
}

async function disconnectGoogle(acct) {
  if (!confirm(t("admin_confirm_disconnect", { email: acct.email }))) return;
  try {
    await api("DELETE", `/api/admin/google/${encodeURIComponent(acct.id)}`);
    delete S.calendars[acct.id];
    await refreshGoogle();
    S.googleNote = null;
  } catch (e) {
    S.googleNote = { ok: false, text: t("admin_error", { msg: e.message }) };
  }
  rerender("google");
}

function renderHA() {
  const c = S.config;
  const domains = S.view.controllable_domains;
  const connected = !!S.view.status?.ha;

  const testLine = S.haTest ? h("p", { class: S.haTest.ok ? "note ok" : "note error", role: "status", text: S.haTest.text }) : null;

  const rooms = c.rooms.map((r, i) =>
    h(
      "div",
      { class: "card-row", dataset: { loc: `rooms.${i}` } },
      field(t("admin_room_name"), textInput(r, "name"), { loc: `rooms.${i}.name` }),
      field(t("admin_room_temperature"), textInput(r, "temperature_entity", { list: "dl-sensor" }), { loc: `rooms.${i}.temperature_entity` }),
      field(t("admin_room_humidity"), textInput(r, "humidity_entity", { list: "dl-sensor", nullable: true }), { loc: `rooms.${i}.humidity_entity` }),
      h("div", { class: "field" }, h("span", { class: "label", text: t("admin_room_lights") }), entityList(r.light_entities, `rooms.${i}.light_entities`, "dl-light")),
      h("div", { class: "row end" }, button(t("admin_remove"), () => { c.rooms.splice(i, 1); markDirty(); rerender("ha"); }, "danger")),
    ),
  );

  const quickFull = c.quick_tiles.length >= M.MAX_QUICK_TILES;
  const tiles = c.tiles.map((tile, i) => {
    const canControl = M.isControllable(tile.entity_id, domains);
    const isQuick = c.quick_tiles.includes(tile.entity_id);
    const quick = h("input", {
      type: "checkbox",
      checked: isQuick,
      disabled: !isQuick && quickFull,
      onchange: (e) => {
        if (!M.setQuick(c, tile.entity_id, e.target.checked)) e.target.checked = false;
        markDirty();
        rerender("ha");
      },
    });
    return h(
      "div",
      { class: "card-row", dataset: { loc: `tiles.${i}` } },
      field(
        t("admin_tile_entity"),
        h("input", {
          type: "text", list: "dl-all", value: tile.entity_id, autocomplete: "off", autocapitalize: "none", spellcheck: "false",
          onchange: (e) => { M.renameTile(c, i, e.target.value.trim(), domains); markDirty(); rerender("ha"); },
        }),
        { loc: `tiles.${i}.entity_id` },
      ),
      h(
        "div",
        { class: "grid2" },
        field(t("admin_tile_label"), textInput(tile, "label", { nullable: true, placeholder: entityName(tile.entity_id) }), { loc: `tiles.${i}.label` }),
        field(t("admin_tile_icon"), textInput(tile, "icon", { nullable: true, placeholder: "mdi-…" }), { loc: `tiles.${i}.icon` }),
      ),
      field(t("admin_tile_group"), select(tile, "group", [["lights", t("lights")], ["devices", t("devices")]]), { loc: `tiles.${i}.group` }),
      field(t("admin_tile_controllable"), checkbox(tile, "controllable", { disabled: !canControl }), {
        loc: `tiles.${i}.controllable`,
        inline: true,
        hint: canControl ? null : t("admin_tile_not_controllable"),
      }),
      field(t("admin_tile_quick"), quick, { inline: true }),
      h("div", { class: "row end" }, button(t("admin_remove"), () => { M.removeTile(c, i); markDirty(); rerender("ha"); }, "danger")),
    );
  });

  const newTile = h("input", { type: "text", list: "dl-all", placeholder: "light.…", autocomplete: "off", autocapitalize: "none", spellcheck: "false" });
  const addTile = () => {
    const eid = newTile.value.trim();
    if (!eid) return;
    c.tiles.push(M.newTile(eid, domains));
    markDirty();
    rerender("ha");
  };
  newTile.addEventListener("keydown", (e) => e.key === "Enter" && (e.preventDefault(), addTile()));

  return section(
    "ha",
    "admin_ha",
    h("div", { class: "row" }, connected ? badge(t("admin_ha_connected"), "ok") : badge(t("admin_ha_disconnected"), "error")),
    field(t("admin_ha_url"), textInput(c, "ha_url", { type: "url", nullable: true, placeholder: "http://homeassistant.local:8123" }), { loc: "ha_url" }),
    field(t("admin_ha_token"), h("div", { class: "row" }, secretInput("ha_token"), setBadge(S.view.secrets_set.ha_token)), { loc: "ha_token", hint: t("admin_secret_hint") }),
    h("div", { class: "row" }, button(t("admin_ha_test"), testHA, "secondary", { id: "ha-test" })),
    testLine,
    S.entities ? null : h("p", { class: "hint", text: t("admin_ha_no_entities") }),
    field(t("admin_weather_entity"), textInput(c, "weather_entity", { list: "dl-weather", nullable: true, placeholder: "weather.…" }), { loc: "weather_entity" }),
    h("h3", { text: t("admin_rooms") }),
    h("div", { class: "stack", dataset: { loc: "rooms" } }, rooms),
    button(t("admin_add_room"), () => {
      c.rooms.push({ name: "", temperature_entity: "", humidity_entity: null, light_entities: [] });
      markDirty();
      rerender("ha");
    }),
    h("h3", { text: t("admin_tiles") }),
    h("p", { class: "hint", dataset: { loc: "quick_tiles" }, text: t("admin_quick_hint") }),
    h("div", { class: "stack", dataset: { loc: "tiles" } }, tiles),
    h("div", { class: "row" }, newTile, button(t("admin_add_tile"), addTile)),
    h("h3", { text: t("admin_status_entities") }),
    entityList(c.status_entities, "status_entities", "dl-all"),
  );
}

function entityName(eid) {
  return S.entities?.find((e) => e.entity_id === eid)?.name || "";
}

async function testHA() {
  const btn = $("#ha-test");
  btn.disabled = true;
  S.haTest = { ok: true, text: t("admin_ha_testing") };
  rerender("ha");
  try {
    const r = await api("POST", "/api/admin/ha/test", { url: S.config.ha_url || "", token: S.draft.ha_token });
    S.haTest = r.ok ? { ok: true, text: t("admin_ha_test_ok", { version: r.version || "?" }) } : { ok: false, text: t("admin_ha_test_failed", { msg: r.error }) };
  } catch (e) {
    S.haTest = { ok: false, text: t("admin_ha_test_failed", { msg: e.message }) };
  }
  rerender("ha");
}

function renderPages() {
  const c = S.config;
  const list = h("div", { class: "stack pages", dataset: { loc: "pages" } });
  c.pages.forEach((page, i) => list.append(pageRow(page, i)));
  makeReorderable(list, (from, to) => {
    if (from === to) return;
    c.pages = M.moveItem(c.pages, from, to);
    markDirty();
    rerender("pages");
  });

  const types = Object.keys(PAGES);
  const pick = h("select", {}, types.map((type) => h("option", { value: type, text: t(PAGES[type].titleKey) })));
  return section(
    "pages",
    "admin_pages",
    h("p", { class: "hint", text: t("admin_drag_hint") }),
    list,
    h(
      "div",
      { class: "row" },
      pick,
      button(t("admin_add_page"), () => {
        c.pages.push({ type: pick.value, enabled: true, seconds: null, options: {} });
        markDirty();
        rerender("pages");
      }),
    ),
  );
}

function pageRow(page, i) {
  const mod = getPage(page.type);
  page.options ||= {};
  const title = mod ? t(mod.titleKey) : t("admin_unknown_page", { type: page.type });
  const settings = (mod?.settings || []).map((s) => pageSetting(page, s, i));
  return h(
    "div",
    { class: "card-row page-row", dataset: { loc: `pages.${i}`, index: String(i) } },
    h("div", { class: "drag", title: t("admin_drag_hint") }, h("span", { class: "grip", "aria-hidden": "true", text: "⠿" }), h("strong", { text: title })),
    h(
      "div",
      { class: "grid2" },
      field(t("admin_page_enabled"), checkbox(page, "enabled"), { loc: `pages.${i}.enabled`, inline: true }),
      field(
        t("admin_page_seconds"),
        numberInput(page, "seconds", { min: 5, nullable: true, placeholder: t("admin_page_seconds_default", { n: S.config.rotation_seconds }) }),
        { loc: `pages.${i}.seconds` },
      ),
    ),
    settings,
    h("div", { class: "row end" }, button(t("admin_remove"), () => { S.config.pages.splice(i, 1); markDirty(); rerender("pages"); }, "danger small")),
  );
}

// Generic control for one entry of a page module's `settings` schema.
function pageSetting(page, s, i) {
  const loc = `pages.${i}.options.${s.key}`;
  const label = t(s.labelKey || s.key);
  const current = page.options[s.key] ?? s.default;
  const store = (v) => {
    if (v === undefined) delete page.options[s.key];
    else page.options[s.key] = v;
    markDirty();
  };
  if (s.type === "boolean") {
    return field(label, h("input", { type: "checkbox", checked: !!current, onchange: (e) => store(e.target.checked) }), { loc, inline: true });
  }
  if (Array.isArray(s.options)) {
    return field(
      label,
      h("select", { onchange: (e) => store(e.target.value) }, s.options.map((o) => h("option", { value: o, selected: o === current, text: t(String(o)) }))),
      { loc },
    );
  }
  if (s.type === "number") {
    return field(label, h("input", { type: "number", min: s.min, max: s.max, value: current ?? "", oninput: (e) => store(e.target.value === "" ? undefined : Number(e.target.value)) }), { loc });
  }
  return field(label, h("input", { type: "text", value: current ?? "", oninput: (e) => store(e.target.value === "" ? undefined : e.target.value) }), { loc });
}

// Drag rows by their .drag header with pointer events. Mouse/pen drag at once; touch needs a
// long press first so normal scrolling keeps working. The row moves live; onMove(from, to)
// runs on release.
function makeReorderable(list, onMove) {
  let drag = null; // {row, from, pointerId, timer, startY, active}

  const rows = () => [...list.children];
  const start = () => {
    drag.active = true;
    drag.row.classList.add("dragging");
    navigator.vibrate?.(15);
  };
  const end = (commit) => {
    if (!drag) return;
    clearTimeout(drag.timer);
    const { row, from, active } = drag;
    drag = null;
    row.classList.remove("dragging");
    if (active && commit) onMove(from, rows().indexOf(row));
  };

  list.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(".drag");
    if (!handle || drag || (e.pointerType === "mouse" && e.button !== 0)) return;
    const row = handle.closest(".page-row");
    drag = { row, from: rows().indexOf(row), pointerId: e.pointerId, startY: e.clientY, startX: e.clientX, active: false, timer: null };
    list.setPointerCapture(e.pointerId); // the list, not the row: rows move in the DOM
    if (e.pointerType === "touch") drag.timer = setTimeout(start, LONG_PRESS_MS);
    else {
      e.preventDefault();
      start();
    }
  });
  list.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    if (!drag.active) {
      if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) > 10) end(false); // a scroll, not a press
      return;
    }
    e.preventDefault();
    const others = rows().filter((r) => r !== drag.row);
    const mids = others.map((r) => {
      const b = r.getBoundingClientRect();
      return b.top + b.height / 2;
    });
    const idx = M.dropIndex(e.clientY, mids);
    if (idx >= others.length) list.append(drag.row);
    else if (others[idx] !== drag.row.nextElementSibling) list.insertBefore(drag.row, others[idx]);
  });
  list.addEventListener("pointerup", (e) => drag && e.pointerId === drag.pointerId && end(true));
  list.addEventListener("pointercancel", (e) => drag && e.pointerId === drag.pointerId && end(true));
  // While dragging, stop the page from scrolling under the finger.
  list.addEventListener("touchmove", (e) => drag?.active && e.cancelable && e.preventDefault(), { passive: false });
  list.addEventListener("contextmenu", (e) => e.target.closest(".drag") && e.preventDefault());
}

function renderPassword() {
  return section(
    "password",
    "admin_password",
    field(t("admin_current_password"), secretInput("current_password", "current-password"), { loc: "current_password" }),
    field(t("admin_new_password"), secretInput("admin_password", "new-password"), { loc: "admin_password" }),
    field(t("admin_repeat_password"), secretInput("admin_password2", "new-password"), { loc: "admin_password2" }),
  );
}

// ---- rendering + errors -------------------------------------------------------------------------

const SECTIONS = { general: renderGeneral, family: renderFamily, google: renderGoogle, ha: renderHA, pages: renderPages, password: renderPassword };

function rerender(id) {
  const old = document.getElementById(id);
  if (old) old.replaceWith(SECTIONS[id]());
}

function renderAll() {
  document.documentElement.lang = S.config.language;
  document.title = `Wallboard – ${t("admin_title")}`;
  $("#title").textContent = t("admin_title");
  $("#photos-link").textContent = t("admin_photos");
  $("#logout").textContent = t("admin_logout");
  $("#save").textContent = t("admin_save");
  $("#app").replaceChildren(...Object.values(SECTIONS).map((render) => render()));
}

function fillDatalists() {
  const fill = (id, domain) =>
    $(`#${id}`).replaceChildren(...M.entitiesOfDomain(S.entities, domain).map((e) => h("option", { value: e.entity_id, text: e.name })));
  fill("dl-all", null);
  fill("dl-person", "person");
  fill("dl-weather", "weather");
  fill("dl-sensor", "sensor");
  fill("dl-light", "light");
}

async function loadEntities(retries = 0) {
  try {
    S.entities = await api("GET", "/api/admin/ha/entities");
  } catch (e) {
    S.entities = null;
    if (retries > 0 && e.status === 409) {
      setTimeout(() => loadEntities(retries - 1), 1500); // HA client is (re)connecting
      return;
    }
  }
  fillDatalists();
  rerender("ha");
}

function clearErrors() {
  document.querySelectorAll(".field-error").forEach((el) => el.remove());
  document.querySelectorAll(".has-error").forEach((el) => el.classList.remove("has-error"));
}

function showErrors(errors) {
  let first = null;
  const global = [];
  for (const err of errors) {
    const key = M.errorTarget(err.loc, (k) => !!document.querySelector(`[data-loc="${CSS.escape(k)}"]`));
    if (!key) {
      global.push(err.msg);
      continue;
    }
    const el = document.querySelector(`[data-loc="${CSS.escape(key)}"]`);
    el.classList.add("has-error");
    el.append(h("p", { class: "field-error", role: "alert", text: err.msg }));
    first ||= el;
  }
  first?.scrollIntoView({ behavior: "smooth", block: "center" });
  setStatus(global.length ? global.join(" · ") : t("admin_save_failed"), "error");
}

async function save() {
  clearErrors();
  const d = S.draft;
  const secrets = {};
  for (const k of SECRET_FIELDS) if (d[k].trim()) secrets[k] = d[k].trim();
  if (d.admin_password || d.admin_password2) {
    if (d.admin_password !== d.admin_password2) {
      showErrors([{ loc: ["admin_password2"], msg: t("admin_password_mismatch") }]);
      return;
    }
    secrets.admin_password = d.admin_password;
    secrets.current_password = d.current_password;
  }
  $("#save").disabled = true;
  setStatus(t("admin_saving"));
  const oldLang = S.view.config.language;
  let passwordChanged = false;
  try {
    if (Object.keys(secrets).length) {
      const r = await api("PUT", "/api/admin/secrets", secrets);
      S.view.secrets_set = r.secrets_set;
      passwordChanged = !!secrets.admin_password;
      for (const k of Object.keys(d)) d[k] = "";
    }
    const view = await api("PUT", "/api/admin/config", M.cleanConfig(S.config));
    S.view = view;
    S.config = structuredClone(view.config);
    S.dirty = false;
    if (view.config.language !== oldLang) await useLanguage(view.config.language);
    renderAll();
    setStatus(passwordChanged ? t("admin_password_changed") : t("admin_saved"), "ok");
    loadEntities(4); // HA may have restarted with a new URL/token
  } catch (e) {
    $("#save").disabled = false;
    if (e.errors) showErrors(e.errors);
    else setStatus(t("admin_error", { msg: e.message }), "error");
  }
}

async function useLanguage(lang) {
  setLanguage(lang);
  try {
    await loadLanguage("en"); // fallback strings
    if (lang !== "en") await loadLanguage(lang);
  } catch (e) {
    console.warn("settings: language load failed", e);
  }
}

async function init() {
  try {
    S.view = await api("GET", "/api/admin/config");
  } catch (e) {
    $("#app").replaceChildren(h("p", { class: "note error", text: e.message }));
    return;
  }
  S.config = structuredClone(S.view.config);
  await useLanguage(S.config.language);

  const params = new URLSearchParams(location.search);
  if (params.get("google_error")) S.googleNote = { ok: false, text: t("admin_google_error", { msg: params.get("google_error") }) };

  const zones = Intl.supportedValuesOf?.("timeZone") || [];
  $("#dl-timezones").replaceChildren(...zones.map((z) => h("option", { value: z })));

  renderAll();
  $("#save").addEventListener("click", save);
  window.addEventListener("beforeunload", (e) => {
    if (S.dirty) {
      e.preventDefault();
      e.returnValue = t("admin_leave_unsaved");
    }
  });
  window.addEventListener("focus", async () => {
    if (!S.connecting) return;
    S.connecting = false;
    try {
      await refreshGoogle();
      rerender("google");
    } catch {
      /* keep the current view */
    }
  });
  if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  loadEntities();
}

init();
