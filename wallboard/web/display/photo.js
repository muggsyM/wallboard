// Photos for the display: the shared nextPhoto() sequence and the page background.
//
//   photo.nextPhoto()        -> PhotoItem | null (user order, or shuffle without repeats)
//   photo.backgroundEl()     -> the background element (two layers + dark veil)
//   photo.sync(state)        -> take the photo list and photos_shuffle from store state
//   photo.advanceBackground()-> cross-fade the background to the next photo (shell calls
//                               it on page change, so it never fights Today's framed photo)
//
// The cross-fade animates opacity only (cheap on the Pi 4 GPU). With no photos the
// layers stay empty and the calm gradient behind them shows.
import { PhotoCycle } from "./lib/photo-cycle.js";

export function createPhoto() {
  const cycle = new PhotoCycle();
  const root = document.createElement("div");
  root.className = "wb-bg";
  const layers = [0, 1].map(() => {
    const d = document.createElement("div");
    d.className = "wb-bg-layer";
    root.appendChild(d);
    return d;
  });
  const veil = document.createElement("div");
  veil.className = "wb-bg-veil";
  root.appendChild(veil);

  let front = 0;
  let currentId = null;
  let hasPhotos = false;
  let loadToken = 0;

  function show(item) {
    if (item && item.id === currentId) return; // must not cancel a load in flight
    const token = ++loadToken;
    if (!item) {
      currentId = null;
      layers.forEach((l) => l.classList.remove("on"));
      root.classList.remove("has-photo");
      return;
    }
    // Decode before fading so the swap never shows a half-loaded image.
    const img = new Image();
    img.onload = () => {
      if (token !== loadToken) return;
      const back = 1 - front;
      layers[back].style.backgroundImage = `url("${item.bg_url}")`;
      layers[back].classList.add("on");
      layers[front].classList.remove("on");
      front = back;
      currentId = item.id;
      root.classList.add("has-photo");
    };
    img.onerror = () => console.warn("background photo failed to load", item.bg_url);
    img.src = item.bg_url;
  }

  return {
    nextPhoto: () => cycle.next(),
    backgroundEl: () => root,
    sync(state) {
      cycle.setItems(state.photos, Boolean(state.config?.photos_shuffle));
      const ids = new Set((state.photos || []).map((p) => p.id));
      const had = hasPhotos;
      hasPhotos = ids.size > 0;
      if (!hasPhotos) show(null);
      else if (!had || (currentId && !ids.has(currentId))) show(cycle.next());
    },
    advanceBackground() {
      if (hasPhotos) show(cycle.next());
    },
  };
}
