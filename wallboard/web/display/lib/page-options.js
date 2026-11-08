// ctx.options for a page: defaults from the module's settings[].default, overlaid with
// the PageRef's options from config; clampSetting() for number settings. Pure: no DOM.

export function pageOptions(mod, pageRef) {
  const defaults = {};
  for (const s of mod?.settings || []) {
    if (s && s.key !== undefined && s.default !== undefined) defaults[s.key] = s.default;
  }
  return { ...defaults, ...(pageRef?.options || {}) };
}

// A number setting from config (a number, possibly a string): rounded and clamped to lo..hi;
// `fallback` when it is missing or not a number.
export function clampSetting(v, lo, hi, fallback) {
  const n = Math.round(Number(v));
  if (v === null || v === undefined || v === "" || !Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, n));
}
