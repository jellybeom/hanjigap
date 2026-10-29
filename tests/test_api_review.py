"""G3b: 확인 필요 정리 · 거래 상세에 쓰는 API (규칙 미리보기 · 되돌리기 · 분류 후보)."""

import datetime as dt
import hashlib
import itertools

import pytest

import hanjigap.web.ledger_api as ledger_mod

_n = itertools.count(1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(ledger_mod, "today", lambda _r: dt.date(2026, 10, 29))


def add_tx(conn, member_id, amount, content, tx_type="이체", category="이체", date="2026-09-10",sub=""):  # fmt: skip
    n = next(_n)
    key = hashlib.sha256(f"r{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, ?, ?, ?, ?, '카드', 1, ?)",
        (member_id, key, f"b{n}", date[:7], date, tx_type, category, sub, content, amount, n),
    )  # fmt: skip
    return key


def detail(api, key, owner="sangbeom"):
    return api.get(f"/api/transactions/{owner}/{key}").json()


# --- 규칙 미리보기 ----------------------------------------------------------------


def test_remember_preview_counts_other_matches(api, api_conn):
    key = add_tx(api_conn, 1, -100_000, "OO학원")
    add_tx(api_conn, 1, -100_000, "oo학원 10월", date="2026-10-10")  # 대소문자 무시 · 다른 달
    add_tx(api_conn, 1, -100_000, "OO학원", date="2026-08-10")
    add_tx(api_conn, 1, 100_000, "OO학원 환불")  # 방향이 다름
    add_tx(api_conn, 2, -100_000, "OO학원")  # 다른 사람
    manual = add_tx(api_conn, 1, -100_000, "OO학원", date="2026-07-10")
    api.put(f"/api/transactions/sangbeom/{manual}/class", json={"result": "expense"})
    r = detail(api, key)["remember"]
    assert r == {
        "possible": True,
        "reason": None,
        "pattern": "OO학원",
        "direction": "out",
        "direction_label": "나간 돈",
        "others": 2,
        "other_months": 2,
    }


def test_remember_preview_not_possible(api, api_conn):
    empty = add_tx(api_conn, 1, -5_000, "")
    long = add_tx(api_conn, 1, 5_000, "가" * 51)
    r = detail(api, empty)["remember"]
    assert (r["possible"], r["reason"]) == (False, "내용이 비어 있어서 규칙으로 만들 수 없어요")
    r = detail(api, long)["remember"]
    assert (r["possible"], r["direction"]) == (False, "in")
    assert "50자" in r["reason"]


def test_remember_preview_uses_edited_content(api, api_conn):
    key = add_tx(api_conn, 1, -30_000, "원래 내용")
    add_tx(api_conn, 1, -30_000, "새 이름 가게")
    api.patch(f"/api/transactions/sangbeom/{key}", json={"content": "새 이름"})
    r = detail(api, key)["remember"]
    assert (r["pattern"], r["others"]) == ("새 이름", 1)


# --- 되돌리기 -------------------------------------------------------------------


def test_undo_removes_class_and_rule(api, api_conn):
    key = add_tx(api_conn, 1, -100_000, "OO학원")
    other = add_tx(api_conn, 1, -100_000, "OO학원", date="2026-08-10")
    r = api.put(
        f"/api/transactions/sangbeom/{key}/class",
        json={"result": "expense", "result_category": "교육", "remember": True},
    ).json()
    rule_id = r["rule"]["id"]
    assert detail(api, other)["source"] == "rule"
    d = api.delete(f"/api/transactions/sangbeom/{key}/class?rule={rule_id}").json()
    assert (d["cls"], d["manual_class"]) == ("unconfirmed", None)
    assert detail(api, other)["cls"] == "unconfirmed"
    assert api.get("/api/class-rules").json()["items"] == []


def test_undo_with_unknown_rule_changes_nothing(api, api_conn):
    key = add_tx(api_conn, 1, -100_000, "OO학원")
    api.put(f"/api/transactions/sangbeom/{key}/class", json={"result": "excluded"})
    r = api.delete(f"/api/transactions/sangbeom/{key}/class?rule=999")
    assert r.status_code == 400
    assert "규칙을 찾을 수 없어요" in r.json()["error"]
    assert detail(api, key)["cls"] == "excluded"  # 직접 지정도 그대로


# --- 분류 후보 ------------------------------------------------------------------


def test_categories(api, api_conn):
    add_tx(api_conn, 1, -10_000, "가게1", "지출", "식비", sub="한식")
    add_tx(api_conn, 1, -10_000, "가게2", "지출", "식비", sub="한식")
    add_tx(api_conn, 2, -10_000, "가게3", "지출", "식비", sub="카페", date="2026-08-03")
    add_tx(api_conn, 1, -10_000, "가게4", "지출", "생활")
    add_tx(api_conn, 1, 3_000_000, "OO공사급여", "수입", "급여")
    lunch = add_tx(api_conn, 1, 12_000, "점심값")
    api.put(
        f"/api/transactions/sangbeom/{lunch}/class",
        json={"result": "expense", "result_category": "모임"},
    )
    edited = add_tx(api_conn, 1, -5_000, "가게5", "지출", "기타")
    api.patch(
        f"/api/transactions/sangbeom/{edited}", json={"category": "생활", "subcategory": "세제"}
    )
    c = api.get("/api/categories").json()
    assert c["expense"] == ["식비", "생활", "모임"]
    assert c["income"] == ["급여"]
    banksalad = {b["category"]: b["subcategories"] for b in c["banksalad"]}
    assert list(banksalad) == ["식비", "생활", "급여", "이체"]  # 같은 수면 이름순
    assert banksalad["식비"] == ["한식", "카페"]
    assert banksalad["생활"] == ["세제"]
    assert "기타" not in banksalad  # 고친 값으로 센다


def test_categories_empty(api):
    assert api.get("/api/categories").json() == {"expense": [], "income": [], "banksalad": []}
