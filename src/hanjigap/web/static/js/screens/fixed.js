// 고정비 (G6): 내역 탭의 '고정비' 칸. 그달 고정비가 빠져나갔는지 · 합계 · 등록·수정·마감
// 데이터: /api/fixed-status (볼 때마다 거래와 맞춰 본 결과), /api/fixed-items (마감한 것)
// 목록은 확인할 것부터: 안 빠져나감 → 금액 다름 → 정산 전·예정 → 빠져나감 (같으면 날짜순)

import { api } from "../api.js";
import { h, replace } from "../dom.js";
import { dayLabel, monthLabel, monthShort, won } from "../format.js";
import { openFixedSheet, statusChip } from "../fixedsheet.js";

const ORDER = { missed: 0, different: 1, pending: 2, upcoming: 3, paid: 4 };
const COUNT_TEXT = [
  ["paid", "빠져나감"],
  ["different", "금액 다름"],
  ["missed", "안 빠져나감"],
  ["pending", "정산 전"],
  ["upcoming", "예정"],
];

const state = { showEnded: false };

/** top(month, settledMonths): 내역 화면의 머리줄·보기·탭 줄 */
export async function renderFixed(root, ctx, isCurrent, top) {
  const params = { month: ctx.month, owner: ctx.owner() };
  const [report, months, ended] = await Promise.all([
    api.get("/api/fixed-status", params),
    api.get("/api/months"),
    state.showEnded ? api.get("/api/fixed-items", { owner: ctx.owner(), include_ended: true }) : null,
  ]);
  if (!isCurrent()) return;
  const settled = months.items.map((m) => m.month).sort();
  const refresh = () => ctx.refresh();
  const items = report.items
    .slice()
    .sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.expected_date.localeCompare(b.expected_date));
  replace(
    root,
    ...top(report.month, settled),
    summary(report),
    listCard(ctx, report, items, refresh),
    endedCard(ctx, ended, refresh),
  );
}

function summary(r) {
  const c = r.counts;
  const parts = COUNT_TEXT.filter(([k]) => c[k]).map(([k, label]) => statusChip(k, `${label} ${c[k]}`));
  return h(
    "section",
    { class: "card", aria: { label: "고정비 요약" } },
    h(
      "div",
      { class: "stack-4" },
      h("span", { class: "k" }, `${monthShort(r.month)} 고정비 · ${r.items.length}건`),
      h("div", { class: "big-num" }, h("span", { class: "num" }, won(r.total)), h("span", { class: "unit" }, "원")),
    ),
    h(
      "div",
      { class: "pair two" },
      h("div", { class: "stack-2" }, h("span", { class: "k" }, "생활 고정비"), h("span", { class: "v" }, `${won(r.total_living)}원`)),
      h("div", { class: "stack-2" }, h("span", { class: "k" }, "저축·상환"), h("span", { class: "v" }, `${won(r.total_saving)}원`)),
    ),
    parts.length ? h("div", { class: "chips" }, parts) : null,
    h("p", { class: "hint" }, "등록한 금액 기준이에요. 실제 나간 금액은 각 항목을 눌러 보세요."),
  );
}

function listCard(ctx, r, items, refresh) {
  const showWho = ctx.view === "all";
  return h(
    "section",
    { class: "card tx-card", aria: { label: "고정비 목록" } },
    h(
      "div",
      { class: "card-head center" },
      h("h2", {}, `${monthShort(r.month)} 고정비`),
      h("button", { class: "chip-btn", type: "button", onclick: () => openFixedSheet(ctx, { onChange: refresh }) }, "+ 고정비 추가"),
    ),
    items.length === 0
      ? h(
          "p",
          { class: "empty pad" },
          "이 달에 해당하는 고정비가 없어요. 매달 나가는 거래를 내역에서 눌러 '이 거래를 고정비로 등록'하면 쉽게 넣을 수 있어요.",
        )
      : null,
    items.map((s) => {
      const it = s.item;
      const gap = s.amount_gap;
      return h(
        "button",
        { class: "tx fixed-row", type: "button", onclick: () => openFixedSheet(ctx, { status: s, onChange: refresh }) },
        h(
          "span",
          { class: "body" },
          h(
            "span",
            { class: "line1" },
            h("span", { class: "text" }, it.name),
            showWho ? h("span", { class: "chip small" }, ctx.nameOf(it.owner)) : null,
          ),
          h("span", { class: "meta" }, [`${dayLabel(s.expected_date)}`, it.method_label, it.pay_card?.name || it.pay_account?.name, it.category_label].filter(Boolean).join(" · ")),
        ),
        h(
          "span",
          { class: "fixed-right" },
          h("span", { class: "amount" }, `${won(it.amount)}원`),
          statusChip(s.status, gap ? `${s.status_label} ${gap > 0 ? "+" : "−"}${won(gap)}` : s.status_label),
        ),
      );
    }),
  );
}

function endedCard(ctx, ended, refresh) {
  const toggle = h(
    "button",
    {
      class: "link-btn muted-link", type: "button",
      onclick: () => {
        state.showEnded = !state.showEnded;
        refresh();
      },
    },
    state.showEnded ? "마감한 고정비 숨기기" : "마감한 고정비 보기",
  );
  if (!ended) return h("div", { class: "center-row" }, toggle);
  const list = ended.items.filter((i) => i.ended);
  return h(
    "section",
    { class: "card tx-card", aria: { label: "마감한 고정비" } },
    h("div", { class: "card-head center" }, h("h2", {}, `마감한 고정비 ${list.length}`), toggle),
    list.length === 0 ? h("p", { class: "empty pad" }, "마감한 고정비가 없어요.") : null,
    list.map((i) =>
      h(
        "button",
        { class: "tx", type: "button", onclick: () => openFixedSheet(ctx, { item: i, onChange: refresh }) },
        h("span", { class: "body" }, h("span", { class: "text" }, i.name), h("span", { class: "meta" }, `${i.schedule_label} · ${monthLabel(i.end_month)}까지`)),
        h("span", { class: "amount muted" }, `${won(i.amount)}원`),
      ),
    ),
  );
}