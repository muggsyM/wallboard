// Small DOM helpers shared by the admin pages (settings, photos). No innerHTML: text goes in
// through textContent / text nodes only. Nothing touches the DOM at import time.
//
//   h(tag, props, ...children)       element builder (class, text, dataset, on<event>, props, attributes)
//   field(label, control, {loc, hint, inline})   labelled field; data-loc = 422 error path
//   textInput / numberInput / checkbox / select  inputs bound to obj[key]
//   button(label, onclick, cls, extra), badge(text, kind)
//   setEditHandler(fn)               called after every edit through a bound input

const PROPS = new Set(["value", "checked", "disabled", "hidden", "selected", "type", "min", "max", "maxLength", "placeholder"]);

let onEdit = () => {};
export function setEditHandler(fn) {
  onEdit = fn;
}

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (PROPS.has(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// A labelled field. `loc` is the config path the server uses in 422 errors ("members.0.color").
export function field(label, control, { loc, hint, inline } = {}) {
  return h(
    "label",
    { class: inline ? "field inline" : "field", dataset: loc ? { loc } : undefined },
    inline ? [control, h("span", { text: label })] : [h("span", { text: label }), control],
    hint ? h("small", { class: "hint", text: hint }) : null,
  );
}

// Inputs bound to obj[key]. `nullable` stores null for an empty value.
export function textInput(obj, key, opts = {}) {
  return h("input", {
    type: opts.type || "text",
    value: obj[key] ?? "",
    placeholder: opts.placeholder,
    list: opts.list,
    maxLength: opts.maxLength,
    autocomplete: "off",
    autocapitalize: opts.list ? "none" : undefined,
    spellcheck: opts.list ? "false" : undefined,
    oninput: (e) => {
      obj[key] = opts.nullable && e.target.value === "" ? null : e.target.value;
      onEdit();
    },
    onchange: opts.onchange,
  });
}

export function numberInput(obj, key, { min, placeholder, nullable } = {}) {
  return h("input", {
    type: "number",
    inputmode: "numeric",
    min,
    placeholder,
    value: obj[key] ?? "",
    oninput: (e) => {
      const v = e.target.value;
      if (v === "") {
        if (nullable) obj[key] = null;
      } else obj[key] = Number(v);
      onEdit();
    },
  });
}

export function checkbox(obj, key, { disabled, onchange } = {}) {
  return h("input", {
    type: "checkbox",
    checked: !!obj[key],
    disabled,
    onchange: (e) => {
      obj[key] = e.target.checked;
      onEdit();
      onchange?.(e);
    },
  });
}

export function select(obj, key, options, onchange) {
  return h(
    "select",
    {
      onchange: (e) => {
        obj[key] = e.target.value;
        onEdit();
        onchange?.(e);
      },
    },
    options.map(([value, label]) => h("option", { value, selected: obj[key] === value, text: label })),
  );
}

export const badge = (text, kind = "") => h("span", { class: `badge ${kind}`, text });

export function button(label, onclick, cls = "secondary", extra = {}) {
  return h("button", { type: "button", class: cls, onclick, ...extra }, label);
}
