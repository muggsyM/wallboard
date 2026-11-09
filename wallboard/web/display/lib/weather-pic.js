// Weather picture for the Today page: the owner's weather gallery (state.weather_pics,
// [{id, url, flags}]) is matched against the current HA condition. Pure: no DOM.
//
// HA's 15 conditions map onto 9 flags; a flag without pictures falls back along FALLBACK
// (clear-night -> sunny, fog -> cloudy -> partlycloudy -> sunny, ...); null when nothing
// matches at all (the page then shows a large weather icon).

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

// The flags tried for a condition, best first: ["lightning", "rainy", "cloudy", ...].
export function flagChain(condition) {
  const chain = [];
  for (let f = CONDITION_FLAG[condition]; f && !chain.includes(f); f = FALLBACK[f]) chain.push(f);
  return chain;
}

// The pictures for a condition: those of the first flag in the chain that has any.
export function candidates(pics, condition) {
  const list = Array.isArray(pics) ? pics : [];
  for (const flag of flagChain(condition)) {
    const hit = list.filter((p) => p && typeof p.url === "string" && Array.isArray(p.flags) && p.flags.includes(flag));
    if (hit.length) return hit;
  }
  return [];
}

// Which picture to show: keep `currentId` while it is still a candidate (no flicker on
// updates), else a random candidate (`rand` in [0, 1)); null when there is none.
export function pickPicture(pics, condition, currentId, rand = Math.random) {
  const list = candidates(pics, condition);
  if (!list.length) return null;
  return list.find((p) => p.id === currentId) ?? list[Math.floor(rand() * list.length) % list.length];
}
