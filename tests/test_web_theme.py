"""G9a: 테마 (화면 모드 × 색 테마 5가지)가 서로 맞는지 정적 검사."""

import re
from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "hanjigap" / "web" / "static"
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
THEMES = ["forest", "ink", "sand", "plum", "graphite"]


def _blocks():
    """선택자 → 그 블록의 색 이름표 이름들"""
    out = {}
    plain = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)  # 주석 빼고
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", plain):
        names = set(re.findall(r"--([a-z-]+)\s*:\s*#", body))
        if names:
            out[" ".join(sel.split())] = names
    return out


def test_every_theme_has_light_and_dark_with_same_tokens():
    blocks = _blocks()
    base = blocks[':root, html[data-theme="forest"]']
    assert {"bg", "brand", "member-b", "on-brand", "pick-on", "debt"} <= base
    for name in THEMES:
        light = next(
            v for k, v in blocks.items() if f'html[data-theme="{name}"]' in k and "dark" not in k
        )
        dark = next(v for k, v in blocks.items() if f'[data-theme="{name}"][data-mode="dark"]' in k)
        assert light == base and dark == base, name


def test_no_colors_outside_theme_blocks():
    # 색은 이름표로만 쓴다 (테마를 바꿔도 안 바뀌는 색이 생기지 않게)
    hex_color = re.compile(r"#[0-9A-Fa-f]{6}\b")
    stray = [
        line
        for line in CSS.splitlines()
        if hex_color.search(line) and not line.strip().startswith("--")
    ]
    assert stray == []
    assert "prefers-color-scheme" not in CSS  # 모드는 data-mode로만


def test_theme_lists_match():
    boot = (STATIC / "js" / "theme-boot.js").read_text(encoding="utf-8")
    theme_js = (STATIC / "js" / "theme.js").read_text(encoding="utf-8")
    assert re.findall(r'"(\w+)"', boot.split("[", 1)[1].split("]", 1)[0]) == THEMES
    assert re.findall(r'key: "(\w+)"', theme_js) == THEMES
    assert sorted(set(re.findall(r'data-theme="(\w+)"', CSS))) == sorted(THEMES)


def test_boot_script_runs_before_css(api):
    html = api.get("/").text
    boot = html.index('<script src="/static/js/theme-boot.js"></script>')
    assert boot < html.index('href="/static/app.css"')
    assert html.count('name="theme-color"') == 1
    assert '<meta name="color-scheme" content="light dark">' in html  # 밝게·어둡게를 스스로 처리
    assert api.get("/static/js/theme-boot.js").headers["content-type"].startswith("text/javascript")


def test_light_mode_refuses_browser_auto_dark():
    # 밝게 모드는 브라우저 자동 다크(크롬 등)를 받지 않고, 어둡게 모드는 브라우저 기본 부품도 어둡게
    assert re.search(r":root \{[^}]*color-scheme: only light;", CSS)
    assert re.search(r'html\[data-mode="dark"\] \{[^}]*color-scheme: dark;', CSS)


def test_samsung_status_bar_left_to_browser():
    # 삼성 인터넷: 상태줄 글자색이 휴대폰 설정을 따름 → theme-color 지움 (밝은 배경에 흰 글자 방지)
    boot = (STATIC / "js" / "theme-boot.js").read_text(encoding="utf-8")
    assert "SamsungBrowser" in boot and "removeChild(meta)" in boot
