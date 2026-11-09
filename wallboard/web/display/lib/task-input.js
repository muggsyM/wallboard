// Tasks add dialog (task-dialog.js): the on-screen keyboard's layouts and typing state, and
// the quick due-date choices. Pure: no DOM.
//
// Keys are characters, or one of the specials: "shift", "back", "space", "num" (to the
// numbers layout), "abc" (back to letters). Shift is one-shot and is on at the start of
// the text (a capital first letter), like a phone keyboard.

export const MAX_TEXT = 200; // Config Todo.text max_length

export const LAYOUTS = {
  letters: [
    [..."ěščřžýáíéůú"],
    [..."qwertzuiop"],
    [..."asdfghjkl"],
    ["shift", ..."yxcvbnm", "back"],
    ["num", ",", "space", ".", "-", "?"],
  ],
  numbers: [
    [..."1234567890"],
    [..."-/:;()&@+%"],
    ["#", "=", "*", "!", "?", "'", '"', "€", "back"],
    ["abc", ",", "space", ".", "-", "?"],
  ],
};

export const initialInput = () => ({ text: "", shift: true, layout: "letters" });

// The state after pressing `key`.
export function applyKey(state, key, max = MAX_TEXT) {
  const s = { ...state };
  switch (key) {
    case "shift":
      s.shift = !s.shift;
      return s;
    case "num":
      s.layout = "numbers";
      return s;
    case "abc":
      s.layout = "letters";
      return s;
    case "back":
      s.text = [...s.text].slice(0, -1).join("");
      if (!s.text) s.shift = true;
      return s;
    case "space":
      if (s.text && !s.text.endsWith(" ") && s.text.length < max) s.text += " ";
      return s;
    default: {
      if (typeof key !== "string" || !key || [...s.text].length >= max) return s;
      s.text += s.shift ? key.toLocaleUpperCase("cs") : key;
      s.shift = false;
      return s;
    }
  }
}

// What a key cap shows (letters follow shift).
export function keyLabel(key, shift) {
  const special = { shift: "⇧", back: "⌫", num: "123", abc: "abc", space: "" };
  if (key in special) return special[key];
  return shift ? key.toLocaleUpperCase("cs") : key;
}

export const DUE_CHOICES = ["none", "today", "tomorrow", "week", "nextweek"];

const isoPlus = (iso, n) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

// The due date (ISO) for a quick choice, from today's local date (ISO): "week" = this
// Sunday (today on a Sunday), "nextweek" = the Sunday after; null for "none".
export function dueFor(choice, todayISO) {
  const toSunday = (7 - new Date(`${todayISO}T00:00:00Z`).getUTCDay()) % 7;
  switch (choice) {
    case "today":
      return todayISO;
    case "tomorrow":
      return isoPlus(todayISO, 1);
    case "week":
      return isoPlus(todayISO, toSunday);
    case "nextweek":
      return isoPlus(todayISO, toSunday + 7);
    default:
      return null;
  }
}

// The text that will be saved: one line, single spaces, trimmed.
export const cleanText = (text) => String(text ?? "").split(/\s+/).filter(Boolean).join(" ");
