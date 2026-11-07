// One shared, minimal toast: a short message near the bottom of the display that fades out.
//
//   showToast(text, ms = 3000)   a new message replaces the current one and restarts the timer
//
// Created on first use inside #app (above the pages, below the night overlay). Fades with
// opacity only; text is set with textContent.
const DEFAULT_MS = 3000;
let el = null;
let timer = null;

export function showToast(text, ms = DEFAULT_MS) {
  if (!el || !el.isConnected) {
    el = document.createElement("div");
    el.className = "wb-toast";
    el.setAttribute("role", "status");
    (document.getElementById("app") || document.body).appendChild(el);
  }
  el.textContent = text;
  // Force a style flush so a toast created just now still fades in.
  void el.offsetWidth;
  el.classList.add("show");
  clearTimeout(timer);
  timer = setTimeout(() => el.classList.remove("show"), ms);
}
