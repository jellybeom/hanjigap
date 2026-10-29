// 홈 (G2): 보기 선택 · 달 고르기 · 순자산 · 소합계 칸 · 알림 · 고정비 · 지출 분류 · 최근 거래
// 데이터: /api/home, /api/months

import { api } from "../api.js";
import { h, icon, replace } from "../dom.js";
import {
  manWon, monthLabel, monthShort, pct, shiftMonth, signedWon, won,
} from "../format.js";
import { ICONS } from "../icons.js";
import { openTxSheet } from "../txsheet.js";
import { monthArrow, neighbors, sparkline, txRow, viewSegment } from "../ui.js";

const ALERT_LINKS = {
  unconfirmed: "#/review",
  fixed_missed: "#/ledger?mode=fixed",
  fixed_different: "#/ledger?mode=fixed",
  negative_category: "#/ledger",
  not_settled: "#/settle",
  renewal: "#/assets",
  inspection: "#/assets",
};
const MAX_CATEGORIES = 6;

export async function render(root, ctx, _screen, isCurrent) {
  const [data, months] = await Promise.all([
    api.get("/api/home", { month: ctx.month, owner: ctx.owner() }),
    api.get("/api/months"),
  ]);
  if (!isCurrent()) return;
  const settledMonths = months.items.map((m) => m.month).sort();
  replace(
    root,
    topbar(ctx),
    viewSegment(ctx),
    monthNav(ctx, data, settledMonths),
    alerts(data.alerts, data.month),
    hero(ctx, data),
    tiles(data),
    fixedRow(data),
    categories(data),
    recent(ctx, data),
  );
}

// --- 머리줄 · 보기 · 달 ---------------------------------------------------------

function topbar(ctx) {
  return h(
    "header",
    { class: "topbar" },
    h("div", { class: "brand" }, icon(ICONS.wallet, 26), h("span", { class: "brand-name" }, "한지갑")),
    h(
      "a",
      { class: "icon-btn", href: "#/settle", aria: { label: "정산 파일 올리기" } },
      icon(ICONS.upload, 20),
    ),
  );
}

function monthNav(ctx, data, settledMonths) {
  const { prev, next } = neighbors(data.month, settledMonths);
  const state = settlementText(ctx, data, settledMonths);
  return h(
    "div",
    { class: "month-nav" },
    monthArrow(ctx, prev, "prev"),
    h(
      "div",
      { class: "label" },
      h("span", { class: "title" }, monthLabel(data.month)),
      state.wait
        ? h("a", { class: "state wait", href: "#/settle" }, state.text)
        : h("span", { class: "state" }, state.text),
    ),
    monthArrow(ctx, next, "next"),
  );
}

function settlementText(ctx, data, settledMonths) {
  const missing = data.settlement.filter((s) => !s.status);
  if (missing.length) {
    return { text: missing.map((s) => `${ctx.nameOf(s.owner)} 정산 전`).join(" · "), wait: true };
  }
  if (data.settlement.every((s) => s.status === "baseline")) {
    return { text: "기준 데이터 (첫 적재)", wait: false };
  }
  const latest = settledMonths[settledMonths.length - 1];
  if (data.month === latest) {
    const next = shiftMonth(latest, 1);
    return { text: `정산 완료 · ${monthShort(next)}분은 ${monthShort(shiftMonth(next, 1))} 초에`, wait: false };
  }
  return { text: "정산 완료", wait: false };
}

// --- 알림 -----------------------------------------------------------------------

function alerts(items, month) {
  if (!items.length) return null;
  return h(
    "section",
    { class: "alerts", aria: { label: "알림" } },
    items.map((a) => {
      // 정산 전 알림은 그 사람·그 달로 바로 파일 고르기
      const href =
        a.kind === "not_settled"
          ? `#/settle?owner=${encodeURIComponent(a.owner)}&month=${month}`
          : a.kind === "inspection" && a.vehicle_id
            ? `#/vehicle?id=${a.vehicle_id}`
            : a.kind === "renewal" && a.property_id
              ? `#/estate?id=${a.property_id}`
              : ALERT_LINKS[a.kind];
      const body = [h("span", { class: "dot", "aria-hidden": "true" }), h("span", { class: "text" }, a.message)];
      if (href) body.push(icon(ICONS.next, 16, { strokeWidth: 2 }));
      return href ? h("a", { class: "alert", href }, body) : h("div", { class: "alert" }, body);
    }),
  );
}

// --- 순자산 ---------------------------------------------------------------------

function hero(ctx, data) {
  const a = data.assets;
  const who = ctx.view === "all" ? "우리 집" : ctx.nameOf(ctx.view);
  const label = `${who} 순자산 · ${monthShort(data.month)} 말 기준`;
  if (!a.has_banksalad) {
    return h(
      "section",
      { class: "hero", aria: { label: "순자산" } },
      h("span", { class: "label" }, label),
      h("div", { class: "big" }, h("span", { class: "num" }, "—")),
      h(
        "p",
        { class: "note" },
        "순자산은 뱅샐 재무현황이 저장된 뒤에 보여요. 다음 정산부터 쌓여요.",
      ),
      a.added > 0
        ? h("p", { class: "note" }, `한지갑에 등록한 차·집 ${won(a.added)}원은 그때 함께 더해요.`)
        : null,
    );
  }
  const prev = a.previous_net_worth;
  const delta = prev === null ? null : a.net_worth - prev;
  const deltaText =
    delta === null
      ? "지난달 기록 없음"
      : `지난달보다 ${signedWon(delta)}원 (${prev ? signedPct(delta, prev) : "—"})`;
  const ratio = a.total_assets ? pct(a.total_liabilities, a.total_assets) : 0;
  return h(
    "section",
    { class: "hero", aria: { label: "순자산" } },
    h(
      "div",
      { style: { display: "flex", "flex-direction": "column", gap: "6px" } },
      h("span", { class: "label" }, label),
      h(
        "div",
        { class: "big" },
        h("span", { class: "num" }, (a.net_worth < 0 ? "−" : "") + won(a.net_worth)),
        h("span", { class: "unit" }, "원"),
      ),
      h("span", { class: "delta" }, deltaText),
    ),
    sparkline(data.trend),
    h("div", { class: "rule" }),
    h(
      "div",
      { class: "pair" },
      pairItem("자산 총계", `${won(a.total_assets)}원`),
      pairItem(`부채 총계 · 자산의 ${ratio}%`, `${won(a.total_liabilities)}원`),
    ),
    a.added > 0 ? h("p", { class: "note" }, `한지갑의 차·집 ${won(a.added)}원 포함`) : null,
    h("a", { class: "hero-link", href: "#/assets" }, "자산 자세히 보기", icon(ICONS.next, 14, { strokeWidth: 2.2 })),
  );
}

function signedPct(delta, base) {
  const value = (delta / Math.abs(base)) * 100;
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}%`;
}

function pairItem(k, v) {
  return h(
    "div",
    { style: { display: "flex", "flex-direction": "column", gap: "2px" } },
    h("span", { class: "k" }, k),
    h("span", { class: "v" }, v),
  );
}

// --- 소합계 칸 -------------------------------------------------------------------

function tiles(data) {
  const s = data.summary;
  const a = data.assets;
  const m = monthShort(data.month);
  const noBank = { value: null, sub: "다음 정산 뒤에 보여요", tone: "" };
  const inv = a.investment;
  const list = [
    {
      label: `${m} 수입`, icon: ICONS.income, color: "pos", href: "#/ledger",
      value: s.income, sub: `지난달보다 ${signedWon(s.income_diff)}원`, tone: s.income_diff >= 0 ? "pos" : "warn",
    },
    {
      label: `${m} 지출`, icon: ICONS.expense, color: "", href: "#/ledger",
      value: s.expense, sub: `지난달보다 ${signedWon(s.expense_diff)}원`, tone: s.expense_diff <= 0 ? "pos" : "warn",
    },
    {
      label: "투자 평가액", icon: ICONS.invest, color: "pos", href: "#/assets",
      ...(a.has_banksalad
        ? {
            value: inv.value,
            sub: inv.rate === null ? `${inv.count}개 상품` : `수익률 ${inv.rate >= 0 ? "+" : "−"}${Math.abs(inv.rate)}% · 원금 ${manWon(inv.principal)}`,
            tone: inv.rate !== null && inv.rate < 0 ? "warn" : "pos",
          }
        : noBank),
    },
    {
      label: `대출 잔액${a.has_banksalad ? ` · ${a.loans.count}건` : ""}`, icon: ICONS.loan, color: "warn", href: "#/assets",
      ...(a.has_banksalad ? { value: a.loans.balance, sub: "뱅샐 대출현황 기준", tone: "" } : noBank),
    },
    {
      label: "현금 · 예금", icon: ICONS.cash, color: "", href: "#/assets",
      ...(a.has_banksalad ? { value: a.cash.amount, sub: `입출금·예적금 ${a.cash.count}개`, tone: "" } : noBank),
    },
    {
      label: `보험${a.has_banksalad ? ` · ${a.insurance.count}건` : ""}`, icon: ICONS.insurance, color: "", href: "#/assets",
      ...(a.has_banksalad ? { value: a.insurance.paid_total, sub: "지금까지 낸 총납입 금액", tone: "" } : noBank),
    },
  ];
  return h(
    "section",
    { class: "tiles", aria: { label: "소합계" } },
    list.map((t) =>
      h(
        "a",
        { class: "tile", href: t.href },
        h(
          "div",
          { class: "head" },
          h("span", { class: `badge-icon ${t.color}` }, icon(t.icon, 18, { strokeWidth: 1.9 })),
          h("span", { class: "label" }, t.label),
        ),
        h(
          "div",
          { class: "value" },
          h("span", { class: "num" }, t.value === null ? "—" : won(t.value)),
          t.value === null ? null : h("span", { class: "unit" }, "원"),
        ),
        h("span", { class: `sub ${t.tone}` }, t.sub),
      ),
    ),
  );
}

// --- 고정비 ---------------------------------------------------------------------

function fixedRow(data) {
  const f = data.fixed;
  const m = monthShort(data.month);
  const missed = f.counts.missed + f.counts.different;
  const text = f.count ? `${f.counts.paid} / ${f.count}건 빠져나감` : "등록한 고정비가 없어요";
  return h(
    "a",
    { class: "row-btn", href: "#/ledger?mode=fixed" },
    h("span", { class: "badge-icon" }, icon(ICONS.calendar, 18, { strokeWidth: 1.9 })),
    h("span", { class: "body" }, h("span", { class: "k" }, `${m} 고정비`), h("span", { class: "v" }, text)),
    missed ? h("span", { class: "chip outline-warn" }, `확인 필요 ${missed}`) : null,
  );
}

// --- 지출 분류 -------------------------------------------------------------------

function categories(data) {
  const m = monthShort(data.month);
  const all = data.expense_by_category;
  const positive = all.filter((c) => c.amount > 0);
  const negative = all.filter((c) => c.amount < 0);
  let rows = positive.slice(0, MAX_CATEGORIES);
  const rest = positive.slice(MAX_CATEGORIES).reduce((sum, c) => sum + c.amount, 0);
  if (rest > 0) rows = [...rows, { category: "그 밖", amount: rest }];
  const total = data.summary.expense;
  const max = Math.max(1, ...rows.map((c) => c.amount));
  return h(
    "section",
    { class: "card", aria: { label: "지출 분류" } },
    h(
      "div",
      { class: "card-head" },
      h("h2", {}, `${m} 지출 분류`),
      h("span", { class: "side" }, `합계 ${total < 0 ? "−" : ""}${won(total)}원`),
    ),
    rows.length === 0 && negative.length === 0 ? h("p", { class: "empty" }, "이 달 지출이 없어요.") : null,
    rows.map((c) =>
      h(
        "div",
        { class: "bar-row" },
        h(
          "div",
          { class: "top" },
          h("span", { class: "name" }, c.category),
          h(
            "span",
            { class: "amt" },
            h("strong", {}, `${won(c.amount)}원`),
            h("span", { class: "muted" }, ` · ${pct(c.amount, total)}%`),
          ),
        ),
        h(
          "div",
          { class: "bar-track" },
          h("div", { class: "bar-fill", style: { width: `${((c.amount / max) * 100).toFixed(1)}%` } }),
        ),
      ),
    ),
    negative.map((c) =>
      h(
        "div",
        { class: "bar-row" },
        h(
          "div",
          { class: "top" },
          h("span", { class: "name" }, c.category),
          h("span", { class: "amt pos" }, h("strong", {}, `−${won(c.amount)}원`), " · 환불이 더 많아요"),
        ),
      ),
    ),
  );
}

// --- 최근 거래 -------------------------------------------------------------------

function recent(ctx, data) {
  const showWho = ctx.view === "all";
  return h(
    "section",
    { class: "card", style: { "padding-bottom": "6px", gap: "0" }, aria: { label: "최근 거래" } },
    h(
      "div",
      { class: "card-head", style: { "align-items": "center", "padding-bottom": "6px" } },
      h("h2", {}, "최근 거래"),
      h("a", { class: "link-btn", href: "#/ledger" }, "전체 보기"),
    ),
    data.recent.length === 0 ? h("p", { class: "empty", style: { padding: "12px 0" } }, "이 달 거래가 없어요.") : null,
    h(
      "div",
      { class: "tx-list" },
      data.recent.map((x) =>
        txRow(ctx, x, {
          showWho, meta: metaLabel(x),
          onclick: () => openTxSheet(ctx, x.owner, x.tx_key, { onChange: () => ctx.refresh() }),
        }),
      ),
    ),
  );
}

function metaLabel(x) {
  if (x.cls === "expense" || x.cls === "income") return x.display_category || x.class_label;
  return x.class_label;
}