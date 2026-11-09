// Weather picture for the Today page: the owner's weather gallery (state.weather_pics,
// [{id, url, flags}]) is matched against the current HA condition. Pure: no DOM.
//
// HA's 15 conditions map onto 9 flags; a flag without pictures falls back along FALLBACK
// (clear-night -> sunny, fog -> cloudy -> partlycloudy -> sunny, ...); null when nothing
// matches at all (the page then shows a large weather icon).
//
// "frozen" (frost / hoarfrost) is not an HA condition: it is tried first when the current
// temperature is at or below FROST_MAX_C and nothing falls (FROST_FLAGS: clear, partly
// cloudy, cloudy, fog, wind); without frost pictures the condition's own chain follows.

export const CONDITION_FLAG = {
  sunny: "sunny",
  "clear-night": "clear-night",
  partlycloudy: "partlycloudy",
  cloudy: "cloudy",
  exceptional: "cloudy",
  fog: "fog",
  rainy: "rainy",
  pouring: "rainy",
  lightning: "lightning",
  "lightning-rainy": "lightning",
  hail: "lightning",
  snowy: "snowy",
  "snowy-rainy": "snowy",
  windy: "windy",
  "windy-variant": "windy",
};

export const FALLBACK = {
  "clear-night": "sunny",
  partlycloudy: "sunny",
  cloudy: "partlycloudy",
  fog: "cloudy",
  rainy: "cloudy",
  lightning: "rainy",
  snowy: "cloudy",
  windy: "cloudy",
};

export const FROST_MAX_C = 0;
const FROST_FLAGS = new Set(["sunny", "clear-night", "partlycloudy", "cloudy", "fog", "windy"]);

// Current temperature in °C from a value and its unit ("°C" / "°F"); null when unknown.
export function toCelsius(value, unit) {
  const n = value === null || value === undefined || value === "" ? NaN : Number(value);
  if (!Number.isFinite(n)) return null;
  return /F/i.test(String(unit ?? "")) ? ((n - 32) * 5) / 9 : n;
}

// The flags tried for a condition (and the temperature in °C, may be null), best first:
// ["lightning", "rainy", "cloudy", ...]; ["frozen", "sunny"] for a frosty clear day.
export function flagChain(condition, tempC = null) {
  const chain = [];
  if (tempC !== null && tempC <= FROST_MAX_C && FROST_FLAGS.has(CONDITION_FLAG[condition])) chain.push("frozen");
  for (let f = CONDITION_FLAG[condition]; f && !chain.includes(f); f = FALLBACK[f]) chain.push(f);
  return chain;
}

// The pictures for a condition: those of the first flag in the chain that has any.
export function candidates(pics, condition, tempC = null) {
  const list = Array.isArray(pics) ? pics : [];
  for (const flag of flagChain(condition, tempC)) {
    const hit = list.filter((p) => p && typeof p.url === "string" && Array.isArray(p.flags) && p.flags.includes(flag));
    if (hit.length) return hit;
  }
  return [];
}

// Which picture to show: keep `currentId` while it is still a candidate (no flicker on
// updates), else a random candidate (`rand` in [0, 1)); null when there is none.
export function pickPicture(pics, condition, currentId, tempC = null, rand = Math.random) {
  const list = candidates(pics, condition, tempC);
  if (!list.length) return null;
  return list.find((p) => p.id === currentId) ?? list[Math.floor(rand() * list.length) % list.length];
}
