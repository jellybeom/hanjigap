"""F3a: 선택지 · 계좌 · 카드 · 결제수단 연결 API."""

import datetime as dt
import hashlib
import itertools
import logging

import pytest

_n = itertools.count(1)


def add_tx(conn, member_id, instrument, tx_type="지출", date="2026-09-10"):
    n = next(_n)
    key = hashlib.sha256(f"k{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, '식비', '', '가게', -1000, ?, 1, ?)",
        (member_id, key, f"b{n}", date[:7], date, tx_type, instrument, n),
    )


ACCOUNT = {"institution": "A은행", "name": "주거래 통장", "kind": "입출금"}
CARD = {"issuer": "B카드", "name": "생활 카드", "kind": "신용"}


def post(api, path, body, status=201):
    r = api.post(path, json=body)
    assert r.status_code == status, r.json()
    return r.json()


# --- 선택지 ---------------------------------------------------------------------


def test_options(api):
    r = api.get("/api/options").json()
    assert {"code": "checking", "label": "입출금"} in r["account_kinds"]
    assert {"code": "-1", "label": "전월"} in r["card_period_month_offsets"]
    assert {"code": "easy_pay", "label": "간편결제"} in r["link_targets"]
    assert {"code": "jeonse", "label": "전세"} in r["contract_types"]
    assert len(r) == 13


# --- 계좌 ----------------------------------------------------------------------


def test_add_account_defaults_to_me(api):
    a = post(api, "/api/accounts", {**ACCOUNT, "last4": "0937", "opened_on": "2015.03"})
    assert a["owner"] == "sangbeom"
    assert (a["kind"], a["kind_label"], a["last4"], a["opened_on"]) == (
        "checking",
        "입출금",
        "0937",
        "2015-03",
    )
    assert a["active"] and a["linked_count"] == 0 and a["linked_instruments"] == []


def test_add_account_for_other_member(api):
    a = post(api, "/api/accounts", {**ACCOUNT, "owner": "jaehee"})
    assert a["owner"] == "jaehee"


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({**ACCOUNT, "owner": "nobody"}, 400, "구성원을 찾을 수 없습니다"),
        ({**ACCOUNT, "kind": "비트코인"}, 400, "계좌 종류를 알 수 없어요"),
        ({**ACCOUNT, "last4": "12345"}, 400, "끝 4자리"),
        ({**ACCOUNT, "memo": "110-123-456789"}, 400, "끝 4자리만 저장"),
        ({**ACCOUNT, "name": " "}, 400, "계좌 이름"),
        ({"institution": "A은행", "kind": "입출금"}, 422, "입력 형식"),
        ({**ACCOUNT, "last4": 937}, 422, "입력 형식"),
    ],
)
def test_add_account_rejects(api, body, status, message):
    r = api.post("/api/accounts", json=body)
    assert r.status_code == status and message in r.json()["error"]


def test_duplicate_account_name(api):
    post(api, "/api/accounts", ACCOUNT)
    r = api.post("/api/accounts", json=ACCOUNT)
    assert r.status_code == 400 and "이미 있어요" in r.json()["error"]
    assert post(api, "/api/accounts", {**ACCOUNT, "owner": "jaehee"})["owner"] == "jaehee"


def test_list_accounts_filters(api):
    a = post(api, "/api/accounts", ACCOUNT)
    post(api, "/api/accounts", {**ACCOUNT, "owner": "jaehee"})
    api.post(f"/api/accounts/{a['id']}/close", json={"closed_on": "2026-10-01"})
    assert [i["owner"] for i in api.get("/api/accounts").json()["items"]] == ["jaehee"]
    everyone = api.get("/api/accounts?include_closed=true").json()["items"]
    assert [(i["owner"], i["active"]) for i in everyone] == [
        ("sangbeom", False),
        ("jaehee", True),
    ]
    mine = api.get("/api/accounts?owner=sangbeom&include_closed=true").json()["items"]
    assert len(mine) == 1
    assert api.get("/api/accounts?owner=nobody").status_code == 400


def test_get_account_not_found(api):
    r = api.get("/api/accounts/99")
    assert (r.status_code, r.json()) == (404, {"error": "계좌를 찾을 수 없어요: 99"})
    assert api.patch("/api/accounts/99", json={"memo": "x"}).status_code == 404
    assert api.post("/api/accounts/99/close").status_code == 404


def test_update_account(api):
    a = post(api, "/api/accounts", {**ACCOUNT, "memo": "월급", "color": "#AA3300"})
    r = api.patch(f"/api/accounts/{a['id']}", json={"name": "월급 통장", "memo": ""})
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["memo"], r.json()["color"]) == ("월급 통장", None, "#aa3300")
    r = api.patch(f"/api/accounts/{a['id']}", json={})
    assert (r.status_code, r.json()) == (400, {"error": "바꿀 내용이 없어요"})
    assert api.patch(f"/api/accounts/{a['id']}", json={"owner": "jaehee"}).status_code == 400


def test_close_defaults_to_today_and_reopen(api, monkeypatch):
    import hanjigap.web.holdings_api as mod

    monkeypatch.setattr(mod, "today", lambda _request: dt.date(2026, 10, 29))
    a = post(api, "/api/accounts", {**ACCOUNT, "opened_on": "2015-03-02"})
    r = api.post(f"/api/accounts/{a['id']}/close")
    assert (r.json()["closed_on"], r.json()["active"]) == ("2026-10-29", False)
    r = api.post(f"/api/accounts/{a['id']}/close", json={"closed_on": "2010-01-01"})
    assert r.status_code == 400 and "개설일" in r.json()["error"]
    r = api.post(f"/api/accounts/{a['id']}/reopen")
    assert (r.json()["closed_on"], r.json()["active"]) == (None, True)


# --- 카드 ----------------------------------------------------------------------


def test_add_card_with_pay_account(api):
    acc = post(api, "/api/accounts", {**ACCOUNT, "owner": "jaehee"})
    c = post(
        api,
        "/api/cards",
        {
            **CARD,
            "last4": "1234",
            "pay_day": 14,
            "period_start_day": 1,
            "period_month_offset": -1,
            "pay_account_id": acc["id"],  # 가족카드: 다른 사람 계좌도 된다
            "annual_fee": 15000,
        },
    )
    assert (c["owner"], c["kind_label"], c["period_label"]) == (
        "sangbeom",
        "신용",
        "전월 1일 ~ 말일",
    )
    assert c["pay_account"] == {"id": acc["id"], "name": "주거래 통장", "owner": "jaehee"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({**CARD, "pay_account_id": 99}, "계좌를 찾을 수 없어요"),  # 내용 속 번호는 400
        ({**CARD, "period_start_day": 1}, "함께 입력"),
        ({**CARD, "pay_day": 32}, "결제일"),
        ({**CARD, "annual_fee": -1}, "연회비"),
        ({**CARD, "kind": "금"}, "카드 종류를"),
    ],
)
def test_add_card_rejects(api, body, message):
    r = api.post("/api/cards", json=body)
    assert r.status_code == 400 and message in r.json()["error"]


def test_update_card_clear_fields(api):
    acc = post(api, "/api/accounts", ACCOUNT)
    c = post(api, "/api/cards", {**CARD, "pay_day": 14, "pay_account_id": acc["id"]})
    r = api.patch(
        f"/api/cards/{c['id']}",
        json={"pay_day": 0, "pay_account_id": 0, "period_start_day": 15, "period_month_offset": -1},
    )
    body = r.json()
    assert (body["pay_day"], body["pay_account"], body["period_label"]) == (
        None,
        None,
        "전월 15일 ~ 당월 14일",
    )


def test_card_close_reopen_and_not_found(api):
    c = post(api, "/api/cards", CARD)
    r = api.post(f"/api/cards/{c['id']}/close", json={"closed_on": "2026-10-01"})
    assert r.json()["closed_on"] == "2026-10-01"
    assert api.get("/api/cards").json()["items"] == []
    assert len(api.get("/api/cards?include_closed=true").json()["items"]) == 1
    assert api.post(f"/api/cards/{c['id']}/reopen").json()["active"]
    assert api.get("/api/cards/99").json() == {"error": "카드를 찾을 수 없어요: 99"}


# --- 결제수단 연결 -----------------------------------------------------------------


def test_instruments_list_and_link(api, api_conn):
    for _ in range(3):
        add_tx(api_conn, 1, "생활 카드 1234")
    add_tx(api_conn, 1, "입출금통장", tx_type="이체")
    add_tx(api_conn, 2, "재희 카드")  # 다른 사람 것은 안 나옴
    card = post(api, "/api/cards", CARD)
    items = api.get("/api/instruments").json()
    assert items["owner"] == "sangbeom"
    assert [(i["instrument"], i["count"], i["linked"]) for i in items["items"]] == [
        ("생활 카드 1234", 3, False),
        ("입출금통장", 1, False),
    ]

    r = api.put(
        "/api/instruments/link",
        json={"instrument": "생활 카드 1234", "target": "카드", "target_id": card["id"]},
    )
    assert r.status_code == 200
    linked = r.json()
    assert (linked["target"], linked["target_id"], linked["link_label"]) == (
        "card",
        card["id"],
        "카드 · 생활 카드",
    )
    assert api.get(f"/api/cards/{card['id']}").json()["linked_instruments"] == ["생활 카드 1234"]
    assert api.get("/api/cards").json()["items"][0]["linked_count"] == 1
    unlinked = api.get("/api/instruments?unlinked=true").json()["items"]
    assert [i["instrument"] for i in unlinked] == ["입출금통장"]

    r = api.post("/api/instruments/unlink", json={"instrument": "생활 카드 1234"})
    assert r.json() == {"unlinked": True}
    assert api.post("/api/instruments/unlink", json={"instrument": "생활 카드 1234"}).json() == {
        "unlinked": False
    }


def test_link_other_members_instrument(api, api_conn):
    add_tx(api_conn, 2, "재희 통장")
    acc = post(api, "/api/accounts", {**ACCOUNT, "owner": "jaehee"})
    r = api.put(
        "/api/instruments/link",
        json={
            "owner": "jaehee",
            "instrument": "재희 통장",
            "target": "account",
            "target_id": acc["id"],
        },
    )
    assert r.json()["target_name"] == "주거래 통장"
    row = api_conn.execute("SELECT linked_by FROM instrument_links").fetchone()
    assert row[0] == 1  # 상범이 연결했다고 남는다
    assert api.get("/api/instruments?owner=jaehee").json()["items"][0]["linked"]


def test_link_easy_pay(api, api_conn):
    add_tx(api_conn, 1, "OO페이")
    r = api.put("/api/instruments/link", json={"instrument": "OO페이", "target": "간편결제"})
    assert (r.json()["target"], r.json()["target_id"], r.json()["link_label"]) == (
        "easy_pay",
        None,
        "간편결제",
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"instrument": "OO페이", "target": "easy_pay", "target_id": 1}, "번호 없이"),
        ({"instrument": "OO페이", "target": "account"}, "계좌·카드를 고르세요"),
        ({"instrument": "OO페이", "target": "은행"}, "연결 대상을 알 수 없어요"),
        ({"instrument": "없는 이름", "target": "easy_pay"}, "그런 결제수단 이름이 없어요"),
        ({"instrument": "OO페이", "target": "account", "target_id": 99}, "계좌를 찾을 수 없어요"),
    ],
)
def test_link_rejects(api, api_conn, body, message):
    add_tx(api_conn, 1, "OO페이")
    r = api.put("/api/instruments/link", json=body)
    assert r.status_code == 400 and message in r.json()["error"]


def test_link_to_other_members_card_rejected(api, api_conn):
    add_tx(api_conn, 1, "생활 카드")
    card = post(api, "/api/cards", {**CARD, "owner": "jaehee"})
    r = api.put(
        "/api/instruments/link",
        json={"instrument": "생활 카드", "target": "card", "target_id": card["id"]},
    )
    assert r.status_code == 400 and "다른 사람의 카드" in r.json()["error"]


def test_failed_link_saves_nothing(api, api_conn):
    add_tx(api_conn, 1, "OO페이")
    api.put(
        "/api/instruments/link", json={"instrument": "OO페이", "target": "card", "target_id": 9}
    )
    assert api_conn.execute("SELECT count(*) FROM instrument_links").fetchone()[0] == 0


def test_log_has_no_names(api, api_conn, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap.web")
    add_tx(api_conn, 1, "비밀 카드 9876")
    api.put("/api/instruments/link", json={"instrument": "비밀 카드 9876", "target": "easy_pay"})
    post(api, "/api/accounts", {**ACCOUNT, "name": "비밀 통장"})
    assert "PUT /api/instruments/link 200" in caplog.text
    assert "POST /api/accounts 201" in caplog.text
    assert "비밀" not in caplog.text
