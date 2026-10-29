// 한지갑 분류 고르기 (확인 필요 정리 화면 · 거래 상세가 함께 씀)
//   나간 돈: 지출 · 저축·투자 · 제외
//   들어온 돈: 수입 · 지출 차감 · 저축·투자 · 제외
//   지출·지출 차감·수입은 분류도 고른다 (많이 쓴 분류 + 직접 입력).
//   '앞으로도 이렇게'를 켜면 같은 사람·같은 방향·같은 내용 거래에 쓸 규칙도 만든다.

import { api, ApiError } from "./api.js";
import { h, icon, replace } from "./dom.js";
import { ICONS } from "./icons.js";

const EXCLUDED = { key: "excluded", result: "excluded", label: "제외", desc: "내 계좌끼리·부부 사이 이체처럼 합계에서 빼요" };
const OUT_OPTIONS = [
  { key: "expense", result: "expense", label: "지출", desc: "쓴 돈", catTitle: "어떤 지출인가요?" },
  { key: "saving", result: "saving", label: "저축·투자", desc: "적금·투자로 옮긴 돈" },
  EXCLUDED,
];
const IN_OPTIONS = [
  { key: "income", result: "income", label: "수입", desc: "번 돈·받은 돈", catTitle: "어떤 수입인가요?" },
  {
    key: "refund", result: "expense", label: "지출 차감", desc: "환불·나눠 낸 돈을 돌려받음",
    catTitle: "어느 지출에서 뺄까요?",
  },
  { key: "saving", result: "saving", label: "저축·투자", desc: "적금 만기·투자에서 돌아온 돈" },
  EXCLUDED,
];
const CHIP_LIMIT = 10;
const CATEGORY_MAX = 30;

/** 화면에 보이는 분류 이름: 들어온 돈을 지출로 정하면 '지출 차감' */
export function optionKey(cls, amount) {
  return cls === "expense" && amount > 0 ? "refund" : cls;
}

export function classText(x) {
  if (x.cls === "unconfirmed") return "확인 필요";
  const label = optionKey(x.cls, x.amount) === "refund" ? "지출 차감" : x.class_label;
  return x.display_category ? `${label} › ${x.display_category}` : label;
}

// 분류 후보는 한 번 받아 두고, 분류를 정하면 다시 받는다
let categories = null;

export function loadCategories() {
  if (!categories) {
    categories = api.get("/api/categories").catch((error) => {
      categories = null;
      throw error;
    });
  }
  return categories;
}

export function forgetCategories() {
  categories = null;
}

/**
 * tx: 거래 상세 (/api/transactions/{owner}/{key})
 * initial: { key, category } 처음 골라 둘 값 (없으면 아무것도 안 고름)
 * onSaved(response, text): 저장한 뒤. response = { transaction, rule }
 */
export function classPicker(ctx, tx, cats, { initial = null, submitLabel = "이렇게 정하기", onSaved, onCancel } = {}) {
  const options = tx.amount > 0 ? IN_OPTIONS : OUT_OPTIONS;
  const suggestion = tx.suggestion ? (tx.suggestion === "expense" && tx.amount > 0 ? "refund" : tx.suggestion) : null;
  const state = {
    key: initial?.key && options.some((o) => o.key === initial.key) ? initial.key : null,
    category: initial?.category || "",
    custom: false,
    remember: false,
    busy: false,
    error: null,
  };
  const wrap = h("div", { class: "picker" });
  const option = () => options.find((o) => o.key === state.key) || null;
  const resultText = () => {
    const o = option();
    if (!o) return "";
    return o.catTitle && state.category ? `${o.label} › ${state.category}` : o.label;
  };
  const ready = () => {
    const o = option();
    return Boolean(o) && (!o.catTitle || state.category.trim().length > 0) && !state.busy;
  };

  function draw() {
    const o = option();
    replace(
      wrap,
      h(
        "div",
        { class: "pick-options", role: "group", aria: { label: "한지갑 분류" } },
        options.map((opt) =>
          h(
            "button",
            {
              class: "pick-opt", type: "button", aria: { pressed: state.key === opt.key },
              onclick: () => {
                if (state.key !== opt.key) {
                  const sameList = o && listFor(o) === listFor(opt);
                  if (!sameList) {
                    state.category = initial?.key === opt.key ? initial.category || "" : "";
                    state.custom = false;
                  }
                }
                state.key = opt.key;
                state.error = null;
                draw();
              },
            },
            h(
              "span",
              { class: "opt-top" },
              h("span", { class: "opt-label" }, opt.label),
              suggestion === opt.key ? h("span", { class: "chip small suggest" }, "추천") : null,
              state.key === opt.key ? icon(ICONS.check, 18, { strokeWidth: 2.4 }) : null,
            ),
            h("span", { class: "opt-desc" }, opt.desc),
          ),
        ),
      ),
      o && o.catTitle ? categoryBlock(o) : null,
      o ? rememberBlock() : null,
      state.error ? h("p", { class: "form-error", role: "alert" }, state.error) : null,
      h(
        "div",
        { class: "pick-actions" },
        onCancel ? h("button", { class: "btn secondary", type: "button", onclick: onCancel }, "취소") : null,
        h(
          "button",
          { class: "btn grow", type: "button", disabled: !ready(), onclick: save },
          state.busy ? "저장하는 중…" : submitLabel,
        ),
      ),
    );
  }

  function listFor(opt) {
    return opt.result === "income" ? "income" : "expense";
  }

  function categoryBlock(o) {
    const pool = [...cats[listFor(o)]];
    const picked = state.category.trim();
    const shown = pool.slice(0, CHIP_LIMIT);
    if (picked && !state.custom && !shown.includes(picked)) shown.unshift(picked);
    const input = h("input", {
      class: "text-input", type: "text", maxlength: CATEGORY_MAX, placeholder: "분류 이름 (예: 경조사)",
      value: state.custom ? state.category : "", enterkeyhint: "done", autocomplete: "off",
      aria: { label: "분류 직접 입력" },
      oninput: (e) => {
        state.category = e.target.value;
        const btn = wrap.querySelector(".pick-actions .btn.grow");
        if (btn) btn.disabled = !ready();
        const preview = wrap.querySelector(".remember .result");
        if (preview) preview.textContent = resultText();
      },
    });
    const block = h(
      "div",
      { class: "pick-cats" },
      h("span", { class: "k" }, o.catTitle),
      h(
        "div",
        { class: "chips", role: "group", aria: { label: "분류" } },
        shown.map((c) =>
          h(
            "button",
            {
              class: "chip-pick", type: "button", aria: { pressed: !state.custom && picked === c },
              onclick: () => {
                state.category = c;
                state.custom = false;
                draw();
              },
            },
            c,
          ),
        ),
        h(
          "button",
          {
            class: "chip-pick", type: "button", aria: { pressed: state.custom },
            onclick: () => {
              state.custom = true;
              state.category = "";
              draw();
              wrap.querySelector(".pick-cats .text-input")?.focus();
            },
          },
          "+ 직접 입력",
        ),
      ),
      state.custom ? input : null,
    );
    return block;
  }

  function rememberBlock() {
    const r = tx.remember;
    if (!r || !r.possible) {
      return h("p", { class: "hint" }, r?.reason || "이 거래는 규칙으로 만들 수 없어요");
    }
    const box = h("input", {
      type: "checkbox", id: `remember-${tx.tx_key}`, checked: state.remember,
      onchange: (e) => {
        state.remember = e.target.checked;
        draw();
      },
    });
    return h(
      "div",
      { class: `remember${state.remember ? " on" : ""}` },
      h("label", { class: "remember-row", for: `remember-${tx.tx_key}` }, box, h("span", {}, "앞으로도 이렇게")),
      h(
        "p",
        { class: "remember-text" },
        `앞으로 ${ctx.nameOf(tx.owner)}의 ${r.direction_label} 중 내용에 「${r.pattern}」 글자가 있으면 → `,
        h("strong", { class: "result" }, resultText()),
      ),
      h(
        "p",
        { class: "hint" },
        r.others
          ? `같은 내용의 다른 거래 ${r.others}건${r.other_months > 1 ? `(${r.other_months}개월)` : ""}도 함께 바뀌어요. 규칙은 나중에 설정에서 고치거나 지울 수 있어요.`
          : "지금은 같은 내용의 다른 거래가 없어요. 규칙은 나중에 설정에서 고치거나 지울 수 있어요.",
      ),
    );
  }

  async function save() {
    if (!ready()) return;
    const o = option();
    const text = resultText();
    state.busy = true;
    state.error = null;
    draw();
    try {
      const res = await api.put(`/api/transactions/${encodeURIComponent(tx.owner)}/${encodeURIComponent(tx.tx_key)}/class`, {
        result: o.result,
        result_category: o.catTitle ? state.category.trim() : null,
        remember: state.remember,
      });
      forgetCategories();
      state.busy = false;
      if (onSaved) onSaved(res, text);
    } catch (error) {
      state.busy = false;
      state.error = error instanceof ApiError ? error.message : "저장하지 못했어요";
      draw();
      if (!(error instanceof ApiError)) console.error(error);
    }
  }

  draw();
  return wrap;
}