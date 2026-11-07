// Pure colour helpers. No DOM.

// "#rgb" | "#rgba" | "#rrggbb" | "#rrggbbaa" -> [r, g, b] 0-255, or null (alpha ignored).
function rgbOf(hex) {
  const m = /^#([0-9a-f]{3,8})$/i.exec(String(hex ?? ""));
  if (!m) return null;
  let h = m[1];
  if (h.length === 3 || h.length === 4) h = [...h.slice(0, 3)].map((c) => c + c).join("");
  else if (h.length === 6 || h.length === 8) h = h.slice(0, 6);
  else return null;
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

// WCAG relative luminance, 0 (black) .. 1 (white).
function luminance([r, g, b]) {
  const lin = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

// Text colour for a solid fill: whichever of black or white has the higher contrast ratio.
// Unparseable colours get white (the display background is dark).
export function readableText(bg) {
  const rgb = rgbOf(bg);
  if (!rgb) return "#fff";
  const l = luminance(rgb);
  return (l + 0.05) / 0.05 >= 1.05 / (l + 0.05) ? "#000" : "#fff";
}
