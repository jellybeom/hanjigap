"""G3a: 12개월 막대그래프 API · 소분류별 합계."""

import datetime as dt
import hashlib
import itertools

import pytest

import hanjigap.web.ledger_api as ledger_mod

_n = itertools.count(1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(ledger_mod, "today", lambda _r: dt.date(2026, 10, 29))


def add_tx(conn, member_id, amount, date, category="식비", sub="", tx_type="지출"):
    n = next(_n)
    key = hashlib.sha256(f"t{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, ?, ?, '가게', ?, '카드', 1, ?)",
        (member_id, key, f"b{n}", date[:7], date, tx_type, category, sub, amount, n),
    )


@pytest.fixture
def data(api_conn):
    add_tx(api_conn, 1, -10_000, "2026-09-05")
    add_tx(api_conn, 2, -5_000, "2026-09-06")
    add_tx(api_conn, 1, 3_000_000, "2026-09-25", "급여", tx_type="수입")
    add_tx(api_conn, 1, -40_000, "2026-07-05")
    api_conn.execute(
        "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count) "
        "VALUES (1, '2026-09', 'settled', 1, 3)"
    )


def test_trend_default(api, data):
    items = api.get("/api/trend").json()["items"]
    assert [i["month"] for i in items][0] == "2025-10" and items[-1]["month"] == "2026-09"
    assert len(items) == 12
    last = items[-1]
    assert (last["expense"], last["income"], last["settled"]) == (15_000, 3_000_000, True)
    assert items[-3]["expense"] == 40_000 and items[-3]["settled"] is False
    assert items[-2] == {
        "month": "2026-08",
        "income": 0,
        "expense": 0,
        "saving": 0,
        "settled": False,
    }


def test_trend_owner_and_range(api, data):
    items = api.get("/api/trend?owner=jaehee&months=3&month=2026-09").json()["items"]
    assert [(i["month"], i["expense"]) for i in items] == [
        ("2026-07", 0),
        ("2026-08", 0),
        ("2026-09", 5_000),
    ]
    assert api.get("/api/trend?months=0").status_code == 400
    assert api.get("/api/trend?months=25").status_code == 400
    assert api.get("/api/trend?month=2026-13").status_code == 400


def test_subcategories(api, api_conn):
    add_tx(api_conn, 1, -10_000, "2026-09-05", "식비", "한식")
    add_tx(api_conn, 1, -3_000, "2026-09-06", "식비", "한식")
    add_tx(api_conn, 1, -7_000, "2026-09-07", "식비", "")
    add_tx(api_conn, 1, 2_000, "2026-09-08", "식비", "카페")  # 환불
    s = api.get("/api/summary?month=2026-09").json()
    assert s["expense_by_subcategory"] == {
        "식비": [
            {"subcategory": "한식", "amount": 13_000},
            {"subcategory": "미분류", "amount": 7_000},
            {"subcategory": "카페", "amount": -2_000},
        ]
    }
    assert s["income_by_subcategory"] == {}
