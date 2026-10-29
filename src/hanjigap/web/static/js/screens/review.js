// 확인 필요 정리 (G3b): 확인 필요 거래를 한 건씩 보여 주고 무엇인지 정한다.
//   나간 돈: 지출 · 저축·투자 · 제외 / 들어온 돈: 수입 · 지출 차감 · 저축·투자 · 제외
//   '앞으로도 이렇게'를 켜면 규칙도 만든다 → 같은 내용 거래는 함께 정리된다.
//   방금 정한 것은 '되돌리기'로 취소할 수 있다 (만든 규칙도 함께 지움).
// 데이터: /api/transactions?cls=unconfirmed, /api/transactions/{owner}/{key}, /api/categories

import { api } from "../api.js";
import { classPicker, loadCategories } from "../classpick.js";
import { h, icon, replace, toast } from "../dom.js";
import { dayLabel, monthLabel, monthShort, txAmount } from "../format.js";
import { ICONS } from "../icons.js";
import { openTxSheet } from "../txsheet.js";

// 이 화면 안에서만 기억하는 상태 (달·보기가 바뀌면 처음부터)
const state = {
  key: "",
  skipped: new Set(), // 건너뛴 거래
  done: 0, // 이번에 정리한 건수 (규칙으로 함께 정리된 것 포함)
  last: null, // 방금 정한 것 { owner, tx_key, content, text, rule_id, expect, extra }
  focus: null, // 되돌린 거래를 먼저 보여 주기
};

const idOf = (x) => `${x.owner}/${x.tx_key}`;
const txPath = (owner, key) => `/api/transactions/${encodeURIComponent(owner)}/${encodeURIComponent(key)}`;

export async function render(root, ctx, _screen, isCurrent) {
  const key = `${ctx.month}|${ctx.view}`;
  if (key !== state.key) {
    state.key = key;
    state.skipped = new Set();
    state.done = 0;
    state.last = null;
    state.focus = null;
  }
  const rerender = () => render(root, ctx, _screen, isCurrent);
  const [queue, cats] = await Promise.all([
    loadAll({ month: ctx.month, owner: ctx.owner(), cls: "unconfirmed" }),
    loadCategories(),
  ]);
  if (!isCurrent()) return;
  const items = queue.items.slice().reverse(); // 오래된 거래부터
  const remaining = items.length;
  if (state.last && state.last.expect !== undefined) {
    // 규칙으로 다른 거래도 함께 정리됐는지
    state.last.extra = Math.max(0, state.last.expect - remaining);
    state.done += state.last.extra;
    delete state.last.expect;
  }
  let current = null;
  if (state.focus) current = items.find((i) => idOf(i) === state.focus) || null;
  state.focus = null;
  if (!current) current = items.find((i) => !state.skipped.has(idOf(i))) || null;

  const head = header(queue.month);
  if (!current) {
    replace(root, head, undoBar(rerender), doneCard(ctx, queue.month, remaining, rerender));
    return;
  }
  const tx = await api.get(txPath(current.owner, current.tx_key));
  if (!isCurrent()) return;
  const more = items.some((i) => idOf(i) !== idOf(current) && !state.skipped.has(idOf(i)));
  replace(
    root,
    head,
    progress(remaining),
    undoBar(rerender),
    txCard(ctx, tx, rerender),
    h(
      "section",
      { class: "card", aria: { label: "분류 고르기" } },
      h("h2", { class: "q-title" }, "이 돈은 무엇인가요?"),
      classPicker(ctx, tx, cats, {
        submitLabel: more ? "정하고 다음으로" : "정하기",
        onSaved: (res, text) => {
          state.done += 1;
          state.skipped.delete(idOf(tx));
          state.last = {
            owner: tx.owner, tx_key: tx.tx_key, content: tx.content, text,
            rule_id: res.rule ? res.rule.id : null, expect: remaining - 1, extra: 0,
          };
          window.scrollTo(0, 0);
          rerender();
        },
      }),
    ),
    h(
      "div",
      { class: "skip-row" },
      h(
        "button",
        {
          class: "link-btn muted-link", type: "button",
          onclick: () => {
            state.skipped.add(idOf(tx));
            window.scrollTo(0, 0);
            rerender();
          },
        },
        "나중에 정할게요 (건너뛰기)",
      ),
    ),
  );
}

async function loadAll(params) {
  const first = await api.get("/api/transactions", { ...params, page: 1 });
  const items = [...first.items];
  for (let page = 2; page <= first.pages; page += 1) {
    const next = await api.get("/api/transactions", { ...params, page });
    items.push(...next.items);
  }
  return { month: first.month, items };
}

// --- 머리줄 · 진행 ---------------------------------------------------------------

function header(month) {
  return h(
    "header",
    { class: "topbar" },
    h("a", { class: "icon-btn plain edge-left", href: "#/ledger", aria: { label: "내역으로" } }, icon(ICONS.prev, 22, { strokeWidth: 2 })),
    h("h1", { class: "bar-title" }, "확인 필요 정리"),
    h("span", { class: "bar-side" }, monthLabel(month)),
  );
}

function progress(remaining) {
  const total = state.done + remaining;
  const ratio = total ? state.done / total : 0;
  return h(
    "div",
    { class: "progress" },
    h(
      "div",
      { class: "progress-text" },
      h("strong", {}, `남은 ${remaining}건`),
      state.done ? h("span", { class: "muted" }, `정리 ${state.done}건`) : null,
    ),
    h(
      "div",
      { class: "bar-track", role: "progressbar", aria: { valuemin: 0, valuemax: total, valuenow: state.done, label: "정리한 비율" } },
      h("div", { class: "bar-fill", style: { width: `${(ratio * 100).toFixed(1)}%` } }),
    ),
  );
}

function undoBar(rerender) {
  const last = state.last;
  if (!last) return null;
  return h(
    "div",
    { class: "undo-bar", role: "status" },
    h(
      "span",
      { class: "text" },
      h("span", { class: "ok" }, icon(ICONS.check, 16, { strokeWidth: 2.4 })),
      h(
        "span",
        {},
        `「${last.content || "내용 없음"}」 → ${last.text}`,
        last.extra ? h("span", { class: "muted" }, ` · 같은 내용 ${last.extra}건도 함께 정리됐어요`) : null,
      ),
    ),
    h(
      "button",
      {
        class: "chip-btn", type: "button",
        onclick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          const query = last.rule_id ? `?rule=${last.rule_id}` : "";
          try {
            await api.del(`${txPath(last.owner, last.tx_key)}/class${query}`);
          } catch (error) {
            btn.disabled = false;
            toast(error.message || "되돌리지 못했어요");
            return;
          }
          state.done = Math.max(0, state.done - 1 - (last.extra || 0));
          state.focus = `${last.owner}/${last.tx_key}`;
          state.last = null;
          toast(last.rule_id ? "되돌렸어요 (만든 규칙도 지웠어요)" : "되돌렸어요");
          rerender();
        },
      },
      icon(ICONS.undo, 15, { strokeWidth: 2 }),
      "되돌리기",
    ),
  );
}

// --- 지금 볼 거래 ----------------------------------------------------------------

function txCard(ctx, tx, rerender) {
  const incoming = tx.amount > 0;
  const sub = tx.subcategory && tx.subcategory !== "미분류" ? ` › ${tx.subcategory}` : "";
  const memo = tx.original.memo;
  return h(
    "section",
    { class: "card review-card", aria: { label: "확인할 거래" } },
    h(
      "div",
      { class: "review-top" },
      h("span", { class: "chip" }, ctx.nameOf(tx.owner)),
      h("span", { class: "meta" }, `${dayLabel(tx.date)} ${tx.time.slice(0, 5)}`),
    ),
    h("p", { class: "review-content" }, tx.content || "(내용 없음)"),
    h(
      "div",
      { class: "review-amount" },
      h("span", { class: `num${incoming ? " pos" : ""}` }, `${txAmount(tx.amount)}원`),
      h("span", { class: "dir" }, incoming ? "들어온 돈" : "나간 돈"),
    ),
    h("span", { class: "meta" }, `뱅샐 ${tx.tx_type} › ${tx.category || "미분류"}${sub}${tx.instrument ? ` · ${tx.instrument}` : ""}`),
    memo ? h("span", { class: "meta" }, `뱅샐 메모: ${memo}`) : null,
    tx.note ? h("span", { class: "meta" }, `한지갑 메모: ${tx.note}`) : null,
    tx.pair_hint
      ? h("p", { class: "note-box" }, "다른 구성원에게 같은 금액이 하루 안에 반대로 오간 거래가 있어요. 부부 사이 이체라면 '제외'가 맞아요.")
      : null,
    h(
      "button",
      {
        class: "link-btn", type: "button",
        onclick: () => openTxSheet(ctx, tx.owner, tx.tx_key, { onChange: rerender }),
      },
      "내용 고치기 · 자세히 보기",
    ),
  );
}

// --- 다 끝났을 때 ---------------------------------------------------------------

function doneCard(ctx, month, remaining, rerender) {
  const nothing = remaining === 0 && state.done === 0;
  const title = remaining ? `건너뛴 ${remaining}건이 남았어요` : nothing ? "확인할 거래가 없어요" : "모두 정리했어요";
  const text = remaining
    ? "나중에 다시 정해도 돼요. 정하기 전까지는 합계에 들어가지 않아요."
    : nothing
      ? `${monthShort(month)}에는 확인 필요 거래가 없어요.`
      : `${monthShort(month)} 확인 필요 거래 ${state.done}건을 모두 정했어요. 정한 분류는 내역에서 거래를 눌러 언제든 바꿀 수 있어요.`;
  return h(
    "section",
    { class: "card done-card" },
    h("span", { class: `done-icon${remaining ? " wait" : ""}`, "aria-hidden": "true" }, icon(remaining ? ICONS.calendar : ICONS.check, 28, { strokeWidth: 2.2 })),
    h("h2", {}, title),
    h("p", { class: "empty" }, text),
    h(
      "div",
      { class: "done-actions" },
      remaining
        ? h(
            "button",
            {
              class: "btn", type: "button",
              onclick: () => {
                state.skipped.clear();
                rerender();
              },
            },
            "건너뛴 거래 다시 보기",
          )
        : null,
      h("a", { class: `btn${remaining ? " secondary" : ""} link`, href: "#/ledger" }, "내역으로"),
      h("a", { class: "btn secondary link", href: "#/" }, "홈으로"),
    ),
  );
}