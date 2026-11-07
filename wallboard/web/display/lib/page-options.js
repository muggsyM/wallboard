// ctx.options for a page: defaults from the module's settings[].default, overlaid with
// the PageRef's options from config. Pure: no DOM.

export function pageOptions(mod, pageRef) {
  const defaults = {};
  for (const s of mod?.settings || []) {
    if (s && s.key !== undefined && s.default !== undefined) defaults[s.key] = s.default;
  }
  return { ...defaults, ...(pageRef?.options || {}) };
}
