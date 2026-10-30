// 테마 (G9a): 화면 모드(시스템·밝게·어둡게) × 색 테마 5가지. 이 기기에만 기억한다.
// 처음 그리기 전 적용은 theme-boot.js가 하고, 여기서는 바꾸기·기억·시스템 변경 따라가기.

export const MODES = [
  ["system", "시스템"],
  ["light", "밝게"],
  ["dark", "어둡게"],
];

// swatch: 미리보기 점 (순자산 카드 색 · 포인트 색 · 두 번째 사람 색)
export const THEMES = [
  { key: "forest", name: "숲", desc: "짙은 초록 · 민트", swatch: ["#143D30", "#2E7D5B", "#8FE0B8"] },
  { key: "ink", name: "잉크 · 코랄", desc: "짙은 남색 · 코랄", swatch: ["#1E2B4F", "#2F5BD3", "#F2A68F"] },
  { key: "sand", name: "모래 · 세이지", desc: "에스프레소 · 캐러멜 · 세이지", swatch: ["#3E3029", "#A0653A", "#9DB59A"] },
  { key: "plum", name: "플럼 · 로즈", desc: "자두색 · 로즈", swatch: ["#43264D", "#8B4FA8", "#E59AB0"] },
  { key: "graphite", name: "그래파이트 · 민트", desc: "먹색 · 민트 · 호박색", swatch: ["#1C1F22", "#0E8C7E", "#F0B35E"] },
];

const KEY_THEME = "hanjigap.theme";
const KEY_MODE = "hanjigap.mode";
const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

function read(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 저장소를 못 쓰면 이번에만 적용
  }
}

export function current() {
  const theme = read(KEY_THEME, "forest");
  return {
    theme: THEMES.some((t) => t.key === theme) ? theme : "forest",
    mode: MODES.some(([m]) => m === read(KEY_MODE, "system")) ? read(KEY_MODE, "system") : "system",
  };
}

export function apply() {
  const { theme, mode } = current();
  const dark = mode === "dark" || (mode === "system" && Boolean(darkQuery && darkQuery.matches));
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-mode", dark ? "dark" : "light");
  // 휴대폰 위쪽 상태줄 색도 맞춘다
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", getComputedStyle(root).getPropertyValue("--bg").trim() || "#F2F3EE");
}

export function setTheme(theme) {
  write(KEY_THEME, theme);
  apply();
}

export function setMode(mode) {
  write(KEY_MODE, mode);
  apply();
}

/** 앱 시작 때 한 번: 상태줄 색 맞추기 · '시스템'이면 기기 설정이 바뀔 때 따라가기 */
export function initTheme() {
  apply();
  if (darkQuery && darkQuery.addEventListener) {
    darkQuery.addEventListener("change", () => {
      if (current().mode === "system") apply();
    });
  }
}