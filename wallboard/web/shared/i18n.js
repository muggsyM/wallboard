// Shared i18n: string lookup plus Intl-based date/time/number helpers.
// No DOM access and no fetch at import time, so it runs in browsers and in node tests.
// Strings live in ./i18n/<lang>.json; load them with loadLanguage() (browser) or
// inject them with setStrings() (tests).

const LOCALES = { cs: "cs-CZ", en: "en-GB" };
const FALLBACK = "en";

const strings = {};
let lang = "cs";
let timezone = "Europe/Prague";
const formatters = new Map(); // "lang|tz|kind" -> Intl.DateTimeFormat

export function setStrings(l, obj) {
  strings[l] = obj;
}

export async function loadLanguage(l, fetchImpl = globalThis.fetch) {
  const res = await fetchImpl(new URL(`./i18n/${l}.json`, import.meta.url));
  if (!res.ok) throw new Error(`cannot load language ${l}: ${res.status}`);
  setStrings(l, await res.json());
}

export function setLanguage(l) {
  lang = l;
}

export function getLanguage() {
  return lang;
}

// An unknown zone would make every Intl formatter throw (blank display), so fall back.
export function setTimezone(tz) {
  timezone = tz || "Europe/Prague";
  try {
    new Intl.DateTimeFormat("en", { timeZone: timezone });
  } catch {
    console.warn(`i18n: unknown timezone "${timezone}", using Europe/Prague`);
    timezone = "Europe/Prague";
  }
}

export function locale() {
  return LOCALES[lang] || LOCALES[FALLBACK];
}

// Lookup chain: current language -> en -> the key itself. {name} placeholders are interpolated.
export function t(key, vars) {
  let s = strings[lang]?.[key] ?? strings[FALLBACK]?.[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return s;
}

const KINDS = {
  time: { hour: "2-digit", minute: "2-digit" },
  longDate: { weekday: "long", day: "numeric", month: "long" },
  weekdayShort: { weekday: "short" },
  monthYear: { month: "long", year: "numeric" },
  dayMonth: { day: "numeric", month: "numeric" },
};

function formatter(kind) {
  const key = `${lang}|${timezone}|${kind}`;
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(locale(), { ...KINDS[kind], hourCycle: "h23", timeZone: timezone });
    formatters.set(key, f);
  }
  return f;
}

// Intl output for cs uses U+00A0 / U+202F in places; normalise to a plain space.
const plain = (s) => s.replace(/[  ]/g, " ");

export const fmtTime = (date) => plain(formatter("time").format(date)); // "07:42"
export const fmtLongDate = (date) => plain(formatter("longDate").format(date)); // "sobota 7. listopadu" / "Saturday 7 November"
export const fmtWeekdayShort = (date) => plain(formatter("weekdayShort").format(date)); // "so" / "Sat"
export const fmtMonthYear = (date) => plain(formatter("monthYear").format(date)); // "listopad 2026" / "November 2026"

// Day and month without padding: cs "7. 11.", en "7/11" (built from parts; en-GB would pad to "07/11").
export function fmtShortDayMonth(date) {
  const parts = formatter("dayMonth").formatToParts(date);
  const day = Number(parts.find((p) => p.type === "day").value);
  const month = Number(parts.find((p) => p.type === "month").value);
  return lang === "cs" ? `${day}. ${month}.` : `${day}/${month}`;
}

// Locale-aware number; returns null when raw is not numeric.
export function fmtNumber(raw, decimals) {
  const n = Number(raw);
  if (raw === "" || raw === null || raw === undefined || typeof raw === "boolean" || !Number.isFinite(n)) return null;
  const max = decimals ?? (Math.abs(n) >= 100 ? 0 : Math.round(n) === n ? 0 : 1);
  return new Intl.NumberFormat(locale(), { minimumFractionDigits: decimals ?? 0, maximumFractionDigits: max }).format(n);
}
