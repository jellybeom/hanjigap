// 차 시트 (G7a): 등록·수정 · 시세 직접 넣기 · 판 날 적기 · 유지비 규칙 더하기
// 저장하면 닫고 onDone(결과)를 부른다.

import { api, ApiError } from "./api.js";
import { h, toast } from "./dom.js";
import { openSheet } from "./sheet.js";

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function today() {
  const d = new Date();
  return `${thisMonth()}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 입력 칸 묶음이 있는 시트를 연다. fields(v) → 요소들, send(v) → Promise */
function formSheet({ title, intro, values, fields, send, done, submitLabel = "저장" }) {
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

function input(v, key, label, attrs = {}) {
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

function select(v, key, label, choices) {
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

const num = (s) => (String(s ?? "").trim() === "" ? null : Number(String(s).replaceAll(",", "").trim()));

// --- 등록·수정 ---

export function openVehicleForm(ctx, vehicle, onDone) {
  const isNew = !vehicle;
  formSheet({
    title: isNew ? "차 추가" : `${vehicle.name} 고치기`,
    intro: isNew ? "구입가와 감가율로 매달 시세를 짐작해요. 중고 시세를 알면 상세 화면에서 직접 넣어 더 정확하게 할 수 있어요." : null,
    values: {
      owner: vehicle?.owner || ctx.owner() || ctx.me.slug,
      name: vehicle?.name || "",
      purchased_on: vehicle?.purchased_on || "",
      purchase_price: vehicle?.purchase_price ? String(vehicle.purchase_price) : "",
      model_year: vehicle?.model_year ? String(vehicle.model_year) : "",
      depreciation_pct: String(vehicle?.depreciation_pct ?? 15),
      inspection_due_on: vehicle?.inspection_due_on || "",
      bs_asset_name: vehicle?.bs_asset_name || "",
      memo: vehicle?.memo || "",
    },
    fields: (v, draw) => [
      isNew
        ? h(
            "div",
            { class: "field" },
            h("span", { class: "k" }, "주인"),
            h(
              "div",
              { class: "seg", role: "group", aria: { label: "주인" } },
              ctx.members.map((m) =>
                h("button", { type: "button", aria: { pressed: v.owner === m.slug }, onclick: () => { v.owner = m.slug; draw(); } }, m.name),
              ),
            ),
          )
        : null,
      h(
        "div",
        { class: "form-grid" },
        input(v, "name", "이름 *", { maxlength: 40, placeholder: "예: 우리 차" }),
        input(v, "model_year", "연식", { maxlength: 4, inputmode: "numeric", placeholder: "예: 2023" }),
        input(v, "purchased_on", "구입일 *", { maxlength: 10, placeholder: "예: 2023-03" }),
        input(v, "purchase_price", "구입가 (원) *", { maxlength: 12, inputmode: "numeric", placeholder: "예: 32000000" }),
        input(v, "depreciation_pct", "1년 감가율 (%)", { maxlength: 5, inputmode: "decimal" }),
        input(v, "inspection_due_on", "다음 검사일", { maxlength: 10, placeholder: "예: 2026-11-20" }),
      ),
      input(v, "bs_asset_name", "뱅샐 동산 이름 (있으면)", { maxlength: 40, placeholder: "뱅샐 재무현황에 차를 넣어 둔 경우 그 이름" }),
      h("p", { class: "hint" }, "뱅샐 재무현황 '동산'에 차가 이미 있으면 한지갑 시세는 순자산에 더하지 않아요 (두 번 세지 않게)."),
      input(v, "memo", "메모", { maxlength: 100 }),
    ],
    send: (v) => {
      const body = {
        name: v.name.trim(),
        purchased_on: v.purchased_on.trim(),
        purchase_price: num(v.purchase_price),
        model_year: num(v.model_year),
        depreciation_pct: num(v.depreciation_pct) ?? 15,
        inspection_due_on: v.inspection_due_on.trim(),
        bs_asset_name: v.bs_asset_name.trim(),
        memo: v.memo.trim(),
      };
      if (isNew) {
        body.owner = v.owner;
        for (const k of ["model_year", "inspection_due_on", "bs_asset_name", "memo"]) if (!body[k]) delete body[k];
        return api.post("/api/vehicles", body).then((r) => onDone && onDone(r));
      }
      if (body.model_year === null) body.model_year = 0; // 0 = 지우기
      return api.patch(`/api/vehicles/${vehicle.id}`, body).then((r) => onDone && onDone(r));
    },
    done: isNew ? "차를 등록했어요" : "저장했어요",
    submitLabel: isNew ? "등록" : "저장",
  });
}

// --- 시세 직접 넣기 ---

export function openValuationSheet(vehicle, onDone) {
  formSheet({
    title: "시세 직접 넣기",
    intro: "중고차 사이트 등에서 본 그 달 시세를 넣어요. 그 달부터는 이 값을 기준으로 감가해요.",
    values: { month: thisMonth(), value: "", memo: "" },
    fields: (v) => [
      h(
        "div",
        { class: "form-grid" },
        input(v, "month", "달", { maxlength: 7, placeholder: "예: 2026-10" }),
        input(v, "value", "시세 (원)", { maxlength: 12, inputmode: "numeric", placeholder: "예: 21000000" }),
      ),
      input(v, "memo", "메모 (어디서 봤는지)", { maxlength: 100 }),
    ],
    send: (v) =>
      api
        .put(`/api/vehicles/${vehicle.id}/valuations/${encodeURIComponent(v.month.trim())}`, { value: num(v.value), memo: v.memo.trim() || null })
        .then((r) => onDone && onDone(r)),
    done: "시세를 넣었어요",
  });
}

// --- 판 날 ---

export function openSoldSheet(vehicle, onDone) {
  const linked = (vehicle.fixed_items || []).filter((f) => !f.ended);
  const fixedNote = linked.length
    ? ` 이 차에 연결된 고정비(${linked.map((f) => f.name).join(", ")})는 그대로예요. 할부·보험이 끝났으면 내역 › 고정비에서 마감해 주세요.`
    : "";
  formSheet({
    title: "판 날 적기",
    intro: `지우지 않고 판 날만 적어요. 판 달부터 순자산에서 빠지고(판 돈은 통장에 들어오니까), 유지비는 판 달까지 세요.${fixedNote}`,
    values: { day: today() },
    fields: (v) => [input(v, "day", "판 날", { maxlength: 10, placeholder: "예: 2026-10-15" })],
    send: (v) => api.post(`/api/vehicles/${vehicle.id}/sold`, { sold_on: v.day.trim() }).then((r) => onDone && onDone(r)),
    done: "판 날을 적었어요",
  });
}

// --- 유지비 규칙 ---

export function openRuleSheet(ctx, vehicle, options, onDone) {
  formSheet({
    title: "유지비 찾는 규칙 더하기",
    intro: "기본 규칙은 뱅샐 '자동차' 분류를 봐요. 다른 분류로 들어오는 세차·정비·하이패스 같은 거래를 더 잡을 때 써요.",
    values: { kind: "text", pattern: "", category: "", subcategory: "", cost_type: "wash", owner: "" },
    fields: (v, draw) => [
      h(
        "div",
        { class: "field" },
        h("span", { class: "k" }, "무엇으로 찾을까요?"),
        h(
          "div",
          { class: "seg", role: "group" },
          options.vehicle_rule_kinds.map((o) =>
            h("button", { type: "button", aria: { pressed: v.kind === o.code }, onclick: () => { v.kind = o.code; draw(); } }, o.label),
          ),
        ),
      ),
      v.kind === "category"
        ? h(
            "div",
            { class: "form-grid" },
            input(v, "category", "뱅샐 분류 *", { maxlength: 30, placeholder: "예: 자동차" }),
            input(v, "subcategory", "소분류", { maxlength: 30, placeholder: "예: 세차" }),
          )
        : input(v, "pattern", v.kind === "text" ? "내용에 들어가는 글자 *" : "뱅샐 결제수단 이름 *", {
            maxlength: 50, placeholder: v.kind === "text" ? "예: 세차" : "예: 하이패스카드 A",
          }),
      select(v, "cost_type", "비용 종류", options.vehicle_cost_types.map((o) => [o.code, o.label])),
      select(v, "owner", v.kind === "instrument" ? "누구의 거래 * (결제수단 이름은 사람마다 달라요)" : "누구의 거래", [
        ["", "모두"],
        ...ctx.members.map((m) => [m.slug, m.name]),
      ]),
    ],
    send: (v) => {
      const body = { kind: v.kind, cost_type: v.cost_type, owner: v.owner || null };
      if (v.kind === "category") {
        body.category = v.category.trim();
        body.subcategory = v.subcategory.trim() || null;
      } else body.pattern = v.pattern.trim();
      return api.post(`/api/vehicles/${vehicle.id}/cost-rules`, body).then((r) => onDone && onDone(r));
    },
    done: "규칙을 더했어요",
    submitLabel: "더하기",
  });
}