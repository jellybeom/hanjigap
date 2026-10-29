"""F4b: 현황 API (자산 · 고정비 월 상태 · 메인 화면 · 차 유지비 · 시세 추이)."""

import datetime as dt
import hashlib
import itertools

import pytest

import hanjigap.web.fixed_api as fixed_mod
import hanjigap.web.ledger_api as ledger_mod
import hanjigap.web.status_api as status_mod
import hanjigap.web.vehicles_api as vehicles_mod

TODAY = dt.date(2026, 10, 29)
_n = itertools.count(1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for mod in (fixed_mod, ledger_mod, status_mod, vehicles_mod):
        monkeypatch.setattr(mod, "today", lambda _r: TODAY)


def add_tx(conn, member_id, amount, content, tx_type="지출", category="식비", sub="",date="2026-09-10", instrument="카드"):  # fmt: skip
    n = next(_n)
    key = hashlib.sha256(f"s{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, ?, ?, ?, ?, ?, 1, ?)",
        (member_id, key, f"b{n}", date[:7], date, tx_type, category, sub, content, amount,
         instrument, n),
    )  # fmt: skip
    return key


def snapshot(conn, member_id, ym, net, holdings=()):
    batch = conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (?, ?, 'zip', 0, 'applied')",
        (member_id, hashlib.sha256(f"{member_id}{ym}".encode()).hexdigest()),
    ).lastrowid
    sid = conn.execute(
        "INSERT INTO bs_snapshots (batch_id, member_id, as_of, year_month, total_assets, "
        "total_liabilities, net_worth, checks_ok) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (batch, member_id, f"{ym}-28", ym, net + 1_000, 1_000, net),
    ).lastrowid
    for seq, (section, group, name, amount) in enumerate(holdings, start=1):
        conn.execute(
            "INSERT INTO bs_holdings (snapshot_id, seq, section, group_name, name, amount) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, seq, section, group, name, amount),
        )


def post(api, path, body, status=201):
    r = api.post(path, json=body)
    assert r.status_code == status, r.json()
    return r.json()


CAR = {"name": "패밀리 SUV", "purchased_on": "2023-03", "purchase_price": 32_000_000}


@pytest.fixture
def car(api):
    return post(api, "/api/vehicles", {**CAR, "owner": "jaehee", "inspection_due_on": "2026-11-20"})


@pytest.fixture
def home_contract(api):
    home = post(api, "/api/properties", {"name": "우리집"})
    body = {
        "contract_type": "전세",
        "amount": 300_000_000,
        "start_on": "2025-03-01",
        "end_on": "2027-02-28",  # 2026-10-29 기준 만기 4개월 남음 → 갱신 의사 통지 기간
    }
    return post(api, f"/api/properties/{home['id']}/contracts", body)


# --- 자산 ----------------------------------------------------------------------


def test_assets_without_banksalad(api, car, home_contract):
    a = api.get("/api/assets").json()
    sb, jh = a["members"]
    assert (sb["banksalad"], jh["banksalad"]) == (None, None)
    assert sb["estates"][0]["value"] == 300_000_000 and sb["estates"][0]["included"]
    assert jh["vehicles"][0]["included"] and jh["vehicles"][0]["formula"].startswith("32,000,000원")
    car_value = jh["vehicles"][0]["value"]
    assert (a["value_month"], a["banksalad_net"]) == ("2026-10", 0)
    assert a["added"] == a["net_worth"] == 300_000_000 + car_value


def test_assets_with_banksalad(api, api_conn, car, home_contract):
    snapshot(api_conn, 1, "2026-09", 50_000_000, [("asset", "현금 자산", "통장", 51_000_000)])
    snapshot(api_conn, 2, "2026-09", 20_000_000, [("asset", "동산", "자동차", 25_000_000)])
    a = api.get("/api/assets").json()
    sb, jh = a["members"]
    assert sb["banksalad"]["net_worth"] == 50_000_000
    assert sb["banksalad"]["groups"] == [
        {"section": "asset", "group": "현금 자산", "amount": 51_000_000, "count": 1}
    ]
    assert sb["net_worth"] == 50_000_000 + 300_000_000  # 뱅샐에 부동산 없음 → 보증금 더함
    v = jh["vehicles"][0]
    assert (v["included"], v["note"]) == (False, "뱅샐 값 사용")  # 뱅샐 '동산'에 이미 있음
    assert (jh["added"], jh["net_worth"]) == (0, 20_000_000)
    assert a["net_worth"] == 370_000_000


def test_banksalad_estate_not_counted_twice(api, api_conn, home_contract):
    snapshot(api_conn, 1, "2026-09", 280_000_000, [("asset", "부동산", "전세보증금", 300_000_000)])
    sb = api.get("/api/assets").json()["members"][0]
    assert (sb["estates"][0]["included"], sb["added"], sb["net_worth"]) == (False, 0, 280_000_000)


def test_assets_for_month(api, api_conn, car):
    snapshot(api_conn, 1, "2026-08", 10, [])
    snapshot(api_conn, 1, "2026-09", 20, [])
    a = api.get("/api/assets?month=2026-08").json()
    assert (a["value_month"], a["members"][0]["banksalad"]["net_worth"]) == ("2026-08", 10)
    assert api.get("/api/assets?month=2026-07").json()["members"][0]["banksalad"] is None
    assert api.get("/api/assets").json()["members"][0]["banksalad"]["net_worth"] == 20  # 최근
    assert api.get("/api/assets?month=2026-7").status_code == 400


def test_sold_car_not_in_later_assets(api, car):
    api.post(f"/api/vehicles/{car['id']}/sold", json={"sold_on": "2026-05-10"})
    assert api.get("/api/assets").json()["members"][1]["vehicles"] == []
    assert len(api.get("/api/assets?month=2026-04").json()["members"][1]["vehicles"]) == 1
    # 판 달 말에는 이미 없다 (판 돈은 통장에 들어오니 두 번 세지 않게)
    assert api.get("/api/assets?month=2026-05").json()["members"][1]["vehicles"] == []


def test_car_sold_this_month(api, car):
    api.post(f"/api/vehicles/{car['id']}/sold", json={"sold_on": "2026-10-29"})
    a = api.get("/api/assets").json()  # 이번 달(10월) 값
    assert a["members"][1]["vehicles"] == [] and a["added"] == 0
    assert len(api.get("/api/assets?month=2026-09").json()["members"][1]["vehicles"]) == 1
    # 차 상세의 시세는 판 달 값('팔 때 추정 시세')을 그대로 보여 준다
    d = api.get(f"/api/vehicles/{car['id']}").json()
    assert d["estimate"]["year_month"] == "2026-10"
    # 유지비는 판 달까지 센다 (더 길게 물어도 판 달에서 끊는다)
    costs = api.get(f"/api/vehicles/{car['id']}/costs?from=2026-09&to=2026-12").json()
    assert costs["to"] == "2026-10"


def test_moved_out_home_not_in_that_month(api, home_contract):
    pid = home_contract["id"]  # 계약을 넣으면 집 상세가 돌아온다
    api.post(f"/api/properties/{pid}/move-out", json={"moved_out_on": "2026-08-20"})
    assert api.get("/api/assets?month=2026-07").json()["members"][0]["estates"] != []
    assert api.get("/api/assets?month=2026-08").json()["members"][0]["estates"] == []


# --- 고정비 월 상태 ---------------------------------------------------------------


@pytest.fixture
def fixed(api, api_conn):
    acc = post(api, "/api/accounts", {"institution": "A은행", "name": "주거래", "kind": "입출금"})
    base = {"method": "자동이체", "pay_account_id": acc["id"], "start_month": "2026-01"}
    post(api, "/api/fixed-items", {**base, "name": "통신비", "category": "통신·구독",
                                   "amount": 55_000, "pay_day": 10})  # fmt: skip
    post(api, "/api/fixed-items", {**base, "name": "적금", "category": "저축",
                                   "amount": 300_000, "pay_day": 20})  # fmt: skip
    add_tx(api_conn, 1, -55_000, "통신사", "지출", "주거/통신", date="2026-09-10")
    api_conn.execute(
        "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count) "
        "VALUES (1, '2026-09', 'settled', 1, 1)"
    )


def test_fixed_status(api, fixed):
    r = api.get("/api/fixed-status").json()  # 비우면 지난달
    assert r["month"] == "2026-09"
    assert r["counts"] == {"paid": 1, "different": 0, "missed": 1, "pending": 0, "upcoming": 0}
    assert (r["total"], r["total_living"], r["total_saving"]) == (355_000, 55_000, 300_000)
    paid = next(i for i in r["items"] if i["status"] == "paid")
    assert (paid["item"]["name"], paid["status_label"], paid["tx"]["amount"]) == (
        "통신비",
        "빠져나감",
        -55_000,
    )
    assert paid["amount_gap"] == 0
    assert api.get("/api/fixed-status?owner=jaehee").json()["items"] == []
    assert api.get("/api/fixed-status?owner=nobody").status_code == 400


# --- 메인 화면 -------------------------------------------------------------------


def test_home(api, api_conn, fixed, car, home_contract):
    add_tx(api_conn, 1, -100_000, "어머니", "이체", "이체", date="2026-09-15")
    add_tx(api_conn, 1, 500_000, "매트리스 환불", "지출", "생활", date="2026-09-05")
    add_tx(api_conn, 1, -40_000, "식당", date="2026-08-05")
    h = api.get("/api/home").json()
    assert h["month"] == "2026-09"
    s = h["summary"]
    assert (s["expense"], s["unconfirmed"], s["expense_diff"]) == (-445_000, 1, -485_000)
    assert h["fixed"]["counts"]["missed"] == 1
    assert h["assets"]["net_worth"] > 300_000_000
    kinds = {a["kind"]: a for a in h["alerts"]}
    assert kinds["not_settled"]["message"] == "재희 9월 정산 전"
    assert kinds["unconfirmed"]["message"] == "확인 필요 거래 1건"
    assert kinds["fixed_missed"]["message"] == "고정비 안 빠져나감 1건"
    assert kinds["negative_category"]["category"] == "생활"
    assert kinds["renewal"]["stage"] == "notice" and "122일 남음" in kinds["renewal"]["message"]
    assert kinds["inspection"]["message"] == "패밀리 SUV 정기검사 22일 남음"


def test_home_quiet_month(api):
    h = api.get("/api/home?month=2026-09").json()
    assert [a["kind"] for a in h["alerts"]] == ["not_settled", "not_settled"]
    assert h["summary"]["expense"] == 0 and h["assets"]["net_worth"] == 0


# --- 차 유지비 · 시세 추이 ---------------------------------------------------------


def test_vehicle_costs(api, api_conn, car):
    add_tx(api_conn, 1, -70_000, "주유소", "지출", "자동차", "주유", date="2026-09-02")
    add_tx(api_conn, 2, -12_000, "고속도로", "지출", "자동차", "통행료", date="2026-08-20")
    add_tx(api_conn, 1, -1_000, "주유소", "지출", "자동차", "주유", date="2026-10-02")  # 이번 달
    r = api.get(f"/api/vehicles/{car['id']}/costs").json()
    assert (r["from"], r["to"], r["months"]) == ("2025-10", "2026-09", 12)
    assert (r["total"], r["monthly_average"]) == (82_000, 6_833)
    assert r["by_type"] == [
        {"cost_type": "fuel", "label": "주유", "amount": 70_000},
        {"cost_type": "toll", "label": "통행료", "amount": 12_000},
    ]
    assert [i["date"] for i in r["items"]] == ["2026-09-02", "2026-08-20"]
    assert r["items"][1]["owner"] == "jaehee"
    r = api.get(f"/api/vehicles/{car['id']}/costs?from=2026-10&to=2026-10").json()
    assert (r["total"], r["by_month"]) == (1_000, [{"month": "2026-10", "amount": 1_000}])
    bad = api.get(f"/api/vehicles/{car['id']}/costs?from=2026-10&to=2026-01")
    assert bad.status_code == 400
    assert api.get("/api/vehicles/99/costs").status_code == 404


def test_value_series(api, car):
    api.put(f"/api/vehicles/{car['id']}/valuations/2024-03", json={"value": 27_000_000})
    items = api.get(f"/api/vehicles/{car['id']}/value-series").json()["items"]
    assert items[0] == {"month": "2023-03", "value": 32_000_000, "anchor": True}
    assert items[-1]["month"] == "2026-10"
    assert next(i for i in items if i["month"] == "2024-03")["anchor"]
    items = api.get(f"/api/vehicles/{car['id']}/value-series?until=2023-05").json()["items"]
    assert [i["month"] for i in items] == ["2023-03", "2023-04", "2023-05"]
    api.post(f"/api/vehicles/{car['id']}/sold", json={"sold_on": "2025-01-15"})
    items = api.get(f"/api/vehicles/{car['id']}/value-series").json()["items"]
    assert items[-1]["month"] == "2025-01"
    assert api.get("/api/vehicles/99/value-series").status_code == 404
