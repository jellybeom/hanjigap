// 설정 (G8): 화면(테마, G9a) · 구성원 이름·별칭 · 분류 규칙 · 바로가기 · 백업(H1) · 앱 정보·로그아웃
// 데이터: /api/members, /api/class-rules, /api/months, /api/health, /api/backups
// 되돌리기는 웹에서 하지 않는다 (미니PC에서 서버를 끄고 명령어로만).
// 로그인 이메일은 웹에서 바꾸지 않는다 (로그인 열쇠라서 미니PC 명령어로만).

import { api, ApiError } from "../api.js";
import { openSheet } from "../sheet.js";
import { h, icon, replace, toast } from "../dom.js";
import { monthLabel } from "../format.js";
import { ICONS } from "../icons.js";
import { MODES, THEMES, current, setMode, setTheme } from "../theme.js";
import { cols } from "../ui.js";

const state = { renaming: null }; // 이름을 고치는 중인 구성원 slug
const SAMSUNG = /SamsungBrowser/.test(navigator.userAgent);

export async function render(root, ctx, _screen, isCurrent) {
  const [members, rules, months, health, backups] = await Promise.all([
    api.get("/api/members"),
    api.get("/api/class-rules"),
    api.get("/api/months"),
    api.get("/api/health"),
    api.get("/api/backups"),
  ]);
  if (!isCurrent()) return;
  const rerender = () => render(root, ctx, _screen, isCurrent);
  const active = rules.items.filter((r) => r.active).length;
  replace(
    root,
    h("header", { class: "topbar" }, h("h1", { class: "screen-title" }, "설정")),
    cols([
      displaySection(rerender),
      h(
        "section",
        { class: "stack-12", aria: { label: "구성원" } },
        h("h2", { class: "section-title" }, "구성원"),
        members.items.map((m) => memberCard(ctx, m, rerender)),
        h("p", { class: "hint" }, "거래 내용에 상대방의 이름이나 별칭이 들어간 일반 이체는 부부 사이 이체로 보고 합계에서 빼요."),
      ),
    ], [
      h(
        "section",
        { class: "stack-8", aria: { label: "분류" } },
        h("h2", { class: "section-title" }, "분류"),
        linkRow("#/rules", "분류 규칙", rules.items.length ? `규칙 ${rules.items.length}개 · 켜진 것 ${active}개` : "아직 없어요 · 확인 필요 정리에서 '앞으로도 이렇게'로 만들어져요"),
        linkRow("#/review", "확인 필요 정리", "수입인지 지출인지 정하지 않은 거래를 하나씩"),
      ),
      h(
        "section",
        { class: "stack-8", aria: { label: "바로가기" } },
        h("h2", { class: "section-title" }, "바로가기"),
        linkRow("#/holdings", "계좌 · 카드 관리", "등록 · 해지 · 뱅샐 결제수단 연결"),
        linkRow("#/ledger?mode=fixed", "고정비", "이번 달 빠져나갔는지 · 등록 · 마감"),
        linkRow("#/settle", "정산", "뱅샐 파일 올리기 · 정산 기록"),
      ),
      backupCard(backups, rerender),
      h(
        "section",
        { class: "card", aria: { label: "앱 정보" } },
        h("div", { class: "card-head" }, h("h2", {}, "앱 정보")),
        h(
          "dl",
          { class: "facts" },
          fact("로그인한 사람", ctx.me.name),
          fact("마지막 정산", months.items.length ? monthLabel(months.items[0].month) : "아직 없음"),
          fact("버전", `한지갑 ${health.version}`),
        ),
        ctx.logoutUrl
          ? h("button", { class: "btn secondary", type: "button", onclick: () => confirmLogout(ctx, ctx.logoutUrl) }, "로그아웃")
          : null,
        h(
          "p",
          { class: "hint" },
          "로그인 이메일은 로그인 열쇠라서 여기서 바꾸지 않아요. 바꿀 때는 미니PC에서 'uv run hanjigap member update 구성원 --email 새주소'를 실행해 주세요.",
        ),
      ),
    ]),
  );
}

// --- 로그아웃 (Cloudflare Access) -------------------------------------------------

// Cloudflare Access 로그아웃은 그 사람(메일)의 세션 전체를 끊는다. (다른 기기·브라우저도 함께)
function confirmLogout(ctx, url) {
  const sheet = openSheet({ title: "로그아웃" });
  sheet.setBody(
    h(
      "p",
      {},
      `로그아웃할까요? ${ctx.me.name} 님 메일로 로그인된 모든 기기와 브라우저에서 함께 로그아웃돼요. 다시 들어올 때 메일로 받는 코드가 필요해요.`,
    ),
    h(
      "div",
      { class: "stack-8" },
      h("button", { class: "btn", type: "button", onclick: () => window.location.assign(url) }, "로그아웃"),
      h("button", { class: "btn secondary", type: "button", onclick: () => sheet.close() }, "그만두기"),
    ),
  );
}

// --- 화면 (테마) -----------------------------------------------------------------

function displaySection(rerender) {
  const now = current();
  return h(
    "section",
    { class: "stack-8", aria: { label: "화면" } },
    h("h2", { class: "section-title" }, "화면"),
    h(
      "div",
      { class: "card" },
      h("span", { class: "k" }, "모드"),
      h(
        "div",
        { class: "seg", role: "group", aria: { label: "화면 모드" } },
        MODES.map(([key, label]) =>
          h(
            "button",
            {
              type: "button", aria: { pressed: now.mode === key },
              onclick: () => {
                setMode(key);
                rerender();
              },
            },
            label,
          ),
        ),
      ),
      h("span", { class: "k" }, "색 테마"),
      h(
        "div",
        { class: "theme-grid", role: "group", aria: { label: "색 테마" } },
        THEMES.map((t) =>
          h(
            "button",
            {
              class: "theme-opt", type: "button", aria: { pressed: now.theme === t.key },
              onclick: () => {
                setTheme(t.key);
                rerender();
              },
            },
            h("span", { class: "theme-dots", "aria-hidden": "true" }, t.swatch.map((c) => h("span", { class: "theme-dot", style: { background: c } }))),
            h("span", { class: "theme-name" }, t.name),
            h("span", { class: "theme-desc" }, t.desc),
            now.theme === t.key ? icon(ICONS.check, 16, { strokeWidth: 2.4 }) : null,
          ),
        ),
      ),
      h("p", { class: "hint" }, "이 기기에만 적용돼요. '시스템'은 휴대폰·PC의 밝게/어둡게 설정을 따라가요."),
      SAMSUNG
        ? h(
            "p",
            { class: "hint" },
            "삼성 인터넷은 자체 다크 모드가 켜져 있으면 한지갑의 색을 덮어써요. 삼성 인터넷 설정에서 '밝게 보기'를 고르거나 실험실의 '웹사이트 다크 테마 사용'을 켜 주세요. 이 브라우저에서 '시스템'은 휴대폰이 아니라 삼성 인터넷의 설정을 따라가요.",
          )
        : null,
    ),
  );
}

// --- 구성원 --------------------------------------------------------------------

function memberCard(ctx, m, rerender) {
  const isMe = m.slug === ctx.me.slug;
  const aliasInput = h("input", {
    class: "text-input", type: "text", maxlength: 20, placeholder: "별칭 더하기 (예: 애칭, 실명)", autocomplete: "off",
    aria: { label: `${m.name} 별칭 더하기` },
    onkeydown: (e) => {
      if (e.key === "Enter") addAlias();
    },
  });

  async function changeAliases(body, message) {
    try {
      await api.put(`/api/members/${encodeURIComponent(m.slug)}/aliases`, body);
      toast(message);
      rerender();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "바꾸지 못했어요");
    }
  }

  function addAlias() {
    const words = aliasInput.value.split(",").map((w) => w.trim()).filter(Boolean);
    if (words.length) changeAliases({ add: words }, `별칭을 더했어요: ${words.join(", ")}`);
  }

  return h(
    "div",
    { class: "card member-card" },
    state.renaming === m.slug ? renameRow(ctx, m, rerender) : h(
      "div",
      { class: "card-head center" },
      h("h3", { class: "member-name" }, m.name, isMe ? h("span", { class: "chip small" }, "나") : null),
      h(
        "button",
        {
          class: "link-btn", type: "button",
          onclick: () => {
            state.renaming = m.slug;
            rerender();
          },
        },
        "이름 바꾸기",
      ),
    ),
    h("span", { class: "k" }, "별칭"),
    h(
      "div",
      { class: "chips" },
      m.aliases.length === 0 ? h("span", { class: "hint" }, "아직 없어요") : null,
      m.aliases.map((a) =>
        h(
          "span",
          { class: "alias-chip" },
          a,
          h(
            "button",
            { class: "alias-x", type: "button", aria: { label: `별칭 ${a} 지우기` }, onclick: () => changeAliases({ remove: [a] }, `별칭 「${a}」을 지웠어요`) },
            icon(ICONS.close, 14, { strokeWidth: 2.4 }),
          ),
        ),
      ),
    ),
    h("div", { class: "pw-row" }, aliasInput, h("button", { class: "btn secondary small", type: "button", onclick: addAlias }, "더하기")),
    h("p", { class: "hint" }, "2~20자. 쉼표로 여러 개를 한 번에 넣을 수 있어요."),
  );
}

function renameRow(ctx, m, rerender) {
  const input = h("input", { class: "text-input", type: "text", value: m.name, maxlength: 20, autocomplete: "off", aria: { label: "새 이름" } });
  const save = async () => {
    try {
      await api.patch(`/api/members/${encodeURIComponent(m.slug)}`, { name: input.value });
      state.renaming = null;
      toast("이름을 바꿨어요");
      ctx.me = null; // 화면 곳곳의 이름도 새로 받아 온다
      ctx.refresh();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "바꾸지 못했어요");
    }
  };
  queueMicrotask(() => input.focus());
  return h(
    "div",
    { class: "stack-8" },
    h("div", { class: "pw-row" }, input, h("button", { class: "btn small", type: "button", onclick: save }, "저장")),
    h(
      "button",
      {
        class: "link-btn muted-link", type: "button",
        onclick: () => {
          state.renaming = null;
          rerender();
        },
      },
      "취소",
    ),
    h("p", { class: "hint" }, "화면에 보이는 이름만 바뀌어요. 이 이름도 부부 사이 이체를 찾는 데 쓰여요."),
  );
}

// --- 백업 ----------------------------------------------------------------------

function backupCard(b, rerender) {
  let busy = false;
  const button = h(
    "button",
    {
      class: "chip-btn", type: "button",
      onclick: async () => {
        if (busy) return;
        busy = true;
        button.disabled = true;
        try {
          const r = await api.post("/api/backups");
          toast(r.message);
          rerender();
        } catch (e) {
          toast(e instanceof ApiError ? e.message : "백업하지 못했어요");
          busy = false;
          button.disabled = false;
        }
      },
    },
    "지금 백업",
  );
  const last = b.last ? `${dateTime(b.last.at)} · ${b.last.label}` : "아직 없어요";
  const copy = b.copy.on
    ? `켜짐 · ${b.copy.count}개${b.copy.last ? ` · 마지막 ${dateTime(b.copy.last.at)}` : ""}`
    : "꺼짐";
  return h(
    "section",
    { class: "card", aria: { label: "백업" } },
    h("div", { class: "card-head center" }, h("h2", {}, "백업"), button),
    h(
      "dl",
      { class: "facts" },
      fact("마지막 백업", last),
      fact("보관", `${b.count}개 · 매일 ${b.counts.daily} · 정산 직전 ${b.counts.settle} · 직접 ${b.counts.manual}`),
      fact("두 번째 사본", copy),
    ),
    h(
      "p",
      { class: "hint" },
      "서버가 켜질 때와 매일 한 번, 정산을 저장하기 직전에 자동으로 백업해요. 되돌리기는 미니PC에서 서버를 끄고 'uv run hanjigap db restore 파일'로 해요.",
    ),
  );
}

/** "2026-10-30T11:07:00" → "10월 30일 11:07" (서버 시간 그대로) */
function dateTime(iso) {
  const [date, time] = iso.split("T");
  const [, m, d] = date.split("-").map(Number);
  return `${m}월 ${d}일 ${time.slice(0, 5)}`;
}

// --- 조각 ----------------------------------------------------------------------

function linkRow(href, title, sub) {
  return h(
    "a",
    { class: "row-btn", href },
    h("span", { class: "body" }, h("span", { class: "v" }, title), h("span", { class: "k" }, sub)),
    icon(ICONS.next, 18, { strokeWidth: 2 }),
  );
}

function fact(label, value) {
  return h("div", { class: "fact" }, h("dt", {}, label), h("dd", {}, value));
}