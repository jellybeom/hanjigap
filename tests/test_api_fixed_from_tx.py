"""G6: 거래 하나로 고정비 등록 초안 만들기 (/api/fixed-items/draft)."""

import datetime as dt

import pytest

import hanjigap.web.fixed_api as fixed_mod
import hanjigap.web.ledger_api as ledger_mod
from test_api_status import add_tx, post

TODAY = dt.date(2026, 10, 29)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for mod in (fixed_mod, ledger_mod):
        monkeypatch.setattr(mod, "today", lambda _r: TODAY)


def draft(api, key, owner="sangbeom"):
    return api.get(f"/api/fixed-items/draft?owner={owner}&tx_key={key}")


def test_draft_with_linked_card(api, api_conn):
    key = add_tx(api_conn, 1, -55_000, "OO텔레콤", "지출", "주거/통신", "휴대폰", "2026-09-14",
                 "카드 A")  # fmt: skip
    card = post(api, "/api/cards", {"issuer": "B카드", "name": "생활 카드", "kind": "신용",
                                    "link": "카드 A"})  # fmt: skip
    r = draft(api, key).json()
    assert r["draft"] == {
        "owner": "sangbeom",
        "name": "OO텔레콤",
        "category": "telecom",
        "amount": 55_000,
        "amount_mode": "fixed",
        "cycle": "monthly",
        "pay_day": 14,
        "method": "card_auto",
        "pay_account_id": None,
        "pay_card_id": card["id"],
        "match_text": "OO텔레콤",
        "match_days": 3,
        "start_month": "2026-09",
    }
    assert (r["instrument"], r["instrument_linked"], r["already"]) == ("카드 A", True, None)
    # 초안 그대로 등록할 수 있다
    item = post(api, "/api/fixed-items", r["draft"])
    assert (item["owner"], item["pay_card"]["id"], item["method_label"]) == (
        "sangbeom",
        card["id"],
        "카드 자동결제",
    )


def test_draft_with_linked_account(api, api_conn):
    key = add_tx(api_conn, 2, -300_000, "적금 납입", "이체", "저축", date="2026-09-20",
                 instrument="입출금통장")  # fmt: skip
    acc = post(api, "/api/accounts", {"institution": "A은행", "name": "생활 통장", "kind": "입출금",
                                      "owner": "jaehee", "link": "입출금통장"})  # fmt: skip
    d = draft(api, key, "jaehee").json()["draft"]
    assert (d["owner"], d["category"], d["method"], d["pay_account_id"]) == (
        "jaehee",
        "saving",
        "auto_transfer",
        acc["id"],
    )


def test_draft_without_link(api, api_conn):
    key = add_tx(api_conn, 1, -100_000, "어머니", "이체", "이체", date="2026-09-05")
    r = draft(api, key).json()
    assert (r["draft"]["method"], r["draft"]["category"], r["instrument_linked"]) == (
        "manual",
        "other",
        False,
    )
    assert post(api, "/api/fixed-items", r["draft"])["method"] == "manual"


def test_draft_uses_edited_content(api, api_conn):
    key = add_tx(api_conn, 1, -9_900, "원래 이름", date="2026-09-03")
    api.patch(f"/api/transactions/sangbeom/{key}", json={"content": "OO 구독"})
    d = draft(api, key).json()["draft"]
    assert (d["name"], d["match_text"], d["category"]) == ("OO 구독", "OO 구독", "telecom")


def test_draft_already_matched(api, api_conn):
    key = add_tx(api_conn, 1, -55_000, "통신사", "지출", "주거/통신", date="2026-09-10")
    item = post(api, "/api/fixed-items", {
        "name": "통신비", "category": "통신·구독", "amount": 55_000, "pay_day": 10,
        "method": "직접 이체", "start_month": "2026-01",
    })  # fmt: skip
    r = draft(api, key).json()
    assert r["already"]["id"] == item["id"]


def test_draft_rejects(api, api_conn):
    income = add_tx(api_conn, 1, 3_000_000, "OO공사급여", "수입", "급여")
    r = draft(api, income)
    assert r.status_code == 400 and "나간 돈만" in r.json()["error"]
    assert draft(api, "0" * 32).status_code == 404
    assert draft(api, income, "nobody").status_code == 400
    assert api.get("/api/fixed-items/draft").status_code == 422
