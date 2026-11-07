import test from "node:test";
import assert from "node:assert/strict";
import { calendarStale, currentWeather } from "../display/header.js";

test("calendar is stale only when accounts exist and none is ok", () => {
  assert.equal(calendarStale({ google: {} }), false); // no accounts: nothing to be stale
  assert.equal(calendarStale({}), false);
  assert.equal(calendarStale(undefined), false);
  assert.equal(calendarStale({ google: { a: "ok", b: "expired" } }), false);
  assert.equal(calendarStale({ google: { a: "error" } }), true);
  assert.equal(calendarStale({ google: { a: "expired", b: "error" } }), true);
});

const forecast = { current: { condition: "rainy", temperature: 4 }, daily: [] };

test("header weather prefers the live HA weather entity", () => {
  const state = {
    config: { weather_entity: "weather.home" },
    ha: { "weather.home": { state: "sunny", attributes: { temperature: 12.4 } } },
    weather: forecast,
  };
  assert.deepEqual(currentWeather(state), { condition: "sunny", temperature: 12.4 });
});

test("header weather falls back to weather.current", () => {
  assert.deepEqual(currentWeather({ config: { weather_entity: "weather.home" }, ha: {}, weather: forecast }), forecast.current);
  assert.deepEqual(currentWeather({ config: { weather_entity: null }, ha: {}, weather: forecast }), forecast.current);
  const unavailable = { "weather.home": { state: "unavailable", attributes: {} } };
  assert.deepEqual(currentWeather({ config: { weather_entity: "weather.home" }, ha: unavailable, weather: forecast }), forecast.current);
  assert.equal(currentWeather({ config: null, ha: {}, weather: null }), null);
});
