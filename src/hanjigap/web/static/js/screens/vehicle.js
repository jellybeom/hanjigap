// 차 상세 (G7a): 추정 시세 · 시세 추이 · 유지비(종류별·월별·거래) · 관련 고정비 · 유지비 규칙 · 관리
// 주소: #/vehicle?id=1
// 데이터: /api/vehicles/{id}, /value-series, /costs, /cost-rules, /api/options

import { api, ApiError } from "../api.js";
import { lineChart, monthBars } from "../charts.js";
import { h, icon, replace, toast } from "../dom.js";
import { dayLabel, monthLabel, pct, won } from "../format.js";
import { openFixedSheet } from "../fixedsheet.js";
import { ICONS } from "../icons.js";
import { openTxSheet } from "../txsheet.js";
import { cols, ord } from "../ui.js";
import { openRuleSheet, openSoldSheet, openValuationSheet, openVehicleForm } from "../vehiclesheet.js";

const COST_LIMIT = 15;
const state = { id: null, allCosts: false };

export async function render(root, ctx, _screen, isCurrent) {
  const id = Number(new URLSearchParams(window.location.hash.split("?")[1] || "").get("id"));
  if (!id) {
    replace(root, h("div", { class: "error-box" }, h("p", { class: "msg" }, "차를 찾을 수 없어요"), h("a", { class: "link-btn", href: "#/assets" }, "자산으로")));
    return;
  }
  if (state.id !== id) {
    state.id = id;
    state.allCosts = false;
  }
  const base = `/api/vehicles/${id}`;
  const [v, series, costs, rules, options] = await Promise.all([
    api.get(base),
    api.get(`${base}/value-series`),
    api.get(`${base}/costs`),
    api.get(`${base}/cost-rules`),
    api.get("/api/options"),
  ]);
  if (!isCurrent()) return;
  const refresh = () => ctx.refresh();
  replace(
    root,
    h(
      "header",
      { class: "topbar" },
      h("a", { class: "icon-btn plain edge-left", href: "#/assets", aria: { label: "자산으로" } }, icon(ICONS.prev, 22, { strokeWidth: 2 })),
      h("h1", { class: "bar-title" }, v.name),
      h("button", { class: "icon-btn plain edge-right", type: "button", aria: { label: "차 정보 고치기" }, onclick: () => openVehicleForm(ctx, v, refresh) }, icon(ICONS.edit, 20, { strokeWidth: 2 })),
    ),
    cols(
      [ord(valueCard(ctx, v), 1), ord(seriesCard(v, series, refresh), 2), ord(fixedCard(ctx, v, refresh), 4)],
      [ord(costCard(ctx, v, costs, refresh), 3), ord(rulesCard(ctx, v, rules, options, refresh), 5), ord(manageCard(v, refresh), 6)],
    ),
  );
}

// --- 시세 ----------------------------------------------------------------------

function valueCard(ctx, v) {
  const e = v.estimate;
  const days = v.inspection_days_left;
  return h(
    "section",
    { class: "hero", aria: { label: "추정 시세" } },
    h(
      "div",
      { class: "stack-6" },
      h("span", { class: "label" }, v.sold_on ? "팔 때 추정 시세 · 순자산에서 빠짐" : e ? `${monthLabel(e.year_month)} 추정 시세` : "추정 시세"),
      h("div", { class: "big" }, h("span", { class: "num" }, e ? won(e.value) : "—"), e ? h("span", { class: "unit" }, "원") : null),
      e ? h("span", { class: "delta" }, `구입가의 ${pct(e.value, v.purchase_price)}% · 한 달에 ${e.monthly_pct}%씩 감가`) : null,
    ),
    h("div", { class: "rule" }),
    h(
      "div",
      { class: "pair" },
      h("div", { class: "stack-2" }, h("span", { class: "k" }, "구입"), h("span", { class: "v" }, `${v.purchased_on} · ${won(v.purchase_price)}원`)),
      h(
        "div",
        { class: "stack-2" },
        h("span", { class: "k" }, v.sold_on ? "판 날" : "다음 검사"),
        h("span", { class: "v" }, v.sold_on ? v.sold_on : v.inspection_due_on ? `${v.inspection_due_on}${days !== null ? ` (${days >= 0 ? `D-${days}` : `${-days}일 지남`})` : ""}` : "안 적음"),
      ),
    ),
    e ? h("p", { class: "note" }, `계산: ${e.formula} · 기준 ${e.anchor.label}`) : null,
    h("p", { class: "note" }, [ctx.nameOf(v.owner), v.model_year ? `${v.model_year}년식` : null, v.bs_asset_name ? `뱅샐 동산 「${v.bs_asset_name}」` : null].filter(Boolean).join(" · ")),
  );
}

function seriesCard(v, series, refresh) {
  const anchors = new Set(v.anchors.map((a) => a.year_month));
  const points = series.items.map((i) => ({ month: i.month, value: i.value, mark: i.anchor || anchors.has(i.month) }));
  return h(
    "section",
    { class: "card", aria: { label: "시세 추이" } },
    h(
      "div",
      { class: "card-head center" },
      h("h2", {}, "시세 추이"),
      v.sold_on ? null : h("button", { class: "chip-btn", type: "button", onclick: () => openValuationSheet(v, refresh) }, "+ 시세 넣기"),
    ),
    lineChart(points, { label: `${v.name} 시세 추이` }),
    h("p", { class: "hint" }, "점은 구입가와 직접 넣은 시세예요. 그 사이는 감가율로 짐작해요."),
    v.valuations.length
      ? h(
          "div",
          { class: "detail-list" },
          v.valuations.map((x) =>
            h(
              "div",
              { class: "detail" },
              h("span", { class: "item-name" }, h("span", { class: "nm" }, monthLabel(x.year_month)), x.memo ? h("span", { class: "muted small" }, x.memo) : null),
              h(
                "span",
                { class: "detail-amt" },
                h("strong", {}, `${won(x.value)}원`),
                h(
                  "button",
                  {
                    class: "link-btn small-link danger", type: "button",
                    onclick: async () => {
                      await act(() => api.del(`/api/vehicles/${v.id}/valuations/${x.year_month}`), "지웠어요");
                      refresh();
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

// --- 유지비 ---------------------------------------------------------------------

function costCard(ctx, v, c, refresh) {
  const items = state.allCosts ? c.items : c.items.slice(0, COST_LIMIT);
  const maxType = Math.max(1, ...c.by_type.map((t) => t.amount));
  return h(
    "section",
    { class: "card", aria: { label: "유지비" } },
    h("div", { class: "card-head" }, h("h2", {}, "유지비"), h("span", { class: "side" }, `${monthLabel(c.from)} ~ ${monthLabel(c.to)}`)),
    h(
      "div",
      { class: "pair two" },
      h("div", { class: "stack-2" }, h("span", { class: "k" }, `${c.months}개월 합계`), h("span", { class: "v" }, `${won(c.total)}원`)),
      h("div", { class: "stack-2" }, h("span", { class: "k" }, "한 달 평균"), h("span", { class: "v" }, `${won(c.monthly_average)}원`)),
    ),
    c.total ? monthBars(c.by_month.map((m) => ({ month: m.month, value: m.amount })), { label: "월별 유지비" }) : null,
    c.by_type.length
      ? h(
          "div",
          { class: "type-bars" },
          c.by_type.map((t) =>
            h(
              "div",
              { class: "bar-row" },
              h("div", { class: "top" }, h("span", { class: "name" }, t.label), h("span", { class: "amt" }, h("strong", {}, `${won(t.amount)}원`), h("span", { class: "muted" }, ` · ${pct(t.amount, c.total)}%`))),
              h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: { width: `${((Math.max(0, t.amount) / maxType) * 100).toFixed(1)}%` } })),
            ),
          ),
        )
      : h("p", { class: "empty" }, "이 기간에 찾은 유지비가 없어요. 아래 '유지비 찾는 규칙'을 확인하거나, 거래 상세에서 '차 유지비에 넣기'를 눌러 주세요."),
    c.items.length
      ? h(
          "div",
          { class: "tx-list" },
          h("span", { class: "k list-title" }, `유지비 거래 ${c.items.length}건`),
          items.map((i) =>
            h(
              "div",
              { class: "tx cost-row" },
              h(
                "button",
                { class: "body plain-btn", type: "button", onclick: () => openTxSheet(ctx, i.owner, i.tx_key, { onChange: refresh }) },
                h("span", { class: "line1" }, h("span", { class: "text" }, i.content || "(내용 없음)"), h("span", { class: "chip small" }, i.cost_label)),
                h("span", { class: "meta" }, `${dayLabel(i.date)} · ${i.manual ? "직접 넣음" : i.reason.replace(/^규칙: /, "")}`),
              ),
              h(
                "span",
                { class: "fixed-right" },
                h("span", { class: "amount" }, `${won(i.cost)}원`),
                h(
                  "button",
                  {
                    class: "link-btn small-link", type: "button",
                    onclick: async () => {
                      const path = `/api/vehicles/${v.id}/tags`;
                      // 직접 넣은 것은 표시를 지우고, 규칙으로 들어온 것은 '빼기' 표시를 한다
                      const ok = i.manual
                        ? await act(() => api.del(`${path}/${encodeURIComponent(i.owner)}/${encodeURIComponent(i.tx_key)}`), "유지비에서 뺐어요")
                        : await act(() => api.put(path, { owner: i.owner, tx_key: i.tx_key, include: false }), "유지비에서 뺐어요");
                      if (ok) refresh();
                    },
                  },
                  "빼기",
                ),
              ),
            ),
          ),
          !state.allCosts && c.items.length > COST_LIMIT
            ? h(
                "button",
                {
                  class: "more-btn", type: "button",
                  onclick: () => {
                    state.allCosts = true;
                    refresh();
                  },
                },
                `${c.items.length - COST_LIMIT}건 더 보기`,
              )
            : null,
        )
      : null,
  );
}

// --- 관련 고정비 · 규칙 · 관리 ---------------------------------------------------------

function fixedCard(ctx, v, refresh) {
  return h(
    "section",
    { class: "card", aria: { label: "관련 고정비" } },
    h("div", { class: "card-head" }, h("h2", {}, "관련 고정비"), h("span", { class: "side" }, "할부·보험 등")),
    v.fixed_items.length
      ? h(
          "div",
          { class: "detail-list" },
          v.fixed_items.map((f) =>
            h(
              "button",
              { class: "detail plain-btn", type: "button", onclick: () => openFixedSheet(ctx, { item: f, onChange: refresh }) },
              h("span", { class: "item-name" }, h("span", { class: "nm" }, f.name), h("span", { class: "muted small" }, `${f.schedule_label} · ${f.method_label}${f.ended ? " · 마감" : ""}`)),
              h("span", { class: "detail-amt" }, h("strong", {}, `${won(f.amount)}원`)),
            ),
          ),
        )
      : h("p", { class: "hint" }, "아직 없어요. 내역 › 고정비에서 고정비를 등록할 때 '자세히 › 관련 차'로 이 차를 고르면 여기에 나와요."),
  );
}

function rulesCard(ctx, v, rules, options, refresh) {
  return h(
    "details",
    { class: "card guide" },
    h("summary", {}, h("span", {}, `유지비 찾는 규칙 ${rules.items.filter((r) => r.active).length}개`), icon(ICONS.next, 16, { strokeWidth: 2 })),
    h("p", { class: "hint pad-x" }, "위에서부터 차례로 맞춰 봐요. 끈 규칙은 쓰지 않아요. 지출 거래만 규칙으로 찾아요."),
    h(
      "div",
      { class: "rule-list" },
      rules.items.map((r) =>
        h(
          "div",
          { class: `rule-row${r.active ? "" : " off"}` },
          h(
            "label",
            { class: "toggle-row" },
            h("input", {
              type: "checkbox", checked: r.active,
              onchange: async (e) => {
                const ok = await act(() => api.patch(`/api/vehicle-cost-rules/${r.id}`, { active: e.target.checked }), e.target.checked ? "켰어요" : "껐어요");
                if (ok) refresh();
              },
            }),
            h("span", {}, `${r.label} → ${r.cost_type_label}${r.owner ? ` (${ctx.nameOf(r.owner)})` : ""}`),
          ),
          r.kind === "category"
            ? null
            : h(
                "button",
                {
                  class: "link-btn small-link danger", type: "button",
                  onclick: async () => {
                    if (await act(() => api.del(`/api/vehicle-cost-rules/${r.id}`), "규칙을 지웠어요")) refresh();
                  },
                },
                "지우기",
              ),
        ),
      ),
    ),
    h("div", { class: "pad-x pad-b" }, h("button", { class: "btn secondary small", type: "button", onclick: () => openRuleSheet(ctx, v, options, refresh) }, "+ 규칙 더하기")),
  );
}

function manageCard(v, refresh) {
  return h(
    "div",
    { class: "center-row stack-6" },
    v.sold_on
      ? h(
          "button",
          {
            class: "link-btn", type: "button",
            onclick: async () => {
              if (await act(() => api.post(`/api/vehicles/${v.id}/sold`, { sold_on: null }), "판 날을 지웠어요")) refresh();
            },
          },
          "판 날 지우기 (다시 가지고 있는 것으로)",
        )
      : h("button", { class: "link-btn danger", type: "button", onclick: () => openSoldSheet(v, refresh) }, "차를 팔았어요 (판 날 적기)"),
  );
}

/** 바로 하는 작은 동작: 성공하면 알림, 실패하면 이유 알림. 성공 여부를 돌려준다. */
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