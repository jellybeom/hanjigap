// 한지갑 화면 틀: 아래 메뉴, 화면 이동(#/주소), 공용 상태(누가 로그인했나, 보기, 달).
// 화면 모듈은 render(root, ctx)를 내보낸다.

import { api, ApiError } from "./api.js";
import { h, icon, replace } from "./dom.js";
import { ICONS } from "./icons.js";
import * as assets from "./screens/assets.js";
import * as home from "./screens/home.js";
import * as ledger from "./screens/ledger.js";
import * as pending from "./screens/pending.js";
import * as review from "./screens/review.js";
import * as settle from "./screens/settle.js";

const TABS = [
  { path: "/", key: "home", label: "홈", icon: ICONS.home },
  { path: "/ledger", key: "ledger", label: "내역", icon: ICONS.ledger },
  { path: "/assets", key: "assets", label: "자산", icon: ICONS.assets },
  { path: "/settle", key: "settle", label: "정산", icon: ICONS.settle },
  { path: "/settings", key: "settings", label: "설정", icon: ICONS.settings },
];

const SCREENS = {
  "/": { tab: "home", module: home },
  "/ledger": { tab: "ledger", module: ledger },
  "/review": { tab: "ledger", module: review },
  "/assets": { tab: "assets", module: assets },
  "/settle": { tab: "settle", module: settle },
  "/settings": { tab: "settings", module: pending, title: "설정", step: "G8" },
};

// 이 기기·이 탭에서만 기억 (실패해도 동작에는 지장 없음)
function remember(key, value) {
  try {
    if (value === undefined) return sessionStorage.getItem(`hanjigap.${key}`);
    sessionStorage.setItem(`hanjigap.${key}`, value);
  } catch {
    return null;
  }
  return value;
}

const ctx = {
  me: null, // { slug, name }
  members: [], // [{ slug, name }]
  view: remember("view") || "all", // all | 구성원 slug
  month: null, // null = 서버 기본(지난달)
  setView(view, { render = true } = {}) {
    ctx.view = view;
    remember("view", view);
    if (render) show();
  },
  setMonth(month) {
    ctx.month = month;
    show();
  },
  go(path) {
    if (currentPath() === path) show();
    else window.location.hash = `#${path}`;
  },
  refresh() {
    show();
  },
  owner() {
    return ctx.view === "all" ? null : ctx.view;
  },
  nameOf(slug) {
    return ctx.members.find((m) => m.slug === slug)?.name || slug;
  },
};

function currentPath() {
  const path = window.location.hash.replace(/^#/, "").split("?")[0];
  return SCREENS[path] ? path : "/";
}

function renderTabs(activeTab) {
  const bar = document.getElementById("tabbar");
  replace(
    bar,
    TABS.map((t) =>
      h(
        "a",
        { class: "tab", href: `#${t.path}`, aria: { current: t.key === activeTab ? "page" : null } },
        icon(t.icon, 22),
        h("span", {}, t.label),
      ),
    ),
  );
}

export function renderError(root, error, retry) {
  const message = error instanceof ApiError ? error.message : "화면을 그리다 문제가 생겼어요";
  replace(
    root,
    h(
      "div",
      { class: "error-box", role: "alert" },
      h("p", { class: "msg" }, message),
      retry ? h("button", { class: "btn secondary", type: "button", onclick: retry }, "다시 시도") : null,
    ),
  );
}

let showing = 0;

async function show() {
  const path = currentPath();
  const screen = SCREENS[path];
  const root = document.getElementById("screen");
  const token = ++showing;
  renderTabs(screen.tab);
  try {
    if (!ctx.me) {
      const me = await api.get("/api/me");
      ctx.me = me.member;
      ctx.members = me.members;
      if (ctx.view !== "all" && !ctx.members.some((m) => m.slug === ctx.view)) ctx.view = "all";
    }
    await screen.module.render(root, ctx, screen, () => token === showing);
  } catch (error) {
    if (token === showing) renderError(root, error, show);
    if (!(error instanceof ApiError)) console.error(error);
  }
}

window.addEventListener("hashchange", () => {
  // 고른 달은 화면을 옮겨도 그대로 둔다 (홈에서 8월을 보다 '전체 보기'를 누르면 내역도 8월)
  window.scrollTo(0, 0);
  show();
});
show();