// 아직 만들지 않은 화면 (다음 단계에서 채운다)

import { h, replace } from "../dom.js";

export async function render(root, _ctx, screen) {
  replace(
    root,
    h("h1", { class: "screen-title" }, screen.title),
    h(
      "div",
      { class: "card" },
      h("p", { class: "empty" }, `이 화면은 다음 단계(${screen.step})에서 만들어요.`),
    ),
  );
}