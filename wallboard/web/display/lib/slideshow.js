// Slideshow sequencing for the Photos page. DOM-free: the page passes in how to pick, load
// and show a photo, so this is testable with fake timers.
//
//   const s = createSlideshow({ next, load, show, dwellMs, setTimeout, clearTimeout });
//   s.start()   show the first photo as soon as it has loaded (nothing is shown before)
//   s.skip()    move on now (photos appeared); s.drop() first forgets the preloaded photo
//               (the shown or preloaded one was deleted)
//   s.stop()    clear the timer; loads that finish later are ignored
//
// next() -> item | null picks the next photo (photo.nextPhoto()); load(item) -> Promise<bool>
// preloads it (true = ready to show without a flash); show(item | null) swaps it in (null =
// no photos). The photo after the shown one is preloaded at once, and swapped in when the
// dwell time is up (or, on a slow load, as soon as it has loaded after that). A photo that
// fails to load is skipped, but only one attempt per dwell period, so a list of broken
// files never turns into a request loop. The same photo again (a list of one) is not
// reloaded: it simply stays.

export function createSlideshow({
  next,
  load,
  show,
  dwellMs,
  setTimeout: setT = globalThis.setTimeout,
  clearTimeout: clearT = globalThis.clearTimeout,
}) {
  let stopped = false;
  let timer = null;
  let currentId = null;
  let empty = false; // show(null) is on screen
  let pending = null; // { item, state: "loading" | "ready" | "failed" | "same" }
  let due = false; // the dwell time is up: swap as soon as the pending photo is ready

  function schedule(fn) {
    clearT(timer);
    timer = setT(fn, dwellMs);
  }

  function prepare() {
    const item = next();
    if (!item) {
      pending = null;
      return;
    }
    if (item.id === currentId) {
      pending = { item, state: "same" };
      return;
    }
    const p = { item, state: "loading" };
    pending = p;
    Promise.resolve(load(item)).then(
      (ok) => settle(p, ok),
      () => settle(p, false),
    );
  }

  function settle(p, ok) {
    if (stopped || pending !== p) return;
    p.state = ok ? "ready" : "failed";
    if (due) advance();
  }

  function advance() {
    if (stopped) return;
    due = true;
    if (!pending) prepare();
    if (!pending) {
      // No photos (any more).
      due = false;
      if (!empty) {
        empty = true;
        currentId = null;
        show(null);
      }
      return;
    }
    if (pending.state === "loading") return; // settle() calls back
    const p = pending;
    pending = null;
    due = false;
    if (p.state === "failed") {
      schedule(advance); // try the next one after a dwell period
      return;
    }
    if (p.state === "ready") {
      empty = false;
      currentId = p.item.id;
      show(p.item);
    }
    schedule(advance);
    prepare();
  }

  return {
    start() {
      advance();
    },
    skip() {
      if (stopped) return;
      clearT(timer);
      timer = null;
      // A pending photo that is still listed stays the next one.
      advance();
    },
    // Forget the pending photo (e.g. it was deleted) so the next advance picks again.
    drop() {
      pending = null;
    },
    currentId: () => currentId,
    pendingId: () => pending?.item.id ?? null,
    isEmpty: () => empty,
    stop() {
      stopped = true;
      clearT(timer);
      timer = null;
      pending = null;
    },
  };
}
