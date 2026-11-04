// 그리기 전에 테마를 붙인다 (켤 때 다른 색이 번쩍이지 않게).
// 모듈이 아닌 보통 스크립트로 <head>에서 CSS보다 먼저 실행한다. 바꾸기·기억은 theme.js.
(function () {
  var THEMES = ["forest", "ink", "sand", "plum", "graphite"];
  var theme = "forest";
  var mode = "system";
  try {
    theme = localStorage.getItem("hanjigap.theme") || theme;
    mode = localStorage.getItem("hanjigap.mode") || mode;
  } catch (e) {
    // 저장소를 못 쓰면 기본값 (숲 · 시스템)
  }
  if (THEMES.indexOf(theme) < 0) theme = "forest";
  var systemDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  var dark = mode === "dark" || (mode !== "light" && systemDark);
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-mode", dark ? "dark" : "light");
  // 삼성 인터넷은 상태줄 글자색을 휴대폰 다크 모드에 맞추고 배경만 theme-color로 칠한다.
  // 밝은 배경에 흰 글자가 되지 않게, 상태줄 색은 브라우저에 맡긴다. (theme.js도 이 표시를 본다)
  if (/SamsungBrowser/.test(navigator.userAgent)) {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.parentNode.removeChild(meta);
  }
})();