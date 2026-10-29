// 고정비 시트 (G6): 이번 달 상태 · 찾은 거래 · 찾는 조건 · 등록·수정 · 마감
//   openFixedSheet(ctx, { status })   현황 목록에서 (status = /api/fixed-status 항목)
//   openFixedSheet(ctx, { item })     마감한 고정비 목록에서 (/api/fixed-items 항목)
//   openFixedSheet(ctx, { draft })    새로 등록 (draft = 미리 채울 값, 거래에서 만든 초안 포함)
// 무언가 바뀌었으면 닫을 때 onChange().

import { api, ApiError } from "./api.js";
import { h, toast } from "./dom.js";
import { dayLabel, monthLabel, won } from "./format.js";
import { openSheet } from "./sheet.js";

const STATUS_TONE = { paid: "ok", different: "warn", missed: "bad", pending: "wait", upcoming: "wait" };

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function statusChip(status, label) {
  return h("span", { class: `chip small status-${STATUS_TONE[status] || "wait"}` }, label);
}

export async function openFixedSheet(ctx, { status = null, item = null, draft = null, note = null, already = null, onChange } = {}) {
  const current = status ? status.item : item;
  let mode = current ? "view" : "edit";
  let changed = false;
  let error = null;
  let busy = false;
  let confirmEnd = false;
  let endMonth = thisMonth();
  const sheet = openSheet({
    title: current ? current.name : "고정비 추가",
    onClose: () => changed && onChange && onChange(),
  });
  sheet.setBody(h("p", { class: "loading" }, "불러오는 중…"));
  let lists;
  try {
    const [options, accounts, cards, vehicles, properties] = await Promise.all([
      api.get("/api/options"),
      api.get("/api/accounts"),
      api.get("/api/cards"),
      api.get("/api/vehicles"),
      api.get("/api/properties"),
    ]);
    lists = { options, accounts: accounts.items, cards: cards.items, vehicles: vehicles.items, properties: properties.items };
  } catch (e) {
    sheet.setBody(h("p", { class: "form-error", role: "alert" }, e instanceof ApiError ? e.message : "불러오지 못했어요"));
    return;
  }

  // 입력 칸 값 (글자로 들고 있다가 저장할 때 바꾼다)
  const src = current || draft || {};
  const v = {
    owner: src.owner || ctx.owner() || ctx.me.slug,
    name: src.name || "",
    category: src.category || "other",
    amount: src.amount ? String(src.amount) : "",
    pay_day: src.pay_day ? String(src.pay_day) : "",
    method: src.method || "auto_transfer",
    pay_account_id: String(src.pay_account?.id ?? src.pay_account_id ?? ""),
    pay_card_id: String(src.pay_card?.id ?? src.pay_card_id ?? ""),
    start_month: src.start_month || thisMonth(),
    match_text: src.match_text || "",
    match_days: String(src.match_days ?? 3),
    amount_mode: src.amount_mode || "fixed",
    cycle: src.cycle || "monthly",
    pay_month: src.pay_month ? String(src.pay_month) : "",
    vehicle_id: String(src.vehicle?.id ?? src.vehicle_id ?? ""),
    property_id: String(src.property?.id ?? src.property_id ?? ""),
    memo: src.memo || "",
  };
  const showMore = Boolean(current && (current.match_text || current.amount_mode === "variable" || current.cycle === "yearly" || current.vehicle || current.property || current.memo));

  function draw() {
    if (!sheet.isOpen) return;
    if (mode === "view") sheet.setBody(...viewBody());
    else sheet.setBody(...formBody());
  }

  // --- 보기 ---

  function viewBody() {
    const it = current;
    const payWith = it.pay_card ? `카드 · ${it.pay_card.name}` : it.pay_account ? `계좌 · ${it.pay_account.name}` : "계좌·카드 없음";
    return [
      h(
        "div",
        { class: "sheet-top" },
        h("div", { class: "sheet-amount" }, `${won(it.amount)}원`, it.amount_mode === "variable" ? h("span", { class: "unit-note" }, " 안팎 (변동)") : null),
        h("span", { class: "meta" }, [it.schedule_label, it.method_label, payWith, ctx.nameOf(it.owner)].join(" · ")),
        h("span", { class: "meta" }, `${it.category_label} · ${monthLabel(it.start_month)}부터${it.end_month ? ` ${monthLabel(it.end_month)}까지` : ""}`),
      ),
      status ? statusSection(status) : null,
      h(
        "section",
        { class: "sheet-sec" },
        h("h3", {}, "거래 찾는 조건"),
        h(
          "dl",
          { class: "facts" },
          fact("결제수단", it.pay_card || it.pay_account ? `${payWith}에 연결된 뱅샐 결제수단 (연결이 없으면 상관없음)` : "상관없음"),
          fact("날짜", `예정일 ±${it.match_days}일`),
          fact("내용", it.match_text ? `「${it.match_text}」 글자가 들어간 거래` : "따지지 않음 (대신 금액이 맞아야 해요)"),
          fact("금액", it.amount_mode === "variable" ? `${won(it.amount)}원 ±30%` : `${won(it.amount)}원`),
        ),
        it.memo ? h("p", { class: "hint" }, `메모: ${it.memo}`) : null,
        it.vehicle || it.property ? h("p", { class: "hint" }, `관련: ${[it.vehicle?.name, it.property?.name].filter(Boolean).join(", ")}`) : null,
      ),
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
          "고치기",
        ),
      ),
      endSection(it),
      error ? h("p", { class: "form-error", role: "alert" }, error) : null,
    ];
  }

  function statusSection(s) {
    const gap = s.amount_gap;
    return h(
      "section",
      { class: "sheet-sec" },
      h("div", { class: "sec-head" }, h("h3", {}, `${monthLabel(s.expected_date.slice(0, 7))} 상태`), statusChip(s.status, s.status_label)),
      h("p", { class: "hint" }, `예정일 ${dayLabel(s.expected_date)}`),
      s.tx
        ? h(
            "button",
            {
              class: "tx found-tx", type: "button",
              onclick: async () => {
                sheet.close();
                const { openTxSheet } = await import("./txsheet.js");
                openTxSheet(ctx, s.item.owner, s.tx.tx_key, { onChange: () => ctx.refresh() });
              },
            },
            h("span", { class: "body" }, h("span", { class: "line1" }, h("span", { class: "text" }, s.tx.content || "(내용 없음)")), h("span", { class: "meta" }, `${dayLabel(s.tx.date)} · 찾은 거래 · 눌러서 보기`)),
            h("span", { class: "amount" }, `${won(-s.tx.amount)}원`),
          )
        : null,
      gap ? h("p", { class: `note-box${gap > 0 ? "" : " ok"}` }, `기준 금액보다 ${gap > 0 ? "+" : "−"}${won(gap)}원 ${gap > 0 ? "더" : "덜"} 나갔어요.`) : null,
      s.status === "missed"
        ? h("p", { class: "note-box" }, "정산한 달인데 조건에 맞는 거래를 못 찾았어요. 실제로 안 나갔거나, 아래 조건(결제수단·날짜·내용)이 실제 거래와 다를 수 있어요. 다르면 '고치기'에서 바꿔 주세요.")
        : null,
      s.status === "pending" ? h("p", { class: "hint" }, "이 달 파일을 아직 정산하지 않았어요.") : null,
      s.status === "upcoming" ? h("p", { class: "hint" }, "아직 날짜가 지나지 않았어요.") : null,
    );
  }

  function endSection(it) {
    if (it.end_month) {
      return h(
        "section",
        { class: "sheet-sec" },
        h("p", { class: "hint" }, `${monthLabel(it.end_month)}까지로 마감했어요.`),
        h("button", { class: "btn secondary small", type: "button", onclick: () => save({ end_month: "" }, "마감을 취소했어요") }, "마감 취소"),
      );
    }
    if (!confirmEnd) {
      return h(
        "button",
        {
          class: "link-btn danger", type: "button",
          onclick: () => {
            confirmEnd = true;
            draw();
          },
        },
        "고정비 마감하기 (해지·완납)",
      );
    }
    return h(
      "section",
      { class: "sheet-sec" },
      h("p", { class: "note-box" }, "지우지 않고 마지막 달만 적어요. 그 다음 달부터 목록과 합계에서 빠져요."),
      h(
        "label",
        { class: "field" },
        h("span", { class: "k" }, "마지막 달"),
        h("input", { class: "text-input", type: "text", value: endMonth, maxlength: 7, placeholder: "예: 2026-10", oninput: (e) => { endMonth = e.target.value.trim(); } }),
      ),
      h(
        "div",
        { class: "pick-actions" },
        h("button", { class: "btn secondary", type: "button", onclick: () => { confirmEnd = false; draw(); } }, "그만두기"),
        h(
          "button",
          {
            class: "btn grow danger-btn", type: "button", disabled: busy,
            onclick: () => run(() => api.post(`/api/fixed-items/${it.id}/end`, { end_month: endMonth }), "마감했어요"),
          },
          "마감",
        ),
      ),
    );
  }

  // --- 등록·수정 ---

  function formBody() {
    const o = lists.options;
    const pick = (list) => list.map((x) => [x.code, x.label]);
    const accounts = lists.accounts.filter((a) => a.owner === v.owner);
    const cards = lists.cards.filter((c) => c.owner === v.owner);
    const field = (key, label, attrs = {}) =>
      h(
        "label",
        { class: "field" },
        h("span", { class: "k" }, label),
        h("input", {
          class: "text-input", type: "text", value: v[key], autocomplete: "off", ...attrs,
          oninput: (e) => {
            v[key] = e.target.value;
          },
        }),
      );
    const select = (key, label, choices, redraw = false) =>
      h(
        "label",
        { class: "field" },
        h("span", { class: "k" }, label),
        h(
          "select",
          {
            class: "text-input",
            onchange: (e) => {
              v[key] = e.target.value;
              if (redraw) draw();
            },
          },
          choices.map(([code, text]) => h("option", { value: code, selected: String(code) === String(v[key]) }, text)),
        ),
      );
    const seg = (key, choices, onpick) =>
      h(
        "div",
        { class: "seg", role: "group" },
        choices.map(([code, text]) =>
          h(
            "button",
            {
              type: "button", aria: { pressed: v[key] === code },
              onclick: () => {
                v[key] = code;
                if (onpick) onpick();
                draw();
              },
            },
            text,
          ),
        ),
      );
    const methodNeeds = v.method === "card_auto" ? "card" : v.method === "auto_transfer" ? "account" : "optional";
    return [
      note ? h("p", { class: "note-box ok" }, note) : null,
      already ? h("p", { class: "note-box" }, `이 거래는 이미 「${already.name}」 고정비와 짝지어져 있어요. 같은 고정비라면 새로 등록하지 않아도 돼요.`) : null,
      h(
        "div",
        { class: "field" },
        h("span", { class: "k" }, "담당 (빠져나가는 계좌·카드의 주인)"),
        seg("owner", ctx.members.map((m) => [m.slug, m.name]), () => {
          v.pay_account_id = "";
          v.pay_card_id = "";
        }),
      ),
      h(
        "div",
        { class: "form-grid" },
        field("name", "이름 *", { maxlength: 50, placeholder: "예: 휴대폰 요금" }),
        select("category", "분류", pick(o.fixed_categories)),
        field("amount", "금액 (원) *", { inputmode: "numeric", maxlength: 12, placeholder: "예: 55000" }),
        field("pay_day", "출금일 (매달) *", { inputmode: "numeric", maxlength: 2, placeholder: "예: 14" }),
      ),
      h(
        "div",
        { class: "field" },
        h("span", { class: "k" }, "출금 방법"),
        seg("method", pick(o.fixed_methods)),
      ),
      methodNeeds === "card"
        ? select("pay_card_id", "카드 *", [["", cards.length ? "고르세요" : "등록한 카드가 없어요"], ...cards.map((c) => [String(c.id), c.name])])
        : select("pay_account_id", methodNeeds === "account" ? "계좌 *" : "계좌 (있으면)", [["", accounts.length ? (methodNeeds === "account" ? "고르세요" : "고르지 않음") : "등록한 계좌가 없어요"], ...accounts.map((a) => [String(a.id), a.name])]),
      (methodNeeds === "card" && !cards.length) || (methodNeeds === "account" && !accounts.length)
        ? h("p", { class: "hint warn" }, "자산 › 계좌·카드 관리에서 먼저 등록해 주세요. 계좌·카드 없이 넣으려면 '직접 이체'를 고르세요.")
        : null,
      field("start_month", "시작 달 *", { maxlength: 7, placeholder: "예: 2026-10" }),
      h(
        "details",
        { class: "more-fields", open: showMore || Boolean(draft?.match_text) },
        h("summary", {}, "자세히 (찾는 글자 · 날짜 범위 · 변동 · 매년 · 차·집 · 메모)"),
        h(
          "div",
          { class: "stack-12" },
          field("match_text", "거래 찾는 글자", { maxlength: 50, placeholder: "내용에 들어가는 글자 (예: 통신)" }),
          h("p", { class: "hint" }, "비우면 금액이 맞는 거래를 찾아요. 금액이 자주 바뀌면 글자를 넣는 게 정확해요."),
          h(
            "div",
            { class: "form-grid" },
            field("match_days", "날짜 범위 (±일)", { inputmode: "numeric", maxlength: 2 }),
            select("amount_mode", "금액", pick(o.fixed_amount_modes)),
            select("cycle", "주기", pick(o.fixed_cycles), true),
            v.cycle === "yearly" ? field("pay_month", "출금 월 *", { inputmode: "numeric", maxlength: 2, placeholder: "예: 3" }) : null,
          ),
          lists.vehicles.length ? select("vehicle_id", "관련 차", [["", "없음"], ...lists.vehicles.map((x) => [String(x.id), x.name])]) : null,
          lists.properties.length ? select("property_id", "관련 집", [["", "없음"], ...lists.properties.map((x) => [String(x.id), x.name])]) : null,
          field("memo", "메모", { maxlength: 100 }),
        ),
      ),
      error ? h("p", { class: "form-error", role: "alert" }, error) : null,
      h(
        "div",
        { class: "pick-actions" },
        h(
          "button",
          {
            class: "btn secondary", type: "button",
            onclick: () => {
              if (current) {
                mode = "view";
                error = null;
                draw();
              } else sheet.close();
            },
          },
          "취소",
        ),
        h("button", { class: "btn grow", type: "button", disabled: busy, onclick: submit }, busy ? "저장하는 중…" : current ? "저장" : "등록"),
      ),
    ];
  }

  function body() {
    const num = (s) => Number(String(s).replaceAll(",", "").trim());
    const out = {
      owner: v.owner,
      name: v.name.trim(),
      category: v.category,
      amount: num(v.amount),
      pay_day: num(v.pay_day),
      method: v.method,
      start_month: v.start_month.trim(),
      amount_mode: v.amount_mode,
      cycle: v.cycle,
      match_text: v.match_text.trim(),
      match_days: v.match_days.trim() === "" ? 3 : num(v.match_days),
      memo: v.memo.trim(),
      pay_account_id: v.method === "card_auto" || !v.pay_account_id ? 0 : Number(v.pay_account_id),
      pay_card_id: v.method === "card_auto" && v.pay_card_id ? Number(v.pay_card_id) : 0,
      vehicle_id: v.vehicle_id ? Number(v.vehicle_id) : 0,
      property_id: v.property_id ? Number(v.property_id) : 0,
    };
    if (v.cycle === "yearly") out.pay_month = num(v.pay_month);
    if (!current) {
      // 새로 넣을 때는 빈 값(0, '')을 보내지 않는다
      for (const k of ["pay_account_id", "pay_card_id", "vehicle_id", "property_id"]) if (!out[k]) delete out[k];
      for (const k of ["match_text", "memo"]) if (!out[k]) delete out[k];
    }
    return out;
  }

  function submit() {
    if (!v.name.trim() || !v.amount.trim() || !v.pay_day.trim()) {
      error = "이름, 금액, 출금일은 꼭 넣어 주세요";
      draw();
      return;
    }
    if (current) save(body(), "저장했어요");
    else run(() => api.post("/api/fixed-items", body()), "등록했어요");
  }

  function save(patch, message) {
    run(() => api.patch(`/api/fixed-items/${current.id}`, patch), message);
  }

  // 저장하면 닫는다. 이 달 상태는 서버가 다시 계산해야 해서 목록을 새로 그린다 (onChange).
  async function run(action, message) {
    if (busy) return;
    busy = true;
    error = null;
    draw();
    try {
      await action();
      changed = true;
      busy = false;
      toast(message);
      sheet.close();
    } catch (e) {
      busy = false;
      error = e instanceof ApiError ? e.message : "저장하지 못했어요";
      draw();
    }
  }

  draw();
}

function fact(label, value) {
  return h("div", { class: "fact" }, h("dt", {}, label), h("dd", {}, value));
}