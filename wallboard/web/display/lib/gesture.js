// Classify one pointer gesture from its total movement (CSS px) and duration (ms).
// Pure: no DOM. Returns "next" (swipe left), "prev" (swipe right), "tap" or null.

export const SWIPE_MIN_PX = 60;
export const GESTURE_MAX_MS = 600;
export const TAP_MAX_PX = 12;
// A swipe must be "mostly horizontal": |dx| at least this many times |dy|.
const HORIZONTAL_RATIO = 1.5;

export function classifyGesture({ dx, dy, dt }) {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (dt >= GESTURE_MAX_MS) return null;
  if (ax >= SWIPE_MIN_PX && ax >= HORIZONTAL_RATIO * ay) return dx < 0 ? "next" : "prev";
  if (ax <= TAP_MAX_PX && ay <= TAP_MAX_PX) return "tap";
  return null;
}
