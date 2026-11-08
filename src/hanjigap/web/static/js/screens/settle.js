// 정산 (G4): 현황 → 1 파일 고르기 → 2 미리보기(아직 저장 전) → 3 결과
// 데이터: /api/months, POST /api/uploads, POST /api/uploads/{id}/apply, DELETE /api/uploads/{id}
// 비밀번호·파일은 서버에 저장하지 않는다. 읽은 내용만 15분 동안 메모리에 둔다 (uploads_api.py).
// 주소로 바로 열기: #/settle?owner=jaehee&month=2026-10 → 그 사람·그 달로 1단계
// 처음 올리는 사람은 미리보기에서 시작 방법을 고른다: 이 달만 넣기(기본) / 지난 달도 함께 넣기(첫 적재)

import { api, ApiError } from "../api.js";
import { h, icon, replace, toast } from "../dom.js";
import { monthLabel, monthShort, shiftMonth, won } from "../format.js";
import { ICONS } from "../icons.js";

const STATUS_TEXT = { settled: "정산됨", baseline: "기준 데이터" };
const KIND_TEXT = {
  first_load: "첫 적재",
  new: "새 달 정산",
  baseline_to_settled: "새 달 정산",
  resettle: "다시 정산",
};
const FRIENDLY = {
  PASSWORD_REQUIRED: "잠긴 파일이에요. 뱅샐에서 내보낼 때 정한 비밀번호를 넣어 주세요",
  WRONG_PASSWORD: "비밀번호가 맞지 않아요. 다시 확인해 주세요",
  DECRYPT_FAILED: "파일을 열지 못했어요. 비밀번호를 확인하거나 파일을 다시 받아 주세요",
  NOT_EXCEL: "뱅샐에서 받은 zip 또는 xlsx 파일을 골라 주세요",
  ZIP_NO_EXCEL: "zip 안에 엑셀 파일이 없어요. 뱅샐에서 받은 파일이 맞는지 확인해 주세요",
  LEDGER_SHEET_NOT_FOUND: "'가계부 내역' 시트가 없어요. 뱅샐에서 받은 파일이 맞는지 확인해 주세요",
};
const EYE = "M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12zM12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z";

// 이 화면 안에서만 기억 (다른 탭에 다녀와도 미리보기가 남아 있게)
const state = {
  step: "status", // status | file | preview | result
  owner: null,
  month: null,
  otherMonth: false,
  file: null,
  password: "",
  busy: false,
  error: null,
  preview: null,
  previewAt: 0, // 미리보기를 받은 시각 (남은 시간 계산)
  result: null,
  firstMode: "settle", // 처음 올릴 때: settle(이 달만) | first_load(지난 달도 함께)
};
let timer = null;

function lastMonth() {
  const d = new Date();
  const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  return shiftMonth(ym, -1);
}

function startFile(owner, month) {
  state.step = "file";
  state.owner = owner;
  state.month = month || lastMonth();
  state.otherMonth = state.month !== lastMonth();
  state.file = null;
  state.password = "";
  state.error = null;
  state.busy = false;
}

export async function render(root, ctx, _screen, isCurrent) {
  clearInterval(timer);
  const rerender = () => render(root, ctx, _screen, isCurrent);
  const query = new URLSearchParams(window.location.hash.split("?")[1] || "");
  if (query.has("owner") && state.step !== "preview") {
    const owner = ctx.members.some((m) => m.slug === query.get("owner")) ? query.get("owner") : ctx.me.slug;
    startFile(owner, /^\d{4}-\d{2}$/.test(query.get("month") || "") ? query.get("month") : null);
    history.replaceState(null, "", "#/settle"); // 다시 그릴 때 또 열리지 않게
  }
  if (state.step === "file") return renderFile(root, ctx, rerender);
  if (state.step === "preview") return renderPreview(root, ctx, rerender);
  if (state.step === "result") return renderResult(root, ctx, rerender);
  const months = await api.get("/api/months");
  if (!isCurrent()) return;
  renderStatus(root, ctx, months, rerender);
}

// --- 현황 -----------------------------------------------------------------------

function renderStatus(root, ctx, months, rerender) {
  const target = lastMonth();
  const row = months.items.find((m) => m.month === target);
  const statusOf = (slug) => row?.members.find((x) => x.owner === slug) || null;
  replace(
    root,
    h("header", { class: "topbar" }, h("h1", { class: "screen-title" }, "정산")),
    h(
      "section",
      { class: "card", aria: { label: "이번에 정산할 달" } },
      h("div", { class: "stack-4" }, h("span", { class: "k" }, "이번에 정산할 달"), h("span", { class: "settle-month" }, monthLabel(target))),
      h(
        "div",
        { class: "people" },
        ctx.members.map((m) => {
          const s = statusOf(m.slug);
          const done = s && s.status === "settled";
          return h(
            "div",
            { class: "person-row" },
            h(
              "span",
              { class: "body" },
              h("span", { class: "v" }, m.name),
              h("span", { class: `state${done ? " pos" : " warn"}` }, s ? `${STATUS_TEXT[s.status]} · ${won(s.tx_count)}건` : "정산 전"),
            ),
            h(
              "button",
              {
                class: `btn small${done ? " secondary" : ""}`, type: "button",
                onclick: () => {
                  startFile(m.slug, target);
                  rerender();
                },
              },
              icon(ICONS.upload, 16, { strokeWidth: 2 }),
              done ? "다시 올리기" : m.slug === ctx.me.slug ? "올리기" : "대신 올리기",
            ),
          );
        }),
      ),
      h("p", { class: "hint" }, `매달 초에 지난달 파일을 올려요. 확정하기 전에 미리보기로 먼저 보여 드려요.`),
    ),
    guide(),
    history_(ctx, months),
  );
}

function guide() {
  return h(
    "details",
    { class: "card guide" },
    h("summary", {}, h("span", {}, "뱅샐 파일 받는 법"), icon(ICONS.next, 16, { strokeWidth: 2 })),
    h(
      "ol",
      { class: "guide-list" },
      h("li", {}, "뱅크샐러드 앱에서 가계부 내역을 엑셀로 내보내요. 기간은 정산할 달의 1일부터 말일까지가 모두 들어가게 해 주세요. 더 길어도 괜찮아요."),
      h("li", {}, "내보낼 때 정한 비밀번호를 기억해 두세요. 받은 zip 파일은 휴대폰이나 PC에 저장해요."),
      h("li", {}, "여기서 [올리기]를 누르고 그 파일과 비밀번호를 넣어요."),
    ),
    h("p", { class: "hint" }, "파일과 비밀번호는 저장하지 않아요. 읽은 내용만 15분 동안 잠깐 두고, 확정하거나 취소하면 바로 지워요."),
  );
}

function history_(ctx, months) {
  const items = months.items.slice(0, 12);
  return h(
    "section",
    { class: "card", aria: { label: "정산 기록" } },
    h("div", { class: "card-head" }, h("h2", {}, "정산 기록"), h("span", { class: "side" }, "최근 12개월")),
    items.length === 0
      ? h("p", { class: "empty" }, "아직 정산한 달이 없어요. 위에서 첫 파일을 올려 주세요. 처음 올릴 때 '이 달만 넣기'와 '지난 달도 함께 넣기' 중에서 고를 수 있어요.")
      : h(
          "div",
          { class: "hist" },
          items.map((m) =>
            h(
              "div",
              { class: "hist-row" },
              h("span", { class: "hist-month" }, monthLabel(m.month)),
              h(
                "span",
                { class: "hist-people" },
                ctx.members.map((p) => {
                  const s = m.members.find((x) => x.owner === p.slug);
                  return h(
                    "span",
                    { class: `chip small${s ? "" : " outline-warn"}` },
                    `${p.name} ${s ? STATUS_TEXT[s.status] : "정산 전"}`,
                  );
                }),
              ),
            ),
          ),
        ),
  );
}

// --- 1단계: 파일 고르기 -------------------------------------------------------------

function topbar(title, onBack) {
  return h(
    "header",
    { class: "topbar" },
    h("button", { class: "icon-btn plain edge-left", type: "button", aria: { label: "뒤로" }, onclick: onBack }, icon(ICONS.prev, 22, { strokeWidth: 2 })),
    h("h1", { class: "bar-title" }, title),
    h("span", { class: "bar-side" }),
  );
}

function steps(current) {
  const names = ["파일", "미리보기", "완료"];
  return h(
    "ol",
    { class: "steps", aria: { label: `${names.length}단계 중 ${current}단계` } },
    names.map((n, i) =>
      h(
        "li",
        { class: i + 1 < current ? "done" : i + 1 === current ? "on" : "", aria: { current: i + 1 === current ? "step" : null } },
        h("span", { class: "num" }, i + 1 < current ? icon(ICONS.check, 14, { strokeWidth: 2.6 }) : String(i + 1)),
        h("span", {}, n),
      ),
    ),
  );
}

function renderFile(root, ctx, rerender) {
  const back = () => {
    state.step = "status";
    rerender();
  };
  const isMe = state.owner === ctx.me.slug;
  const fileInput = h("input", {
    class: "sr-only", type: "file", id: "settle-file", accept: ".zip,.xlsx",
    onchange: (e) => {
      state.file = e.target.files[0] || null;
      state.error = null;
      rerender();
    },
  });
  const pw = h("input", {
    class: "text-input", type: "password", id: "settle-pw", value: state.password,
    autocomplete: "off", autocapitalize: "off", spellcheck: "false", placeholder: "뱅샐에서 내보낼 때 정한 비밀번호",
    oninput: (e) => {
      state.password = e.target.value;
    },
    onkeydown: (e) => {
      if (e.key === "Enter") send();
    },
  });
  const months = [];
  for (let i = 0; i < 13; i += 1) months.push(shiftMonth(lastMonth(), -i));
  if (!months.includes(state.month)) months.push(state.month);

  async function send() {
    if (!state.file || state.busy) return;
    state.busy = true;
    state.error = null;
    rerender();
    const form = new FormData();
    form.append("file", state.file);
    if (state.password) form.append("password", state.password);
    form.append("owner", state.owner);
    form.append("month", state.month);
    try {
      state.preview = await api.upload("/api/uploads", form);
      state.firstMode = "settle";
      state.previewAt = Date.now();
      state.password = ""; // 다 썼으면 화면에서도 지운다
      state.step = "preview";
    } catch (error) {
      const code = error instanceof ApiError ? error.body.code : null;
      state.error = FRIENDLY[code] || (error instanceof ApiError ? error.message : "파일을 읽지 못했어요");
      state.errorCode = code;
      if (!(error instanceof ApiError)) console.error(error);
    }
    state.busy = false;
    rerender();
  }

  replace(
    root,
    topbar("파일 올리기", back),
    steps(1),
    h(
      "section",
      { class: "card" },
      h(
        "div",
        { class: "field" },
        h("span", { class: "k" }, "누구 것인가요?"),
        h(
          "div",
          { class: "seg", role: "group", aria: { label: "누구 파일" } },
          ctx.members.map((m) =>
            h(
              "button",
              {
                type: "button", aria: { pressed: state.owner === m.slug },
                onclick: () => {
                  state.owner = m.slug;
                  state.error = null;
                  rerender();
                },
              },
              m.name,
            ),
          ),
        ),
        isMe ? null : h("span", { class: "hint warn" }, `${ctx.nameOf(state.owner)} 대신 올리는 중이에요. ${ctx.nameOf(state.owner)}의 뱅샐 파일이 맞는지 확인해 주세요.`),
      ),
      h(
        "div",
        { class: "field" },
        h("span", { class: "k" }, "어느 달인가요?"),
        state.otherMonth
          ? h(
              "select",
              {
                class: "text-input", aria: { label: "정산할 달" },
                onchange: (e) => {
                  state.month = e.target.value;
                },
              },
              months.map((m) => h("option", { value: m, selected: m === state.month }, m === lastMonth() ? `${monthLabel(m)} (지난달)` : monthLabel(m))),
            )
          : h(
              "div",
              { class: "month-line" },
              h("strong", {}, `${monthLabel(state.month)} (지난달)`),
              h(
                "button",
                {
                  class: "link-btn", type: "button",
                  onclick: () => {
                    state.otherMonth = true;
                    rerender();
                  },
                },
                "다른 달 다시 정산하기",
              ),
            ),
      ),
      h(
        "div",
        { class: "field" },
        h("span", { class: "k" }, "뱅샐 파일"),
        fileInput,
        h(
          "label",
          { class: `drop${state.file ? " has" : ""}`, for: "settle-file" },
          icon(state.file ? ICONS.check : ICONS.upload, 22, { strokeWidth: 2 }),
          state.file
            ? h("span", { class: "drop-text" }, h("strong", {}, state.file.name), h("span", { class: "muted" }, `${(state.file.size / 1024 / 1024).toFixed(1)}MB · 눌러서 바꾸기`))
            : h("span", { class: "drop-text" }, h("strong", {}, "파일 고르기"), h("span", { class: "muted" }, "뱅샐에서 받은 zip 또는 xlsx")),
        ),
      ),
      h(
        "div",
        { class: "field" },
        h("label", { class: "k", for: "settle-pw" }, "비밀번호"),
        h(
          "div",
          { class: "pw-row" },
          pw,
          h(
            "button",
            {
              class: "icon-btn", type: "button", aria: { label: "비밀번호 보기", pressed: false },
              onclick: (e) => {
                const show = pw.type === "password";
                pw.type = show ? "text" : "password";
                e.currentTarget.setAttribute("aria-pressed", String(show));
              },
            },
            icon(EYE, 20),
          ),
        ),
        h("span", { class: "hint" }, "잠기지 않은 xlsx 파일이면 비워 두세요. 비밀번호는 파일을 여는 데만 쓰고 저장하지 않아요."),
      ),
      state.error ? h("p", { class: "form-error", role: "alert" }, state.error) : null,
      h(
        "button",
        { class: "btn", type: "button", disabled: !state.file || state.busy, onclick: send },
        state.busy ? "파일을 읽는 중…" : "미리보기",
      ),
    ),
  );
  if (state.errorCode === "PASSWORD_REQUIRED" || state.errorCode === "WRONG_PASSWORD") pw.focus();
  state.errorCode = null;
}

// --- 2단계: 미리보기 -------------------------------------------------------------

function remaining() {
  const left = state.preview.expires_in * 1000 - (Date.now() - state.previewAt);
  return Math.max(0, Math.floor(left / 1000));
}

function clock(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

async function cancelUpload() {
  const p = state.preview;
  state.preview = null;
  if (!p) return;
  try {
    await api.del(`/api/uploads/${encodeURIComponent(p.upload_id)}`);
  } catch {
    // 이미 시간이 지났거나 서버가 다시 켜졌으면 지울 것도 없다
  }
}

function renderPreview(root, ctx, rerender) {
  const p = state.preview;
  const s = p.month_summary;
  const back = async () => {
    await cancelUpload();
    state.step = "file";
    state.file = null;
    rerender();
  };
  const left = h("span", { class: "time-left" }, `남은 시간 ${clock(remaining())}`);
  const removed = p.changes ? p.changes.removed : 0;
  const history = p.first_time ? p.history : null;
  const useHistory = Boolean(history) && state.firstMode === "first_load";
  const kindText = p.first_time ? (useHistory ? KIND_TEXT.first_load : "첫 정산") : KIND_TEXT[p.kind] || p.kind;
  const warnings = useHistory ? [...new Set([...p.warnings, ...history.warnings])] : p.warnings;

  async function confirm() {
    if (state.busy) return;
    if (!p.will_save) {
      await cancelUpload();
      state.step = "status";
      toast("저장하지 않고 닫았어요");
      rerender();
      return;
    }
    state.busy = true;
    state.error = null;
    rerender();
    try {
      const body = p.first_time ? { mode: useHistory ? "first_load" : "settle" } : {};
      state.result = await api.post(`/api/uploads/${encodeURIComponent(p.upload_id)}/apply`, body);
      state.preview = null;
      state.step = "result";
    } catch (error) {
      state.error = error instanceof ApiError ? error.message : "저장하지 못했어요";
      if (error instanceof ApiError && error.status === 404) {
        state.preview = null;
        state.step = "file";
        state.file = null;
      }
      if (!(error instanceof ApiError)) console.error(error);
    }
    state.busy = false;
    rerender();
  }

  replace(
    root,
    topbar("미리보기", back),
    steps(2),
    p.first_time ? startChoice(p, history, rerender) : null, // 처음이면 시작 방법부터
    h(
      "section",
      { class: "card", aria: { label: "미리보기" } },
      h(
        "div",
        { class: "card-head" },
        h("h2", {}, `${ctx.nameOf(p.owner)} · ${monthLabel(p.month)}`),
        h("span", { class: "chip" }, kindText),
      ),
      h("p", { class: "hint" }, "아직 저장하지 않았어요. 내용을 보고 [확정]을 눌러 주세요."),
      h(
        "dl",
        { class: "facts" },
        fact(`${monthShort(p.month)} 거래`, `${won(s.count)}건`),
        fact("수입", `${won(s.income)}원`),
        fact("지출", `${won(s.expense)}원`),
        fact("이체", `들어옴 ${won(s.transfer_in)}원 · 나감 ${won(s.transfer_out)}원`),
        p.changes && !p.first_time
          ? fact("바뀌는 것", `새로 ${won(p.changes.added)} · 바뀜 ${won(p.changes.changed)} · 사라짐 ${won(removed)} · 그대로 ${won(p.changes.unchanged)}`)
          : null,
        useHistory ? fact("함께 넣는 달", firstLoadText(history)) : null,
        useHistory && history.excluded.length ? fact("빼는 달", excludedText(history.excluded)) : null,
        fact("재무현황 표", statusText(p.file.status_tables)),
        p.auto_excluded ? fact("자동으로 빼는 항목", `${p.auto_excluded}개 (설정의 '한지갑에서 뺄 항목')`) : null,
        fact("파일", `${p.file.period_start || "?"} ~ ${p.file.period_end || "?"} · ${won(p.file.rows)}건`),
      ),
      h("p", { class: "hint" }, "수입·지출은 뱅샐 타입 기준 합계예요. 확인 필요 정리는 확정한 뒤에 해요."),
    ),
    p.status_only ? h("p", { class: "note-box ok" }, "거래는 그대로예요. 확정하면 재무현황(순자산) 표만 저장해요.") : null,
    p.kind === "resettle" && removed > 0
      ? h("p", { class: "note-box" }, `이미 정산한 달에서 거래 ${won(removed)}건이 사라져요. 뱅샐에서 지운 거래라면 맞아요. 아니라면 파일 기간을 확인해 주세요.`)
      : null,
    warningCard(warnings),
    state.error ? h("p", { class: "form-error", role: "alert" }, state.error) : null,
    h(
      "div",
      { class: "pick-actions" },
      h("button", { class: "btn secondary", type: "button", onclick: back }, "취소"),
      h(
        "button",
        { class: "btn grow", type: "button", disabled: state.busy, onclick: confirm },
        state.busy ? "저장하는 중…" : p.will_save ? (p.status_only ? "재무현황만 저장" : "확정") : "저장할 게 없어요 · 닫기",
      ),
    ),
    h("div", { class: "center-row" }, left),
  );
  timer = setInterval(() => {
    if (!left.isConnected) {
      clearInterval(timer);
      return;
    }
    const sec = remaining();
    left.textContent = sec > 0 ? `남은 시간 ${clock(sec)}` : "시간이 지났어요. 파일을 다시 올려 주세요";
    if (sec <= 0) clearInterval(timer);
  }, 1000);
}

// 처음 올리는 사람: 어디서부터 시작할지
function startChoice(p, history, rerender) {
  const option = (mode, label, desc, badge) =>
    h(
      "button",
      {
        class: "pick-opt", type: "button", aria: { pressed: state.firstMode === mode },
        onclick: () => {
          state.firstMode = mode;
          rerender();
        },
      },
      h(
        "span",
        { class: "opt-top" },
        h("span", { class: "opt-label" }, label),
        badge ? h("span", { class: "chip small" }, badge) : null,
        state.firstMode === mode ? icon(ICONS.check, 16, { strokeWidth: 2.4 }) : null,
      ),
      h("span", { class: "opt-desc" }, desc),
    );
  return h(
    "section",
    { class: "card", aria: { label: "시작 방법" } },
    h("div", { class: "card-head" }, h("h2", {}, "처음 올리는 파일이에요")),
    h("p", { class: "hint" }, "한지갑을 어디서부터 시작할지 골라 주세요."),
    h(
      "div",
      { class: "pick-options one", role: "group", aria: { label: "시작 방법" } },
      option(
        "settle",
        `${monthLabel(p.month)}만 넣기`,
        `${monthShort(p.month)} 거래와 지금의 재무현황(순자산)으로 시작해요. 지난 달은 나중에 정산 화면에서 한 달씩 넣을 수 있어요.`,
        "권장",
      ),
      history
        ? option(
            "first_load",
            "지난 달도 함께 넣기",
            `${firstLoadText(history)}. 뱅샐에서 분류를 덜 한 달도 그대로 들어가요.`,
          )
        : null,
    ),
  );
}

const WARN_LIMIT = 3;

function warningCard(warnings) {
  if (!warnings.length) return null;
  const rest = warnings.slice(WARN_LIMIT);
  return h(
    "section",
    { class: "card warn-list", aria: { label: "확인할 점" } },
    h("h2", {}, `확인할 점 ${warnings.length}개`),
    h("ul", {}, warnings.slice(0, WARN_LIMIT).map((w) => h("li", {}, w))),
    rest.length
      ? h(
          "details",
          { class: "more-warn" },
          h("summary", {}, `${rest.length}개 더 보기`),
          h("ul", {}, rest.map((w) => h("li", {}, w))),
        )
      : null,
  );
}

function fact(label, value) {
  return h("div", { class: "fact" }, h("dt", {}, label), h("dd", {}, value));
}

function firstLoadText(f) {
  const ms = f.months;
  if (!ms.length) return "없음";
  const base = ms.filter((m) => m.status === "baseline");
  const parts = [];
  if (base.length) parts.push(`${monthLabel(base[0].month)} ~ ${monthLabel(base[base.length - 1].month)} 기준 데이터 (${base.length}개월)`);
  const settled = ms.filter((m) => m.status === "settled");
  if (settled.length) parts.push(`${monthLabel(settled[0].month)} 정산`);
  parts.push(`거래 ${won(f.total_rows)}건`);
  return parts.join(" · ");
}

function excludedText(excluded) {
  const byReason = new Map();
  for (const e of excluded) byReason.set(e.reason, [...(byReason.get(e.reason) || []), monthLabel(e.month)]);
  return [...byReason].map(([reason, ms]) => `${ms.join(", ")} (${reason})`).join(" · ");
}

function statusText(t) {
  if (!t || !t.found) return "없음 · 순자산은 저장되지 않아요";
  return t.ok ? "있음 · 합계 일치" : "있음 · 합계가 맞지 않아요 (그래도 저장해요)";
}

// --- 3단계: 결과 -----------------------------------------------------------------

function renderResult(root, ctx, rerender) {
  const r = state.result;
  const s = r.summary;
  const n = s.unconfirmed;
  const toStatus = () => {
    state.step = "status";
    state.result = null;
    rerender();
  };
  replace(
    root,
    topbar("완료", toStatus),
    steps(3),
    h(
      "section",
      { class: "card done-card" },
      h("span", { class: "done-icon", "aria-hidden": "true" }, icon(ICONS.check, 28, { strokeWidth: 2.2 })),
      h("h2", {}, r.message),
      h("p", { class: "empty" }, `${ctx.nameOf(r.owner)} · ${monthLabel(r.month)}${countsText(r)}`),
    ),
    r.saved && !r.status_only
      ? h(
          "section",
          { class: "card", aria: { label: "이 달 요약" } },
          h("div", { class: "card-head" }, h("h2", {}, `${monthShort(r.month)} 요약`), h("span", { class: "side" }, ctx.nameOf(r.owner))),
          h(
            "dl",
            { class: "facts" },
            fact("수입", `${won(s.income)}원`),
            fact("지출", `${won(s.expense)}원`),
            fact("저축·투자", `${won(s.saving)}원`),
            fact("남은 돈", `${s.net < 0 ? "−" : ""}${won(s.net)}원`),
          ),
          n ? h("p", { class: "hint" }, `확인 필요 ${n}건은 아직 합계에 들어가지 않았어요.`) : null,
        )
      : null,
    h(
      "div",
      { class: "done-actions" },
      n
        ? h(
            "button",
            {
              class: "btn", type: "button",
              onclick: () => {
                state.step = "status";
                state.result = null;
                ctx.setView(r.owner, { render: false });
                ctx.month = r.month;
                ctx.go("/review");
              },
            },
            `확인 필요 ${n}건 지금 정리하기`,
          )
        : null,
      h(
        "button",
        {
          class: `btn${n ? " secondary" : ""}`, type: "button",
          onclick: () => {
            state.step = "status";
            state.result = null;
            ctx.month = r.month;
            ctx.go("/");
          },
        },
        "홈에서 보기",
      ),
      h("button", { class: "btn secondary", type: "button", onclick: toStatus }, "다른 파일 올리기"),
    ),
  );
}

function countsText(r) {
  if (!r.saved || r.status_only) return "";
  if (r.mode === "first_load") return ` · 거래 ${won(r.rows)}건`;
  return ` · 새로 ${won(r.added)} · 바뀜 ${won(r.changed)} · 사라짐 ${won(r.removed)}`;
}