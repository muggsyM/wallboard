// Photos: a full-screen slideshow of the gallery (the whole #app area, no header).
//
// - Order: ctx.photo.nextPhoto(), so the user's order or shuffle from Settings applies.
// - Each photo stays `seconds_per_photo` (4..120, default 10); the page itself stays as long
//   as its rotation `seconds` say (default PageRef for "photos": 60 s). The photo timer
//   runs only while the page is mounted.
// - Two layers cross-fade on opacity only (no transform or blur: Pi 4 GPU budget). The next
//   photo is preloaded into the hidden layer (lib/slideshow.js), so a swap never flashes.
//   Portrait photos cover the screen; landscape ones are contained, centred, over their own
//   pre-blurred bg_url (cover), which the server renders, so no CSS blur.
// - Bottom left the album name (`show_caption`, default on), bottom right a small clock.
// - No photos: a calm hint on the chalkboard to add some in Settings.
// Taps and swipes are the shell's (tap: next page, swipe: page).
import { t, fmtTime } from "../../shared/i18n.js";
import { clampSetting } from "../lib/page-options.js";
import { createSlideshow } from "../lib/slideshow.js";

const DEFAULT_SECONDS = 10;
const FADE_MS = 1200; // matches .wb-ph-layer transition in display.css

export const clampPhotoSeconds = (v) => clampSetting(v, 4, 120, DEFAULT_SECONDS);

// JSON.stringify quotes and escapes the URL for CSS url("...").
const cssUrl = (u) => `url(${JSON.stringify(String(u))})`;

export default {
  type: "photos",
  titleKey: "photos",
  settings: [
    { key: "seconds_per_photo", type: "number", labelKey: "seconds_per_photo", default: DEFAULT_SECONDS, min: 4, max: 120 },
    { key: "show_caption", type: "boolean", labelKey: "show_caption", default: true },
  ],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-ph-layer"><img decoding="async" alt=""></div>
      <div class="wb-ph-layer"><img decoding="async" alt=""></div>
      <div class="wb-ph-empty" hidden><i class="mdi mdi-image-multiple-outline"></i><span></span></div>
      <div class="wb-ph-caption" hidden></div>
      <div class="wb-ph-clock" data-clock="time">${fmtTime(new Date())}</div>`;
    const layers = [...root.querySelectorAll(".wb-ph-layer")].map((el) => ({ el, img: el.querySelector("img") }));
    const empty = root.querySelector(".wb-ph-empty");
    const caption = root.querySelector(".wb-ph-caption");
    const showCaption = ctx.options?.show_caption !== false;
    let front = 0; // index of the visible layer
    let fadeUntil = 0; // the back layer is still fading out until then
    let alive = true;
    let loadToken = 0; // a newer load (after a skip) supersedes an older one
    const waits = new Set();

    const wait = (ms) =>
      new Promise((resolve) => {
        const id = setTimeout(() => {
          waits.delete(id);
          resolve();
        }, ms);
        waits.add(id);
      });

    // Preload into the hidden layer (after its fade-out), resolving true once decoded.
    async function load(item) {
      const token = ++loadToken;
      const current = () => alive && token === loadToken;
      if (fadeUntil > Date.now()) await wait(fadeUntil - Date.now());
      if (!current()) return false;
      const { el, img } = layers[1 - front];
      el.classList.toggle("landscape", Boolean(item.landscape));
      el.style.backgroundImage = item.landscape && item.bg_url ? cssUrl(item.bg_url) : "";
      const bg = item.landscape && item.bg_url ? new Image() : null;
      const bgDone = bg ? new Promise((r) => { bg.onload = bg.onerror = r; bg.src = item.bg_url; }) : null;
      const ok = await new Promise((resolve) => {
        img.onload = () => resolve(true);
        img.onerror = () => resolve(false);
        img.src = item.url;
      });
      if (!current()) return false;
      img.onload = img.onerror = null;
      if (ok && img.decode) await img.decode().catch(() => {});
      if (bgDone) await bgDone;
      if (!ok && current()) console.warn("photos: photo failed to load", item.url);
      return current() && ok;
    }

    function show(item) {
      empty.hidden = Boolean(item);
      if (!item) {
        for (const l of layers) l.el.classList.remove("on");
        caption.hidden = true;
        return;
      }
      front = 1 - front;
      layers[front].el.classList.add("on");
      layers[1 - front].el.classList.remove("on");
      fadeUntil = Date.now() + FADE_MS;
      caption.textContent = item.caption ?? "";
      caption.hidden = !showCaption || !item.caption;
    }

    const slides = createSlideshow({
      next: () => ctx.photo.nextPhoto(),
      load,
      show,
      dwellMs: clampPhotoSeconds(ctx.options?.seconds_per_photo) * 1000,
    });

    function update(state) {
      const hint = t("photos_empty");
      if (empty.lastElementChild.textContent !== hint) empty.lastElementChild.textContent = hint;
      const ids = new Set((state.photos || []).map((p) => p.id));
      const gone = (id) => id !== null && !ids.has(id);
      if (gone(slides.pendingId()) || gone(slides.currentId())) {
        slides.drop();
        slides.skip();
      } else if (slides.isEmpty() && ids.size) {
        slides.skip();
      }
    }

    update(ctx.store.get());
    slides.start();
    return {
      update,
      destroy() {
        alive = false;
        slides.stop();
        for (const id of waits) clearTimeout(id);
        for (const { img } of layers) {
          img.onload = img.onerror = null;
          img.removeAttribute("src"); // release the decoded bitmaps (weeks-long kiosk)
        }
        root.innerHTML = "";
      },
    };
  },
};
