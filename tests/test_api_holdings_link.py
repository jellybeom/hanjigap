"""G5b: 계좌·카드를 등록하면서 뱅샐 결제수단 이름을 바로 연결 (한 번에, 실패하면 둘 다 취소)."""

import pytest

from test_api_holdings import ACCOUNT, CARD, add_tx, post


@pytest.fixture
def instruments(api_conn):
    add_tx(api_conn, 1, "카드 A")
    add_tx(api_conn, 1, "카드 A")
    add_tx(api_conn, 1, "입출금통장", "이체")
    add_tx(api_conn, 2, "재희 카드")


def usage(api, owner="sangbeom"):
    items = api.get(f"/api/instruments?owner={owner}").json()["items"]
    return {i["instrument"]: i for i in items}


def test_add_card_and_link(api, instruments):
    c = post(api, "/api/cards", {**CARD, "link": "카드 A"})
    assert (c["linked_count"], c["linked_instruments"]) == (1, ["카드 A"])
    u = usage(api)["카드 A"]
    assert (u["target"], u["target_id"], u["target_name"]) == ("card", c["id"], "생활 카드")


def test_add_account_and_link(api, instruments):
    a = post(api, "/api/accounts", {**ACCOUNT, "link": "입출금통장"})
    assert a["linked_instruments"] == ["입출금통장"]
    assert usage(api)["입출금통장"]["target"] == "account"


def test_add_for_other_member_and_link(api, instruments):
    c = post(api, "/api/cards", {**CARD, "owner": "jaehee", "link": "재희 카드"})
    assert c["owner"] == "jaehee"
    assert usage(api, "jaehee")["재희 카드"]["target_id"] == c["id"]


def test_failed_link_cancels_add(api, instruments):
    r = api.post("/api/cards", json={**CARD, "link": "없는 이름"})
    assert r.status_code == 400 and "결제수단 이름이 없어요" in r.json()["error"]
    assert api.get("/api/cards").json()["items"] == []
    # 다른 사람의 결제수단 이름도 내 카드에는 연결되지 않는다
    r = api.post("/api/cards", json={**CARD, "link": "재희 카드"})
    assert r.status_code == 400
    assert api.get("/api/cards").json()["items"] == []


def test_without_link_unchanged(api, instruments):
    c = post(api, "/api/cards", CARD)
    assert c["linked_count"] == 0
    assert usage(api)["카드 A"]["linked"] is False
