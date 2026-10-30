// 분류 규칙 (G8): 목록 · 켜기/끄기 · 순서(▲▼) · 편집·추가 시트 (저장 전 미리보기) · 삭제
// 주소: #/rules
// 데이터: /api/class-rules, /api/class-rules/preview, /api/categories
// 규칙은 위에서부터 차례로 맞춰 본다. 직접 지정한 거래는 규칙보다 먼저다.

import { api, ApiError } from "../api.js";
import { h, icon, replace, toast } from "../dom.js";
import { won } from "../format.js";
import { ICONS } from "../icons.js";
import { openSheet } from "../sheet.js";

const UP = "M6 15l6-6 6 6";
const DOWN = "M6 9l6 6 6-6";
const RESULTS = [["expense", "지출"], ["income", "수입"], ["saving", "저축·투자"], ["excluded", "제외"]];
const DIRECTIONS = [["any", "모두"], ["in", "들어온 돈"], ["out", "나간 돈"]];

export async function render(root, ctx, _screen, isCurrent) {
  const rules = await api.get("/api/class-rules");
  if (!isCurrent()) return;
  const refresh = () => ctx.refresh();
  const items = rules.items;
  replace(
    root,
    h(
      "header",
      { class: "topbar" },
      h("a", { class: "icon-btn plain edge-left", href: "#/settings", aria: { label: "설정으로" } }, icon(ICONS.prev, 22, { strokeWidth: 2 })),
      h("h1", { class: "bar-title" }, "분류 규칙"),
      h("button", { class: "icon-btn plain edge-right", type: "button", aria: { label: "규칙 더하기" }, onclick: () => openRuleSheet(ctx, null, refresh) }, h("span", { class: "plus" }, "+")),
    ),
    h("p", { class: "hint" }, "거래마다 위에서부터 차례로 맞춰 보고, 처음 맞는 규칙으로 정해요. 거래에서 직접 정한 분류는 규칙보다 먼저예요. 규칙은 지난 달 거래에도 똑같이 적용돼요."),
    items.length === 0
      ? h(
          "div",
          { class: "card" },
          h("p", { class: "empty" }, "아직 규칙이 없어요. 확인 필요 정리에서 '앞으로도 이렇게'를 켜거나, 오른쪽 위 +로 직접 만들 수 있어요."),
        )
      : h(
          "section",
          { class: "card tx-card", aria: { label: "규칙 목록" } },
          items.map((r, i) =>
            h(
              "div",
              { class: `rule-item${r.active ? "" : " off"}` },
              h("input", {
                type: "checkbox", checked: r.active, aria: { label: r.active ? "규칙 끄기" : "규칙 켜기" },
                onchange: (e) => act(() => api.patch(`/api/class-rules/${r.id}`, { active: e.target.checked }), e.target.checked ? "켰어요" : "껐어요", refresh),
              }),
              h(
                "button",
                { class: "rule-text plain-btn", type: "button", onclick: () => openRuleSheet(ctx, r, refresh) },
                h("span", { class: "rule-no" }, `${i + 1}`),
                h("span", { class: "rule-label" }, r.label, r.owner ? h("span", { class: "chip small" }, ctx.nameOf(r.owner)) : null),
                r.memo ? h("span", { class: "meta" }, r.memo) : null,
              ),
              h(
                "span",
                { class: "order-btns" },
                h("button", { class: "icon-btn plain small-icon", type: "button", disabled: i === 0, aria: { label: "위로" }, onclick: () => move(items, i, -1, refresh) }, icon(UP, 18, { strokeWidth: 2.2 })),
                h("button", { class: "icon-btn plain small-icon", type: "button", disabled: i === items.length - 1, aria: { label: "아래로" }, onclick: () => move(items, i, 1, refresh) }, icon(DOWN, 18, { strokeWidth: 2.2 })),
              ),
            ),
          ),
        ),
  );
}

function move(items, i, step, refresh) {
  const ids = items.map((r) => r.id);
  [ids[i], ids[i + step]] = [ids[i + step], ids[i]];
  act(() => api.put("/api/class-rules/order", { ids }), "순서를 바꿨어요", refresh);
}

async function act(action, message, refresh) {
  try {
    await action();
    toast(message);
  } catch (e) {
    toast(e instanceof ApiError ? e.message : "하지 못했어요");
  }
  refresh();
}

// --- 편집·추가 시트 -----------------------------------------------------------------

async function openRuleSheet(ctx, rule, refresh) {
  const isNew = !rule;
  let changed = false;
  const sheet = openSheet({ title: isNew ? "규칙 더하기" : "규칙 고치기", onClose: () => changed && refresh() });
  let cats = { expense: [], income: [], banksalad: [] };
  try {
    cats = await api.get("/api/categories");
  } catch {
    // 후보 목록은 덤
  }
  const v = {
    owner: rule?.owner || "",
    direction: rule?.direction || "out",
    tx_type: rule?.tx_type || "",
    pattern: rule?.pattern || "",
    category: rule?.category || "",
    subcategory: rule?.subcategory || "",
    instrument: rule?.instrument || "",
    amount: rule?.amount ? String(rule.amount) : "",
    result: rule?.result || "expense",
    result_category: rule?.result_category || "",
    memo: rule?.memo || "",
  };
  let error = null;
  let busy = false;
  let confirmDelete = false;
  let timer = null;
  const previewBox = h("div", { class: "preview-box", aria: { live: "polite" } });

  const conditions = () => ({
    owner: v.owner || null,
    direction: v.direction,
    tx_type: v.tx_type || null,
    pattern: v.pattern.trim() || null,
    category: v.category.trim() || null,
    subcategory: v.subcategory.trim() || null,
    instrument: v.instrument.trim() || null,
    amount: v.amount.trim() ? Number(v.amount.replaceAll(",", "")) : null,
    result: v.result,
    result_category: v.result_category.trim() || null,
  });

  async function preview() {
    try {
      const p = await api.post("/api/class-rules/preview", conditions());
      replace(
        previewBox,
        h("strong", {}, `맞는 거래 ${won(p.count)}건${p.months ? ` · ${p.months}개월` : ""}`),
        p.manual ? h("span", { class: "meta" }, ` · 직접 정한 ${p.manual}건은 그대로`) : null,
        h("span", { class: "meta block" }, p.label),
        p.examples.map((x) =>
          h("span", { class: "example" }, `${x.date.slice(5).replace("-", ".")} ${x.content || "(내용 없음)"} · ${x.amount > 0 ? "+" : "−"}${won(x.amount)}원${ctx.members.length > 1 ? ` · ${ctx.nameOf(x.owner)}` : ""}`),
        ),
        p.count > 50 ? h("span", { class: "warn small" }, "맞는 거래가 많아요. 조건이 너무 넓지 않은지 확인해 주세요.") : null,
      );
    } catch (e) {
      replace(previewBox, h("span", { class: "meta" }, e instanceof ApiError ? e.message : "미리보기를 못 했어요"));
    }
  }
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(preview, 350);
  };

  const field = (key, label, attrs = {}) =>
    h(
      "label",
      { class: "field" },
      h("span", { class: "k" }, label),
      h("input", {
        class: "text-input", type: "text", value: v[key], autocomplete: "off", ...attrs,
        oninput: (e) => {
          v[key] = e.target.value;
          schedule();
        },
      }),
    );
  const seg = (key, choices, label) =>
    h(
      "div",
      { class: "field" },
      h("span", { class: "k" }, label),
      h(
        "div",
        { class: "seg", role: "group", aria: { label } },
        choices.map(([code, text]) =>
          h("button", { type: "button", aria: { pressed: v[key] === code }, onclick: () => { v[key] = code; draw(); schedule(); } }, text),
        ),
      ),
    );

  async function save() {
    if (busy) return;
    busy = true;
    error = null;
    draw();
    const c = conditions();
    try {
      if (isNew) {
        await api.post("/api/class-rules", { ...c, memo: v.memo.trim() || null });
      } else {
        // 비운 조건은 ''(금액은 0)로 보내야 지워진다
        await api.patch(`/api/class-rules/${rule.id}`, {
          owner: v.owner, direction: v.direction, tx_type: v.tx_type, pattern: v.pattern.trim(),
          category: v.category.trim(), subcategory: v.subcategory.trim(), instrument: v.instrument.trim(),
          amount: c.amount ?? 0, result: v.result, result_category: v.result_category.trim(), memo: v.memo.trim(),
        });
      }
      changed = true;
      toast(isNew ? "규칙을 더했어요 (맨 위)" : "저장했어요");
      sheet.close();
    } catch (e) {
      busy = false;
      error = e instanceof ApiError ? e.message : "저장하지 못했어요";
      draw();
    }
  }

  async function remove() {
    try {
      await api.del(`/api/class-rules/${rule.id}`);
      changed = true;
      toast("규칙을 지웠어요");
      sheet.close();
    } catch (e) {
      error = e instanceof ApiError ? e.message : "지우지 못했어요";
      draw();
    }
  }

  function draw() {
    const resultCats = v.result === "income" ? cats.income : cats.expense;
    sheet.setBody(
      h("h3", { class: "sub-title" }, "이런 거래면"),
      seg("owner", [["", "모두"], ...ctx.members.map((m) => [m.slug, m.name])], "누구의 거래"),
      seg("direction", DIRECTIONS, "돈의 방향"),
      field("pattern", "내용에 들어가는 글자", { maxlength: 50, placeholder: "예: 학원 (대소문자 상관없음)" }),
      h(
        "details",
        { class: "more-fields", open: Boolean(v.tx_type || v.category || v.instrument || v.amount) },
        h("summary", {}, "조건 더 보기 (뱅샐 타입 · 분류 · 결제수단 · 금액)"),
        h(
          "div",
          { class: "stack-12" },
          h(
            "label",
            { class: "field" },
            h("span", { class: "k" }, "뱅샐 타입"),
            h(
              "select",
              { class: "text-input", onchange: (e) => { v.tx_type = e.target.value; schedule(); } },
              [["", "상관없음"], ["수입", "수입"], ["지출", "지출"], ["이체", "이체"]].map(([code, text]) => h("option", { value: code, selected: v.tx_type === code }, text)),
            ),
          ),
          h(
            "div",
            { class: "form-grid" },
            field("category", "뱅샐 분류", { maxlength: 50, list: "rule-cats" }),
            field("subcategory", "뱅샐 소분류", { maxlength: 50 }),
            field("instrument", "결제수단 이름", { maxlength: 50, placeholder: "뱅샐 이름 그대로" }),
            field("amount", "금액 (원, 부호 없이)", { maxlength: 12, inputmode: "numeric" }),
          ),
          h("datalist", { id: "rule-cats" }, cats.banksalad.map((b) => h("option", { value: b.category }))),
        ),
      ),
      h("h3", { class: "sub-title" }, "미리보기 (저장 전)"),
      previewBox,
      h("h3", { class: "sub-title" }, "이렇게 정해요"),
      seg("result", RESULTS, "한지갑 분류"),
      v.result === "excluded" || v.result === "saving"
        ? null
        : h(
            "div",
            { class: "stack-6" },
            field("result_category", v.result === "income" ? "보여줄 수입 분류" : "보여줄 지출 분류", { maxlength: 30, list: "rule-result-cats", placeholder: "비우면 뱅샐 분류 그대로" }),
            h("datalist", { id: "rule-result-cats" }, resultCats.map((c) => h("option", { value: c }))),
          ),
      field("memo", "메모", { maxlength: 100 }),
      error ? h("p", { class: "form-error", role: "alert" }, error) : null,
      h(
        "div",
        { class: "pick-actions" },
        h("button", { class: "btn secondary", type: "button", onclick: () => sheet.close() }, "취소"),
        h("button", { class: "btn grow", type: "button", disabled: busy, onclick: save }, busy ? "저장하는 중…" : isNew ? "더하기" : "저장"),
      ),
      isNew
        ? null
        : confirmDelete
          ? h(
              "div",
              { class: "pick-actions" },
              h("button", { class: "btn secondary", type: "button", onclick: () => { confirmDelete = false; draw(); } }, "그만두기"),
              h("button", { class: "btn grow danger-btn", type: "button", onclick: remove }, "규칙 지우기"),
            )
          : h("button", { class: "link-btn danger", type: "button", onclick: () => { confirmDelete = true; draw(); } }, "이 규칙 지우기 (이 규칙으로 정해진 거래는 다시 뱅샐 기본값으로)"),
    );
  }
  draw();
  preview();
}