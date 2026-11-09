// Tasks page: the "new task" dialog with its own Czech on-screen keyboard (mockup "prani",
// Úkoly – nový úkol). The kiosk has no usable system keyboard, so the page draws one.
//
//   openTaskDialog(root, ctx, member, {onClose}) -> {close()}
//
// Layout: a veil over the board, a sheet at the top (member, text field with a caret, due
// date chips with the resulting date, Zrušit / Uložit) and the keyboard docked at the
// bottom (lib/task-input.js: layouts, typing state, due dates). Keys act on pointerdown
// (fast typing), ⌫ repeats while held. The dialog holds the board (ctx.hold: no rotation,
// no night mode), carries data-modal so the shell neither pages nor swipes under it, and
// closes on × / Zrušit, after IDLE_CLOSE_MS without a touch, or with the page. Uložit sends
// action "todo.new" + "add" {member_id, text, due} (server: todos.py); on failure the
// dialog stays open with the text and a toast.
import { safeColor } from "./header.js";
import { t, fmtShortDayMonth, fmtWeekdayShort } from "../shared/i18n.js";
import { localDate, noonOf } from "./lib/layout.js";
import { readableText } from "./lib/color.js";
import { createIdleTimer } from "./lib/day-detail.js";
import { LAYOUTS, initialInput, applyKey, keyLabel, DUE_CHOICES, dueFor, cleanText } from "./lib/task-input.js";
import { showToast } from "./toast.js";

export const IDLE_CLOSE_MS = 60000;
const REPEAT_DELAY_MS = 450;
const REPEAT_MS = 70;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

export function openTaskDialog(root, ctx, member, { onClose = () => {} } = {}) {
  const tz = ctx.store.get().config?.timezone || "Europe/Prague";
  let input = initialInput();
  let due = "none";
  let saving = false;
  let closed = false;
  const release = ctx.hold?.() ?? null;
  const idle = createIdleTimer({ ms: IDLE_CLOSE_MS, onExpire: () => close() });
  const color = safeColor(member.color);

  const overlay = el("div", "wb-tadd");
  overlay.dataset.tap = "";
  overlay.dataset.modal = "";
  overlay.style.setProperty("--wb-member", color);

  const sheet = el("div", "wb-tadd-sheet");
  const top = el("div", "wb-tadd-top");
  const av = el("span", "wb-tadd-av", member.initial ?? "");
  av.style.background = color;
  av.style.color = readableText(color);
  const x = el("span", "wb-tadd-x", "×");
  x.dataset.act = "close";
  top.append(av, el("span", "wb-tadd-title", t("task_new_for", { name: member.name ?? "" })), x);
  const field = el("div", "wb-tadd-field");
  const typed = el("span", "wb-tadd-text");
  const caret = el("span", "wb-tadd-caret");
  const placeholder = el("span", "wb-tadd-ph", t("task_placeholder"));
  field.append(placeholder, typed, caret);
  const chips = el("div", "wb-tadd-chips");
  const dueNote = el("div", "wb-tadd-note");
  const dueBox = el("div", "wb-tadd-due");
  dueBox.append(el("span", "wb-tadd-lbl", t("task_due")), chips, dueNote);
  const cancel = el("span", "wb-tadd-btn", t("task_cancel"));
  cancel.dataset.act = "close";
  const save = el("span", "wb-tadd-btn save", t("task_save"));
  save.dataset.act = "save";
  save.style.background = color;
  save.style.color = readableText(color);
  const buttons = el("div", "wb-tadd-btns");
  buttons.append(cancel, save);
  sheet.append(top, field, dueBox, buttons);
  const kb = el("div", "wb-kb");
  overlay.append(sheet, kb);

  function drawText() {
    typed.textContent = input.text;
    placeholder.hidden = Boolean(input.text);
    save.classList.toggle("off", !cleanText(input.text) || saving);
  }

  function drawChips() {
    const today = localDate(Date.now(), tz);
    chips.replaceChildren(
      ...DUE_CHOICES.map((c) => {
        const chip = el("span", `wb-tadd-chip${c === due ? " on" : ""}`, t(`task_due_${c}`));
        chip.dataset.due = c;
        return chip;
      }),
    );
    const iso = dueFor(due, today);
    dueNote.textContent = iso ? `${fmtWeekdayShort(noonOf(iso, tz))} ${fmtShortDayMonth(noonOf(iso, tz))}` : "";
  }

  function drawKeys() {
    kb.replaceChildren(
      ...LAYOUTS[input.layout].map((row) => {
        const r = el("div", "wb-kb-row");
        for (const k of row) {
          const key = el("span", `wb-key${k.length > 1 ? ` wb-key-${k}` : ""}`, keyLabel(k, input.shift));
          if (k === "shift" && input.shift) key.classList.add("on");
          if (k === "space") key.textContent = t("task_space");
          key.dataset.key = k;
          r.append(key);
        }
        return r;
      }),
    );
  }

  function press(k) {
    const before = input;
    input = applyKey(input, k);
    if (input.layout !== before.layout || input.shift !== before.shift) drawKeys();
    drawText();
  }

  async function doSave() {
    const text = cleanText(input.text);
    if (!text || saving) return;
    if (!ctx.canAct()) return;
    saving = true;
    drawText();
    const data = { member_id: member.id, text, due: dueFor(due, localDate(Date.now(), tz)) };
    try {
      const res = await ctx.store.sendAction("todo.new", "add", data);
      if (!res?.ok) throw res;
      close();
    } catch {
      showToast(t("action_failed"));
    } finally {
      saving = false;
      if (!closed) drawText();
    }
  }

  // Keys on pointerdown; ⌫ repeats while held. Buttons and chips on a tap (pointerup).
  let repeat = null;
  const stopRepeat = () => {
    clearTimeout(repeat?.delay);
    clearInterval(repeat?.every);
    repeat = null;
  };
  let downOn = null;
  overlay.addEventListener("pointerdown", (e) => {
    if (!e.isPrimary) return;
    idle.poke();
    const key = e.target.closest?.(".wb-key");
    downOn = key ? null : e.target.closest?.("[data-act], [data-due]");
    if (!key) return;
    e.preventDefault();
    key.classList.add("down");
    press(key.dataset.key);
    if (key.dataset.key === "back") {
      repeat = { delay: setTimeout(() => (repeat.every = setInterval(() => press("back"), REPEAT_MS)), REPEAT_DELAY_MS) };
    }
  });
  const up = (e) => {
    stopRepeat();
    for (const k of kb.querySelectorAll(".wb-key.down")) k.classList.remove("down");
    const target = downOn;
    downOn = null;
    if (e.type !== "pointerup" || !target || !target.contains(e.target)) return;
    if (target.dataset.due) {
      due = target.dataset.due;
      drawChips();
    } else if (target.dataset.act === "close") close();
    else if (target.dataset.act === "save") doSave();
  };
  overlay.addEventListener("pointerup", up);
  overlay.addEventListener("pointercancel", up);
  overlay.addEventListener("pointerleave", up);

  function close() {
    if (closed) return;
    closed = true;
    stopRepeat();
    idle.stop();
    release?.();
    overlay.remove();
    onClose();
  }

  drawText();
  drawChips();
  drawKeys();
  root.append(overlay);
  idle.start();
  return { close };
}
