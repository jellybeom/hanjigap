"""F3c: 집 API (집 · 계약 이력 · 만기 알림 · 시세 · 이사 나감 · 고정비 연결)."""

import datetime as dt

import pytest

import hanjigap.web.estates_api as estates_mod
import hanjigap.web.fixed_api as fixed_mod

TODAY = dt.date(2025, 10, 1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(estates_mod, "today", lambda _r: TODAY)
    monkeypatch.setattr(fixed_mod, "today", lambda _r: TODAY)


def post(api, path, body, status=201):
    r = api.post(path, json=body)
    assert r.status_code == status, r.json()
    return r.json()


JEONSE = {
    "contract_type": "전세",
    "amount": 300_000_000,
    "start_on": "2024-03-01",
    "end_on": "2026-02-28",
}


@pytest.fixture
def home(api):
    return post(api, "/api/properties", {"name": "우리집", "address_memo": "OO동 OO아파트"})


# --- 집 -----------------------------------------------------------------------


def test_add_property(home):
    assert (home["kind"], home["kind_label"], home["active"]) == ("apartment", "아파트", True)
    assert (home["current_contract"], home["value"], home["contracts"]) == (None, None, [])
    assert (home["valuations"], home["fixed_items"]) == ([], [])


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({"name": "집", "kind": "성"}, 400, "집 종류를 알 수 없어요"),
        ({"name": " "}, 400, "집 이름"),
        ({"name": "집", "memo": "1234567890"}, 400, "끝 4자리"),
        ({}, 422, "입력 형식"),
    ],
)
def test_add_property_rejects(api, body, status, message):
    r = api.post("/api/properties", json=body)
    assert r.status_code == status and message in r.json()["error"]


def test_property_name_unique(api, home):
    r = api.post("/api/properties", json={"name": "우리집"})
    assert r.status_code == 400 and "같은 이름" in r.json()["error"]


def test_update_property(api, home):
    r = api.patch(f"/api/properties/{home['id']}", json={"kind": "빌라", "address_memo": ""})
    assert (r.json()["kind"], r.json()["address_memo"]) == ("villa", None)
    assert api.patch(f"/api/properties/{home['id']}", json={}).status_code == 400


def test_property_not_found(api):
    assert api.get("/api/properties/9").json() == {"error": "집을 찾을 수 없어요: 9"}
    for method, path, body in [
        ("patch", "/api/properties/9", {"memo": "x"}),
        ("post", "/api/properties/9/move-out", {"moved_out_on": None}),
        ("post", "/api/properties/9/contracts", JEONSE),
        ("put", "/api/properties/9/valuations/2026-09", {"value": 1}),
        ("delete", "/api/properties/9/valuations/2026-09", None),
        ("patch", "/api/property-contracts/9", {"memo": "x"}),
        ("post", "/api/property-contracts/9/end", {"ended_on": None}),
    ]:
        kwargs = {} if body is None else {"json": body}
        assert getattr(api, method)(path, **kwargs).status_code == 404, path


# --- 계약 ---------------------------------------------------------------------


def test_add_jeonse(api, home):
    p = post(api, f"/api/properties/{home['id']}/contracts", JEONSE)
    c = p["current_contract"]
    assert (c["owner"], c["contract_type"], c["type_label"], c["amount_label"]) == (
        "sangbeom",
        "jeonse",
        "전세",
        "보증금",
    )
    assert c["renewal"] == {
        "end_on": "2026-02-28",
        "days_left": 150,
        "notice_from": "2025-08-28",
        "notice_until": "2025-12-28",
        "stage": "notice",
        "stage_label": "갱신 의사를 알리는 기간",
    }
    assert p["value"] == {
        "year_month": "2025-10",
        "value": 300_000_000,
        "owner": "sangbeom",
        "basis": "deposit",
        "basis_month": "2024-03",
        "label": "전세 보증금",
    }
    assert [x["id"] for x in p["contracts"]] == [c["id"]]


def test_contract_for_other_member(api, home):
    body = {**JEONSE, "owner": "jaehee"}
    p = post(api, f"/api/properties/{home['id']}/contracts", body)
    assert (p["current_contract"]["owner"], p["value"]["owner"]) == ("jaehee", "jaehee")


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"contract_type": "반전세"}, "계약 종류를 알 수 없어요"),
        ({"end_on": None}, "전세 계약은 만기일"),
        ({"monthly_rent": 500_000}, "월세 계약에만"),
        ({"owner": "nobody"}, "구성원을 찾을 수 없습니다"),
        ({"amount": 0}, "보증금을 입력하세요"),
    ],
)
def test_add_contract_rejects(api, home, over, message):
    r = api.post(f"/api/properties/{home['id']}/contracts", json={**JEONSE, **over})
    assert r.status_code == 400 and message in r.json()["error"]


def test_renewal_keeps_history(api, home):
    post(api, f"/api/properties/{home['id']}/contracts", JEONSE)
    renewed = {**JEONSE, "amount": 315_000_000, "start_on": "2026-03-01", "end_on": "2028-02-29"}
    p = post(api, f"/api/properties/{home['id']}/contracts", renewed)
    old, new = p["contracts"]
    assert (old["ended_on"], old["open"], old["renewal"]) == ("2026-02-28", False, None)
    assert (new["open"], p["current_contract"]["id"]) == (True, new["id"])
    r = api.post(
        f"/api/properties/{home['id']}/contracts", json={**JEONSE, "start_on": "2025-01-01"}
    )
    assert r.status_code == 400 and "지금 계약 시작일보다 늦어야" in r.json()["error"]


def test_update_contract(api, home):
    p = post(api, f"/api/properties/{home['id']}/contracts", JEONSE)
    cid = p["current_contract"]["id"]
    r = api.patch(f"/api/property-contracts/{cid}", json={"amount": 310_000_000, "owner": "jaehee"})
    c = r.json()["current_contract"]
    assert (c["amount"], c["owner"]) == (310_000_000, "jaehee")
    r = api.patch(
        f"/api/property-contracts/{cid}", json={"contract_type": "월세", "monthly_rent": 300_000}
    )
    assert r.json()["current_contract"]["monthly_rent"] == 300_000
    r = api.patch(f"/api/property-contracts/{cid}", json={"end_on": "2020-01-01"})
    assert r.status_code == 400 and "만기일이 시작일보다 늦어야" in r.json()["error"]
    assert api.patch(f"/api/property-contracts/{cid}", json={}).status_code == 400


def test_end_and_reopen_contract(api, home):
    p = post(api, f"/api/properties/{home['id']}/contracts", JEONSE)
    cid = p["current_contract"]["id"]
    r = api.post(f"/api/property-contracts/{cid}/end", json={"ended_on": "2025-09-15"})
    assert (r.json()["current_contract"], r.json()["value"]) == (None, None)
    r = api.post(f"/api/property-contracts/{cid}/end", json={"ended_on": None})
    assert r.json()["current_contract"]["id"] == cid
    r = api.post(f"/api/property-contracts/{cid}/end", json={"ended_on": "2020-01-01"})
    assert r.status_code == 400 and "시작일보다 빨라요" in r.json()["error"]
    assert api.post(f"/api/property-contracts/{cid}/end", json={}).status_code == 422


# --- 이사 나감 ------------------------------------------------------------------


def test_move_out(api, home):
    post(api, f"/api/properties/{home['id']}/contracts", JEONSE)
    r = api.post(f"/api/properties/{home['id']}/move-out", json={"moved_out_on": "2025-09-30"})
    p = r.json()
    assert (p["active"], p["current_contract"], p["contracts"][0]["ended_on"]) == (
        False,
        None,
        "2025-09-30",
    )
    assert api.get("/api/properties").json()["items"] == []
    assert len(api.get("/api/properties?include_moved_out=true").json()["items"]) == 1
    r = api.post(f"/api/properties/{home['id']}/contracts", json=JEONSE)
    assert r.status_code == 400 and "이사 나간 집이에요" in r.json()["error"]
    r = api.post(f"/api/properties/{home['id']}/move-out", json={"moved_out_on": None})
    assert r.json()["active"] and r.json()["contracts"][0]["ended_on"] == "2025-09-30"


# --- 시세 ---------------------------------------------------------------------


def test_own_value_from_valuations(api, home):
    own = {"contract_type": "자가", "amount": 650_000_000, "start_on": "2025-05-20"}
    p = post(api, f"/api/properties/{home['id']}/contracts", own)
    assert (p["value"]["basis"], p["value"]["label"]) == ("purchase", "2025년 5월 매입가")
    assert p["current_contract"]["renewal"] is None
    r = api.put(
        f"/api/properties/{home['id']}/valuations/2025-09",
        json={"value": 680_000_000, "memo": "같은 단지 실거래"},
    )
    body = r.json()
    assert (body["value"]["value"], body["value"]["label"]) == (
        680_000_000,
        "2025년 9월 직접 입력 시세",
    )
    assert body["valuations"] == [
        {
            "year_month": "2025-09",
            "value": 680_000_000,
            "source": "manual",
            "source_label": "직접 입력",
            "memo": "같은 단지 실거래",
        }
    ]
    r = api.put(
        f"/api/properties/{home['id']}/valuations/2025-10",
        json={"value": 700_000_000, "source": "실거래가"},
    )
    assert r.json()["value"]["label"] == "2025년 10월 실거래가 시세"
    r = api.delete(f"/api/properties/{home['id']}/valuations/2025-10")
    assert r.json()["deleted"] and r.json()["property"]["value"]["value"] == 680_000_000
    assert not api.delete(f"/api/properties/{home['id']}/valuations/2025-10").json()["deleted"]


@pytest.mark.parametrize(
    ("ym", "body", "message"),
    [
        ("2025-9", {"value": 1}, "YYYY-MM"),
        ("2025-09", {"value": 0}, "1원 이상"),
        ("2025-09", {"value": 1, "source": "호가"}, "시세 출처를 알 수 없어요"),
    ],
)
def test_valuation_rejects(api, home, ym, body, message):
    r = api.put(f"/api/properties/{home['id']}/valuations/{ym}", json=body)
    assert r.status_code == 400 and message in r.json()["error"]


# --- 고정비 연결 ------------------------------------------------------------------


def test_fixed_items_on_property(api, home):
    fee = {
        "name": "관리비",
        "category": "주거",
        "amount": 250_000,
        "amount_mode": "변동",
        "pay_day": 25,
        "method": "직접 이체",
        "start_month": "2024-03",
        "property_id": home["id"],
    }
    item = post(api, "/api/fixed-items", fee)
    assert item["property"] == {"id": home["id"], "name": "우리집"}
    detail = api.get(f"/api/properties/{home['id']}").json()
    assert [i["name"] for i in detail["fixed_items"]] == ["관리비"]
    api.patch(f"/api/fixed-items/{item['id']}", json={"property_id": 0})
    assert api.get(f"/api/properties/{home['id']}").json()["fixed_items"] == []
