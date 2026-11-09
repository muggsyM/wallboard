// Tasks: one list per family member (the first 4 members, 2 x 2), mockup "prani" screen 3.
// Bigger or longer-running jobs ("pay Gymnathon for the 2nd term", "ski socks"), entered on
// the Settings page (config.todos). Each card: the member's initial in their colour, the
// name, the number of open items, a rule in their colour, then the items: open ones first
// (dated by due date, then undated in entered order), then the ones ticked today (struck
// through). Items ticked on an earlier day are hidden (the server prunes them later). A due
// date shows as "dnes" / "zítra" / weekday within a week / "31. 1."; due today, tomorrow or
// overdue in the warning colour.
//
// Tapping an item ticks or unticks it: action "todo.<id>" + command "toggle" over the display
// socket (server: todos.py); a tap, not a swipe (lib/gesture.js, like tiles). The tick shows
// at once; a failure reverts it with a toast.
// Holding an item (LONG_PRESS_MS without moving) shows Smazat / Zrušit under it; Smazat sends
// "todo.<id>" + "delete". The "+" in a card's header opens the add dialog with the on-screen
// keyboard (task-dialog.js) for that member.
// View-only viewers can neither tick, delete nor add (no "+" for them). When a card
// overflows, its open items are trimmed from the end (done ones first) with "+N more".
import { renderHeader, safeColor } from "../header.js";
import { t, getLanguage, fmtWeekdayShort, fmtShortDayMonth } from "../../shared/i18n.js";
import { localDate, addDays, noonOf } from "../lib/layout.js";
import { showToast } from "../toast.js";
import { classifyGesture, TAP_MAX_PX } from "../lib/gesture.js";
import { openTaskDialog } from "../task-dialog.js";

const MINUTE_CHECK_MS = 20000;
const MAX_LISTS = 4;
export const LONG_PRESS_MS = 600;
const MENU_CLOSE_MS = 10000;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// Pure: the lists for the page. `pending` = Map todo id -> done-or-not the display already
// shows for a tap still in flight.
export function taskLists(config, todayISO, pending = new Map()) {
  const todos = Array.isArray(config?.todos) ? config.todos : [];
  return (config?.members || []).slice(0, MAX_LISTS).map((member) => {
    const mine = todos
      .filter((x) => x && x.member_id === member.id)
      .map((x, i) => ({ ...x, order: i, done: pending.has(x.id) ? (pending.get(x.id) ? todayISO : null) : x.done ?? null }));
    const open = mine.filter((x) => !x.done);
    open.sort((a, b) => (a.due && b.due ? a.due.localeCompare(b.due) : a.due ? -1 : b.due ? 1 : 0) || a.order - b.order);
    const done = mine.filter((x) => x.done && x.done >= todayISO);
    return { member, open, done, count: open.length };
  });
}

// Pure: {text, soon} for a due date (ISO) relative to today; null without a date.
export function dueLabel(due, todayISO, tz) {
  if (!due) return null;
  const soon = due <= addDays(todayISO, 1);
  if (due === todayISO) return { text: t("due_today"), soon };
  if (due === addDays(todayISO, 1)) return { text: t("due_tomorrow"), soon };
  if (due > todayISO && due <= addDays(todayISO, 6)) return { text: fmtWeekdayShort(noonOf(due, tz)), soon };
  return { text: fmtShortDayMonth(noonOf(due, tz)), soon };
}

const overflows = (box) => box.scrollHeight > box.clientHeight + 1;

export default {
  type: "tasks",
  titleKey: "tasks",
  settings: [],
  mount(root, ctx) {
    root.innerHTML = `<div class="wb-page-header"></div><div class="wb-tasks"></div>`;
    const header = root.querySelector(".wb-page-header");
    const grid = root.querySelector(".wb-tasks");
    const pending = new Map(); // todo id -> ticked? while the tap is in flight
    let last = null;
    let alive = true;
    let menuId = null; // the item whose Smazat / Zrušit row is open
    let menuTimer = null;
    let dialog = null; // the open add dialog

    function setMenu(id) {
      clearTimeout(menuTimer);
      menuId = id;
      if (id) {
        ctx.holdRotation?.(MENU_CLOSE_MS);
        menuTimer = setTimeout(() => alive && setMenu(null), MENU_CLOSE_MS);
      }
      render(ctx.store.get(), true);
    }

    function remove(id) {
      setMenu(null);
      if (!ctx.canAct()) return;
      ctx.store.sendAction(`todo.${id}`, "delete")
        .then((res) => {
          if (!res?.ok) throw res;
        })
        .catch(() => showToast(t("action_failed")));
    }

    function openAdd(memberId) {
      const member = (ctx.store.get().config?.members || []).find((m) => m.id === memberId);
      if (!member || !ctx.canAct() || dialog) return;
      setMenu(null);
      dialog = openTaskDialog(root, ctx, member, { onClose: () => (dialog = null) });
    }

    function itemRow(x, today, tz) {
      const r = el("div", "wb-task");
      r.dataset.tap = ""; // the shell must not page on a tap here
      if (x.done) r.classList.add("done");
      r.append(el("span", "wb-task-box"));
      const txt = el("span", "wb-task-t", x.text ?? "");
      const due = x.done ? null : dueLabel(x.due, today, tz);
      if (due) txt.append(el("span", `wb-task-due${due.soon ? " soon" : ""}`, ` · ${due.text}`));
      r.append(txt);
      r.dataset.id = x.id;
      if (x.id !== menuId) return r;
      r.classList.add("held");
      const menu = el("div", "wb-task-menu");
      menu.dataset.tap = "";
      const del = el("span", "wb-task-act del", t("task_delete"));
      del.dataset.act = "delete";
      del.dataset.id = x.id;
      const keep = el("span", "wb-task-act", t("task_cancel"));
      keep.dataset.act = "keep";
      menu.append(del, keep);
      return [r, menu];
    }

    function tap(x) {
      if (!ctx.canAct()) return; // view-only viewer: like a tile, a tap does nothing
      if (pending.has(x.id)) return;
      pending.set(x.id, !x.done);
      ctx.holdRotation?.(10000);
      render(ctx.store.get(), true);
      ctx.store.sendAction(`todo.${x.id}`, "toggle")
        .then((res) => {
          if (!res?.ok) throw res;
        })
        .catch(() => showToast(t("action_failed")))
        .finally(() => {
          pending.delete(x.id);
          if (alive) render(ctx.store.get(), true);
        });
    }

    function card(list, today, tz) {
      const c = el("div", "wb-tcard");
      const color = safeColor(list.member.color);
      const h = el("div", "wb-tcard-h");
      const av = el("span", "wb-tcard-av", list.member.initial ?? "");
      av.style.background = color;
      h.append(av, el("span", "wb-tcard-nm", list.member.name ?? ""), el("span", "wb-tcard-n", String(list.count)));
      if (ctx.canAct()) {
        const plus = el("span", "wb-tcard-add", "+");
        plus.dataset.tap = "";
        plus.dataset.add = list.member.id;
        plus.style.borderColor = color;
        plus.style.color = color;
        h.append(plus);
      }
      const rule = el("div", "wb-tcard-rule");
      rule.style.background = color;
      const items = el("div", "wb-tcard-items");
      c.append(h, rule, items);
      const fill = (cap) => {
        const all = [...list.open, ...list.done];
        const shown = all.slice(0, cap);
        const rows = shown.flatMap((x) => itemRow(x, today, tz));
        if (!all.length) rows.push(el("div", "wb-tcard-empty", t("no_tasks")));
        if (all.length > cap) rows.push(el("div", "wb-tcard-more", t("more_n", { n: all.length - cap })));
        items.replaceChildren(...rows);
      };
      const total = list.open.length + list.done.length;
      fill(total);
      return { c, fit: () => { for (let cap = total - 1; cap >= 0 && overflows(c); cap--) fill(cap); } };
    }

    // Pointer events delegated to the grid (like tiles): a tap (not a swipe) on an item ticks
    // it, holding it opens its menu, a tap on "+" opens the add dialog.
    let press = null;
    const cancelPress = () => {
      clearTimeout(press?.timer);
      press = null;
    };
    grid.addEventListener("pointerdown", (e) => {
      cancelPress();
      if (!e.isPrimary) return;
      const target = e.target.closest?.(".wb-task[data-id], .wb-task-act, .wb-tcard-add");
      if (!target) return;
      press = { target, x: e.clientX, y: e.clientY, t: Date.now(), long: false };
      if (target.matches(".wb-task[data-id]") && ctx.canAct()) {
        const p = press;
        p.timer = setTimeout(() => {
          p.long = true;
          setMenu(target.dataset.id);
        }, LONG_PRESS_MS);
      }
    });
    grid.addEventListener("pointermove", (e) => {
      if (press && Math.hypot(e.clientX - press.x, e.clientY - press.y) > TAP_MAX_PX) clearTimeout(press.timer);
    });
    grid.addEventListener("pointerup", (e) => {
      const p = press;
      cancelPress();
      if (!p || !e.isPrimary || p.long) return;
      if (classifyGesture({ dx: e.clientX - p.x, dy: e.clientY - p.y, dt: Date.now() - p.t }) !== "tap") return;
      const target = p.target;
      if (target.dataset.add) return openAdd(target.dataset.add);
      if (target.dataset.act === "delete") return remove(target.dataset.id);
      if (target.dataset.act === "keep") return setMenu(null);
      if (menuId) return setMenu(null); // a tap elsewhere just closes the menu
      const today = localDate(Date.now(), ctx.store.get().config?.timezone || "Europe/Prague");
      const item = taskLists(ctx.store.get().config, today, pending).flatMap((l) => [...l.open, ...l.done]).find((x) => x.id === target.dataset.id);
      if (item) tap(item);
    });
    grid.addEventListener("pointercancel", cancelPress);

    const same = (a, b) =>
      a && b && a.tz === b.tz && a.day === b.day && a.config === b.config && a.lang === b.lang && a.act === b.act;

    function render(state, force = false) {
      const tz = state.config?.timezone || "Europe/Prague";
      const today = localDate(Date.now(), tz);
      renderHeader(header, state, { titleKey: "tasks" });
      // act: the "+" buttons follow the viewer message that arrives after the snapshot.
      const key = { tz, day: today, config: state.config, lang: getLanguage(), act: ctx.canAct() };
      if (!force && same(key, last)) return;
      last = key;
      const cards = taskLists(state.config, today, pending).map((l) => card(l, today, tz));
      grid.replaceChildren(...cards.map((x) => x.c));
      for (const x of cards) x.fit();
    }

    const refit = () => alive && render(ctx.store.get(), true);
    const timer = setInterval(() => render(ctx.store.get()), MINUTE_CHECK_MS);
    if (document.fonts && document.fonts.status !== "loaded") document.fonts.ready.then(refit);
    document.fonts?.addEventListener?.("loadingdone", refit);
    render(ctx.store.get());
    return {
      update: (state) => render(state),
      destroy() {
        alive = false;
        cancelPress();
        clearTimeout(menuTimer);
        dialog?.close();
        clearInterval(timer);
        document.fonts?.removeEventListener?.("loadingdone", refit);
        root.innerHTML = "";
      },
    };
  },
};
