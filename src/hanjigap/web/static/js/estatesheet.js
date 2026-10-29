// 집 시트 (G7b): 집 등록·수정 · 계약 넣기(갱신)·고치기·끝내기 · 시세 넣기 · 이사 나감
// 저장하면 닫고 onDone(결과)를 부른다. 결과는 대부분 집 상세(/api/properties/{id}).

import { api } from "./api.js";
import { h } from "./dom.js";
import { formSheet, input, num, select, thisMonth, today } from "./forms.js";

const TYPE_HINT = {
  jeonse: "보증금이 자산 가치예요 (돌려받을 돈).",
  monthly: "보증금이 자산 가치예요. 월세는 고정비로 따로 넣어 주세요.",
  own: "매입가로 시작하고, 시세를 넣으면 그 값을 자산 가치로 써요.",
};

function seg(v, key, choices, draw, label) {
  return h(
    "div",
    { class: "field" },
    h("span", { class: "k" }, label),
    h(
      "div",
      { class: "seg", role: "group", aria: { label } },
      choices.map(([code, text]) =>
        h("button", { type: "button", aria: { pressed: v[key] === code }, onclick: () => { v[key] = code; draw(); } }, text),
      ),
    ),
  );
}

// --- 집 ---

export function openPropertyForm(options, prop, onDone) {
  const isNew = !prop;
  formSheet({
    title: isNew ? "집 추가" : `${prop.name} 고치기`,
    intro: isNew ? "집을 먼저 등록하고, 다음 화면에서 계약(전세·월세·자가)을 넣어요." : null,
    values: { name: prop?.name || "", kind: prop?.kind || "apartment", address_memo: prop?.address_memo || "", memo: prop?.memo || "" },
    fields: (v) => [
      h(
        "div",
        { class: "form-grid" },
        input(v, "name", "이름 *", { maxlength: 40, placeholder: "예: 지금 사는 집" }),
        select(v, "kind", "종류", options.property_kinds.map((o) => [o.code, o.label])),
      ),
      input(v, "address_memo", "주소 메모", { maxlength: 100, placeholder: "예: OO동 아파트 (동·호수는 적지 않아도 돼요)" }),
      input(v, "memo", "메모", { maxlength: 100 }),
    ],
    send: (v) => {
      const body = { name: v.name.trim(), kind: v.kind, address_memo: v.address_memo.trim(), memo: v.memo.trim() };
      if (isNew) {
        for (const k of ["address_memo", "memo"]) if (!body[k]) delete body[k];
        return api.post("/api/properties", body).then((r) => onDone && onDone(r));
      }
      return api.patch(`/api/properties/${prop.id}`, body).then((r) => onDone && onDone(r));
    },
    done: isNew ? "집을 등록했어요" : "저장했어요",
    submitLabel: isNew ? "등록" : "저장",
  });
}

// --- 계약 ---

/** contract가 없으면 새 계약 (진행 중 계약이 있으면 갱신: 그 계약은 새 계약 전날로 끝난다) */
export function openContractForm(ctx, prop, contract, onDone) {
  const isNew = !contract;
  const renewing = isNew && prop.current_contract;
  const base = contract || (renewing ? prop.current_contract : null);
  formSheet({
    title: isNew ? (renewing ? "새 계약 (갱신·재계약)" : "계약 넣기") : "계약 고치기",
    intro: renewing ? `지금 계약(${renewing.type_label} ${renewing.start_on} ~)은 새 계약 시작 전날로 끝나요. 기록은 남아요.` : null,
    values: {
      owner: contract?.owner || base?.owner || ctx.owner() || ctx.me.slug,
      contract_type: base?.contract_type || "jeonse",
      amount: contract ? String(contract.amount) : renewing ? String(renewing.amount) : "",
      monthly_rent: contract?.monthly_rent ? String(contract.monthly_rent) : renewing?.monthly_rent ? String(renewing.monthly_rent) : "",
      start_on: contract?.start_on || (renewing?.end_on ? nextDay(renewing.end_on) : ""),
      end_on: contract?.end_on || "",
      memo: contract?.memo || "",
    },
    fields: (v, draw) => [
      seg(v, "owner", ctx.members.map((m) => [m.slug, m.name]), draw, "주인 (보증금·매입가를 낸 사람)"),
      seg(v, "contract_type", [["jeonse", "전세"], ["monthly", "월세"], ["own", "자가"]], draw, "계약 종류"),
      h("p", { class: "hint" }, TYPE_HINT[v.contract_type]),
      h(
        "div",
        { class: "form-grid" },
        input(v, "amount", v.contract_type === "own" ? "매입가 (원) *" : "보증금 (원) *", { maxlength: 13, inputmode: "numeric", placeholder: "예: 300000000" }),
        v.contract_type === "monthly" ? input(v, "monthly_rent", "월세 (원)", { maxlength: 10, inputmode: "numeric", placeholder: "예: 500000" }) : null,
        input(v, "start_on", v.contract_type === "own" ? "산 날 *" : "시작일 *", { maxlength: 10, placeholder: "예: 2025-03-01" }),
        v.contract_type === "own" ? null : input(v, "end_on", "만기일", { maxlength: 10, placeholder: "예: 2027-02-28" }),
      ),
      v.contract_type === "own" ? null : h("p", { class: "hint" }, "만기일을 넣으면 6개월 전부터 2개월 전까지 '갱신 의사를 알리는 기간'을 알려 드려요."),
      input(v, "memo", "메모", { maxlength: 100 }),
    ],
    send: (v) => {
      const own = v.contract_type === "own";
      const body = {
        owner: v.owner,
        contract_type: v.contract_type,
        amount: num(v.amount),
        start_on: v.start_on.trim(),
        end_on: own ? "" : v.end_on.trim(),
        monthly_rent: v.contract_type === "monthly" ? num(v.monthly_rent) ?? 0 : 0,
        memo: v.memo.trim(),
      };
      if (isNew) {
        for (const k of ["end_on", "memo"]) if (!body[k]) delete body[k];
        if (!body.monthly_rent) delete body.monthly_rent;
        return api.post(`/api/properties/${prop.id}/contracts`, body).then((r) => onDone && onDone(r));
      }
      return api.patch(`/api/property-contracts/${contract.id}`, body).then((r) => onDone && onDone(r));
    },
    done: isNew ? "계약을 넣었어요" : "저장했어요",
    submitLabel: isNew ? "넣기" : "저장",
  });
}

export function openEndContractSheet(contract, onDone) {
  formSheet({
    title: "계약 끝내기",
    intro: "이사는 그대로 두고 계약만 끝낼 때 써요 (예: 집을 팔았는데 아직 이사 전). 끝난 날의 달 말부터 자산에서 빠져요.",
    values: { day: contract.end_on && contract.end_on < today() ? contract.end_on : today() },
    fields: (v) => [input(v, "day", "끝난 날", { maxlength: 10, placeholder: "예: 2026-10-15" })],
    send: (v) => api.post(`/api/property-contracts/${contract.id}/end`, { ended_on: v.day.trim() }).then((r) => onDone && onDone(r)),
    done: "계약을 끝냈어요",
    submitLabel: "끝내기",
  });
}

// --- 시세 · 이사 나감 ---

export function openEstateValuationSheet(options, prop, onDone) {
  formSheet({
    title: "시세 넣기",
    intro: "자가일 때 그 달부터 이 값을 자산 가치로 써요. 국토부 실거래가나 시세 사이트에서 본 값을 넣어요.",
    values: { month: thisMonth(), value: "", source: "lookup", memo: "" },
    fields: (v) => [
      h(
        "div",
        { class: "form-grid" },
        input(v, "month", "달", { maxlength: 7, placeholder: "예: 2026-10" }),
        input(v, "value", "시세 (원)", { maxlength: 13, inputmode: "numeric", placeholder: "예: 520000000" }),
      ),
      select(v, "source", "어디서", options.valuation_sources.map((o) => [o.code, o.label])),
      input(v, "memo", "메모", { maxlength: 100, placeholder: "예: 같은 단지 84㎡ 9월 거래" }),
    ],
    send: (v) =>
      api
        .put(`/api/properties/${prop.id}/valuations/${encodeURIComponent(v.month.trim())}`, { value: num(v.value), source: v.source, memo: v.memo.trim() || null })
        .then((r) => onDone && onDone(r)),
    done: "시세를 넣었어요",
  });
}

export function openMoveOutSheet(prop, onDone) {
  const linked = (prop.fixed_items || []).filter((f) => !f.ended);
  const note = linked.length ? ` 이 집에 연결된 고정비(${linked.map((f) => f.name).join(", ")})는 그대로예요. 끝났으면 내역 › 고정비에서 마감해 주세요.` : "";
  formSheet({
    title: "이사 나감",
    intro: `지우지 않고 이사 나간 날만 적어요. 진행 중인 계약도 그날 끝나고, 그 달 말부터 순자산에서 빠져요(돌려받은 보증금은 통장에 들어오니까).${note}`,
    values: { day: today() },
    fields: (v) => [input(v, "day", "이사 나간 날", { maxlength: 10, placeholder: "예: 2026-10-15" })],
    send: (v) => api.post(`/api/properties/${prop.id}/move-out`, { moved_out_on: v.day.trim() }).then((r) => onDone && onDone(r)),
    done: "이사 나감을 적었어요",
    submitLabel: "적기",
  });
}

function nextDay(day) {
  const d = new Date(`${day}T00:00:00`);
  d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}