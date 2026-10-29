// 자산 (G5a): 순자산 · 사람별 · 자산 구성(그룹 펼치기) · 부채 · 대출 · 투자 · 보험 · 차·집
// 데이터: /api/assets (뱅샐 재무현황 + 한지갑 차·집, 같은 자산은 두 번 세지 않음)
// 달은 기본이 '각자 가장 최근 기록'. 화살표로 기록이 있는 달을 고를 수 있다.
// 계좌·카드 관리는 #/holdings (G5b), 차·집 상세는 G7.

import { api } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { dayLabel, monthLabel, monthShort, pct, signedWon, won } from "../format.js";
import { ICONS } from "../icons.js";
import { neighbors, sparkline, viewSegment } from "../ui.js";

// 이 화면 안에서만 기억 (다른 탭에 다녀와도 유지)
const state = {
  month: null, // null = 각자 가장 최근 기록
  open: new Set(), // 펼친 묶음 ("asset:자유입출금 자산" 등)
};

const SECTION_TITLES = { asset: "자산 구성", liability: "부채" };

export async function render(root, ctx, _screen, isCurrent) {
  const rerender = () => render(root, ctx, _screen, isCurrent);
  const data = await api.get("/api/assets", { month: state.month, owner: ctx.owner() });
  if (!isCurrent()) return;
  if (state.month && !data.months.includes(state.month)) state.month = null;
  const showWho = ctx.view === "all";
  const items = data.members.flatMap((m) => (m.banksalad ? m.banksalad.items.map((x) => ({ ...x, owner: m.owner })) : []));
  const has = data.breakdown.has_banksalad;
  replace(
    root,
    h("header", { class: "topbar" }, h("h1", { class: "screen-title" }, "자산")),
    viewSegment(ctx),
    monthNav(data, rerender),
    hero(ctx, data),
    showWho ? memberSplit(ctx, data) : null,
    has ? groupCard(ctx, "asset", items, data) : null,
    has ? groupCard(ctx, "liability", items, data) : null,
    has ? investCard(ctx, items, data.breakdown.investment, showWho) : null,
    has ? loanCard(ctx, items, showWho) : null,
    has ? insuranceCard(ctx, items, showWho) : null,
    thingsCard(ctx, data, showWho),
    h(
      "a",
      { class: "row-btn", href: "#/holdings" },
      h("span", { class: "body" }, h("span", { class: "v" }, "계좌·카드 관리"), h("span", { class: "k" }, "등록 · 해지 · 뱅샐 결제수단 연결")),
      icon(ICONS.next, 18, { strokeWidth: 2 }),
    ),
  );
}

// --- 달 ------------------------------------------------------------------------

function monthNav(data, rerender) {
  const months = data.months;
  const current = data.month || data.trend_end;
  const { prev, next } = months.length ? neighbors(current, months) : { prev: null, next: null };
  const go = (m) => {
    state.month = m === months[months.length - 1] ? null : m; // 가장 최근 달이면 '최근 기록'으로
    rerender();
  };
  const asOf = data.members
    .filter((m) => m.banksalad)
    .map((m) => `${m.name} ${dayLabel(m.banksalad.as_of)}`)
    .join(" · ");
  return h(
    "div",
    { class: "month-nav" },
    arrow(prev, "prev", go),
    h(
      "div",
      { class: "label" },
      h("span", { class: "title" }, data.month ? monthLabel(data.month) : "가장 최근 기록"),
      h("span", { class: "state muted-state" }, asOf ? `${asOf} 기준` : "뱅샐 재무현황 없음"),
    ),
    arrow(next, "next", go),
  );
}

function arrow(target, direction, go) {
  const isPrev = direction === "prev";
  return h(
    "button",
    {
      class: "icon-btn plain", type: "button", disabled: !target,
      aria: { label: target ? `${isPrev ? "이전" : "다음"} 기록 (${monthLabel(target)})` : isPrev ? "이전 기록 없음" : "다음 기록 없음" },
      onclick: () => target && go(target),
    },
    icon(isPrev ? ICONS.prev : ICONS.next, 20, { strokeWidth: 2 }),
  );
}

// --- 순자산 ---------------------------------------------------------------------

function hero(ctx, data) {
  const who = ctx.view === "all" ? "우리 집" : ctx.nameOf(ctx.view);
  const b = data.breakdown;
  if (!b.has_banksalad) {
    return h(
      "section",
      { class: "hero", aria: { label: "순자산" } },
      h("span", { class: "label" }, `${who} 순자산`),
      h("div", { class: "big" }, h("span", { class: "num" }, data.added ? won(data.added) : "—"), data.added ? h("span", { class: "unit" }, "원") : null),
      h("p", { class: "note" }, "뱅샐 재무현황이 저장되면 통장·투자·대출까지 함께 보여요. 정산 화면에서 파일을 올리면 저장돼요."),
      data.added ? h("p", { class: "note" }, "지금은 한지갑에 등록한 차·집 값만 있어요.") : null,
    );
  }
  const t = data.trend;
  const last = t[t.length - 1].net_worth;
  const before = t[t.length - 2].net_worth;
  const delta = last !== null && before !== null ? last - before : null;
  const end = data.trend_end;
  const ratio = b.total_assets ? pct(b.total_liabilities, b.total_assets) : 0;
  return h(
    "section",
    { class: "hero", aria: { label: "순자산" } },
    h(
      "div",
      { class: "stack-6" },
      h("span", { class: "label" }, `${who} 순자산`),
      h("div", { class: "big" }, h("span", { class: "num" }, (data.net_worth < 0 ? "−" : "") + won(data.net_worth)), h("span", { class: "unit" }, "원")),
      h(
        "span",
        { class: "delta" },
        delta === null
          ? `${monthShort(end)} 직전 달 기록 없음`
          : `${monthShort(end)} 말, 한 달 전보다 ${signedWon(delta)}원${before ? ` (${signedPct(delta, before)})` : ""}`,
      ),
    ),
    sparkline(t),
    h("div", { class: "rule" }),
    h(
      "div",
      { class: "pair" },
      pairItem("자산 총계", `${won(b.total_assets)}원`),
      pairItem(`부채 총계 · 자산의 ${ratio}%`, `${won(b.total_liabilities)}원`),
    ),
    data.added > 0 ? h("p", { class: "note" }, `한지갑의 차·집 ${won(data.added)}원 포함`) : null,
  );
}

function signedPct(delta, base) {
  const value = (delta / Math.abs(base)) * 100;
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}%`;
}

function pairItem(k, v) {
  return h("div", { class: "stack-2" }, h("span", { class: "k" }, k), h("span", { class: "v" }, v));
}

function memberSplit(ctx, data) {
  const parts = data.members.map((m) => ({ owner: m.owner, value: m.net_worth }));
  const positive = parts.map((p) => Math.max(0, p.value));
  const sum = positive.reduce((a, v) => a + v, 0);
  if (parts.length < 2 || sum <= 0) return null;
  return h(
    "section",
    { class: "card", aria: { label: "사람별 순자산" } },
    h("div", { class: "card-head" }, h("h2", {}, "사람별")),
    h(
      "div",
      { class: "split" },
      h("div", { class: "split-bar" }, parts.map((p, i) => h("div", { class: `split-part p${i}`, style: { width: `${((positive[i] / sum) * 100).toFixed(1)}%` } }))),
      h(
        "div",
        { class: "split-legend" },
        parts.map((p, i) =>
          h("span", { class: "legend" }, h("span", { class: `swatch p${i}`, "aria-hidden": "true" }), `${ctx.nameOf(p.owner)} ${p.value < 0 ? "−" : ""}${won(p.value)}원`),
        ),
      ),
    ),
  );
}

// --- 자산 구성 · 부채 (묶음 펼치기) ------------------------------------------------------

function groupCard(ctx, section, allItems, data) {
  const items = allItems.filter((x) => x.section === section);
  const groups = new Map();
  for (const x of items) {
    const key = x.group || "기타";
    const g = groups.get(key) || { name: key, amount: 0, items: [] };
    g.amount += x.amount || 0;
    g.items.push(x);
    groups.set(key, g);
  }
  if (section === "asset") addThings(groups, data);
  const list = [...groups.values()].sort((a, b) => b.amount - a.amount);
  const total = list.reduce((a, g) => a + Math.max(0, g.amount), 0);
  const max = Math.max(1, ...list.map((g) => g.amount));
  const showWho = ctx.view === "all";
  const card = h(
    "section",
    { class: "card cat-card", aria: { label: SECTION_TITLES[section] } },
    h("div", { class: "card-head" }, h("h2", {}, SECTION_TITLES[section]), h("span", { class: "side" }, `${won(total)}원`)),
    list.length === 0 ? h("p", { class: "empty pad" }, section === "asset" ? "자산 항목이 없어요." : "부채가 없어요.") : null,
    list.map((g) => {
      const key = `${section}:${g.name}`;
      const open = state.open.has(key);
      return h(
        "div",
        { class: "cat-row" },
        h(
          "button",
          {
            class: "cat-btn", type: "button", aria: { expanded: open },
            onclick: () => {
              if (open) state.open.delete(key);
              else state.open.add(key);
              card.replaceWith(groupCard(ctx, section, allItems, data)); // 이 카드만 다시 그리기
            },
          },
          h(
            "span",
            { class: "top" },
            h("span", { class: "name" }, icon(ICONS.next, 14, { strokeWidth: 2.2 }), g.name, h("span", { class: "muted count" }, ` ${g.items.length}`)),
            h("span", { class: "amt" }, h("strong", {}, `${won(g.amount)}원`), h("span", { class: "muted" }, ` · ${pct(g.amount, total)}%`)),
          ),
          g.amount > 0
            ? h("span", { class: "bar-track" }, h("span", { class: `bar-fill${section === "liability" ? " debt" : ""}`, style: { width: `${((g.amount / max) * 100).toFixed(1)}%` } }))
            : null,
        ),
        open
          ? h(
              "div",
              { class: "sub-list" },
              g.items
                .slice()
                .sort((a, b) => (b.amount || 0) - (a.amount || 0))
                .map((x) =>
                  h(
                    "div",
                    { class: "sub-row item-row" },
                    h(
                      "span",
                      { class: "item-name" },
                      h("span", {}, x.name),
                      h("span", { class: "muted small" }, [showWho ? ctx.nameOf(x.owner) : null, x.institution, x.note].filter(Boolean).join(" · ")),
                    ),
                    h("span", { class: "amt" }, h("strong", {}, `${won(x.amount || 0)}원`)),
                  ),
                ),
            )
          : null,
      );
    }),
  );
  return card;
}

/** 한지갑에서 더한 차·집은 '한지갑 차·집' 묶음으로 (뱅샐에 이미 있는 것은 빼고) */
function addThings(groups, data) {
  const things = data.members.flatMap((m) => [
    ...m.vehicles.filter((v) => v.included).map((v) => ({ owner: m.owner, name: v.name, amount: v.value, institution: "차 · 추정 시세" })),
    ...m.estates.filter((e) => e.included).map((e) => ({ owner: m.owner, name: e.name, amount: e.value, institution: `집 · ${e.label}` })),
  ]);
  if (!things.length) return;
  groups.set("한지갑 차·집", { name: "한지갑 차·집", amount: things.reduce((a, x) => a + x.amount, 0), items: things });
}

// --- 투자 · 대출 · 보험 -----------------------------------------------------------

function investCard(ctx, items, sum, showWho) {
  const list = items.filter((x) => x.section === "investment").sort((a, b) => (b.amount || 0) - (a.amount || 0));
  if (!list.length) return null;
  return h(
    "section",
    { class: "card", aria: { label: "투자" } },
    h("div", { class: "card-head" }, h("h2", {}, "투자"), h("span", { class: "side" }, `${list.length}개 상품`)),
    h(
      "div",
      { class: "pair three" },
      stat("평가금액", `${won(sum.value)}원`),
      stat("원금", `${won(sum.principal)}원`),
      stat("수익률", sum.rate === null ? "—" : `${sum.rate >= 0 ? "+" : "−"}${Math.abs(sum.rate)}%`, sum.rate === null ? "" : sum.rate >= 0 ? "pos" : "danger"),
    ),
    h(
      "div",
      { class: "detail-list" },
      list.map((x) => {
        const rate = x.rate === null || x.rate === undefined ? null : Number(x.rate);
        return detailRow(
          x.name,
          [showWho ? ctx.nameOf(x.owner) : null, x.institution, x.group, x.principal ? `원금 ${won(x.principal)}원` : null].filter(Boolean).join(" · "),
          `${won(x.amount || 0)}원`,
          rate === null ? null : h("span", { class: rate >= 0 ? "pos" : "danger" }, `${rate >= 0 ? "+" : "−"}${Math.abs(rate).toFixed(1)}%`),
        );
      }),
    ),
  );
}

function loanCard(ctx, items, showWho) {
  const list = items.filter((x) => x.section === "loan").sort((a, b) => (b.amount || 0) - (a.amount || 0));
  if (!list.length) return null;
  const total = list.reduce((a, x) => a + (x.amount || 0), 0);
  return h(
    "section",
    { class: "card", aria: { label: "대출" } },
    h("div", { class: "card-head" }, h("h2", {}, "대출"), h("span", { class: "side" }, `잔액 ${won(total)}원`)),
    h(
      "div",
      { class: "detail-list" },
      list.map((x) => {
        const paid = x.principal && x.amount !== null ? Math.max(0, Math.min(100, pct(x.principal - x.amount, x.principal))) : null;
        return h(
          "div",
          { class: "detail-row loan" },
          detailRow(
            x.name,
            [showWho ? ctx.nameOf(x.owner) : null, x.institution, x.group].filter(Boolean).join(" · "),
            `${won(x.amount || 0)}원`,
            [x.rate !== null && x.rate !== undefined ? `금리 ${Number(x.rate).toFixed(2)}%` : null, x.end_date ? `만기 ${x.end_date}` : null].filter(Boolean).join(" · ") || null,
          ),
          paid !== null
            ? h(
                "div",
                { class: "repay" },
                h("span", { class: "bar-track" }, h("span", { class: "bar-fill", style: { width: `${paid}%` } })),
                h("span", { class: "muted small" }, `원금 ${won(x.principal)}원 중 ${paid}% 갚음`),
              )
            : null,
        );
      }),
    ),
  );
}

function insuranceCard(ctx, items, showWho) {
  const list = items.filter((x) => x.section === "insurance");
  if (!list.length) return null;
  const total = list.reduce((a, x) => a + (x.amount || 0), 0);
  return h(
    "section",
    { class: "card", aria: { label: "보험" } },
    h("div", { class: "card-head" }, h("h2", {}, "보험"), h("span", { class: "side" }, `${list.length}건`)),
    h("p", { class: "hint" }, `지금까지 낸 총납입 금액 ${won(total)}원 (자산 합계에는 들어가지 않아요)`),
    h(
      "div",
      { class: "detail-list" },
      list.map((x) =>
        detailRow(
          x.name,
          [showWho ? ctx.nameOf(x.owner) : null, x.institution, x.status].filter(Boolean).join(" · "),
          `${won(x.amount || 0)}원`,
          x.end_date ? `만기 ${x.end_date}` : null,
        ),
      ),
    ),
  );
}

function stat(k, v, tone = "") {
  return h("div", { class: "stack-2" }, h("span", { class: "k" }, k), h("span", { class: `v ${tone}` }, v));
}

function detailRow(name, meta, amount, extra) {
  return h(
    "div",
    { class: "detail" },
    h("span", { class: "item-name" }, h("span", { class: "nm" }, name), meta ? h("span", { class: "muted small" }, meta) : null),
    h("span", { class: "detail-amt" }, h("strong", {}, amount), extra ? h("span", { class: "small" }, extra) : null),
  );
}

// --- 차 · 집 --------------------------------------------------------------------

function thingsCard(ctx, data, showWho) {
  const rows = data.members.flatMap((m) => [
    ...m.vehicles.map((v) => ({ owner: m.owner, kind: "차", name: v.name, value: v.value, how: v.formula, included: v.included })),
    ...m.estates.map((e) => ({ owner: m.owner, kind: "집", name: e.name, value: e.value, how: e.label, included: e.included })),
  ]);
  if (!rows.length) return null;
  return h(
    "section",
    { class: "card", aria: { label: "차와 집" } },
    h("div", { class: "card-head" }, h("h2", {}, "차 · 집"), h("span", { class: "side" }, `${monthShort(data.value_month)} 기준`)),
    h(
      "div",
      { class: "detail-list" },
      rows.map((r) =>
        detailRow(
          r.name,
          [showWho ? ctx.nameOf(r.owner) : null, r.kind, r.how].filter(Boolean).join(" · "),
          `${won(r.value)}원`,
          r.included ? null : h("span", { class: "chip small" }, "뱅샐 값 사용"),
        ),
      ),
    ),
    h("p", { class: "hint" }, "뱅샐 재무현황에 동산·부동산이 이미 있으면 한지갑 값은 더하지 않아요(두 번 세지 않게). 차·집 상세는 G7에서 열려요."),
  );
}