"""G2: 화면 파일 제공 · 보안 정책 · 홈 API 확장 (보기 선택 · 소합계 칸 · 추이 · 최근 거래)."""

import datetime as dt
import hashlib
import itertools
import logging
import re
from pathlib import Path

import pytest

import hanjigap.web.fixed_api as fixed_mod
import hanjigap.web.ledger_api as ledger_mod
import hanjigap.web.status_api as status_mod

STATIC = Path(__file__).parents[1] / "src" / "hanjigap" / "web" / "static"
_n = itertools.count(1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for mod in (fixed_mod, ledger_mod, status_mod):
        monkeypatch.setattr(mod, "today", lambda _r: dt.date(2026, 10, 29))


# --- 화면 파일 -------------------------------------------------------------------


def test_index_page(api):
    r = api.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<script type="module" src="/static/js/app.js">' in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/static/js/app.js", "text/javascript"),
        ("/static/js/screens/home.js", "text/javascript"),
        ("/static/app.css", "text/css"),
        ("/static/manifest.webmanifest", "application/manifest+json"),
        ("/static/icons/icon.svg", "image/svg+xml"),
        ("/static/icons/icon-192.png", "image/png"),
        ("/static/icons/apple-touch-icon.png", "image/png"),
    ],
)
def test_static_files(api, path, content_type):
    r = api.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(content_type)
    assert r.headers["cache-control"] == "no-cache"


def test_static_cannot_escape(api):
    assert api.get("/static/../app.py").status_code == 404
    assert api.get("/static/%2e%2e/app.py").status_code == 404


def test_docs_without_csp_in_dev(api):
    r = api.get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_api_has_csp_and_no_store(api):
    r = api.get("/api/me")
    assert "content-security-policy" in r.headers and r.headers["cache-control"] == "no-store"


def test_static_log_label(api, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap.web")
    api.get("/")
    api.get("/static/js/app.js")
    assert "GET / 200" in caplog.text and "GET /static/{file} 200" in caplog.text


# --- 화면 코드 점검 (보안 정책과 맞는지, 빠진 파일은 없는지) ----------------------------------


def _js_files():
    return sorted(STATIC.rglob("*.js"))


def test_no_inline_script_or_style_in_html():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)  # 모든 script는 src로
    assert "style=" not in html and "<style" not in html
    assert "http://" not in html and "https://" not in html  # 바깥 파일을 부르지 않는다


@pytest.mark.parametrize("path", _js_files(), ids=lambda p: p.name)
def test_js_safe_and_imports_exist(path):
    text = path.read_text(encoding="utf-8")
    code = re.sub(r"^\s*//.*$", "", text, flags=re.M)  # 주석 빼고
    code = code.replace('"http://www.w3.org/2000/svg"', "")  # SVG 이름표 (주소 아님)
    assert not re.search(r"\.(inner|outer)HTML\s*=|insertAdjacentHTML", code)  # 데이터는 글자로만
    assert "eval(" not in code and "new Function" not in code
    assert "https://" not in code and "http://" not in code
    for target in re.findall(r'from\s+"(\.[^"]+)"', text):
        assert (path.parent / target).resolve().exists(), f"{path.name}: {target} 없음"


def test_manifest_icons_exist():
    import json

    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    for item in manifest["icons"]:
        assert (STATIC / item["src"].removeprefix("/static/")).exists()
    assert manifest["start_url"] == "/" and manifest["display"] == "standalone"


# --- 홈 API 확장 -----------------------------------------------------------------


def add_tx(conn, member_id, amount, content, date="2026-09-10", tx_type="지출", category="식비"):
    n = next(_n)
    key = hashlib.sha256(f"w{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, ?, '', ?, ?, '카드', 1, ?)",
        (member_id, key, f"b{n}", date[:7], date, tx_type, category, content, amount, n),
    )


def snapshot(conn, member_id, ym, holdings):
    batch = conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (?, ?, 'zip', 0, 'applied')",
        (member_id, hashlib.sha256(f"{member_id}{ym}".encode()).hexdigest()),
    ).lastrowid
    assets = sum(h[3] for h in holdings if h[0] == "asset")
    debt = sum(h[3] for h in holdings if h[0] == "liability")
    sid = conn.execute(
        "INSERT INTO bs_snapshots (batch_id, member_id, as_of, year_month, total_assets, "
        "total_liabilities, net_worth, checks_ok) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (batch, member_id, f"{ym}-28", ym, assets, debt, assets - debt),
    ).lastrowid
    for seq, (section, group, name, amount, principal) in enumerate(holdings, start=1):
        conn.execute(
            "INSERT INTO bs_holdings (snapshot_id, seq, section, group_name, name, amount, "
            "principal) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, seq, section, group, name, amount, principal),
        )


HOLDINGS = [
    ("asset", "자유입출금 자산", "통장", 10_000_000, None),
    ("asset", "저축성 자산", "적금", 5_000_000, None),
    ("asset", "투자성 자산", "증권", 12_000_000, None),
    ("liability", "장기대출", "대출", 7_000_000, None),
    ("investment", "주식", "주식", 12_000_000, 10_000_000),
    ("loan", "신용대출", "대출", 7_000_000, 8_000_000),
    ("insurance", None, "보험", 900_000, None),
]


def test_home_breakdown_and_trend(api, api_conn):
    snapshot(api_conn, 1, "2026-08", HOLDINGS[:1])
    snapshot(api_conn, 1, "2026-09", HOLDINGS)
    h = api.get("/api/home").json()
    a = h["assets"]
    assert (a["has_banksalad"], a["net_worth"], a["previous_net_worth"]) == (
        True,
        20_000_000,
        10_000_000,
    )
    assert (a["total_assets"], a["total_liabilities"]) == (27_000_000, 7_000_000)
    assert a["investment"] == {
        "value": 12_000_000,
        "principal": 10_000_000,
        "rate": 20.0,
        "count": 1,
    }
    assert a["loans"] == {"balance": 7_000_000, "count": 1}
    assert a["cash"] == {"amount": 15_000_000, "count": 2}  # 입출금 + 저축성
    assert a["insurance"] == {"paid_total": 900_000, "count": 1}
    assert [p["month"] for p in h["trend"]][:2] == ["2025-10", "2025-11"]
    assert len(h["trend"]) == 12 and h["trend"][-1]["net_worth"] == 20_000_000
    assert h["trend"][0]["net_worth"] is None


def test_home_without_banksalad(api):
    a = api.get("/api/home").json()["assets"]
    assert (a["has_banksalad"], a["investment"]["rate"], a["previous_net_worth"]) == (
        False,
        None,
        None,
    )


def test_home_owner_view(api, api_conn):
    snapshot(api_conn, 1, "2026-09", HOLDINGS)
    add_tx(api_conn, 1, -10_000, "식당")
    add_tx(api_conn, 2, -30_000, "마트", category="생활")
    add_tx(api_conn, 2, -5_000, "카페", date="2026-08-10")
    h = api.get("/api/home?owner=jaehee").json()
    assert (h["owner"], h["summary"]["expense"], h["summary"]["expense_diff"]) == (
        "jaehee",
        30_000,
        25_000,
    )
    assert h["assets"]["has_banksalad"] is False  # 재희 기록 없음
    assert [s["owner"] for s in h["settlement"]] == ["jaehee"]
    assert [r["content"] for r in h["recent"]] == ["마트"]
    assert any(a["kind"] == "not_settled" for a in h["alerts"])  # 알림은 집 전체
    assert api.get("/api/home?owner=nobody").status_code == 400


def test_home_recent_is_latest_five(api, api_conn):
    for day in range(1, 9):
        add_tx(api_conn, 1, -day, f"가게{day}", date=f"2026-09-{day:02d}")
    recent = api.get("/api/home").json()["recent"]
    assert [r["content"] for r in recent] == ["가게8", "가게7", "가게6", "가게5", "가게4"]
    assert {"owner", "class_label", "display_category"} <= set(recent[0])
