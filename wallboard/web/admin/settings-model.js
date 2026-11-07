// Pure helpers for the settings page: config edits, drag-reorder maths and error lookup.
// No DOM access, so node tests can import this module directly.

export const MAX_QUICK_TILES = 4;
const LIGHT_DOMAINS = new Set(["light", "switch", "fan", "input_boolean"]);

export const domainOf = (entityId) => String(entityId || "").split(".", 1)[0];

// Move arr[from] so it ends up at index `to` (index in the resulting array). Returns a new array.
export function moveItem(arr, from, to) {
  const out = arr.slice();
  const [item] = out.splice(from, 1);
  out.splice(Math.max(0, Math.min(to, out.length)), 0, item);
  return out;
}

// Final index of a dragged row: `mids` are the vertical midpoints of the *other* rows in
// order; the row goes before the first one whose midpoint is below the pointer.
export function dropIndex(y, mids) {
  let i = 0;
  while (i < mids.length && y > mids[i]) i++;
  return i;
}

export const locKey = (loc) => (loc || []).join(".");

// The most specific key that has a field on the page: ["members", 2, "color"] tries
// "members.2.color", "members.2", "members". Returns null when nothing matches.
export function errorTarget(loc, hasKey) {
  for (let n = (loc || []).length; n > 0; n--) {
    const key = locKey(loc.slice(0, n));
    if (hasKey(key)) return key;
  }
  return null;
}

// Short random id; crypto.getRandomValues also works on plain-HTTP LAN pages (randomUUID does not).
export function newId(prefix, cryptoImpl = globalThis.crypto) {
  const bytes = cryptoImpl.getRandomValues(new Uint8Array(4));
  return prefix + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export const guessGroup = (entityId) => (LIGHT_DOMAINS.has(domainOf(entityId)) ? "lights" : "devices");
export const isControllable = (entityId, domains) => (domains || []).includes(domainOf(entityId));

export function newTile(entityId, domains) {
  return { entity_id: entityId, label: null, icon: null, group: guessGroup(entityId), controllable: isControllable(entityId, domains) };
}

// Turn a quick tile on/off. Returns false (and changes nothing) past the limit.
export function setQuick(config, entityId, on) {
  const rest = config.quick_tiles.filter((e) => e !== entityId);
  if (on && rest.length >= MAX_QUICK_TILES) return false;
  config.quick_tiles = on ? [...rest, entityId] : rest;
  return true;
}

export function removeTile(config, index) {
  const [tile] = config.tiles.splice(index, 1);
  if (tile && !config.tiles.some((t) => t.entity_id === tile.entity_id)) {
    config.quick_tiles = config.quick_tiles.filter((e) => e !== tile.entity_id);
  }
}

// Change a tile's entity; quick tiles follow, and controllable is cleared for domains that
// cannot be controlled.
export function renameTile(config, index, entityId, domains) {
  const tile = config.tiles[index];
  const old = tile.entity_id;
  tile.entity_id = entityId;
  if (!isControllable(entityId, domains)) tile.controllable = false;
  if (old !== entityId && !config.tiles.some((t) => t.entity_id === old)) {
    config.quick_tiles = config.quick_tiles.map((e) => (e === old ? entityId : e));
  }
}

export const memberAccounts = (config, memberId) => config.google_accounts.filter((a) => a.member_id === memberId);

// Show a calendar for memberId, or hide it (memberId null). One CalendarRef per account+calendar.
export function setCalendar(config, account, calendar, memberId) {
  const same = (c) => c.account_id === account.id && c.calendar_id === calendar.id;
  const rest = config.calendars.filter((c) => !same(c));
  if (memberId) {
    const i = config.calendars.findIndex(same);
    const ref = { account_id: account.id, calendar_id: calendar.id, name: calendar.name, member_id: memberId, enabled: true };
    if (i >= 0) {
      config.calendars[i] = ref;
      return;
    }
    rest.push(ref);
  }
  config.calendars = rest;
}

export const calendarRef = (config, accountId, calendarId) =>
  config.calendars.find((c) => c.account_id === accountId && c.calendar_id === calendarId) || null;

// Optional strings: an empty input means "not set" (null), as the server models expect.
export function cleanConfig(config) {
  const c = structuredClone(config);
  const blank = (v) => (typeof v === "string" && v.trim() === "" ? null : v);
  c.ha_url = blank(c.ha_url);
  c.weather_entity = blank(c.weather_entity);
  for (const m of c.members) m.person_entity = blank(m.person_entity);
  for (const r of c.rooms) {
    r.humidity_entity = blank(r.humidity_entity);
    r.light_entities = r.light_entities.filter((e) => e.trim());
  }
  for (const t of c.tiles) {
    t.label = blank(t.label);
    t.icon = blank(t.icon);
  }
  c.status_entities = c.status_entities.filter((e) => e.trim());
  return c;
}

export const entitiesOfDomain = (entities, domain) => (entities || []).filter((e) => !domain || e.domain === domain);

export const isLocalHost = (hostname) => ["localhost", "127.0.0.1", "[::1]", "::1"].includes(hostname);
