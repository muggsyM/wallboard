// Self-reload decisions (ruling R26). Pure: no DOM, no network.
//
// trackBuild(known, incoming) -> {known, reload}
//   The server sends its build id (a hash of web/) in every snapshot. The first one is
//   remembered; a later different one means the server was updated, so reload to pick up
//   the new display code. `known` stays the loaded build until the page actually reloads.
//
// nightlyReloadMinute(night) -> local minute of day for the nightly safety reload: the
//   night-mode start when night mode is on, else 03:30 (long-running renderer leaks).
// crossedMinute(prev, cur, target) -> true once, on the tick where local time reaches target.

import { parseHHMM } from "./night.js";

const DEFAULT_NIGHTLY_MINUTE = 3 * 60 + 30;
const DAY = 24 * 60;

export function trackBuild(known, incoming) {
  if (!incoming) return { known, reload: false };
  if (!known) return { known: incoming, reload: false };
  return { known, reload: incoming !== known };
}

export function nightlyReloadMinute(night) {
  const start = night?.enabled ? parseHHMM(night.start) : null;
  return start ?? DEFAULT_NIGHTLY_MINUTE;
}

export function crossedMinute(prev, cur, target) {
  if (prev === null || prev === undefined || prev === cur) return false;
  if (prev < cur) return prev < target && target <= cur;
  if (prev - cur < DAY / 2) return false; // small step back (NTP), not midnight
  return target > prev || target <= cur; // wrapped past midnight
}
