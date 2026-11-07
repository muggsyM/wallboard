// Month: a Monday-first 6-week grid filling the page below the header and a colour legend
// (every configured member: colour dot + name). Each day shows its number (today: filled
// blue on a blue ring), the forecast icon when there is one, the Czech holiday name (pink),
// and up to `max_events_per_day` entries: "time title" with the member colour on the left
// edge, all-day events solid. Out-of-month days are faded to 28 %, past days to 50 %,
// weekends tinted. Look: mockups/month-v2.html.
//
// Multi-day all-day events are continuous bars (lib/layout.js assignLanes + barPiece): each
// bar keeps one lane (vertical slot) across its week row, lanes come first in a cell (empty
// lanes as invisible spacers) and the holiday label and other entries follow, so pieces line
// up. A piece reaches into the grid gap towards its neighbours (the cell clips with a
// margin of half the gap); bars break at Sunday -> Monday and the Monday piece repeats the
// title. Bars are always drawn; the per-day limit and the overflow trim apply to the other
// entries (pickVisible: past timed events go first, then from the end) plus "+N more".
//
// The grid is rebuilt only when the calendar/weather payload, members, language, zone or
// local minute changes (checked on every store update and every 20 s).
import { renderHeader, safeColor } from "../header.js";
import { t, getLanguage, fmtMonthYear, fmtWeekdayShort } from "../../shared/i18n.js";
import { WEATHER_ICONS } from "../../shared/ha-format.js";
import { localDate, noonOf, bucket, isPast, pickVisible, monthGrid, assignLanes, barPiece } from "../lib/layout.js";
import { readableText } from "../lib/color.js";
import { chipTime } from "./week.js";

const MINUTE_CHECK_MS = 20000;
const DEFAULT_MAX = 3;

// The setting comes from config (number, possibly a string); 1..6, default 3.
export function clampMaxEvents(v) {
  const n = Math.round(Number(v));
  if (v === null || v === undefined || v === "" || !Number.isFinite(n)) return DEFAULT_MAX;
  return Math.min(6, Math.max(1, n));
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

const isWeekend = (iso) => {
  const d = new Date(`${iso}T00:00:00Z`).getUTCDay();
  return d === 0 || d === 6;
};

function weatherIcon(weather, iso) {
  const d = (weather?.daily || []).find((x) => x?.date === iso);
  return d ? WEATHER_ICONS[d.condition] || "mdi-weather-partly-cloudy" : null;
}

function solid(node, ev) {
  const color = safeColor(ev.color);
  node.style.background = color;
  node.style.color = readableText(color);
}

function bar(ev, iso, column) {
  const p = barPiece(ev, iso, column);
  const b = el("div", "wb-me all bar", p.title ? ev.title ?? "" : " ");
  for (const k of ["roundL", "roundR", "extL", "extR"]) if (p[k]) b.classList.add(k);
  solid(b, ev);
  return b;
}

function entry(ev, iso, tz, now, today) {
  const e = el("div", "wb-me");
  if (ev.all_day) {
    e.classList.add("all");
    solid(e, ev);
  } else {
    e.style.borderLeftColor = safeColor(ev.color);
    // Earlier days are faded as a whole; only today's finished events fade on their own.
    if (iso === today && isPast(ev, now, tz)) e.classList.add("past");
  }
  const time = chipTime(ev, iso, tz);
  if (time) e.append(el("b", "", time), " ");
  e.append(ev.title ?? "");
  return e;
}

const overflows = (box) => box.scrollHeight > box.clientHeight + 1;

// One day cell. `lanes` = the week row's Map event -> lane.
function dayCell(d) {
  const { iso, column, today, monthKey, events, lanes, holiday, icon, cap, tz, now } = d;
  const c = el("div", "wb-mc");
  if (iso.slice(0, 7) !== monthKey) c.classList.add("out");
  else if (iso < today) c.classList.add("past");
  if (isWeekend(iso)) c.classList.add("we");
  if (iso === today) c.classList.add("today");

  const head = el("div", "wb-mc-n");
  head.append(el("span", "", String(Number(iso.slice(8)))));
  if (icon) head.append(el("i", `mdi ${icon}`));

  const barsHere = events.filter((ev) => lanes.has(ev));
  const others = events.filter((ev) => !lanes.has(ev));
  const laneRows = barsHere.length ? Math.max(...barsHere.map((ev) => lanes.get(ev))) + 1 : 0;
  const top = [head];
  for (let lane = 0; lane < laneRows; lane++) {
    const ev = barsHere.find((x) => lanes.get(x) === lane);
    top.push(ev ? bar(ev, iso, column) : el("div", "wb-me all gap", " "));
  }
  if (holiday) top.push(el("div", "wb-mc-hol", holiday));

  const past = (ev) => isPast(ev, now, tz);
  const fill = (capacity) => {
    const { shown, hidden } = pickVisible(others, capacity, past);
    const items = shown.map((ev) => entry(ev, iso, tz, now, today));
    if (hidden) items.push(el("div", "wb-mc-more", t("more_n", { n: hidden })));
    c.replaceChildren(...top, ...items);
  };
  let capacity = Math.max(0, cap - laneRows);
  fill(capacity);
  // Called once the grid is laid out: drop entries until the 2-line clamps fit the cell.
  const fit = () => {
    while (capacity > 0 && overflows(c)) fill(--capacity);
  };
  return { c, fit };
}

export default {
  type: "month",
  titleKey: "month",
  settings: [{ key: "max_events_per_day", type: "number", labelKey: "max_events_per_day", default: DEFAULT_MAX }],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-page-header"></div><div class="wb-legend"></div><div class="wb-month"></div>`;
    const header = root.querySelector(".wb-page-header");
    const legend = root.querySelector(".wb-legend");
    const grid = root.querySelector(".wb-month");
    const cap = clampMaxEvents(ctx.options?.max_events_per_day);
    let last = null; // key of the last grid build
    let alive = true;

    const same = (a, b) =>
      a && b && a.tz === b.tz && a.minute === b.minute && a.calendar === b.calendar && a.weather === b.weather &&
      a.lang === b.lang && a.members === b.members;

    function renderLegend(members) {
      legend.replaceChildren(
        ...(members || []).map((m) => {
          const item = el("span", "wb-legend-i");
          const dot = el("i");
          dot.style.background = safeColor(m.color);
          item.append(dot, m.name ?? "");
          return item;
        }),
      );
    }

    function render(state, force = false) {
      const now = Date.now();
      const tz = state.config?.timezone || "Europe/Prague";
      const today = localDate(now, tz);
      renderHeader(header, state, { titleKey: "month", subtitle: fmtMonthYear(noonOf(today, tz)) });
      const key = {
        tz, minute: Math.floor(now / 60000), calendar: state.calendar, weather: state.weather, lang: getLanguage(),
        members: state.config?.members,
      };
      if (!force && same(key, last)) return;
      last = key;
      renderLegend(key.members);

      const days = monthGrid(today);
      const byDay = bucket(state.calendar?.events || [], days[0], days.length, tz);
      const holidays = state.calendar?.holidays || {};
      const heads = days.slice(0, 7).map((iso) => el("div", "wb-mh", fmtWeekdayShort(noonOf(iso, tz))));
      const cells = [];
      for (let w = 0; w < 6; w++) {
        const week = days.slice(w * 7, w * 7 + 7);
        const lanes = assignLanes(week, [...new Set(week.flatMap((iso) => byDay.get(iso)))]);
        week.forEach((iso, column) => {
          cells.push(dayCell({
            iso, column, today, monthKey: today.slice(0, 7), events: byDay.get(iso), lanes,
            holiday: typeof holidays[iso] === "string" ? holidays[iso] : "", icon: weatherIcon(state.weather, iso),
            cap, tz, now,
          }));
        });
      }
      grid.replaceChildren(...heads, ...cells.map((x) => x.c));
      // After layout: measuring needs the cells in place.
      for (const x of cells) x.fit();
    }

    const refit = () => alive && render(ctx.store.get(), true);
    const timer = setInterval(() => render(ctx.store.get()), MINUTE_CHECK_MS);
    // Cell height depends on the page size and the web fonts; re-measure when either changes.
    let ro = null;
    let lastHeight = 0;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(([entry]) => {
        const h = Math.round(entry.contentRect.height);
        if (h !== lastHeight) {
          const first = lastHeight === 0;
          lastHeight = h;
          if (!first) refit();
        }
      });
      ro.observe(grid);
    }
    if (document.fonts && document.fonts.status !== "loaded") document.fonts.ready.then(refit);
    document.fonts?.addEventListener?.("loadingdone", refit);

    render(ctx.store.get());
    return {
      update: (state) => render(state),
      destroy() {
        alive = false;
        clearInterval(timer);
        ro?.disconnect();
        document.fonts?.removeEventListener?.("loadingdone", refit);
        root.innerHTML = "";
      },
    };
  },
};
