"""F4a: 가계부 API (달 · 월 요약 · 거래 · 웹 수정 · 확인 필요 처리 · 분류 규칙 · 별칭)."""

import datetime as dt
import hashlib
import itertools
import logging

import pytest

import hanjigap.web.ledger_api as ledger_mod

_n = itertools.count(1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(ledger_mod, "today", lambda _r: dt.date(2026, 10, 29))


def add_tx(conn, member_id, amount, content, tx_type="지출", category="식비", date="2026-09-10",time="10:00:00", memo=None):  # fmt: skip
    n = next(_n)
    key = hashlib.sha256(f"l{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, memo, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, '', ?, ?, '카드', ?, 1, ?)",
        (member_id, key, f"b{n}", date[:7], date, time, tx_type, category, content, amount,
         memo, n),
    )  # fmt: skip
    return key


def settle(conn, member_id, ym, status="settled", count=0):
    conn.execute(
        "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count) "
        "VALUES (?, ?, ?, 1, ?)",
        (member_id, ym, status, count),
    )


@pytest.fixture
def month(api_conn):
    """2026-09: 상범·재희 거래 여러 종류"""
    c = api_conn
    keys = {
        "pay": add_tx(c, 1, 3_000_000, "OO공사급여", "수입", "급여", "2026-09-25"),
        "food": add_tx(c, 1, -50_000, "식당", date="2026-09-05"),
        "refund": add_tx(c, 1, 8_000, "식당 환불", date="2026-09-06"),
        "mart": add_tx(c, 2, -200_000, "마트", "지출", "생활", "2026-09-07"),
        "card": add_tx(c, 1, -900_000, "카드대금", "이체", "카드대금", "2026-09-14"),
        "parents": add_tx(c, 1, -100_000, "어머니", "이체", "이체", "2026-09-15"),
        "lunch": add_tx(c, 1, 12_000, "점심값", "이체", "이체", "2026-09-16"),
        "wife": add_tx(c, 1, 1_000_000, "이재희", "이체", "이체", "2026-09-13"),
        "save": add_tx(c, 1, -300_000, "적금", "이체", "저축", "2026-09-20"),
    }
    add_tx(c, 1, -40_000, "식당", date="2026-08-05")  # 지난달
    settle(c, 1, "2026-08", "baseline", 1)
    settle(c, 1, "2026-09", "settled", 9)
    return keys


# --- 달 · 요약 -------------------------------------------------------------------


def test_months(api, month):
    items = api.get("/api/months").json()["items"]
    assert [i["month"] for i in items] == ["2026-09", "2026-08"]
    assert items[0]["members"] == [
        {"owner": "sangbeom", "status": "settled", "label": "정산됨", "tx_count": 9}
    ]


def test_summary(api, month):
    s = api.get("/api/summary").json()  # 비우면 지난달 (2026-09)
    assert s["month"] == "2026-09" and s["owner"] is None
    assert (s["income"], s["expense"], s["saving"]) == (3_000_000, 242_000, 300_000)
    assert s["net"] == 3_000_000 - 242_000 - 300_000
    assert s["counts"]["excluded"] == 2  # 카드대금 · 부부 이체(구성원 이름 '재희')
    assert s["unconfirmed"] == {
        "count": 2,
        "in_count": 1,
        "in_amount": 12_000,
        "out_count": 1,
        "out_amount": 100_000,
    }
    assert s["expense_by_category"] == [
        {"category": "생활", "amount": 200_000},
        {"category": "식비", "amount": 42_000},
    ]
    assert s["by_member"] == [
        {"owner": "sangbeom", "income": 3_000_000, "expense": 42_000, "saving": 300_000},
        {"owner": "jaehee", "income": 0, "expense": 200_000, "saving": 0},
    ]
    assert s["previous"]["month"] == "2026-08"
    assert (s["previous"]["expense"], s["previous"]["expense_diff"]) == (40_000, 202_000)
    assert s["settlement"] == [
        {"owner": "sangbeom", "status": "settled", "label": "정산됨"},
        {"owner": "jaehee", "status": None, "label": "정산 전"},
    ]


def test_summary_for_one_member(api, month):
    s = api.get("/api/summary?month=2026-09&owner=jaehee").json()
    assert (s["owner"], s["expense"], s["income"]) == ("jaehee", 200_000, 0)
    assert [m["owner"] for m in s["by_member"]] == ["jaehee"]


@pytest.mark.parametrize("query", ["month=2026-13", "month=2026-9", "owner=nobody"])
def test_summary_rejects(api, query):
    assert api.get(f"/api/summary?{query}").status_code == 400


# --- 거래 목록 -------------------------------------------------------------------


def test_transactions_list(api, month):
    r = api.get("/api/transactions?month=2026-09").json()
    assert (r["total"], r["page"], r["pages"]) == (9, 1, 1)
    assert [i["date"] for i in r["items"]][:2] == ["2026-09-25", "2026-09-20"]  # 최신순
    wife = next(i for i in r["items"] if i["tx_key"] == month["wife"])
    assert (wife["cls"], wife["class_label"], wife["source_label"]) == (
        "excluded",
        "제외",
        "부부 이체",
    )
    parents = next(i for i in r["items"] if i["tx_key"] == month["parents"])
    assert (parents["suggestion"], parents["suggestion_label"]) == ("expense", "지출")
    assert r["class_counts"]["unconfirmed"] == 2


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("cls=unconfirmed", {"parents", "lunch"}),
        ("cls=확인 필요", {"parents", "lunch"}),
        ("cls=expense&category=식비", {"food", "refund"}),
        ("q=식당", {"food", "refund"}),
        ("owner=jaehee", {"mart"}),
    ],
)
def test_transactions_filters(api, month, query, expected):
    r = api.get(f"/api/transactions?month=2026-09&{query}").json()
    assert {i["tx_key"] for i in r["items"]} == {month[k] for k in expected}


def test_transactions_paging(api, api_conn):
    for d in range(1, 61):
        add_tx(api_conn, 1, -1, f"가게{d}", date=f"2026-09-{(d % 28) + 1:02d}")
    first = api.get("/api/transactions?month=2026-09").json()
    second = api.get("/api/transactions?month=2026-09&page=2").json()
    assert (first["total"], first["pages"], len(first["items"]), len(second["items"])) == (
        60,
        2,
        50,
        10,
    )
    assert api.get("/api/transactions?month=2026-09&page=0").status_code == 400
    assert api.get("/api/transactions?month=2026-09&cls=용돈").status_code == 400


# --- 거래 상세 · 웹 수정 -----------------------------------------------------------


def test_transaction_detail(api, month):
    d = api.get(f"/api/transactions/sangbeom/{month['food']}").json()
    assert d["original"] == {
        "tx_type": "지출",
        "category": "식비",
        "subcategory": "",
        "content": "식당",
        "memo": None,
    }
    assert (d["edit"], d["manual_class"], d["edited"]) == (None, None, False)
    assert api.get(f"/api/transactions/jaehee/{month['food']}").status_code == 404
    assert api.get(f"/api/transactions/nobody/{month['food']}").status_code == 404


def test_edit_transaction(api, month):
    key = month["parents"]
    r = api.patch(
        f"/api/transactions/sangbeom/{key}",
        json={"category": "경조/선물", "content": "어머니 생신", "note": "꽃 포함"},
    )
    d = r.json()
    assert (d["content"], d["category"], d["note"], d["edited"]) == (
        "어머니 생신",
        "경조/선물",
        "꽃 포함",
        True,
    )
    assert d["edit"]["edited_by"] == "sangbeom"
    assert d["original"]["content"] == "어머니"  # 원래 값은 그대로
    d = api.patch(f"/api/transactions/sangbeom/{key}", json={"note": ""}).json()
    assert (d["note"], d["content"]) == (None, "어머니 생신")  # 메모만 되돌림
    found = api.get("/api/transactions?month=2026-09&q=생신").json()["items"]
    assert [i["tx_key"] for i in found] == [key]
    d = api.delete(f"/api/transactions/sangbeom/{key}/edit").json()
    assert (d["content"], d["edited"], d["edit"]) == ("어머니", False, None)


def test_edit_changes_expense_category(api, month):
    api.patch(f"/api/transactions/sangbeom/{month['food']}", json={"category": "카페"})
    cats = api.get("/api/summary?month=2026-09").json()["expense_by_category"]
    assert {"category": "카페", "amount": 50_000} in cats


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({}, 400, "바꿀 내용이 없어요"),
        ({"note": "계좌 110-123-456789"}, 400, "끝 4자리"),
        ({"category": "가" * 31}, 400, "30자 이하"),
    ],
)
def test_edit_rejects(api, month, body, status, message):
    r = api.patch(f"/api/transactions/sangbeom/{month['food']}", json=body)
    assert r.status_code == status and message in r.json()["error"]


def test_edit_unknown_tx(api, month):
    assert (
        api.patch("/api/transactions/sangbeom/" + "0" * 32, json={"note": "x"}).status_code == 404
    )


# --- 확인 필요 처리 ---------------------------------------------------------------


def test_set_class_as_expense_reduction(api, month):
    r = api.put(
        f"/api/transactions/sangbeom/{month['lunch']}/class",
        json={"result": "지출", "result_category": "식비"},
    ).json()
    t = r["transaction"]
    assert (t["cls"], t["display_category"], t["source"], r["rule"]) == (
        "expense",
        "식비",
        "manual",
        None,
    )
    assert t["manual_class"]["classified_by"] == "sangbeom"
    s = api.get("/api/summary?month=2026-09").json()
    assert {"category": "식비", "amount": 30_000} in s["expense_by_category"]  # 42,000 − 12,000
    d = api.delete(f"/api/transactions/sangbeom/{month['lunch']}/class").json()
    assert (d["cls"], d["manual_class"]) == ("unconfirmed", None)


def test_set_class_remember(api, api_conn, month):
    r = api.put(
        f"/api/transactions/sangbeom/{month['parents']}/class",
        json={"result": "expense", "result_category": "부모님", "remember": True},
    ).json()
    assert r["rule"]["label"] == "나간 돈 · 내용 「어머니」 → 지출 › 부모님"
    assert r["rule"]["owner"] == "sangbeom"
    later = add_tx(api_conn, 1, -100_000, "어머니", "이체", "이체", "2026-10-15")
    item = api.get(f"/api/transactions/sangbeom/{later}").json()
    assert (item["cls"], item["source"], item["display_category"]) == ("expense", "rule", "부모님")


def test_set_class_remember_fails_saves_nothing(api, api_conn):
    key = add_tx(api_conn, 1, -5_000, "", "이체", "이체")
    r = api.put(
        f"/api/transactions/sangbeom/{key}/class", json={"result": "expense", "remember": True}
    )
    assert r.status_code == 400 and "내용이 비어" in r.json()["error"]
    assert api_conn.execute("SELECT count(*) FROM tx_classes").fetchone()[0] == 0


def test_set_class_rejects(api, month):
    url = f"/api/transactions/sangbeom/{month['lunch']}/class"
    assert api.put(url, json={"result": "확인 필요"}).status_code == 400
    assert api.put(url, json={}).status_code == 422


# --- 분류 규칙 -------------------------------------------------------------------


def test_class_rules_crud(api, month):
    r = api.post(
        "/api/class-rules",
        json={"result": "수입", "owner": "sangbeom", "direction": "in", "pattern": "점심값",
              "result_category": "정산"},  # fmt: skip
    )
    assert r.status_code == 201
    rule = r.json()
    assert (rule["owner"], rule["direction_label"], rule["result_label"]) == (
        "sangbeom",
        "들어온 돈",
        "수입",
    )
    lunch = api.get(f"/api/transactions/sangbeom/{month['lunch']}").json()
    assert (lunch["cls"], lunch["rule_id"]) == ("income", rule["id"])

    r = api.patch(f"/api/class-rules/{rule['id']}", json={"owner": "", "result": "expense"})
    assert (r.json()["owner"], r.json()["result"]) == (None, "expense")
    r = api.patch(f"/api/class-rules/{rule['id']}", json={"active": False})
    assert r.json()["active"] is False
    assert api.patch(f"/api/class-rules/{rule['id']}", json={}).status_code == 400

    other = api.post("/api/class-rules", json={"result": "excluded", "pattern": "x"}).json()
    order = [i["id"] for i in api.get("/api/class-rules").json()["items"]]
    assert order == [other["id"], rule["id"]]  # 새 규칙이 맨 위
    r = api.put("/api/class-rules/order", json={"ids": [rule["id"], other["id"]]})
    assert [i["id"] for i in r.json()["items"]] == [rule["id"], other["id"]]
    assert api.put("/api/class-rules/order", json={"ids": [rule["id"]]}).status_code == 400

    assert api.delete(f"/api/class-rules/{rule['id']}").json() == {"deleted": True}
    assert api.delete(f"/api/class-rules/{rule['id']}").status_code == 404
    assert api.patch("/api/class-rules/999", json={"active": True}).status_code == 404


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"result": "income"}, "조건이 하나 이상"),
        ({"result": "income", "pattern": "x", "owner": "nobody"}, "구성원을 찾을 수 없습니다"),
        ({"result": "income", "tx_type": "입금"}, "뱅샐 타입은"),
    ],
)
def test_class_rule_rejects(api, body, message):
    r = api.post("/api/class-rules", json=body)
    assert r.status_code == 400 and message in r.json()["error"]


# --- 구성원 · 별칭 ---------------------------------------------------------------


def test_members_and_aliases(api, month):
    r = api.put("/api/members/jaehee/aliases", json={"add": ["이재희", "잴리"]})
    assert r.json() == {
        "slug": "jaehee",
        "name": "재희",
        "aliases": ["이재희", "잴리"],
        "search_names": ["재희", "이재희", "잴리"],
    }
    r = api.put("/api/members/jaehee/aliases", json={"remove": ["잴리"]})
    assert r.json()["aliases"] == ["이재희"]
    items = api.get("/api/members").json()["items"]
    assert [(m["slug"], m["aliases"]) for m in items] == [("sangbeom", []), ("jaehee", ["이재희"])]
    assert "email" not in items[0]


def test_alias_rejects(api):
    r = api.put("/api/members/sangbeom/aliases", json={"add": ["재희"]})
    assert r.status_code == 400 and "다른 구성원의 이름" in r.json()["error"]
    assert api.put("/api/members/nobody/aliases", json={"add": ["가나"]}).status_code == 404


def test_alias_affects_classification(api, api_conn):
    key = add_tx(api_conn, 2, -30_000, "버미", "이체", "이체")
    assert api.get(f"/api/transactions/jaehee/{key}").json()["cls"] == "unconfirmed"
    api.put("/api/members/sangbeom/aliases", json={"add": ["버미"]})
    assert api.get(f"/api/transactions/jaehee/{key}").json()["cls"] == "excluded"


def test_log_has_no_contents(api, month, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap.web")
    api.get("/api/transactions?month=2026-09&q=어머니")
    api.patch(f"/api/transactions/sangbeom/{month['food']}", json={"note": "비밀메모"})
    assert "GET /api/transactions 200" in caplog.text
    assert "PATCH /api/transactions/{owner}/{tx_key} 200" in caplog.text
    assert "어머니" not in caplog.text and "비밀메모" not in caplog.text
    assert month["food"] not in caplog.text
