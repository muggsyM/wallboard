import test from "node:test";
import assert from "node:assert/strict";
import { CONDITION_FLAG, flagChain, candidates, pickPicture, toCelsius } from "../display/lib/weather-pic.js";
import { WEATHER_ICONS } from "../shared/ha-format.js";

const pic = (id, ...flags) => ({ id, url: `/media/${id}.jpg`, flags });

test("every HA condition has a flag", () => {
  for (const c of Object.keys(WEATHER_ICONS)) assert.ok(CONDITION_FLAG[c], c);
});

test("flag chain falls back towards sunny and never loops", () => {
  assert.deepEqual(flagChain("hail"), ["lightning", "rainy", "cloudy", "partlycloudy", "sunny"]);
  assert.deepEqual(flagChain("clear-night"), ["clear-night", "sunny"]);
  assert.deepEqual(flagChain("sunny"), ["sunny"]);
  assert.deepEqual(flagChain("unknown"), []);
});

test("candidates: the first flag in the chain that has pictures", () => {
  const pics = [pic("a", "sunny"), pic("b", "rainy", "lightning"), pic("c", "rainy"), pic("d")];
  assert.deepEqual(candidates(pics, "pouring").map((p) => p.id), ["b", "c"]);
  assert.deepEqual(candidates(pics, "lightning-rainy").map((p) => p.id), ["b"]);
  assert.deepEqual(candidates(pics, "fog").map((p) => p.id), ["a"]); // fog -> cloudy -> partly -> sunny
  assert.deepEqual(candidates(pics, ""), []);
  assert.deepEqual(candidates(null, "sunny"), []);
});

test("pickPicture keeps the current one while it matches, else picks at random", () => {
  const pics = [pic("a", "rainy"), pic("b", "rainy"), pic("s", "sunny")];
  assert.equal(pickPicture(pics, "rainy", "b", null, () => 0).id, "b");
  assert.equal(pickPicture(pics, "rainy", null, null, () => 0.99).id, "b");
  assert.equal(pickPicture(pics, "sunny", "b", null, () => 0).id, "s"); // condition changed
  assert.equal(pickPicture([pic("x")], "sunny", null), null);
});

test("frost: tried first at 0 °C or below when nothing falls", () => {
  assert.deepEqual(flagChain("sunny", -3), ["frozen", "sunny"]);
  assert.deepEqual(flagChain("fog", 0), ["frozen", "fog", "cloudy", "partlycloudy", "sunny"]);
  assert.deepEqual(flagChain("sunny", 0.5), ["sunny"]);
  assert.deepEqual(flagChain("sunny", null), ["sunny"]);
  assert.deepEqual(flagChain("snowy", -5), ["snowy", "cloudy", "partlycloudy", "sunny"]);
  assert.deepEqual(flagChain("rainy", -1), ["rainy", "cloudy", "partlycloudy", "sunny"]);
});

test("frost pictures win when frosty, the weather's own picture otherwise", () => {
  const pics = [pic("f", "frozen"), pic("s", "sunny")];
  assert.deepEqual(candidates(pics, "sunny", -2).map((p) => p.id), ["f"]);
  assert.deepEqual(candidates(pics, "sunny", 4).map((p) => p.id), ["s"]);
  assert.deepEqual(candidates([pic("s", "sunny")], "sunny", -2).map((p) => p.id), ["s"]); // no frost picture
  assert.equal(pickPicture(pics, "sunny", "s", -2).id, "f"); // it froze: switch
});

test("toCelsius: °C as is, °F converted, unknown null", () => {
  assert.equal(toCelsius(-2, "°C"), -2);
  assert.equal(toCelsius("32", "°F"), 0);
  assert.equal(toCelsius(5, undefined), 5);
  assert.equal(toCelsius(null, "°C"), null);
  assert.equal(toCelsius("x", "°C"), null);
});
