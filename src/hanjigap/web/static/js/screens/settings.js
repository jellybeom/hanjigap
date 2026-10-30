// 설정 (G8): 화면(테마, G9a) · 구성원 이름·별칭 · 분류 규칙 · 바로가기 · 앱 정보
// 데이터: /api/members, /api/class-rules, /api/months, /api/health
// 로그인 이메일은 웹에서 바꾸지 않는다 (로그인 열쇠라서 미니PC 명령어로만).

import { api, ApiError } from "../api.js";
import { h, icon, replace, toast } from "../dom.js";
import { monthLabel } from "../format.js";
import { ICONS } from "../icons.js";
import { MODES, THEMES, current, setMode, setTheme } from "../theme.js";
import { cols } from "../ui.js";

const state = { renaming: null }; // 이름을 고치는 중인 구성원 slug

export async function render(root, ctx, _screen, isCurrent) {
  const [members, rules, months, health] = await Promise.all([
    api.get("/api/members"),
    api.get("/api/class-rules"),
    api.get("/api/months"),
    api.get("/api/health"),
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
        h(
          "p",
          { class: "hint" },
          "로그인 이메일은 로그인 열쇠라서 여기서 바꾸지 않아요. 바꿀 때는 미니PC에서 'uv run hanjigap member update 구성원 --email 새주소'를 실행해 주세요.",
        ),
      ),
    ]),
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