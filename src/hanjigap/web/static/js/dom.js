// 화면 요소 만들기. 데이터는 항상 글자(textContent)로만 넣는다 (innerHTML 쓰지 않음).
//   h("div", { class: "card", onclick: fn, aria: { label: "..." } }, "글자", 다른요소)
//   style은 객체로: { style: { width: "40%" } } (보안 정책상 style 속성 대신 CSSOM 사용)

const SVG_NS = "http://www.w3.org/2000/svg";
const SVG_TAGS = new Set(["svg", "path", "circle", "line", "polyline", "rect", "g"]);

export function h(tag, attrs, ...children) {
  const svg = SVG_TAGS.has(tag);
  const el = svg ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") {
      el.setAttribute("class", value);
    } else if (key === "style") {
      for (const [prop, v] of Object.entries(value)) el.style.setProperty(prop, v);
    } else if (key === "aria") {
      for (const [name, v] of Object.entries(value)) {
        if (v !== null && v !== undefined) el.setAttribute(`aria-${name}`, String(v));
      }
    } else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else if (child instanceof Node) el.appendChild(child);
    else el.appendChild(document.createTextNode(String(child)));
  }
}

export function icon(d, size = 20, attrs = {}) {
  return h(
    "svg",
    {
      width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
      "stroke-width": attrs.strokeWidth || 1.8, "stroke-linecap": "round",
      "stroke-linejoin": "round", "aria-hidden": "true",
    },
    h("path", { d }),
  );
}

export function replace(target, ...children) {
  target.replaceChildren();
  append(target, children);
}

let toastTimer = null;

export function toast(message) {
  document.querySelector(".toast")?.remove();
  const el = h("div", { class: "toast", role: "status" }, message);
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 3500);
}