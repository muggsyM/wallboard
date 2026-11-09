// Slideshow sequencing for the Photos page. DOM-free: the page passes in how to pick, load
// and show a photo, so this is testable with fake timers.
//
//   const s = createSlideshow({ next, load, show, dwellMs, setTimeout, clearTimeout });
//   s.start()   show the first photo as soon as it has loaded (nothing is shown before)
//   s.skip()    move on now (manual "next", or photos appeared)
//   s.back()    go back to the photo shown before (manual "previous"); false = none
//   s.prune(isListed) forget deleted photos; true = the shown or upcoming one was among
//               them (the page then calls skip())
//   s.stop()    clear the timer; loads that finish later are ignored
//
// next() -> item | null picks the next photo (photo.nextPhoto()); load(item) -> Promise<bool>
// preloads it (true = ready to show without a flash); show(item | null) swaps it in (null =
// no photos). The photo after the shown one is preloaded at once, and swapped in when the
// dwell time is up (or, on a slow load, as soon as it has loaded after that). A photo that
// fails to load is skipped, but only one attempt per dwell period, so a list of broken
// files never turns into a request loop. The same photo again (a list of one) is not
// reloaded: it simply stays.
//
// History: the shown photos are remembered (up to MAX_HISTORY). back() steps back through
// them; going forward (skip() or the dwell timer) first replays the remembered ones and only
// then asks next() for a new photo, so back + forward returns to the same photo.

export const MAX_HISTORY = 50;

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
  // Shown photos as unique entries { item } (the same photo may repeat); history[cursor] is
  // on screen. `fresh` is the photo next() already picked but not yet shown.
  let history = [];
  let cursor = -1;
  let fresh = null;
  let pending = null; // { item, entry (history entry | null = fresh), state: "loading" | "ready" | "failed" | "same" }
  let due = false; // the dwell time is up: swap as soon as the pending photo is ready

  function schedule(fn) {
    clearT(timer);
    timer = setT(fn, dwellMs);
  }

  // The photo one step forward (dir 1) or back (dir -1) from the shown one, or null.
  function target(dir) {
    if (dir < 0) return cursor > 0 ? { item: history[cursor - 1].item, entry: history[cursor - 1] } : null;
    if (cursor < history.length - 1) return { item: history[cursor + 1].item, entry: history[cursor + 1] };
    if (!fresh) fresh = next();
    return fresh ? { item: fresh, entry: null } : null;
  }

  function prepare(dir = 1) {
    const t = target(dir);
    if (!t) {
      pending = null;
      return;
    }
    if (t.item.id === currentId) {
      pending = { ...t, state: "same" };
      return;
    }
    const p = { ...t, state: "loading" };
    pending = p;
    Promise.resolve(load(t.item)).then(
      (ok) => settle(p, ok),
      () => settle(p, false),
    );
  }

  function settle(p, ok) {
    if (stopped || pending !== p) return;
    p.state = ok ? "ready" : "failed";
    if (due) advance();
  }

  // Move the history cursor to the pending photo (a fresh one is appended).
  function commit(p) {
    if (p.entry) {
      const i = history.indexOf(p.entry);
      if (p.state === "failed") {
        // A remembered photo that no longer loads is forgotten.
        if (i >= 0) {
          history.splice(i, 1);
          if (i < cursor) cursor--;
        }
      } else if (i >= 0) {
        cursor = i;
      }
      return;
    }
    fresh = null; // consumed: the next forward step asks next() again
    if (p.state !== "ready") return;
    history.splice(cursor + 1);
    history.push({ item: p.item });
    if (history.length > MAX_HISTORY) history.shift();
    cursor = history.length - 1;
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
        history = [];
        cursor = -1;
        show(null);
      }
      return;
    }
    if (pending.state === "loading") return; // settle() calls back
    const p = pending;
    pending = null;
    due = false;
    commit(p);
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
    back() {
      if (stopped) return false;
      const t = target(-1);
      if (!t) return false;
      clearT(timer);
      timer = null;
      // The forward photo preloaded so far is reloaded later (the hidden layer is reused).
      pending = null;
      due = true;
      const p = { ...t, state: "loading" };
      pending = p;
      Promise.resolve(load(t.item)).then(
        (ok) => settle(p, ok),
        () => settle(p, false),
      );
      return true;
    },
    prune(isListed) {
      const keep = (e) => isListed(e.item.id);
      const before = history.slice(0, cursor + 1).filter(keep).length;
      history = history.filter(keep);
      cursor = Math.min(before, history.length) - 1;
      if (fresh && !isListed(fresh.id)) fresh = null;
      const gone = (id) => id !== null && !isListed(id);
      const hit = gone(currentId) || gone(pending?.item.id ?? null);
      if (hit || (pending?.entry && !history.includes(pending.entry))) pending = null;
      return hit;
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
