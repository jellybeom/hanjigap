// 아래에서 올라오는 시트 (브라우저 기본 <dialog>).
// 뒤 화면은 눌리지 않고, 뒤로 가기 키(Esc)·바깥 누르기·닫기 버튼으로 닫힌다.
//   const sheet = openSheet({ title: "거래", onClose: () => ... });
//   sheet.setBody(요소...);  sheet.close();

import { h, icon, replace } from "./dom.js";
import { ICONS } from "./icons.js";

export function openSheet({ title, onClose }) {
  const before = document.activeElement;
  const heading = h("h2", { class: "sheet-title", id: "sheet-title" }, title);
  const body = h("div", { class: "sheet-body" });
  const dialog = h(
    "dialog",
    { class: "sheet", aria: { labelledby: "sheet-title" } },
    h(
      "div",
      { class: "sheet-inner" },
      h("span", { class: "sheet-handle", "aria-hidden": "true" }),
      h(
        "div",
        { class: "sheet-head" },
        heading,
        h(
          "button",
          { class: "icon-btn plain edge-right", type: "button", aria: { label: "닫기" }, onclick: () => close() },
          icon(ICONS.close, 22, { strokeWidth: 2 }),
        ),
      ),
      body,
    ),
  );
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    if (dialog.open) dialog.close();
    dialog.remove();
    document.documentElement.classList.remove("sheet-open");
    if (before && typeof before.focus === "function" && document.contains(before)) before.focus();
    if (onClose) onClose();
  }
  dialog.addEventListener("cancel", (e) => {
    e.preventDefault();
    close();
  });
  // 시트 바깥(어두운 부분)을 누르면 닫기
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) close();
  });
  document.body.appendChild(dialog);
  document.documentElement.classList.add("sheet-open");
  dialog.showModal();
  return {
    close,
    setTitle: (text) => {
      heading.textContent = text;
    },
    setBody: (...children) => replace(body, ...children),
    get isOpen() {
      return !closed;
    },
  };
}