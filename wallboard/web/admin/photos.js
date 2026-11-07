// Photos page (admin only, phone first). Plain ES module, no build step.
//
// Two views on one page: the album list (#) and one album (#album=<id>). Albums: drag handle to
// reorder, inline rename, show/hide switch, "new album". Inside an album: upload with per-file
// progress (one XHR per file, at most two at a time), a thumbnail grid with drag-to-reorder
// (long press on touch), and a per-photo menu to delete (undo toast, the DELETE is sent after
// 5 s or when the page is left) or move the photo to another album.
// All DOM is built with h() / textContent; no innerHTML with data.

import { t, tn, setLanguage, loadLanguage, getLanguage } from "/static/shared/i18n.js";
import * as M from "/static/admin/photos-model.js";
import { moveItem } from "/static/admin/settings-model.js";
import { h, button, badge } from "/static/admin/dom.js";
import { makeReorderable } from "/static/admin/reorder.js";
import { api, CSRF } from "/static/admin/api.js";

const LONG_PRESS_MS = 300;
const UNDO_MS = 5000;
const TOAST_MS = 4000;
const UPLOAD_CONCURRENCY = 2;

const S = {
  albums: [], // GET /api/admin/photos: [{id, name, hidden, photos: [{id, thumb_url}]}]
  shuffle: false,
  pending: new Map(), // photo id -> {timer, toast, sending}: deleted locally, DELETE not sent yet
  upload: null, // {albumId, box, running}
};

const $ = (sel) => document.querySelector(sel);
const isPending = (photo) => S.pending.has(photo.id);
const visiblePhotos = (album) => album.photos.filter((p) => !isPending(p));
const albumById = (id) => S.albums.find((a) => a.id === id) || null;
const openAlbumId = () => new URLSearchParams(location.hash.slice(1)).get("album");

// ---- toasts -----------------------------------------------------------------------------------

function toast(text, { kind = "", action, onAction, ms = TOAST_MS } = {}) {
  const el = h(
    "div",
    { class: `toast ${kind}` },
    h("span", { text }),
    action ? h("button", { type: "button", class: "link", onclick: onAction, text: action }) : null,
  );
  $("#toasts").append(el);
  setTimeout(() => el.remove(), ms);
  return el;
}

const errorToast = (text) => toast(text, { kind: "error" });

// ---- data -------------------------------------------------------------------------------------

async function refresh() {
  try {
    S.albums = (await api("GET", "/api/admin/photos")).albums;
  } catch (e) {
    errorToast(t("admin_photos_load_failed", { msg: e.message }));
  }
  render();
}

// Optimistic change: apply() now, send(), and on failure undo() plus a reload from the server.
async function optimistic(apply, send, undo) {
  apply();
  render();
  try {
    await send();
  } catch (e) {
    if (e.status === 401) return;
    undo();
    render();
    errorToast(t("admin_photos_save_failed"));
    refresh();
  }
}

function moveAlbum(from, to) {
  if (from === to) return;
  const before = S.albums;
  optimistic(
    () => (S.albums = moveItem(before, from, to)),
    () => api("PUT", "/api/admin/albums/order", { ids: S.albums.map((a) => a.id) }),
    () => (S.albums = before),
  );
}

function movePhotoInAlbum(album, from, to) {
  if (from === to) return;
  const before = album.photos;
  optimistic(
    () => (album.photos = M.reorderVisible(before, from, to, isPending)),
    () => api("PUT", `/api/admin/albums/${encodeURIComponent(album.id)}/order`, { ids: album.photos.map((p) => p.id) }),
    () => (album.photos = before),
  );
}

function patchAlbum(album, patch) {
  const before = { name: album.name, hidden: album.hidden };
  optimistic(
    () => Object.assign(album, patch),
    () => api("PATCH", `/api/admin/albums/${encodeURIComponent(album.id)}`, patch),
    () => Object.assign(album, before),
  );
}

function setShuffle(on) {
  optimistic(
    () => (S.shuffle = on),
    () => api("PUT", "/api/admin/photos/shuffle", { shuffle: on }),
    () => (S.shuffle = !on),
  );
}

async function createAlbum(input) {
  const name = M.cleanAlbumName(input.value);
  if (name === null) {
    errorToast(t("admin_photos_name_invalid"));
    return;
  }
  try {
    const a = await api("POST", "/api/admin/albums", { name });
    S.albums.push({ id: a.id, name: a.name, hidden: a.hidden, photos: [] });
    location.hash = `album=${encodeURIComponent(a.id)}`; // straight in, ready to upload
  } catch (e) {
    if (e.status !== 401) errorToast(t("admin_photos_save_failed"));
  }
}

function moveToAlbum(photo, from, target) {
  const before = { from: from.photos, target: target.photos };
  const index = target.photos.length; // at the end of the target album
  optimistic(
    () => {
      from.photos = from.photos.filter((p) => p.id !== photo.id);
      target.photos = [...target.photos, photo];
    },
    async () => {
      await api("POST", `/api/admin/photos/${encodeURIComponent(photo.id)}/move`, { album_id: target.id, index });
      toast(t("admin_photos_moved", { name: target.name }));
    },
    () => {
      from.photos = before.from;
      target.photos = before.target;
    },
  );
}

// ---- delete with undo ---------------------------------------------------------------------------

function deletePhoto(photo) {
  const id = photo.id;
  const entry = { sending: false, timer: setTimeout(() => sendDelete(id), UNDO_MS) };
  entry.toast = toast(t("admin_photos_deleted"), { action: t("admin_photos_undo"), onAction: () => undoDelete(id), ms: UNDO_MS });
  S.pending.set(id, entry);
  render();
}

function undoDelete(id) {
  const entry = S.pending.get(id);
  if (!entry || entry.sending) return;
  clearTimeout(entry.timer);
  entry.toast.remove();
  S.pending.delete(id);
  render();
}

// keepalive: the request still goes out when this runs from pagehide.
async function sendDelete(id) {
  const entry = S.pending.get(id);
  if (!entry || entry.sending) return;
  entry.sending = true;
  clearTimeout(entry.timer);
  entry.toast.remove();
  let gone = true;
  try {
    await api("DELETE", `/api/admin/photos/${encodeURIComponent(id)}`, undefined, { keepalive: true });
  } catch (e) {
    gone = e.status === 404; // already deleted elsewhere
    if (!gone && e.status !== 401) errorToast(t("admin_photos_delete_failed"));
  }
  S.pending.delete(id);
  if (gone) for (const a of S.albums) a.photos = a.photos.filter((p) => p.id !== id);
  render();
}

function flushDeletes() {
  for (const id of [...S.pending.keys()]) sendDelete(id);
}

// ---- upload -------------------------------------------------------------------------------------

function uploadFile(albumId, item) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    const finish = (status, text) => {
      const r = M.uploadOutcome(status, text, t);
      if (r.ok) item.bar.value = 1;
      item.row.classList.add(r.ok ? "ok" : "error");
      item.status.textContent = r.ok ? t("admin_photos_uploaded") : r.error;
      resolve(r.ok);
    };
    xhr.open("POST", "/api/admin/photos/upload");
    xhr.setRequestHeader("X-Requested-With", CSRF["X-Requested-With"]);
    xhr.setRequestHeader("Accept-Language", getLanguage());
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      item.bar.value = e.loaded / e.total;
      item.status.textContent = e.loaded >= e.total ? t("admin_photos_processing") : `${Math.round((100 * e.loaded) / e.total)} %`;
    };
    xhr.onload = () => {
      if (xhr.status === 401) location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      finish(xhr.status, xhr.responseText);
    };
    xhr.onerror = xhr.onabort = xhr.ontimeout = () => finish(0, "");
    const form = new FormData();
    form.append("album_id", albumId);
    form.append("files[]", item.file, item.file.name);
    xhr.send(form);
  });
}

async function startUpload(album, fileList) {
  const files = [...fileList];
  if (!files.length || S.upload?.running) return;
  const list = h("ul", { class: "uploads" });
  const summary = h("p", { class: "upload-summary", role: "status" });
  const box = h("div", { class: "upload-box" }, list, summary);
  S.upload = { albumId: album.id, box, running: true };
  const items = files.map((file) => {
    const item = {
      file,
      bar: h("progress", { max: 1, value: 0 }),
      status: h("span", { class: "up-status", text: t("admin_photos_queued") }),
    };
    item.row = h("li", { class: "up-row" }, h("span", { class: "up-name", text: file.name }), item.status, item.bar);
    list.append(item.row);
    return item;
  });
  render();
  let ok = 0;
  await M.runPool(items, UPLOAD_CONCURRENCY, async (item) => {
    if (await uploadFile(album.id, item)) ok++;
  });
  S.upload.running = false;
  summary.replaceChildren(
    h("span", { text: t("admin_photos_upload_summary", { ok, total: files.length }) }),
    button(t("admin_photos_close"), () => {
      S.upload = null;
      render();
    }, "secondary small"),
  );
  await refresh();
}

// ---- album list view ----------------------------------------------------------------------------

function switchInput(checked, onchange, label) {
  return h("label", { class: "switch" }, h("input", { type: "checkbox", role: "switch", checked, onchange: (e) => onchange(e.target.checked) }), h("span", { text: label }));
}

function nameEditor(album, row) {
  const input = h("input", { type: "text", class: "rename", value: album.name, maxLength: M.MAX_ALBUM_NAME, "aria-label": t("admin_photos_album_name") });
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const name = M.cleanAlbumName(input.value);
    if (!save || name === album.name) return render();
    if (name === null) {
      errorToast(t("admin_photos_name_invalid"));
      return render();
    }
    patchAlbum(album, { name });
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      finish(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      finish(false);
    }
  });
  input.addEventListener("blur", () => finish(true));
  row.querySelector(".album-main").replaceWith(input);
  input.focus();
  input.select();
}

function albumRow(album) {
  const shown = visiblePhotos(album);
  const href = `#album=${encodeURIComponent(album.id)}`;
  const cover = shown[0]
    ? h("img", { class: "cover", src: shown[0].thumb_url, alt: "", loading: "lazy", decoding: "async", draggable: "false" })
    : h("span", { class: "cover empty" });
  const row = h(
    "div",
    { class: `card-row album-row${album.hidden ? " is-hidden" : ""}` },
    h("span", { class: "drag", "aria-hidden": "true" }, h("span", { class: "grip", text: "⋮⋮" })),
    h(
      "a",
      { class: "album-main", href, "aria-label": t("admin_photos_open", { name: album.name }) },
      cover,
      h(
        "span",
        { class: "album-text" },
        h("span", { class: "album-name", text: album.name }),
        h("small", { class: "muted" }, tn("admin_photos_count", shown.length), album.hidden ? " · " : "", album.hidden ? t("admin_photos_hidden") : ""),
      ),
    ),
    h("button", {
      type: "button",
      class: "icon-btn",
      "aria-label": t("admin_photos_rename", { name: album.name }),
      text: "✎",
      onclick: () => nameEditor(album, row),
    }),
    switchInput(!album.hidden, (on) => patchAlbum(album, { hidden: !on }), t("admin_photos_show")),
  );
  return row;
}

function renderList() {
  const list = h("div", { class: "stack albums" }, S.albums.map(albumRow));
  makeReorderable(list, moveAlbum, { longPressMs: LONG_PRESS_MS });
  const nameInput = h("input", {
    type: "text",
    maxLength: M.MAX_ALBUM_NAME,
    placeholder: t("admin_photos_album_name"),
    "aria-label": t("admin_photos_album_name"),
    onkeydown: (e) => e.key === "Enter" && (e.preventDefault(), createAlbum(nameInput)),
  });
  return [
    h(
      "section",
      { class: "panel" },
      h("h2", { text: t("admin_photos_display") }),
      switchInput(S.shuffle, setShuffle, t("admin_photos_shuffle")),
      h("p", { class: "hint", text: t("admin_photos_shuffle_hint") }),
      h("a", { class: "btn secondary", href: "/api/admin/photos.zip", download: "photos.zip", text: t("admin_photos_download") }),
    ),
    h(
      "section",
      { class: "panel" },
      h("h2", { text: t("admin_photos_albums") }),
      S.albums.length ? h("p", { class: "hint", text: t("admin_drag_hint") }) : h("p", { class: "muted", text: t("admin_photos_no_albums") }),
      list,
      h("h3", { text: t("admin_photos_new_album") }),
      h("div", { class: "row" }, nameInput, button(t("admin_photos_create"), () => createAlbum(nameInput), "")),
    ),
  ];
}

// ---- album view ---------------------------------------------------------------------------------

function openMenu(photo, album) {
  const dlg = $("#photo-menu");
  const act = (fn) => () => {
    dlg.close();
    fn();
  };
  const others = S.albums.filter((a) => a.id !== album.id);
  dlg.replaceChildren(
    h("img", { class: "sheet-thumb", src: photo.thumb_url, alt: "" }),
    button(t("admin_photos_delete"), act(() => deletePhoto(photo)), "danger"),
    ...others.map((a) => button(t("admin_photos_move_to", { name: a.name }), act(() => moveToAlbum(photo, album, a)))),
    button(t("admin_photos_cancel"), () => dlg.close(), "link"),
  );
  dlg.showModal();
}

function renderAlbum(album) {
  const shown = visiblePhotos(album);
  const busy = !!S.upload?.running;
  const picker = h("input", {
    type: "file",
    accept: "image/*,.heic",
    multiple: true,
    disabled: busy, // one upload batch at a time
    class: "visually-hidden",
    onchange: (e) => {
      startUpload(album, e.target.files);
      e.target.value = "";
    },
  });
  const grid = h(
    "div",
    { class: "thumbs" },
    shown.map((p) =>
      h(
        "div",
        { class: "thumb", dataset: { id: p.id } },
        h("img", { src: p.thumb_url, alt: "", loading: "lazy", decoding: "async", draggable: "false" }),
        h("button", { type: "button", class: "thumb-menu", "aria-label": t("admin_photos_menu"), text: "⋯", onclick: () => openMenu(p, album) }),
      ),
    ),
  );
  makeReorderable(grid, (from, to) => movePhotoInAlbum(album, from, to), { handle: ".thumb", ignore: ".thumb-menu", grid: true, longPressMs: LONG_PRESS_MS });
  return [
    h("p", { class: "back" }, h("a", { href: "#", text: `‹ ${t("admin_photos_back")}` })),
    h(
      "section",
      { class: "panel" },
      h(
        "div",
        { class: "album-head" },
        h("h2", { text: album.name }),
        album.hidden ? badge(t("admin_photos_hidden")) : null,
        h("span", { class: "muted", text: tn("admin_photos_count", shown.length) }),
      ),
      h("label", { class: `btn upload-btn${busy ? " disabled" : ""}` }, picker, t("admin_photos_upload")),
      S.upload?.albumId === album.id ? S.upload.box : null,
      shown.length ? h("p", { class: "hint", text: t("admin_drag_hint") }) : h("p", { class: "muted", text: t("admin_photos_empty") }),
      grid,
    ),
  ];
}

// ---- page ---------------------------------------------------------------------------------------

function render() {
  const id = openAlbumId();
  const album = id ? albumById(id) : null;
  const title = album ? album.name : t("admin_photos");
  document.title = `Wallboard – ${title}`;
  $("#title").textContent = t("admin_photos");
  $("#settings-link").textContent = t("admin_title");
  $("#logout").textContent = t("admin_logout");
  $("#app").replaceChildren(...(album ? renderAlbum(album) : renderList()));
}

async function init() {
  let view;
  try {
    [view, S.albums] = await Promise.all([api("GET", "/api/admin/config"), api("GET", "/api/admin/photos").then((r) => r.albums)]);
  } catch (e) {
    $("#app").replaceChildren(h("p", { class: "note error", text: e.message }));
    return;
  }
  S.shuffle = !!view.config.photos_shuffle;
  const lang = view.config.language;
  setLanguage(lang);
  document.documentElement.lang = lang;
  try {
    await loadLanguage("en"); // fallback strings
    if (lang !== "en") await loadLanguage(lang);
  } catch (e) {
    console.warn("photos: language load failed", e);
  }
  render();
  const menu = $("#photo-menu");
  menu.addEventListener("click", (e) => e.target === menu && menu.close()); // tap on the backdrop
  window.addEventListener("hashchange", () => {
    render();
    window.scrollTo(0, 0);
  });
  // Leaving the page (or hiding it on a phone) sends the pending deletes right away.
  window.addEventListener("pagehide", flushDeletes);
  document.addEventListener("visibilitychange", () => document.visibilityState === "hidden" && flushDeletes());
}

init();
