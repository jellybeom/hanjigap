// 작은 그래프 (SVG·막대). 바깥 라이브러리 없이 그린다.
//   lineChart(points, { label })   points: [{ month, value, mark }]  mark = 점 찍기 (직접 넣은 값 등)
//   monthBars(items, { label })    items:  [{ month, value }]  막대 (가장 큰 달 진하게)

import { h } from "./dom.js";
import { manWon, monthDot } from "./format.js";

export function lineChart(points, { label }) {
  if (points.length < 2) return h("p", { class: "hint" }, "두 달 이상 쌓이면 그래프로 보여요.");
  const W = 320, H = 120, PX = 6, PY = 10;
  const values = points.map((p) => p.value);
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const x = (i) => PX + (i * (W - 2 * PX)) / (points.length - 1);
  const y = (v) => PY + ((max - v) / span) * (H - 2 * PY);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join(" ");
  const marks = points.map((p, i) => (p.mark ? h("circle", { cx: x(i).toFixed(1), cy: y(p.value).toFixed(1), r: 3.5, class: "chart-mark" }) : null));
  const last = points.length - 1;
  return h(
    "div",
    { class: "chart" },
    h("div", { class: "chart-y" }, h("span", {}, manWon(max)), h("span", {}, manWon(min))),
    h(
      "svg",
      { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", aria: { label } },
      h("path", { d, class: "chart-line", fill: "none" }),
      marks,
      h("circle", { cx: x(last).toFixed(1), cy: y(points[last].value).toFixed(1), r: 4.5, class: "chart-now" }),
    ),
    h("div", { class: "chart-x" }, h("span", {}, monthDot(points[0].month)), h("span", {}, monthDot(points[last].month))),
  );
}

export function monthBars(items, { label }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  const peak = items.reduce((best, i) => (i.value > (best?.value ?? 0) ? i : best), null);
  const cols = { "grid-template-columns": `repeat(${items.length}, minmax(0, 1fr))` };
  return h(
    "div",
    { class: "bars-wrap" },
    h(
      "div",
      { class: "bars", style: cols, role: "img", aria: { label } },
      items.map((i) =>
        h(
          "span",
          { class: `bar${peak && i.month === peak.month ? " on" : ""}`, title: `${monthDot(i.month)} ${i.value.toLocaleString("ko-KR")}원` },
          h("span", { class: "fill", style: { height: `${Math.max(2, (Math.max(0, i.value) / max) * 100).toFixed(1)}%` } }),
        ),
      ),
    ),
    h("div", { class: "bar-labels", style: cols, "aria-hidden": "true" }, items.map((i) => h("span", {}, String(Number(i.month.slice(5)))))),
  );
}