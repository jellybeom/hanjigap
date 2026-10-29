// 여러 화면이 함께 쓰는 조각: 보기 선택(합산·상범·재희), 달 이동 화살표, 거래 한 줄, 순자산 추이 선.

import { h, icon } from "./dom.js";
import { dayLabel, monthDot, monthLabel, txAmount } from "./format.js";
import { ICONS } from "./icons.js";

export function viewSegment(ctx) {
  const options = [["all", "합산"], ...ctx.members.map((m) => [m.slug, m.name])];
  return h(
    "div",
    { class: "seg", role: "group", aria: { label: "보기 선택" } },
    options.map(([key, label]) =>
      h(
        "button",
        { type: "button", aria: { pressed: ctx.view === key }, onclick: () => ctx.setView(key) },
        label,
      ),
    ),
  );
}

/** 정산된 달 목록에서 바로 앞·뒤 달 */
export function neighbors(month, settledMonths) {
  const earlier = settledMonths.filter((m) => m < month);
  const later = settledMonths.filter((m) => m > month);
  return {
    prev: earlier.length ? earlier[earlier.length - 1] : null,
    next: later.length ? later[0] : null,
  };
}

export function monthArrow(ctx, target, direction, size = 20) {
  const isPrev = direction === "prev";
  const label = target
    ? `${isPrev ? "이전" : "다음"} 달 (${monthLabel(target)})`
    : isPrev
      ? "이전 달 없음"
      : "다음 달 (아직 정산 전)";
  return h(
    "button",
    {
      class: "icon-btn plain", type: "button", disabled: !target, aria: { label },
      onclick: () => target && ctx.setMonth(target),
    },
    icon(isPrev ? ICONS.prev : ICONS.next, size, { strokeWidth: 2 }),
  );
}

/** 거래 한 줄. meta에는 날짜 대신 분류·결제수단 등을 넣을 수 있다. */
export function txRow(ctx, x, { showWho, meta, onclick, showDate = true }) {
  const tag = onclick ? "button" : "div";
  return h(
    tag,
    { class: "tx", type: onclick ? "button" : null, onclick },
    h(
      "span",
      { class: "body" },
      h(
        "span",
        { class: "line1" },
        h("span", { class: "text" }, x.content || "(내용 없음)"),
        showWho ? h("span", { class: "chip small" }, ctx.nameOf(x.owner)) : null,
        x.edited ? h("span", { class: "chip small outline-warn" }, "웹에서 수정") : null,
        x.cls === "unconfirmed" ? h("span", { class: "chip small outline-warn" }, "확인 필요") : null,
      ),
      h("span", { class: "meta" }, showDate ? `${dayLabel(x.date)} · ${meta}` : meta),
    ),
    h("span", { class: `amount${x.amount > 0 ? " pos" : ""}` }, `${txAmount(x.amount)}원`),
  );
}

/** 순자산 추이 선 (기록이 없는 달은 건너뜀). trend: [{ month, net_worth|null }] */
export function sparkline(trend) {
  const points = trend.filter((p) => p.net_worth !== null);
  if (points.length < 2) {
    return h("p", { class: "note" }, "순자산 추이는 두 달 이상 쌓이면 그려져요.");
  }
  const W = 310, H = 56, P = 5;
  const values = points.map((p) => p.net_worth);
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const index = (month) => trend.findIndex((p) => p.month === month);
  const xy = points.map((p) => [
    (index(p.month) * (W - 2 * P)) / (trend.length - 1) + P,
    P + ((max - p.net_worth) / span) * (H - 2 * P),
  ]);
  const d = xy.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const last = xy[xy.length - 1];
  return h(
    "div",
    { style: { display: "flex", "flex-direction": "column", gap: "4px" } },
    h(
      "svg",
      {
        class: "spark", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", fill: "none",
        role: "img", aria: { label: "최근 12개월 순자산 추이" },
      },
      h("path", { d, stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", class: "spark-line" }),
      h("circle", { cx: last[0], cy: last[1], r: 4, fill: "currentColor" }),
    ),
    h(
      "div",
      { class: "axis" },
      h("span", {}, monthDot(trend[0].month)),
      h("span", {}, "최근 12개월"),
      h("span", {}, monthDot(trend[trend.length - 1].month)),
    ),
  );
}