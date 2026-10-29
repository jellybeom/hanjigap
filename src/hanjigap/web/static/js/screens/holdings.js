// 계좌·카드 관리 (G5b): 계좌 · 카드 목록 · 등록·수정·해지 · 뱅샐 결제수단 연결
// 데이터: /api/accounts, /api/cards, /api/instruments, /api/options
// 결제수단 이름은 사람마다 다르다 (각자의 뱅샐 파일에 나온 이름). 연결도 사람별이다.
// 번호는 끝 4자리만 받는다. 해지는 지우지 않고 해지일만 적는다.

import { api, ApiError } from "../api.js";
import { h, icon, replace, toast } from "../dom.js";
import { won } from "../format.js";
import { ICONS } from "../icons.js";
import { openSheet } from "../sheet.js";
import { viewSegment } from "../ui.js";

const COLORS = ["#143d30", "#2e7d5b", "#1f5fa8", "#6b4fbb", "#b4237a", "#c2410c", "#a16207", "#525252"];
const MONTHS = { 0: "당월", "-1": "전월", "-2": "전전월" };
const END_MONTHS = { 0: "익월", "-1": "당월", "-2": "전월" };

const state = { showClosed: false };
const EASY_PAY_WORDS = /페이|pay/i;

/** 결제수단은 1년 넘게 걸쳐 있어서 연도까지 쓴다 (2026.04.02) */
function fullDate(date) {
  return date ? date.replaceAll("-", ".") : "";
}
let options = null;

export async function render(root, ctx, _screen, isCurrent) {
  const owners = ctx.owner() ? [ctx.owner()] : ctx.members.map((m) => m.slug);
  const q = { owner: ctx.owner(), include_closed: state.showClosed || null };
  const [accounts, cards, opts, ...usages] = await Promise.all([
    api.get("/api/accounts", q),
    api.get("/api/cards", q),
    options || api.get("/api/options"),
    ...owners.map((o) => api.get("/api/instruments", { owner: o })),
  ]);
  if (!isCurrent()) return;
  options = opts;
  const page = {
    ctx,
    accounts: accounts.items,
    cards: cards.items,
    usages: usages.flatMap((u) => u.items.map((i) => ({ ...i, owner: u.owner }))),
    refresh: () => ctx.refresh(),
  };
  const linkable = page.usages.filter((u) => u.instrument);
  const unlinked = linkable.filter((u) => !u.linked);
  const instrumentsCard = instrumentsSection(page, linkable);
  replace(
    root,
    h(
      "header",
      { class: "topbar" },
      h("a", { class: "icon-btn plain edge-left", href: "#/assets", aria: { label: "자산으로" } }, icon(ICONS.prev, 22, { strokeWidth: 2 })),
      h("h1", { class: "bar-title" }, "계좌 · 카드"),
      h("span", { class: "bar-side" }),
    ),
    viewSegment(ctx),
    unlinked.length
      ? h(
          "button",
          {
            class: "banner-warn", type: "button",
            onclick: () => instrumentsCard.scrollIntoView({ behavior: "smooth", block: "start" }),
          },
          h("span", { class: "dot", "aria-hidden": "true" }),
          h("span", { class: "text" }, `연결 안 된 결제수단 ${unlinked.length}개 · 계좌나 카드에 이어 주세요`),
          icon(ICONS.next, 16, { strokeWidth: 2 }),
        )
      : null,
    listCard(page, "account"),
    listCard(page, "card"),
    h(
      "label",
      { class: "toggle-row" },
      h("input", {
        type: "checkbox", checked: state.showClosed,
        onchange: (e) => {
          state.showClosed = e.target.checked;
          page.refresh();
        },
      }),
      h("span", {}, "해지한 계좌·카드도 보기"),
    ),
    instrumentsCard,
  );
}

// --- 목록 ----------------------------------------------------------------------

function listCard(page, kind) {
  const { ctx } = page;
  const isAccount = kind === "account";
  const items = isAccount ? page.accounts : page.cards;
  const showWho = ctx.view === "all";
  return h(
    "section",
    { class: "card tx-card", aria: { label: isAccount ? "계좌" : "카드" } },
    h(
      "div",
      { class: "card-head center" },
      h("h2", {}, isAccount ? `계좌 ${items.length}` : `카드 ${items.length}`),
      h(
        "button",
        {
          class: "chip-btn", type: "button",
          onclick: () => openItemSheet(page, kind, null, { owner: ctx.owner() || ctx.me.slug }),
        },
        isAccount ? "+ 계좌 추가" : "+ 카드 추가",
      ),
    ),
    items.length === 0
      ? h("p", { class: "empty pad" }, isAccount ? "등록한 계좌가 없어요. 월급 통장처럼 자주 쓰는 계좌부터 넣어 주세요." : "등록한 카드가 없어요.")
      : null,
    items.map((x) =>
      h(
        "button",
        { class: `tx holding${x.active ? "" : " closed"}`, type: "button", onclick: () => openItemSheet(page, kind, x, {}) },
        h("span", { class: "swatch big", style: { background: x.color || "var(--chip-bg)" }, "aria-hidden": "true" }),
        h(
          "span",
          { class: "body" },
          h(
            "span",
            { class: "line1" },
            h("span", { class: "text" }, x.name),
            showWho ? h("span", { class: "chip small" }, ctx.nameOf(x.owner)) : null,
            x.active ? null : h("span", { class: "chip small outline-warn" }, "해지"),
          ),
          h("span", { class: "meta" }, isAccount ? accountMeta(x) : cardMeta(x)),
        ),
        x.linked_count ? h("span", { class: "chip small" }, `연결 ${x.linked_count}`) : h("span", { class: "muted small" }, "연결 없음"),
      ),
    ),
  );
}

function accountMeta(a) {
  return [a.institution, a.kind_label, a.last4 ? `끝 ${a.last4}` : null].filter(Boolean).join(" · ");
}

function cardMeta(c) {
  return [c.issuer, c.kind_label, c.last4 ? `끝 ${c.last4}` : null, c.pay_day ? `매달 ${c.pay_day}일 결제` : null].filter(Boolean).join(" · ");
}

// --- 결제수단 -------------------------------------------------------------------

function instrumentsSection(page, linkable) {
  const { ctx } = page;
  const showWho = ctx.view === "all";
  const linked = linkable.filter((u) => u.linked).length;
  return h(
    "section",
    { class: "card tx-card", aria: { label: "뱅샐 결제수단" } },
    h("div", { class: "card-head center" }, h("h2", {}, "뱅샐 결제수단"), h("span", { class: "side" }, `${linkable.length}개 중 ${linked}개 연결`)),
    h("p", { class: "hint pad-b" }, "뱅샐 거래에 나온 결제수단 이름을 계좌·카드에 이어 두면 고정비와 차 유지비를 결제수단으로 찾을 수 있어요."),
    linkable.length === 0 ? h("p", { class: "empty pad" }, "아직 정산한 거래가 없어요.") : null,
    linkable
      .slice()
      .sort((a, b) => Number(a.linked) - Number(b.linked) || b.count - a.count)
      .map((u) =>
        h(
          "button",
          { class: "tx", type: "button", onclick: () => openLinkSheet(page, u) },
          h(
            "span",
            { class: "body" },
            h("span", { class: "line1" }, h("span", { class: "text" }, u.instrument), showWho ? h("span", { class: "chip small" }, ctx.nameOf(u.owner)) : null),
            h("span", { class: "meta" }, `${won(u.count)}건 · 마지막 ${fullDate(u.last_date)}`),
          ),
          u.linked
            ? h("span", { class: "chip small" }, u.link_label)
            : h("span", { class: "chip small outline-warn" }, "연결 안 됨"),
        ),
      ),
  );
}

function openLinkSheet(page, u) {
  const { ctx } = page;
  let changed = false;
  const sheet = openSheet({
    title: u.instrument,
    onClose: () => changed && page.refresh(),
  });
  const mine = (items) => items.filter((x) => x.owner === u.owner && x.active);
  const accounts = mine(page.accounts);
  const cards = mine(page.cards);
  // 지출이 많으면 카드, 이체가 많으면 계좌를 먼저 보여 준다
  const cardFirst = u.expense >= u.transfer;
  let pick = u.linked ? { target: u.target, id: u.target_id } : null;
  let error = null;
  let busy = false;
  const same = (t, id) => pick && pick.target === t && (pick.id ?? null) === (id ?? null);

  const suggestEasy = EASY_PAY_WORDS.test(u.instrument);

  function choice(target, id, label, meta, suggested = false) {
    return h(
      "button",
      {
        class: "pick-opt row", type: "button", aria: { pressed: Boolean(same(target, id)) },
        onclick: () => {
          pick = { target, id };
          draw();
        },
      },
      h(
        "span",
        { class: "opt-top" },
        h("span", { class: "opt-label" }, label),
        suggested ? h("span", { class: "chip small suggest" }, "추천") : null,
        same(target, id) ? icon(ICONS.check, 18, { strokeWidth: 2.4 }) : null,
      ),
      meta ? h("span", { class: "opt-desc" }, meta) : null,
    );
  }

  async function save() {
    if (!pick || busy) return;
    busy = true;
    error = null;
    draw();
    try {
      await api.put("/api/instruments/link", { owner: u.owner, instrument: u.instrument, target: pick.target, target_id: pick.id ?? null });
      changed = true;
      toast("연결했어요");
      sheet.close();
    } catch (e) {
      busy = false;
      error = e instanceof ApiError ? e.message : "연결하지 못했어요";
      draw();
    }
  }

  function draw() {
    const groups = [
      ["카드", cards.map((c) => choice("card", c.id, c.name, cardMeta(c)))],
      ["계좌", accounts.map((a) => choice("account", a.id, a.name, accountMeta(a)))],
    ];
    if (!cardFirst) groups.reverse();
    const easy = h("div", { class: "pick-group" }, h("span", { class: "k" }, "그 밖"), choice("easy_pay", null, "간편결제", "카카오페이·네이버페이처럼 계좌·카드가 아닌 결제", suggestEasy));
    sheet.setBody(
      h(
        "div",
        { class: "sheet-top" },
        h("span", { class: "meta" }, `${ctx.nameOf(u.owner)} · ${won(u.count)}건 · ${fullDate(u.first_date)} ~ ${fullDate(u.last_date)}`),
        h("span", { class: "meta" }, `지출 ${won(u.expense)} · 수입 ${won(u.income)} · 이체 ${won(u.transfer)}건`),
        h("span", { class: "meta" }, `지금: ${u.linked ? u.link_label : "연결 안 됨"}`),
      ),
      h(
        "section",
        { class: "sheet-sec" },
        h("h3", {}, "어디에 이을까요?"),
        suggestEasy ? easy : null,
        groups.map(([title, list]) =>
          list.length ? h("div", { class: "pick-group" }, h("span", { class: "k" }, title), list) : null,
        ),
        suggestEasy ? null : easy,
        !accounts.length && !cards.length ? h("p", { class: "hint" }, `${ctx.nameOf(u.owner)}에게 등록한 계좌·카드가 아직 없어요. 아래에서 새로 등록하면서 바로 이을 수 있어요.`) : null,
        error ? h("p", { class: "form-error", role: "alert" }, error) : null,
        h("button", { class: "btn", type: "button", disabled: !pick || busy, onclick: save }, busy ? "잇는 중…" : "연결하기"),
      ),
      h(
        "section",
        { class: "sheet-sec" },
        h("h3", {}, "목록에 없으면"),
        h(
          "div",
          { class: "sec-actions" },
          ["card", "account"].map((kind) =>
            h(
              "button",
              {
                class: "btn secondary small", type: "button",
                onclick: () => {
                  sheet.close();
                  openItemSheet(page, kind, null, { owner: u.owner, link: u.instrument, guess: u.instrument });
                },
              },
              kind === "card" ? "+ 새 카드로 등록하고 잇기" : "+ 새 계좌로 등록하고 잇기",
            ),
          ),
        ),
        u.linked
          ? h(
              "button",
              {
                class: "link-btn danger", type: "button",
                onclick: async () => {
                  try {
                    await api.post("/api/instruments/unlink", { owner: u.owner, instrument: u.instrument });
                    changed = true;
                    toast("연결을 끊었어요");
                    sheet.close();
                  } catch (e) {
                    error = e instanceof ApiError ? e.message : "연결을 끊지 못했어요";
                    draw();
                  }
                },
              },
              "연결 끊기",
            )
          : null,
      ),
    );
  }
  draw();
}

// --- 계좌·카드 등록·수정 -------------------------------------------------------------

function openItemSheet(page, kind, item, { owner, link = null, guess = "" }) {
  const { ctx } = page;
  const isAccount = kind === "account";
  const isNew = !item;
  const word = isAccount ? "계좌" : "카드";
  let changed = false;
  let error = null;
  let busy = false;
  let confirmClose = false;
  const v = {
    owner: item ? item.owner : owner,
    org: item ? (isAccount ? item.institution : item.issuer) : "",
    name: item ? item.name : guess,
    kind: item ? item.kind : isAccount ? "checking" : "credit",
    last4: item?.last4 || "",
    opened_on: item?.opened_on || "",
    pay_day: item?.pay_day ? String(item.pay_day) : "",
    period_start_day: item?.period_start_day ? String(item.period_start_day) : "",
    period_month_offset: item?.period_month_offset !== null && item?.period_month_offset !== undefined ? String(item.period_month_offset) : "",
    pay_account_id: item?.pay_account ? String(item.pay_account.id) : "",
    annual_fee: item?.annual_fee !== null && item?.annual_fee !== undefined ? String(item.annual_fee) : "",
    color: item?.color || "",
    number_hint: item?.number_hint || "",
    memo: item?.memo || "",
  };
  const sheet = openSheet({
    title: isNew ? `${word} 추가` : item.name,
    onClose: () => changed && page.refresh(),
  });

  const input = (key, label, attrs = {}) =>
    h(
      "label",
      { class: "field" },
      h("span", { class: "k" }, label),
      h("input", {
        class: "text-input", type: "text", value: v[key], autocomplete: "off", ...attrs,
        oninput: (e) => {
          v[key] = e.target.value;
          if (attrs.onchanged) attrs.onchanged();
        },
      }),
    );
  const select = (key, label, list, onchange) =>
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
            if (onchange) onchange();
          },
        },
        list.map(([code, text]) => h("option", { value: code, selected: String(code) === String(v[key]) }, text)),
      ),
    );

  function periodText() {
    if (v.kind === "check") return "체크카드는 쓰는 날 바로 출금돼요";
    const start = Number(v.period_start_day) || 1;
    const offset = v.period_month_offset === "" ? "-1" : v.period_month_offset;
    const base = !v.period_start_day && v.period_month_offset === "" ? " (비워 두면 이렇게 봐요)" : "";
    if (start === 1) return `이용기간: ${MONTHS[offset]} 1일 ~ 말일${base}`;
    return `이용기간: ${MONTHS[offset]} ${start}일 ~ ${END_MONTHS[offset]} ${start - 1}일`;
  }

  function body() {
    const intOrNull = (s) => (s.trim() === "" ? null : Number(s.trim()));
    const out = {
      [isAccount ? "institution" : "issuer"]: v.org.trim(),
      name: v.name.trim(),
      kind: v.kind,
      last4: v.last4.trim(),
      color: v.color,
      number_hint: v.number_hint.trim(),
      memo: v.memo.trim(),
    };
    if (isAccount) out.opened_on = v.opened_on.trim();
    else {
      out.pay_day = intOrNull(v.pay_day) ?? 0; // 0 = 지우기
      const start = intOrNull(v.period_start_day);
      const offset = v.period_month_offset === "" ? null : Number(v.period_month_offset);
      if (start !== null || offset !== null) {
        out.period_start_day = start ?? 1;
        out.period_month_offset = offset ?? -1;
      } else {
        out.period_start_day = 0; // 둘 다 0 = 지우기 (기본 '전월 1일 ~ 말일')
        out.period_month_offset = 0;
      }
      out.pay_account_id = v.pay_account_id ? Number(v.pay_account_id) : 0;
      const fee = intOrNull(v.annual_fee.replaceAll(",", ""));
      if (fee !== null) out.annual_fee = fee;
    }
    if (isNew) {
      // 새로 넣을 때는 빈 칸과 '지우기' 표시(0)는 보내지 않는다. 당월(0)은 진짜 값이라 남긴다.
      for (const k of Object.keys(out)) if (out[k] === "") delete out[k];
      if (out.pay_day === 0) delete out.pay_day;
      if (out.pay_account_id === 0) delete out.pay_account_id;
      if (out.period_start_day === 0) {
        delete out.period_start_day;
        delete out.period_month_offset;
      }
      out.owner = v.owner;
      if (link) out.link = link;
    }
    return out;
  }

  async function save() {
    if (busy) return;
    if (!v.org.trim() || !v.name.trim()) {
      error = `${isAccount ? "기관" : "카드사"}과 이름은 꼭 넣어 주세요`;
      draw();
      return;
    }
    busy = true;
    error = null;
    draw();
    const path = isAccount ? "/api/accounts" : "/api/cards";
    try {
      if (isNew) await api.post(path, body());
      else await api.patch(`${path}/${item.id}`, body());
      changed = true;
      toast(isNew ? (link ? `등록하고 「${link}」에 이었어요` : "등록했어요") : "저장했어요");
      sheet.close();
    } catch (e) {
      busy = false;
      error = e instanceof ApiError ? e.message : "저장하지 못했어요";
      draw();
    }
  }

  async function closeOrReopen() {
    const path = `${isAccount ? "/api/accounts" : "/api/cards"}/${item.id}/${item.active ? "close" : "reopen"}`;
    try {
      await api.post(path, {});
      changed = true;
      toast(item.active ? "해지로 표시했어요" : "다시 쓰는 것으로 바꿨어요");
      sheet.close();
    } catch (e) {
      error = e instanceof ApiError ? e.message : "바꾸지 못했어요";
      draw();
    }
  }

  function draw() {
    const kinds = (isAccount ? options.account_kinds : options.card_kinds).map((o) => [o.code, o.label]);
    const payAccounts = page.accounts.filter((a) => a.owner === v.owner && (a.active || String(a.id) === v.pay_account_id));
    const period = h("p", { class: "hint" }, periodText());
    sheet.setBody(
      link ? h("p", { class: "note-box ok" }, `등록하면 뱅샐 결제수단 「${link}」에 바로 이어요.`) : null,
      isNew && !link
        ? h(
            "div",
            { class: "field" },
            h("span", { class: "k" }, "누구 것인가요?"),
            h(
              "div",
              { class: "seg", role: "group", aria: { label: "주인" } },
              ctx.members.map((m) =>
                h(
                  "button",
                  {
                    type: "button", aria: { pressed: v.owner === m.slug },
                    onclick: () => {
                      v.owner = m.slug;
                      v.pay_account_id = "";
                      draw();
                    },
                  },
                  m.name,
                ),
              ),
            ),
          )
        : h("p", { class: "hint" }, `${ctx.nameOf(v.owner)}의 ${word}`),
      h(
        "div",
        { class: "form-grid" },
        input("org", isAccount ? "기관 *" : "카드사 *", { maxlength: 30, placeholder: isAccount ? "예: OO은행" : "예: OO카드" }),
        input("name", "이름 *", { maxlength: 40, placeholder: isAccount ? "예: 월급 통장" : "예: 생활비 카드" }),
        select("kind", "종류", kinds, () => draw()),
        input("last4", "번호 끝 4자리", { maxlength: 4, inputmode: "numeric", pattern: "[0-9]*", placeholder: "예: 0937" }),
      ),
      isAccount
        ? input("opened_on", "개설일", { maxlength: 10, placeholder: "예: 2024-03 또는 2024-03-15" })
        : h(
            "div",
            { class: "stack-6" },
            h(
              "div",
              { class: "form-grid" },
              input("pay_day", "결제일 (매달)", { maxlength: 2, inputmode: "numeric", placeholder: "예: 14" }),
              select("pay_account_id", "결제 계좌", [["", "고르지 않음"], ...payAccounts.map((a) => [String(a.id), a.name])]),
            ),
            v.kind === "check"
              ? null
              : h(
                  "div",
                  { class: "form-grid" },
                  input("period_start_day", "이용기간 시작일", {
                    maxlength: 2, inputmode: "numeric", placeholder: "예: 1",
                    onchanged: () => {
                      period.textContent = periodText();
                    },
                  }),
                  select("period_month_offset", "시작 달", [["", "기본 (전월)"], ["-2", "전전월"], ["-1", "전월"], ["0", "당월"]], () => {
                    period.textContent = periodText();
                  }),
                ),
            period,
            input("annual_fee", "연회비 (원)", { maxlength: 9, inputmode: "numeric", placeholder: "예: 15000" }),
          ),
      h(
        "div",
        { class: "field" },
        h("span", { class: "k" }, "색 (목록에서 구분용)"),
        h(
          "div",
          { class: "swatches", role: "group", aria: { label: "색" } },
          h("button", { class: "swatch-btn none", type: "button", aria: { pressed: !v.color, label: "색 없음" }, onclick: () => { v.color = ""; draw(); } }, "없음"),
          COLORS.map((c) =>
            h("button", {
              class: "swatch-btn", type: "button", style: { background: c }, aria: { pressed: v.color === c, label: `색 ${c}` },
              onclick: () => {
                v.color = c;
                draw();
              },
            }),
          ),
        ),
      ),
      input("number_hint", "전체 번호 보는 곳", { maxlength: 100, placeholder: "예: 은행 앱 › 계좌 상세 (번호 자체는 적지 마세요)" }),
      input("memo", "메모", { maxlength: 100 }),
      error ? h("p", { class: "form-error", role: "alert" }, error) : null,
      h(
        "div",
        { class: "pick-actions" },
        h("button", { class: "btn secondary", type: "button", onclick: () => sheet.close() }, "취소"),
        h("button", { class: "btn grow", type: "button", disabled: busy, onclick: save }, busy ? "저장하는 중…" : isNew ? "등록" : "저장"),
      ),
      isNew ? null : manageSection(),
    );
  }

  function manageSection() {
    const names = item.linked_instruments || [];
    return h(
      "section",
      { class: "sheet-sec" },
      h("h3", {}, "연결된 뱅샐 결제수단"),
      h("p", { class: names.length ? "" : "hint" }, names.length ? names.join(", ") : "아직 없어요. 계좌·카드 화면 아래 '뱅샐 결제수단'에서 이을 수 있어요."),
      item.active
        ? confirmClose
          ? h(
              "div",
              { class: "stack-6" },
              h("p", { class: "note-box" }, `오늘 날짜로 해지 표시를 해요. 지우지 않고 기록은 남아요. 연결된 결제수단도 그대로예요.`),
              h(
                "div",
                { class: "pick-actions" },
                h("button", { class: "btn secondary", type: "button", onclick: () => { confirmClose = false; draw(); } }, "그만두기"),
                h("button", { class: "btn grow danger-btn", type: "button", onclick: closeOrReopen }, "해지로 표시"),
              ),
            )
          : h("button", { class: "link-btn danger", type: "button", onclick: () => { confirmClose = true; draw(); } }, `${word} 해지로 표시하기`)
        : h(
            "div",
            { class: "stack-6" },
            h("p", { class: "hint" }, `${item.closed_on} 해지`),
            h("button", { class: "btn secondary small", type: "button", onclick: closeOrReopen }, "다시 쓰는 것으로 바꾸기"),
          ),
    );
  }

  draw();
}