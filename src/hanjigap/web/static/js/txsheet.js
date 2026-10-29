// 거래 상세 (아래에서 올라오는 시트)
//   한지갑 분류: 어떻게 정해졌는지 · 분류 바꾸기 · 직접 지정 풀기
//   뱅샐 내용: 분류·소분류·내용·한지갑 메모 고치기 · 뱅샐 값으로 되돌리기
//   타입(수입·지출·이체)은 뱅샐에서만 바꾼다.
// 무언가 바뀌었으면 시트를 닫을 때 onChange()를 부른다 (뒤 화면 새로 그리기).

import { api, ApiError } from "./api.js";
import { classPicker, classText, forgetCategories, loadCategories, optionKey } from "./classpick.js";
import { h, icon, toast } from "./dom.js";
import { dayLabel, txAmount } from "./format.js";
import { ICONS } from "./icons.js";
import { openSheet } from "./sheet.js";

const TEXT_MAX = 100;
const CATEGORY_MAX = 30;

export async function openTxSheet(ctx, owner, txKey, { onChange } = {}) {
  let changed = false;
  let mode = "view"; // view | class | edit
  let tx = null;
  let cats = null;
  let busy = false;
  let error = null;
  const path = `/api/transactions/${encodeURIComponent(owner)}/${encodeURIComponent(txKey)}`;
  const sheet = openSheet({
    title: "거래",
    onClose: () => {
      if (changed && onChange) onChange();
    },
  });
  sheet.setBody(h("p", { class: "loading" }, "불러오는 중…"));
  try {
    [tx, cats] = await Promise.all([api.get(path), loadCategories()]);
  } catch (e) {
    sheet.setBody(h("p", { class: "form-error", role: "alert" }, e instanceof ApiError ? e.message : "거래를 불러오지 못했어요"));
    if (!(e instanceof ApiError)) console.error(e);
    return;
  }

  const updated = (next, message) => {
    tx = next;
    changed = true;
    mode = "view";
    error = null;
    toast(message);
    draw();
  };

  async function run(action, message) {
    if (busy) return;
    busy = true;
    error = null;
    try {
      const next = await action();
      forgetCategories();
      cats = await loadCategories();
      busy = false;
      updated(next, message);
    } catch (e) {
      busy = false;
      error = e instanceof ApiError ? e.message : "저장하지 못했어요";
      draw();
      if (!(e instanceof ApiError)) console.error(e);
    }
  }

  function draw() {
    if (!sheet.isOpen) return;
    sheet.setTitle(tx.content || "(내용 없음)");
    sheet.setBody(top(), classSection(), banksaladSection());
  }

  function top() {
    const when = `${dayLabel(tx.date)} ${tx.time.slice(0, 5)}`;
    return h(
      "div",
      { class: "sheet-top" },
      h("div", { class: `sheet-amount${tx.amount > 0 ? " pos" : ""}` }, `${txAmount(tx.amount)}원`),
      h("span", { class: "meta" }, [when, ctx.nameOf(tx.owner), tx.instrument].filter(Boolean).join(" · ")),
    );
  }

  // --- 한지갑 분류 ---

  function classSection() {
    const unconfirmed = tx.cls === "unconfirmed";
    const manual = tx.manual_class;
    const how = manual
      ? `직접 지정 · ${manual.classified_by ? ctx.nameOf(manual.classified_by) : "알 수 없음"} · ${shortDate(manual.classified_at)}`
      : `${tx.source_label} · ${tx.reason}`;
    const sec = h(
      "section",
      { class: "sheet-sec", aria: { label: "한지갑 분류" } },
      h(
        "div",
        { class: "sec-head" },
        h("h3", {}, "한지갑 분류"),
        h("span", { class: `chip${unconfirmed ? " outline-warn" : ""}` }, classText(tx)),
      ),
      h("p", { class: "hint" }, `정한 방법: ${how}`),
      tx.pair_hint ? h("p", { class: "note-box" }, "다른 구성원에게 같은 금액이 하루 안에 반대로 오간 거래가 있어요. 부부 사이 이체라면 '제외'가 맞아요.") : null,
    );
    if (mode === "class") {
      sec.appendChild(
        classPicker(ctx, tx, cats, {
          initial: unconfirmed ? null : { key: optionKey(tx.cls, tx.amount), category: tx.display_category },
          onCancel: () => {
            mode = "view";
            draw();
          },
          onSaved: (res, text) => updated(res.transaction, res.rule ? `정했어요 · ${text} (규칙도 만들었어요)` : `정했어요 · ${text}`),
        }),
      );
      return sec;
    }
    sec.appendChild(
      h(
        "div",
        { class: "sec-actions" },
        h(
          "button",
          {
            class: "btn secondary small", type: "button",
            onclick: () => {
              mode = "class";
              error = null;
              draw();
            },
          },
          unconfirmed ? "분류 정하기" : "분류 바꾸기",
        ),
        manual
          ? h(
              "button",
              {
                class: "link-btn", type: "button",
                onclick: () => run(() => api.del(`${path}/class`), "직접 지정을 풀었어요"),
              },
              "직접 지정 풀기",
            )
          : null,
      ),
    );
    if (manual) sec.appendChild(h("p", { class: "hint" }, "직접 지정을 풀면 규칙이나 뱅샐 기본값으로 다시 정해져요."));
    if (error && mode === "view") sec.appendChild(h("p", { class: "form-error", role: "alert" }, error));
    return sec;
  }

  // --- 뱅샐 내용 ---

  function banksaladSection() {
    const o = tx.original;
    const edit = tx.edit;
    const sec = h(
      "section",
      { class: "sheet-sec", aria: { label: "뱅샐 내용" } },
      h(
        "div",
        { class: "sec-head" },
        h("h3", {}, "뱅샐 내용"),
        edit ? h("span", { class: "chip small outline-warn" }, "웹에서 수정") : null,
      ),
    );
    if (mode === "edit") {
      sec.appendChild(editForm());
      return sec;
    }
    const cat = (c, s) => (s ? `${c || "미분류"} › ${s}` : c || "미분류");
    sec.appendChild(
      h(
        "dl",
        { class: "facts" },
        fact("타입", tx.tx_type, "뱅샐에서만 바꿔요"),
        fact("분류", cat(tx.category, tx.subcategory), edit && (edit.category || edit.subcategory) ? `뱅샐: ${cat(o.category, o.subcategory)}` : null),
        fact("내용", tx.content || "(없음)", edit && edit.content ? `뱅샐: ${o.content || "(없음)"}` : null),
        o.memo ? fact("뱅샐 메모", o.memo) : null,
        fact("한지갑 메모", tx.note || "없음"),
      ),
    );
    sec.appendChild(
      h(
        "div",
        { class: "sec-actions" },
        h(
          "button",
          {
            class: "btn secondary small", type: "button",
            onclick: () => {
              mode = "edit";
              error = null;
              draw();
            },
          },
          icon(ICONS.edit, 16, { strokeWidth: 2 }),
          "고치기",
        ),
        edit
          ? h(
              "button",
              {
                class: "link-btn", type: "button",
                onclick: () => run(() => api.del(`${path}/edit`), "뱅샐 값으로 되돌렸어요"),
              },
              "뱅샐 값으로 되돌리기",
            )
          : null,
      ),
    );
    if (edit) {
      sec.appendChild(
        h("p", { class: "hint" }, `웹에서 고침: ${edit.edited_by ? ctx.nameOf(edit.edited_by) : "알 수 없음"} · ${shortDate(edit.edited_at)}`),
      );
    }
    return sec;
  }

  function fact(label, value, note = null) {
    return h(
      "div",
      { class: "fact" },
      h("dt", {}, label),
      h("dd", {}, value, note ? h("span", { class: "fact-note" }, note) : null),
    );
  }

  function editForm() {
    const o = tx.original;
    const listId = `cats-${tx.tx_key}`;
    const subListId = `subs-${tx.tx_key}`;
    const subOptions = h("datalist", { id: subListId });
    const fillSubs = (category) => {
      const found = cats.banksalad.find((b) => b.category === category.trim());
      subOptions.replaceChildren(...(found ? found.subcategories.map((s) => h("option", { value: s })) : []));
    };
    const field = (name, label, value, attrs = {}) =>
      h(
        "label",
        { class: "field" },
        h("span", { class: "k" }, label),
        h(attrs.multiline ? "textarea" : "input", {
          class: "text-input", name, type: attrs.multiline ? null : "text", value: attrs.multiline ? null : value,
          maxlength: attrs.max || TEXT_MAX, list: attrs.list || null, autocomplete: "off",
          placeholder: attrs.placeholder || null, rows: attrs.multiline ? 2 : null,
          oninput: attrs.oninput || null,
        }, attrs.multiline ? value : null),
      );
    const form = h(
      "form",
      {
        class: "edit-form",
        onsubmit: (e) => {
          e.preventDefault();
          const data = new FormData(form);
          const patch = {};
          for (const key of ["category", "subcategory", "content"]) {
            const v = String(data.get(key) || "").trim();
            if (v === (tx[key] || "")) continue;
            patch[key] = v === (o[key] || "") ? "" : v; // 뱅샐 값과 같으면 고친 것을 푼다
          }
          const note = String(data.get("note") || "").trim();
          if (note !== (tx.note || "")) patch.note = note;
          if (!Object.keys(patch).length) {
            mode = "view";
            draw();
            return;
          }
          run(() => api.patch(path, patch), "고쳤어요");
        },
      },
      field("category", "분류", tx.category, { max: CATEGORY_MAX, list: listId, placeholder: o.category || "분류", oninput: (e) => fillSubs(e.target.value) }),
      field("subcategory", "소분류", tx.subcategory, { max: CATEGORY_MAX, list: subListId, placeholder: o.subcategory || "소분류" }),
      field("content", "내용", tx.content, { placeholder: o.content || "내용" }),
      field("note", "한지갑 메모", tx.note || "", { multiline: true, placeholder: "둘이 보는 메모 (뱅샐에는 안 들어가요)" }),
      h("datalist", { id: listId }, cats.banksalad.map((b) => h("option", { value: b.category }))),
      subOptions,
      h("p", { class: "hint" }, "칸을 비우면 그 칸은 뱅샐 값으로 돌아가요. 분류를 고치면 지출 분류별 합계와 규칙 판단에도 쓰여요."),
      error ? h("p", { class: "form-error", role: "alert" }, error) : null,
      h(
        "div",
        { class: "pick-actions" },
        h(
          "button",
          {
            class: "btn secondary", type: "button",
            onclick: () => {
              mode = "view";
              error = null;
              draw();
            },
          },
          "취소",
        ),
        h("button", { class: "btn grow", type: "submit" }, "저장"),
      ),
    );
    fillSubs(tx.category || "");
    return form;
  }

  draw();
}

function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}