// Drag-to-reorder for a vertical list or a wrapping grid, shared by the admin pages. Nothing
// touches the DOM at import time (dropIndex / gridDropIndex are pure and tested in node).
//
//   makeReorderable(list, onMove, {handle = ".drag", longPressMs = 350, grid = false, ignore = null})
//
// A row is a direct child of `list`; it is dragged by an element matching `handle` inside it
// (`ignore`: a selector for controls inside the handle that must not start a drag).
// `grid: true` places the item by pointer x and y over the other items' rects (reading order).
// Mouse/pen drag at once; touch needs a long press first so normal scrolling keeps working
// (moving more than 10 px before that counts as a scroll). The row moves live; onMove(from, to)
// runs on release with the row's old and new index.

// Final index of a dragged row: `mids` are the vertical midpoints of the *other* rows in
// order; the row goes before the first one whose midpoint is below the pointer.
export function dropIndex(y, mids) {
  let i = 0;
  while (i < mids.length && y > mids[i]) i++;
  return i;
}

// Final index of a dragged grid item: `rects` ({left, top, width, height}) are the *other*
// items in reading order. The item goes before the first one that is on a lower row than the
// pointer, or on the pointer's row with its horizontal midpoint right of the pointer.
export function gridDropIndex(x, y, rects) {
  let i = 0;
  while (i < rects.length) {
    const r = rects[i];
    if (y < r.top || (y < r.top + r.height && x < r.left + r.width / 2)) break;
    i++;
  }
  return i;
}

export function makeReorderable(list, onMove, { handle = ".drag", longPressMs = 350, grid = false, ignore = null } = {}) {
  let drag = null; // {row, from, pointerId, timer, startX, startY, active}

  const rows = () => [...list.children];
  const rowOf = (el) => {
    while (el && el.parentElement !== list) el = el.parentElement;
    return el;
  };
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
    const grip = e.target.closest(handle);
    if (!grip || !list.contains(grip) || drag || (e.pointerType === "mouse" && e.button !== 0)) return;
    if (ignore && e.target.closest(ignore)) return;
    const row = rowOf(grip);
    drag = { row, from: rows().indexOf(row), pointerId: e.pointerId, startY: e.clientY, startX: e.clientX, active: false, timer: null };
    list.setPointerCapture(e.pointerId); // the list, not the row: rows move in the DOM
    if (e.pointerType === "touch") drag.timer = setTimeout(start, longPressMs);
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
    const rects = others.map((r) => r.getBoundingClientRect());
    const idx = grid
      ? gridDropIndex(e.clientX, e.clientY, rects)
      : dropIndex(e.clientY, rects.map((b) => b.top + b.height / 2));
    if (idx >= others.length) list.append(drag.row);
    else if (others[idx] !== drag.row.nextElementSibling) list.insertBefore(drag.row, others[idx]);
  });
  list.addEventListener("pointerup", (e) => drag && e.pointerId === drag.pointerId && end(true));
  list.addEventListener("pointercancel", (e) => drag && e.pointerId === drag.pointerId && end(true));
  // While dragging, stop the page from scrolling under the finger.
  list.addEventListener("touchmove", (e) => drag?.active && e.cancelable && e.preventDefault(), { passive: false });
  list.addEventListener("contextmenu", (e) => e.target.closest(handle) && e.preventDefault());
}
