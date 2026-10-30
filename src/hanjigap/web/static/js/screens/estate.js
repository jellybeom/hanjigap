// 집 상세 (G7b): 지금 자산 가치 · 만기·갱신 안내 · 지금 계약 · 계약 이력 · 시세 기록 · 관련 고정비 · 이사 나감
// 주소: #/estate?id=1
// 데이터: /api/properties/{id} (계약·시세·고정비 포함), /api/options

import { api, ApiError } from "../api.js";
import { lineChart } from "../charts.js";
import { h, icon, replace, toast } from "../dom.js";
import {
  openContractForm, openEndContractSheet, openEstateValuationSheet, openMoveOutSheet, openPropertyForm,
} from "../estatesheet.js";
import { monthLabel, won } from "../format.js";
import { openFixedSheet } from "../fixedsheet.js";
import { ICONS } from "../icons.js";
import { cols } from "../ui.js";

const STAGE_TONE = { before: "wait", notice: "warn", closing: "bad", overdue: "bad" };

export async function render(root, ctx, _screen, isCurrent) {
  const id = Number(new URLSearchParams(window.location.hash.split("?")[1] || "").get("id"));
  if (!id) {
    replace(root, h("div", { class: "error-box" }, h("p", { class: "msg" }, "집을 찾을 수 없어요"), h("a", { class: "link-btn", href: "#/assets" }, "자산으로")));
    return;
  }
  const [p, options] = await Promise.all([api.get(`/api/properties/${id}`), api.get("/api/options")]);
  if (!isCurrent()) return;
  const refresh = () => ctx.refresh();
  const c = p.current_contract;
  replace(
    root,
    h(
      "header",
      { class: "topbar" },
      h("a", { class: "icon-btn plain edge-left", href: "#/assets", aria: { label: "자산으로" } }, icon(ICONS.prev, 22, { strokeWidth: 2 })),
      h("h1", { class: "bar-title" }, p.name),
      h("button", { class: "icon-btn plain edge-right", type: "button", aria: { label: "집 정보 고치기" }, onclick: () => openPropertyForm(options, p, refresh) }, icon(ICONS.edit, 20, { strokeWidth: 2 })),
    ),
    cols(
      [valueCard(ctx, p), c && c.renewal ? renewalCard(c) : null, contractCard(ctx, p, refresh), historyCard(ctx, p, refresh)],
      [valuationCard(options, p, refresh), fixedCard(ctx, p, refresh), manageRow(p, refresh)],
    ),
  );
}

// --- 자산 가치 · 만기 -------------------------------------------------------------------

function valueCard(ctx, p) {
  const v = p.value;
  const c = p.current_contract;
  return h(
    "section",
    { class: "hero", aria: { label: "자산 가치" } },
    h(
      "div",
      { class: "stack-6" },
      h("span", { class: "label" }, p.moved_out_on ? `이사 나감 · ${p.moved_out_on} · 순자산에서 빠짐` : v ? `${monthLabel(v.year_month)} 자산 가치` : "자산 가치"),
      h("div", { class: "big" }, h("span", { class: "num" }, v ? won(v.value) : "—"), v ? h("span", { class: "unit" }, "원") : null),
      v ? h("span", { class: "delta" }, v.label) : h("span", { class: "delta" }, p.moved_out_on ? "지난 계약은 아래 이력에 남아 있어요" : "진행 중인 계약이 없어요"),
    ),
    h("div", { class: "rule" }),
    h(
      "div",
      { class: "pair" },
      h("div", { class: "stack-2" }, h("span", { class: "k" }, "주인"), h("span", { class: "v" }, v ? ctx.nameOf(v.owner) : c ? ctx.nameOf(c.owner) : "없음")),
      h("div", { class: "stack-2" }, h("span", { class: "k" }, "종류"), h("span", { class: "v" }, p.kind_label)),
    ),
    p.address_memo ? h("p", { class: "note" }, p.address_memo) : null,
  );
}

function renewalCard(c) {
  const r = c.renewal;
  const days = r.days_left;
  return h(
    "section",
    { class: `card renewal ${STAGE_TONE[r.stage]}`, aria: { label: "만기 안내" } },
    h(
      "div",
      { class: "card-head center" },
      h("h2", {}, days >= 0 ? `만기까지 ${won(days)}일` : `만기 ${won(-days)}일 지남`),
      h("span", { class: `chip small status-${STAGE_TONE[r.stage]}` }, r.stage_label),
    ),
    h(
      "dl",
      { class: "facts" },
      fact("만기일", r.end_on),
      fact("갱신 의사 알리는 기간", `${r.notice_from} ~ ${r.notice_until}`),
    ),
    h(
      "p",
      { class: "hint" },
      r.stage === "notice"
        ? "지금이 집주인에게 갱신 여부를 알리는 기간이에요. 이 기간에 아무 말이 없으면 같은 조건으로 묵시적 갱신이 될 수 있어요."
        : r.stage === "closing"
          ? "갱신 의사를 알리는 기간이 지났어요. 재계약했다면 아래 '새 계약'으로 넣어 주세요."
          : r.stage === "overdue"
            ? "만기가 지났어요. 재계약했으면 '새 계약', 이사 나갔으면 '이사 나감'으로 정리해 주세요."
            : "만기 6개월 전부터 2개월 전까지가 갱신 의사를 알리는 기간이에요 (주택임대차보호법).",
    ),
  );
}

// --- 계약 ---------------------------------------------------------------------------

function contractCard(ctx, p, refresh) {
  const c = p.current_contract;
  if (p.moved_out_on) return null;
  if (!c) {
    return h(
      "section",
      { class: "card", aria: { label: "지금 계약" } },
      h("h2", { class: "q-title" }, "계약을 넣어 주세요"),
      h("p", { class: "hint" }, "전세·월세는 보증금이, 자가는 매입가(시세를 넣으면 시세)가 이 집의 자산 가치가 돼요."),
      h("button", { class: "btn", type: "button", onclick: () => openContractForm(ctx, p, null, refresh) }, "계약 넣기"),
    );
  }
  // 미리 넣어 둔 갱신 계약은 시작일 전까지 '다음 계약' (그때까지는 이전 계약이 자산 가치)
  const upcoming = c.start_on > todayText();
  return h(
    "section",
    { class: "card", aria: { label: "지금 계약" } },
    h("div", { class: "card-head" }, h("h2", {}, upcoming ? "다음 계약" : "지금 계약"), h("span", { class: "side" }, upcoming ? `${c.start_on}부터 · ${c.type_label}` : c.type_label)),
    upcoming ? h("p", { class: "hint" }, "아직 시작 전이에요. 시작일 전까지는 이전 계약이 자산 가치예요.") : null,
    h(
      "dl",
      { class: "facts" },
      fact("주인", ctx.nameOf(c.owner)),
      fact(c.amount_label, `${won(c.amount)}원`),
      c.monthly_rent ? fact("월세", `${won(c.monthly_rent)}원`) : null,
      fact("기간", c.end_on ? `${c.start_on} ~ ${c.end_on}` : `${c.start_on}부터`),
      c.memo ? fact("메모", c.memo) : null,
    ),
    h(
      "div",
      { class: "sec-actions" },
      h("button", { class: "btn small", type: "button", onclick: () => openContractForm(ctx, p, null, refresh) }, c.contract_type === "own" ? "새 계약" : "새 계약 (갱신)"),
      h("button", { class: "btn secondary small", type: "button", onclick: () => openContractForm(ctx, p, c, refresh) }, "고치기"),
      h("button", { class: "link-btn", type: "button", onclick: () => openEndContractSheet(c, refresh) }, "계약만 끝내기"),
    ),
  );
}

function historyCard(ctx, p, refresh) {
  const past = p.contracts.filter((c) => !c.open);
  if (!past.length) return null;
  // 진행 중 계약이 없고 이사 나간 집도 아니면 (이사 나감을 취소한 경우 등) 마지막 계약을 되살릴 수 있게
  const reopenable = !p.current_contract && !p.moved_out_on ? past[past.length - 1] : null;
  return h(
    "section",
    { class: "card", aria: { label: "계약 이력" } },
    h("div", { class: "card-head" }, h("h2", {}, "지난 계약"), h("span", { class: "side" }, `${past.length}건`)),
    h(
      "div",
      { class: "detail-list" },
      past
        .slice()
        .reverse()
        .map((c) =>
          h(
            "div",
            { class: "detail" },
            h(
              "span",
              { class: "item-name" },
              h("span", { class: "nm" }, `${c.type_label} · ${c.start_on} ~ ${c.ended_on || c.end_on || ""}`),
              h("span", { class: "muted small" }, [ctx.nameOf(c.owner), c.monthly_rent ? `월세 ${won(c.monthly_rent)}원` : null, c.memo].filter(Boolean).join(" · ")),
            ),
            h(
              "span",
              { class: "detail-amt" },
              h("strong", {}, `${won(c.amount)}원`),
              c === reopenable
                ? h(
                    "button",
                    {
                      class: "link-btn small-link", type: "button",
                      onclick: async () => {
                        if (await act(() => api.post(`/api/property-contracts/${c.id}/end`, { ended_on: null }), "다시 진행 중인 계약으로 돌렸어요")) refresh();
                      },
                    },
                    "다시 진행 중으로",
                  )
                : h("span", { class: "small" }, c.amount_label),
            ),
          ),
        ),
    ),
  );
}

// --- 시세 · 고정비 · 이사 나감 -----------------------------------------------------------

function valuationCard(options, p, refresh) {
  const own = p.current_contract?.contract_type === "own";
  if (!own && !p.valuations.length) return null;
  const points = p.valuations.map((x) => ({ month: x.year_month, value: x.value, mark: true }));
  return h(
    "section",
    { class: "card", aria: { label: "시세 기록" } },
    h(
      "div",
      { class: "card-head center" },
      h("h2", {}, "시세 기록"),
      p.moved_out_on ? null : h("button", { class: "chip-btn", type: "button", onclick: () => openEstateValuationSheet(options, p, refresh) }, "+ 시세 넣기"),
    ),
    points.length >= 2 ? lineChart(points, { label: `${p.name} 시세 기록` }) : null,
    points.length === 0 ? h("p", { class: "hint" }, "아직 없어요. 시세를 넣기 전까지는 매입가를 자산 가치로 써요.") : null,
    points.length
      ? h(
          "div",
          { class: "detail-list" },
          p.valuations
            .slice()
            .reverse()
            .map((x) =>
              h(
                "div",
                { class: "detail" },
                h("span", { class: "item-name" }, h("span", { class: "nm" }, monthLabel(x.year_month)), h("span", { class: "muted small" }, [x.source_label, x.memo].filter(Boolean).join(" · "))),
                h(
                  "span",
                  { class: "detail-amt" },
                  h("strong", {}, `${won(x.value)}원`),
                  h(
                    "button",
                    {
                      class: "link-btn small-link danger", type: "button",
                      onclick: async () => {
                        if (await act(() => api.del(`/api/properties/${p.id}/valuations/${x.year_month}`), "지웠어요")) refresh();
                      },
                    },
                    "지우기",
                  ),
                ),
              ),
            ),
        )
      : null,
  );
}

function fixedCard(ctx, p, refresh) {
  return h(
    "section",
    { class: "card", aria: { label: "관련 고정비" } },
    h("div", { class: "card-head" }, h("h2", {}, "관련 고정비"), h("span", { class: "side" }, "관리비·월세 등")),
    p.fixed_items.length
      ? h(
          "div",
          { class: "detail-list" },
          p.fixed_items.map((f) =>
            h(
              "button",
              { class: "detail plain-btn", type: "button", onclick: () => openFixedSheet(ctx, { item: f, onChange: refresh }) },
              h("span", { class: "item-name" }, h("span", { class: "nm" }, f.name), h("span", { class: "muted small" }, `${f.schedule_label} · ${f.method_label}${f.ended ? " · 마감" : ""}`)),
              h("span", { class: "detail-amt" }, h("strong", {}, `${won(f.amount)}원`)),
            ),
          ),
        )
      : h("p", { class: "hint" }, "아직 없어요. 내역 › 고정비에서 관리비·월세를 등록할 때 '자세히 › 관련 집'으로 이 집을 고르면 여기에 나와요."),
  );
}

function manageRow(p, refresh) {
  return h(
    "div",
    { class: "center-row" },
    p.moved_out_on
      ? h(
          "button",
          {
            class: "link-btn", type: "button",
            onclick: async () => {
              if (await act(() => api.post(`/api/properties/${p.id}/move-out`, { moved_out_on: null }), "이사 나감을 취소했어요. 계약은 '지난 계약'에서 다시 진행 중으로 돌릴 수 있어요")) refresh();
            },
          },
          "이사 나감 취소 (다시 사는 집으로)",
        )
      : h("button", { class: "link-btn danger", type: "button", onclick: () => openMoveOutSheet(p, refresh) }, "이사 나갔어요 (이사 나간 날 적기)"),
  );
}

function todayText() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function fact(label, value) {
  return h("div", { class: "fact" }, h("dt", {}, label), h("dd", {}, value));
}

async function act(action, message) {
  try {
    await action();
    toast(message);
    return true;
  } catch (e) {
    toast(e instanceof ApiError ? e.message : "하지 못했어요");
    return false;
  }
}