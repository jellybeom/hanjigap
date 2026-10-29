"""F3b: 고정비 · 자동차 API."""

import datetime as dt
import hashlib
import itertools

import pytest

import hanjigap.web.fixed_api as fixed_mod
import hanjigap.web.vehicles_api as vehicles_mod

_n = itertools.count(1)
TODAY = dt.date(2026, 10, 29)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(fixed_mod, "today", lambda _r: TODAY)
    monkeypatch.setattr(vehicles_mod, "today", lambda _r: TODAY)


def add_tx(conn, member_id, content="주유소", category="자동차", sub="주유"):
    n = next(_n)
    key = hashlib.sha256(f"v{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, '2026-09', '2026-09-10', '10:00:00', '지출', ?, ?, ?, -1000, "
        "'카드', 1, ?)",
        (member_id, key, f"b{n}", category, sub, content, n),
    )
    return key


def post(api, path, body, status=201):
    r = api.post(path, json=body)
    assert r.status_code == status, r.json()
    return r.json()


def account(api, owner="sangbeom", name="주거래 통장"):
    body = {"institution": "A은행", "name": name, "kind": "입출금", "owner": owner}
    return post(api, "/api/accounts", body)


ALLOWANCE = {
    "name": "용돈",
    "category": "용돈",
    "amount": 500_000,
    "pay_day": 25,
    "method": "자동이체",
    "start_month": "2026-01",
}
CAR = {"name": "패밀리 SUV", "purchased_on": "2023-03", "purchase_price": 32_000_000}


# --- 고정비 ---------------------------------------------------------------------


def test_fixed_owner_follows_account(api):
    acc = account(api, owner="jaehee")
    item = post(api, "/api/fixed-items", {**ALLOWANCE, "pay_account_id": acc["id"]})
    assert item["owner"] == "jaehee"
    assert item["pay_account"] == {"id": acc["id"], "name": "주거래 통장", "owner": "jaehee"}
    assert (item["category_label"], item["group"], item["method_label"]) == (
        "용돈",
        "living",
        "자동이체",
    )
    assert (item["schedule_label"], item["ended"]) == ("매월 25일", False)


def test_fixed_manual_owner(api):
    body = {**ALLOWANCE, "method": "직접 이체"}
    assert post(api, "/api/fixed-items", body)["owner"] == "sangbeom"
    assert post(api, "/api/fixed-items", {**body, "owner": "jaehee"})["owner"] == "jaehee"


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"owner": "jaehee"}, "담당자는 출금 계좌·카드의 주인"),
        ({"method": "카드 자동결제"}, "카드를 골라 주세요"),
        ({"cycle": "매년"}, "출금 월"),
        ({"amount": 0}, "금액"),
        ({"vehicle_id": 9}, "차량을 찾을 수 없어요"),
        ({"property_id": 9}, "집을 찾을 수 없어요"),
        ({"owner": "nobody"}, "구성원을 찾을 수 없습니다"),
    ],
)
def test_fixed_rejects(api, over, message):
    acc = account(api)
    r = api.post("/api/fixed-items", json={**ALLOWANCE, "pay_account_id": acc["id"], **over})
    assert r.status_code == 400 and message in r.json()["error"]


def test_fixed_list_filters(api):
    acc = account(api)
    post(api, "/api/fixed-items", {**ALLOWANCE, "pay_account_id": acc["id"]})
    yearly = {
        **ALLOWANCE,
        "name": "자동차보험",
        "category": "보험",
        "cycle": "매년",
        "pay_month": 3,
        "pay_day": 14,
        "method": "직접 이체",
        "owner": "jaehee",
    }
    post(api, "/api/fixed-items", yearly)

    def names(query):
        return [i["name"] for i in api.get(f"/api/fixed-items{query}").json()["items"]]

    assert names("") == ["자동차보험", "용돈"]  # 출금일 순
    assert names("?month=2026-10") == ["용돈"]
    assert names("?month=2027-03") == ["자동차보험", "용돈"]
    assert names("?owner=jaehee") == ["자동차보험"]
    assert api.get("/api/fixed-items?month=2026-13").status_code == 400


def test_fixed_update_and_owner_follows(api):
    mine, hers = account(api), account(api, owner="jaehee")
    item = post(api, "/api/fixed-items", {**ALLOWANCE, "pay_account_id": mine["id"]})
    r = api.patch(f"/api/fixed-items/{item['id']}", json={"pay_account_id": hers["id"]})
    assert r.json()["owner"] == "jaehee"
    r = api.patch(
        f"/api/fixed-items/{item['id']}", json={"method": "직접 이체", "pay_account_id": 0}
    )
    assert (r.json()["owner"], r.json()["pay_account"]) == ("jaehee", None)  # 담당 그대로
    r = api.patch(f"/api/fixed-items/{item['id']}", json={"owner": "sangbeom", "memo": "월말"})
    assert (r.json()["owner"], r.json()["memo"]) == ("sangbeom", "월말")
    assert api.patch(f"/api/fixed-items/{item['id']}", json={}).status_code == 400


def test_fixed_end_and_reopen(api):
    item = post(api, "/api/fixed-items", {**ALLOWANCE, "method": "직접 이체"})
    r = api.post(f"/api/fixed-items/{item['id']}/end")
    assert (r.json()["end_month"], r.json()["ended"]) == ("2026-10", False)  # 이번 달까지는 유효
    r = api.post(f"/api/fixed-items/{item['id']}/end", json={"end_month": "2026-09"})
    assert (r.json()["end_month"], r.json()["ended"]) == ("2026-09", True)
    assert api.get("/api/fixed-items").json()["items"] == []
    assert len(api.get("/api/fixed-items?include_ended=true").json()["items"]) == 1
    r = api.patch(f"/api/fixed-items/{item['id']}", json={"end_month": ""})
    assert r.json()["end_month"] is None
    r = api.post(f"/api/fixed-items/{item['id']}/end", json={"end_month": "2025-12"})
    assert r.status_code == 400 and "시작 월보다 빨라요" in r.json()["error"]


def test_fixed_not_found(api):
    assert api.get("/api/fixed-items/9").json() == {"error": "고정비를 찾을 수 없어요: 9"}
    assert api.patch("/api/fixed-items/9", json={"memo": "x"}).status_code == 404
    assert api.post("/api/fixed-items/9/end").status_code == 404


# --- 차량 ----------------------------------------------------------------------


def test_add_vehicle_with_rules(api, api_conn):
    v = post(api, "/api/vehicles", {**CAR, "owner": "jaehee", "inspection_due_on": "2026-11-28"})
    assert (v["owner"], v["active"], v["depreciation_pct"]) == ("jaehee", True, 15.0)
    assert v["inspection_days_left"] == 30
    e = v["estimate"]
    assert (e["year_month"], e["months"], e["anchor"]["source"]) == ("2026-10", 43, "purchase")
    assert e["formula"].startswith("32,000,000원 × 0.85^(43/12) = ")
    assert v["anchors"][0]["label"] == "2023년 3월 구입가 32,000,000원"
    assert (v["valuations"], v["fixed_items"]) == ([], [])
    rules = api.get(f"/api/vehicles/{v['id']}/cost-rules").json()["items"]
    assert len(rules) == 9 and rules[0]["label"] == "분류 자동차 › 주유"
    assert rules[0]["owner"] is None and rules[0]["cost_type_label"] == "주유"


def test_add_vehicle_defaults_to_me(api):
    assert post(api, "/api/vehicles", CAR)["owner"] == "sangbeom"


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"purchase_price": 0}, "구입가"),
        ({"depreciation_pct": 60}, "감가율"),
        ({"purchased_on": "2023/03"}, "구입일"),
        ({"inspection_due_on": "내일"}, "정기검사 예정일"),
    ],
)
def test_add_vehicle_rejects(api, over, message):
    r = api.post("/api/vehicles", json={**CAR, **over})
    assert r.status_code == 400 and message in r.json()["error"]


def test_failed_vehicle_makes_no_rules(api, api_conn):
    post(api, "/api/vehicles", CAR)
    r = api.post("/api/vehicles", json=CAR)  # 같은 사람·같은 이름
    assert r.status_code == 400 and "이미 있어요" in r.json()["error"]
    assert api_conn.execute("SELECT count(*) FROM vehicle_cost_rules").fetchone()[0] == 9


def test_update_vehicle(api):
    v = post(api, "/api/vehicles", CAR)
    r = api.patch(f"/api/vehicles/{v['id']}", json={"depreciation_pct": 12, "memo": "첫 차"})
    assert (r.json()["depreciation_pct"], r.json()["memo"]) == (12.0, "첫 차")
    assert "0.88^" in r.json()["estimate"]["formula"]
    assert api.patch(f"/api/vehicles/{v['id']}", json={}).status_code == 400


def test_sold_and_cancel(api):
    v = post(api, "/api/vehicles", CAR)
    r = api.post(f"/api/vehicles/{v['id']}/sold", json={"sold_on": "2026-05-20"})
    assert (r.json()["active"], r.json()["estimate"]["year_month"]) == (False, "2026-05")
    assert api.get("/api/vehicles").json()["items"] == []
    assert len(api.get("/api/vehicles?include_sold=true").json()["items"]) == 1
    r = api.post(f"/api/vehicles/{v['id']}/sold", json={"sold_on": None})
    assert r.json()["active"] and r.json()["sold_on"] is None
    r = api.post(f"/api/vehicles/{v['id']}/sold", json={"sold_on": "2020-01-01"})
    assert r.status_code == 400 and "구입일보다 빨라요" in r.json()["error"]
    assert api.post(f"/api/vehicles/{v['id']}/sold", json={}).status_code == 422


def test_valuations(api):
    v = post(api, "/api/vehicles", CAR)
    r = api.put(f"/api/vehicles/{v['id']}/valuations/2026-09", json={"value": 25_000_000})
    body = r.json()
    assert body["valuations"] == [{"year_month": "2026-09", "value": 25_000_000, "memo": None}]
    assert body["estimate"]["anchor"]["source"] == "manual"
    assert body["estimate"]["formula"].startswith("25,000,000원 × 0.85^(1/12)")
    r = api.put(
        f"/api/vehicles/{v['id']}/valuations/2026-09", json={"value": 24_000_000, "memo": "견적"}
    )
    assert r.json()["valuations"][0]["memo"] == "견적"
    r = api.delete(f"/api/vehicles/{v['id']}/valuations/2026-09")
    assert r.json()["deleted"] and r.json()["vehicle"]["valuations"] == []
    assert not api.delete(f"/api/vehicles/{v['id']}/valuations/2026-09").json()["deleted"]


@pytest.mark.parametrize(
    ("ym", "value", "message"),
    [("2020-01", 1, "구입한 달보다"), ("2026-9", 1, "YYYY-MM"), ("2026-09", 0, "1원 이상")],
)
def test_valuation_rejects(api, ym, value, message):
    v = post(api, "/api/vehicles", CAR)
    r = api.put(f"/api/vehicles/{v['id']}/valuations/{ym}", json={"value": value})
    assert r.status_code == 400 and message in r.json()["error"]


def test_vehicle_detail_has_fixed_items(api):
    v = post(api, "/api/vehicles", {**CAR, "owner": "jaehee"})
    acc = account(api, owner="jaehee")
    post(
        api,
        "/api/fixed-items",
        {
            **ALLOWANCE,
            "name": "자동차 할부",
            "category": "대출",
            "pay_account_id": acc["id"],
            "vehicle_id": v["id"],
        },
    )
    detail = api.get(f"/api/vehicles/{v['id']}").json()
    assert [(i["name"], i["group"], i["vehicle"]["name"]) for i in detail["fixed_items"]] == [
        ("자동차 할부", "saving", "패밀리 SUV")
    ]


def test_vehicle_not_found(api):
    assert api.get("/api/vehicles/9").json() == {"error": "차량을 찾을 수 없어요: 9"}
    for method, path in [
        ("patch", "/api/vehicles/9"),
        ("post", "/api/vehicles/9/sold"),
        ("put", "/api/vehicles/9/valuations/2026-09"),
        ("get", "/api/vehicles/9/cost-rules"),
        ("get", "/api/vehicles/9/tags"),
        ("delete", "/api/vehicles/9/tags/sangbeom/" + "0" * 32),
    ]:
        kwargs = {"json": {"value": 1, "sold_on": None, "memo": "x"}} if method != "get" else {}
        if method == "delete":
            kwargs = {}
        assert getattr(api, method)(path, **kwargs).status_code == 404, path


# --- 유지비 규칙 ------------------------------------------------------------------


def test_add_and_manage_rules(api):
    v = post(api, "/api/vehicles", CAR)
    r = post(
        api,
        f"/api/vehicles/{v['id']}/cost-rules",
        {"kind": "글자", "cost_type": "정비", "pattern": "카센터"},
    )
    assert (r["label"], r["owner"], r["cost_type"]) == ("글자 「카센터」", None, "repair")
    r2 = post(
        api,
        f"/api/vehicles/{v['id']}/cost-rules",
        {"kind": "결제수단", "cost_type": "통행료", "pattern": "하이패스카드", "owner": "jaehee"},
    )
    assert r2["owner"] == "jaehee"
    off = api.patch(f"/api/vehicle-cost-rules/{r['id']}", json={"active": False}).json()
    assert off["active"] is False
    active = api.get(f"/api/vehicles/{v['id']}/cost-rules?include_inactive=false").json()["items"]
    assert r["id"] not in [i["id"] for i in active]
    assert api.delete(f"/api/vehicle-cost-rules/{r['id']}").json() == {"deleted": True}
    assert api.delete(f"/api/vehicle-cost-rules/{r['id']}").status_code == 404
    assert api.patch("/api/vehicle-cost-rules/999", json={"active": True}).status_code == 404


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"kind": "instrument", "cost_type": "toll", "pattern": "하이패스"}, "누구의 결제수단"),
        ({"kind": "text", "cost_type": "간식", "pattern": "x"}, "비용 종류를 알 수 없어요"),
        ({"kind": "category", "cost_type": "fuel"}, "분류"),
        ({"kind": "text", "cost_type": "fuel", "pattern": "x", "owner": "nobody"}, "구성원"),
    ],
)
def test_rule_rejects(api, body, message):
    v = post(api, "/api/vehicles", CAR)
    r = api.post(f"/api/vehicles/{v['id']}/cost-rules", json=body)
    assert r.status_code == 400 and message in r.json()["error"]


# --- 거래 붙이기·빼기 -------------------------------------------------------------


def test_tags(api, api_conn):
    v = post(api, "/api/vehicles", {**CAR, "owner": "jaehee"})
    key = add_tx(api_conn, 2, "블랙박스 설치", "생활", "생활서비스")
    r = api.put(
        f"/api/vehicles/{v['id']}/tags",
        json={"owner": "jaehee", "tx_key": key, "include": True, "cost_type": "정비"},
    )
    assert r.status_code == 200
    tag = r.json()
    assert (tag["owner"], tag["include"], tag["cost_type"], tag["tagged_by"]) == (
        "jaehee",
        True,
        "repair",
        "sangbeom",
    )
    r = api.put(
        f"/api/vehicles/{v['id']}/tags", json={"owner": "jaehee", "tx_key": key, "include": False}
    )
    assert (r.json()["include"], r.json()["cost_type"]) == (False, None)
    assert len(api.get(f"/api/vehicles/{v['id']}/tags").json()["items"]) == 1
    assert api.delete(f"/api/vehicles/{v['id']}/tags/jaehee/{key}").json() == {"deleted": True}
    assert api.delete(f"/api/vehicles/{v['id']}/tags/jaehee/{key}").json() == {"deleted": False}


def test_tag_rejects(api, api_conn):
    v = post(api, "/api/vehicles", CAR)
    key = add_tx(api_conn, 2)
    r = api.put(f"/api/vehicles/{v['id']}/tags", json={"tx_key": key, "include": True})
    assert r.status_code == 400 and "거래를 찾을 수 없어요" in r.json()["error"]  # 상범 거래 아님
    r = api.put(
        f"/api/vehicles/{v['id']}/tags",
        json={"owner": "jaehee", "tx_key": key, "include": True, "cost_type": "간식"},
    )
    assert r.status_code == 400 and "비용 종류" in r.json()["error"]
    assert api.delete(f"/api/vehicles/{v['id']}/tags/nobody/{key}").status_code == 400
