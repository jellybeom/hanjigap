// 내역 (G3a): 지출·수입 요약 · 12개월 막대 · 분류별(소분류 펼치기) · 거래 목록 · 검색
// 데이터: /api/summary, /api/trend, /api/transactions, /api/months
// 확인 필요 정리 화면과 거래 상세는 G3b.

import { api } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { dayLabel, monthLabel, monthShort, pct, signedWon, won } from "../format.js";
import { ICONS } from "../icons.js";
import { monthArrow, neighbors, txRow, viewSegment } from "../ui.js";

const MODES = [
  ["expense", "지출"],
  ["income", "수입"],
  ["fixed", "고정비"],
];
const CAT_LIMIT = 6;
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
const SEARCH_ICON = "M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM16 16l4.5 4.5";
const FILTER_ICON = "M4 6h16M7 12h10M10 18h4";

// 이 화면 안에서만 기억하는 상태 (다른 탭에 다녀와도 유지)
const state = {
  mode: "expense",
  category: null, // 거래 목록을 이 분류로 거르기
  cls: null, // 'unconfirmed'면 확인 필요만
  open: null, // 소분류를 펼친 분류
  allCats: false, // 분류를 모두 보기 (기본은 위 6개)
  search: null, // null = 검색 아님, 문자열 = 검색 중
  key: "", // 달·보기가 바뀌면 거르기를 푼다
};

export async function render(root, ctx, _screen, isCurrent) {
  const key = `${ctx.month}|${ctx.view}`;
  if (key !== state.key) {
    state.key = key;
    state.category = null;
    state.cls = null;
    state.open = null;
    state.allCats = false;
  }
  const rerender = () => render(root, ctx, _screen, isCurrent);
  if (state.search !== null) return renderSearch(root, ctx, rerender, isCurrent);
  if (state.mode === "fixed") return renderFixed(root, ctx, rerender);

  const params = { month: ctx.month, owner: ctx.owner() };
  const [summary, months] = await Promise.all([api.get("/api/summary", params), api.get("/api/months")]);
  const month = summary.month;
  const trend = await api.get("/api/trend", { ...params, month });
  if (!isCurrent()) return;
  const settled = months.items.map((m) => m.month).sort();
  const list = h("section", { class: "card tx-card", aria: { label: "거래" } });
  replace(
    root,
    header(ctx, rerender),
    viewSegment(ctx),
    modeRow(ctx, month, settled, rerender),
    unconfirmedBanner(summary, list, ctx),
    summaryCard(ctx, summary, trend, settled),
    categoryCard(summary, list, ctx),
    list,
  );
  await loadList(list, ctx, month);
}

// --- 머리줄 · 탭 -----------------------------------------------------------------

function header(ctx, rerender) {
  return h(
    "header",
    { class: "topbar" },
    h("a", { class: "icon-btn plain edge-left", href: "#/", aria: { label: "홈으로" } }, icon(ICONS.prev, 22, { strokeWidth: 2 })),
    h("h1", { class: "bar-title" }, "수입 · 지출"),
    h(
      "button",
      {
        class: "icon-btn plain edge-right", type: "button", aria: { label: "거래 검색" },
        onclick: () => {
          state.search = "";
          rerender();
        },
      },
      icon(SEARCH_ICON, 21, { strokeWidth: 1.9 }),
    ),
  );
}

function modeRow(ctx, month, settled, rerender) {
  const { prev, next } = neighbors(month, settled);
  return h(
    "div",
    { class: "mode-row" },
    h(
      "div",
      { class: "modes", role: "group", aria: { label: "지출, 수입 또는 고정비" } },
      MODES.map(([key, label]) =>
        h(
          "button",
          {
            type: "button", aria: { pressed: state.mode === key },
            onclick: () => {
              state.mode = key;
              state.category = null;
              state.cls = null;
              state.open = null;
              rerender();
            },
          },
          label,
        ),
      ),
    ),
    month
      ? h(
          "div",
          { class: "month-mini" },
          monthArrow(ctx, prev, "prev", 18),
          h("span", {}, monthLabel(month)),
          monthArrow(ctx, next, "next", 18),
        )
      : null,
  );
}

function renderFixed(root, ctx, rerender) {
  replace(
    root,
    header(ctx, rerender),
    viewSegment(ctx),
    modeRow(ctx, null, [], rerender),
    h("div", { class: "card" }, h("p", { class: "empty" }, "고정비 화면은 다음 단계(G6)에서 만들어요.")),
  );
}

// --- 확인 필요 띠 ---------------------------------------------------------------

function unconfirmedBanner(summary, list, ctx) {
  const n = summary.unconfirmed.count;
  if (!n) return null;
  return h(
    "button",
    {
      class: "banner-warn", type: "button",
      onclick: () => {
        state.cls = "unconfirmed";
        state.category = null;
        loadList(list, ctx, summary.month).then(() => list.scrollIntoView({ behavior: "smooth", block: "start" }));
      },
    },
    h("span", { class: "dot", "aria-hidden": "true" }),
    h("span", { class: "text" }, `확인 필요 ${n}건 · 수입인지 지출인지 정해 주세요`),
    icon(ICONS.next, 16, { strokeWidth: 2 }),
  );
}

// --- 요약 · 막대 -----------------------------------------------------------------

function summaryCard(ctx, s, trend, settled) {
  const mode = state.mode;
  const label = mode === "income" ? "수입" : "지출";
  const who = ctx.view === "all" ? "합산" : ctx.nameOf(ctx.view);
  const total = s[mode];
  const prev = s.previous[mode];
  const delta = total - prev;
  const good = mode === "income" ? delta >= 0 : delta <= 0;
  const deltaPct = prev ? ` (${delta >= 0 ? "+" : "−"}${Math.abs(Math.round((delta / prev) * 100))}%)` : "";
  return h(
    "section",
    { class: "card", aria: { label: `${label} 요약` } },
    h(
      "div",
      { class: "stack-4" },
      h("span", { class: "k" }, `${who} · ${monthShort(s.month)} ${label}`),
      h("div", { class: "big-num" }, h("span", { class: "num" }, (total < 0 ? "−" : "") + won(total)), h("span", { class: "unit" }, "원")),
      h("span", { class: `delta ${good ? "pos" : "warn"}` }, `지난달보다 ${signedWon(delta)}원${deltaPct}`),
    ),
    ctx.view === "all" ? split(ctx, s, mode) : null,
    bars(ctx, trend.items, s.month, mode, settled),
  );
}

function split(ctx, s, mode) {
  const parts = s.by_member.map((m) => ({ owner: m.owner, value: Math.max(0, m[mode]) }));
  const sum = parts.reduce((a, p) => a + p.value, 0);
  if (parts.length < 2 || sum <= 0) return null;
  return h(
    "div",
    { class: "split" },
    h(
      "div",
      { class: "split-bar" },
      parts.map((p, i) =>
        h("div", { class: `split-part p${i}`, style: { width: `${((p.value / sum) * 100).toFixed(1)}%` } }),
      ),
    ),
    h(
      "div",
      { class: "split-legend" },
      parts.map((p, i) =>
        h(
          "span",
          { class: "legend" },
          h("span", { class: `swatch p${i}`, "aria-hidden": "true" }),
          `${ctx.nameOf(p.owner)} ${won(p.value)}원 · ${pct(p.value, sum)}%`,
        ),
      ),
    ),
  );
}

function bars(ctx, items, month, mode, settled) {
  const max = Math.max(1, ...items.map((i) => i[mode]));
  return h(
    "div",
    { class: "bars-wrap" },
    h(
      "div",
      { class: "bars", role: "img", aria: { label: `최근 12개월 ${mode === "income" ? "수입" : "지출"} 막대 그래프, ${monthShort(month)} 강조` } },
      items.map((i) => {
        const on = i.month === month;
        const canGo = !on && settled.includes(i.month);
        const height = `${Math.max(2, (Math.max(0, i[mode]) / max) * 100).toFixed(1)}%`;
        return h(
          "button",
          {
            class: `bar${on ? " on" : ""}`, type: "button", disabled: !canGo, tabindex: canGo ? null : "-1",
            aria: { label: `${monthLabel(i.month)} ${won(i[mode])}원` },
            onclick: () => canGo && ctx.setMonth(i.month),
          },
          h("span", { class: "fill", style: { height } }),
        );
      }),
    ),
    h(
      "div",
      { class: "bar-labels", "aria-hidden": "true" },
      items.map((i) => h("span", { class: i.month === month ? "on" : "" }, String(Number(i.month.slice(5))))),
    ),
    h("span", { class: "hint" }, `최근 12개월 (월) · 진한 막대가 ${monthShort(month)} · 막대를 누르면 그 달로`),
  );
}

// --- 분류별 ---------------------------------------------------------------------

function categoryCard(s, list, ctx) {
  const mode = state.mode;
  const cats = s[`${mode}_by_category`];
  const subs = s[`${mode}_by_subcategory`];
  const total = s[mode];
  const max = Math.max(1, ...cats.map((c) => c.amount));
  const card = h(
    "section",
    { class: "card cat-card", aria: { label: "분류별" } },
    h("div", { class: "card-head" }, h("h2", {}, "분류별"), h("span", { class: "side" }, "눌러서 소분류 보기")),
  );
  if (!cats.length) card.appendChild(h("p", { class: "empty pad" }, "이 달에는 없어요."));
  const hidden = state.allCats || cats.length <= CAT_LIMIT + 1 ? 0 : cats.length - CAT_LIMIT;
  for (const c of hidden ? cats.slice(0, CAT_LIMIT) : cats) {
    const open = state.open === c.category;
    const row = h(
      "div",
      { class: "cat-row" },
      h(
        "button",
        {
          class: "cat-btn", type: "button", aria: { expanded: open },
          onclick: () => {
            state.open = open ? null : c.category;
            card.replaceWith(categoryCard(s, list, ctx));
          },
        },
        h(
          "span",
          { class: "top" },
          h("span", { class: "name" }, icon(ICONS.next, 14, { strokeWidth: 2.2 }), c.category),
          c.amount < 0
            ? h("span", { class: "amt pos" }, h("strong", {}, `−${won(c.amount)}원`), " · 환불이 더 많아요")
            : h("span", { class: "amt" }, h("strong", {}, `${won(c.amount)}원`), h("span", { class: "muted" }, ` · ${pct(c.amount, total)}%`)),
        ),
        c.amount > 0
          ? h("span", { class: "bar-track" }, h("span", { class: "bar-fill", style: { width: `${((c.amount / max) * 100).toFixed(1)}%` } }))
          : null,
      ),
      open
        ? h(
            "div",
            { class: "sub-list" },
            (subs[c.category] || []).map((sb) =>
              h(
                "div",
                { class: "sub-row" },
                h("span", {}, sb.subcategory),
                h("span", { class: "amt" }, h("strong", {}, `${sb.amount < 0 ? "−" : ""}${won(sb.amount)}원`), h("span", { class: "muted" }, c.amount > 0 ? ` · ${pct(sb.amount, c.amount)}%` : "")),
              ),
            ),
            h(
              "button",
              {
                class: "link-btn", type: "button",
                onclick: () => {
                  state.category = c.category;
                  state.cls = null;
                  loadList(list, ctx, s.month).then(() => list.scrollIntoView({ behavior: "smooth", block: "start" }));
                },
              },
              `${c.category} 거래만 보기`,
            ),
          )
        : null,
    );
    card.appendChild(row);
  }
  if (hidden) {
    card.appendChild(
      h(
        "button",
        {
          class: "more-btn", type: "button",
          onclick: () => {
            state.allCats = true;
            card.replaceWith(categoryCard(s, list, ctx));
          },
        },
        `분류 ${hidden}개 더 보기`,
      ),
    );
  }
  return card;
}

// --- 거래 목록 -------------------------------------------------------------------

async function loadList(section, ctx, month) {
  const filter = state.cls ? { cls: state.cls } : { cls: state.mode, category: state.category };
  const params = { month, owner: ctx.owner(), ...filter };
  const first = await api.get("/api/transactions", { ...params, page: 1 });
  const items = [...first.items];
  let page = 1;
  const draw = (pages) => {
    const label = state.cls
      ? "확인 필요"
      : state.category || (state.mode === "income" ? "수입" : "지출");
    const filtered = Boolean(state.cls || state.category);
    replace(
      section,
      h(
        "div",
        { class: "card-head center" },
        h("h2", {}, `${monthShort(month)} ${label} 거래`),
        filtered
          ? h(
              "button",
              {
                class: "chip-btn", type: "button", aria: { label: "거르기 풀기" },
                onclick: () => {
                  state.cls = null;
                  state.category = null;
                  loadList(section, ctx, month);
                },
              },
              `${label} ✕`,
            )
          : h("span", { class: "side with-icon" }, icon(FILTER_ICON, 16, { strokeWidth: 2 }), `${first.total}건`),
      ),
      items.length === 0 ? h("p", { class: "empty pad" }, `이 달 ${label} 거래가 없어요.`) : null,
      groupByDate(ctx, items),
      page < pages
        ? h(
            "button",
            {
              class: "more-btn", type: "button",
              onclick: async () => {
                page += 1;
                const next = await api.get("/api/transactions", { ...params, page });
                items.push(...next.items);
                draw(next.pages);
              },
            },
            `더 보기 (${first.total - items.length}건 남음)`,
          )
        : null,
    );
  };
  draw(first.pages);
}

function groupByDate(ctx, items) {
  const showWho = ctx.view === "all";
  const groups = [];
  for (const x of items) {
    const last = groups[groups.length - 1];
    if (last && last.date === x.date) last.items.push(x);
    else groups.push({ date: x.date, items: [x] });
  }
  return groups.map((g) =>
    h(
      "div",
      { class: "day-group" },
      h("span", { class: "day" }, `${dayLabel(g.date)} (${WEEKDAYS[new Date(`${g.date}T00:00:00`).getDay()]})`),
      g.items.map((x) => txRow(ctx, x, { showWho, meta: metaOf(x), showDate: false })),
    ),
  );
}

function metaOf(x) {
  if (x.cls === "unconfirmed") return `뱅샐 ${x.tx_type} › ${x.category || "미분류"} · ${x.instrument || "결제수단 없음"}`;
  const cat = x.display_category || x.class_label;
  const sub = x.subcategory && x.subcategory !== "미분류" && x.subcategory !== cat ? ` › ${x.subcategory}` : "";
  const base = x.cls === "expense" || x.cls === "income" ? `${cat}${sub}` : `${x.class_label} · ${cat}`;
  return x.instrument ? `${base} · ${x.instrument}` : base;
}

// --- 검색 ----------------------------------------------------------------------

async function renderSearch(root, ctx, rerender, isCurrent) {
  const months = await api.get("/api/months");
  if (!isCurrent()) return;
  const settled = months.items.map((m) => m.month).sort();
  const month = ctx.month || settled[settled.length - 1] || null;
  const input = h("input", {
    class: "search-input", type: "search", placeholder: "내용·메모로 찾기", value: state.search,
    enterkeyhint: "search", autocomplete: "off", aria: { label: "찾을 글자" },
  });
  const results = h("section", { class: "card tx-card", aria: { label: "검색 결과" } });
  let timer = null;
  const run = async () => {
    state.search = input.value;
    const q = input.value.trim();
    if (!q) {
      replace(results, h("p", { class: "empty pad" }, "내용이나 메모에 들어간 글자로 찾아요. 이 달의 모든 거래(저축·제외·확인 필요 포함)에서 찾아요."));
      return;
    }
    const params = { month, owner: ctx.owner(), q };
    const first = await api.get("/api/transactions", { ...params, page: 1 });
    const items = [...first.items];
    let page = 1;
    const draw = (pages) =>
      replace(
        results,
        h("div", { class: "card-head center" }, h("h2", {}, `찾은 거래 ${first.total}건`), h("span", { class: "side" }, monthLabel(month))),
        items.length ? groupByDate(ctx, items) : h("p", { class: "empty pad" }, "찾는 거래가 없어요."),
        page < pages
          ? h(
              "button",
              {
                class: "more-btn", type: "button",
                onclick: async () => {
                  page += 1;
                  const next = await api.get("/api/transactions", { ...params, page });
                  items.push(...next.items);
                  draw(next.pages);
                },
              },
              "더 보기",
            )
          : null,
      );
    draw(first.pages);
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, 300);
  });
  const { prev, next } = month ? neighbors(month, settled) : { prev: null, next: null };
  replace(
    root,
    h(
      "form",
      {
        class: "search-bar", role: "search",
        onsubmit: (e) => {
          e.preventDefault();
          input.blur();
          run();
        },
      },
      h(
        "button",
        {
          class: "icon-btn plain edge-left", type: "button", aria: { label: "검색 닫기" },
          onclick: () => {
            state.search = null;
            rerender();
          },
        },
        icon(ICONS.prev, 22, { strokeWidth: 2 }),
      ),
      input,
    ),
    month
      ? h("div", { class: "month-mini center" }, monthArrow(ctx, prev, "prev", 18), h("span", {}, monthLabel(month)), monthArrow(ctx, next, "next", 18))
      : null,
    results,
  );
  input.focus();
  run();
}