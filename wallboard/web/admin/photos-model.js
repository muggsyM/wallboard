// Pure helpers for the photos page: order merging, album names, upload results and a small
// worker pool. No DOM access, so node tests can import this module directly.

import { moveItem } from "./settings-model.js";

export const MAX_ALBUM_NAME = 100; // same limit as the server (photos_api.MAX_ALBUM_NAME)

// The server wants a permutation of *all* ids, but photos waiting for their delayed delete are
// not shown. Hidden ids keep their slots; the visible ids fill the other slots in their new order.
export function mergeOrder(fullIds, visibleIds) {
  const visible = new Set(visibleIds);
  const next = visibleIds[Symbol.iterator]();
  return fullIds.map((id) => (visible.has(id) ? next.next().value : id));
}

// Move visible item `from` to visible index `to` (indices among the items for which
// isHidden() is false). Returns the new full array; hidden items stay where they were.
export function reorderVisible(items, from, to, isHidden) {
  const visible = items.filter((x) => !isHidden(x));
  const moved = moveItem(visible, from, to);
  const byId = new Map(items.map((x) => [x.id, x]));
  return mergeOrder(
    items.map((x) => x.id),
    moved.map((x) => x.id),
  ).map((id) => byId.get(id));
}

// Trimmed name, or null when it is empty or longer than the server allows.
export function cleanAlbumName(raw) {
  const name = String(raw ?? "").trim();
  return name && name.length <= MAX_ALBUM_NAME ? name : null;
}

// One file per upload request: turn the XHR status/body into {ok, error}. The server's per-file
// error text is already in the UI language (Accept-Language); everything else is generic.
export function uploadOutcome(status, responseText, tr) {
  if (status === 413) return { ok: false, error: tr("admin_photos_too_large") };
  if (status === 0) return { ok: false, error: tr("admin_photos_network_error") };
  if (status === 200) {
    try {
      const r = JSON.parse(responseText).results[0];
      if (r.ok) return { ok: true, error: null };
      if (r.error) return { ok: false, error: r.error };
    } catch {
      /* fall through to the generic message */
    }
  }
  return { ok: false, error: tr("admin_photos_upload_failed") };
}

// Run worker(item, index) for every item, at most `limit` at a time, starting in order.
// A worker that throws does not stop the others.
export async function runPool(items, limit, worker) {
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const i = next++;
      try {
        await worker(items[i], i);
      } catch {
        /* the worker reports its own errors; keep the other uploads going */
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
}
