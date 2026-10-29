// 입력 시트 공용 조각 (차·집 시트가 함께 씀)
//   formSheet({ title, intro, values, fields(v, draw), send(v), done, submitLabel })
//   input(v, key, label, attrs) · select(v, key, label, [[code, text]]) · num("1,000") → 1000

import { ApiError } from "./api.js";
import { h, toast } from "./dom.js";
import { openSheet } from "./sheet.js";

export function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function today() {
  const d = new Date();
  return `${thisMonth()}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 입력 칸 묶음이 있는 시트를 연다. fields(v) → 요소들, send(v) → Promise */
export function formSheet({ title, intro, values, fields, send, done, submitLabel = "저장" }) {
  const v = { ...values };
  let error = null;
  let busy = false;
  const sheet = openSheet({ title });
  const draw = () =>
    sheet.setBody(
      intro ? h("p", { class: "hint" }, intro) : null,
      ...fields(v, draw),
      error ? h("p", { class: "form-error", role: "alert" }, error) : null,
      h(
        "div",
        { class: "pick-actions" },
        h("button", { class: "btn secondary", type: "button", onclick: () => sheet.close() }, "취소"),
        h(
          "button",
          {
            class: "btn grow", type: "button", disabled: busy,
            onclick: async () => {
              busy = true;
              error = null;
              draw();
              try {
                const result = await send(v);
                toast(done);
                sheet.close();
                return result;
              } catch (e) {
                busy = false;
                error = e instanceof ApiError ? e.message : "저장하지 못했어요";
                draw();
              }
              return null;
            },
          },
          busy ? "저장하는 중…" : submitLabel,
        ),
      ),
    );
  draw();
  return sheet;
}

export function input(v, key, label, attrs = {}) {
  return h(
    "label",
    { class: "field" },
    h("span", { class: "k" }, label),
    h("input", {
      class: "text-input", type: "text", value: v[key] ?? "", autocomplete: "off", ...attrs,
      oninput: (e) => {
        v[key] = e.target.value;
      },
    }),
  );
}

export function select(v, key, label, choices) {
  return h(
    "label",
    { class: "field" },
    h("span", { class: "k" }, label),
    h(
      "select",
      {
        class: "text-input",
        onchange: (e) => {
          v[key] = e.target.value;
        },
      },
      choices.map(([code, text]) => h("option", { value: code, selected: String(code) === String(v[key] ?? "") }, text)),
    ),
  );
}

export const num = (s) => (String(s ?? "").trim() === "" ? null : Number(String(s).replaceAll(",", "").trim()));