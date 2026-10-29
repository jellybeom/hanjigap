"""G7a: 거래 하나가 차마다 유지비로 들어가는지 (/api/vehicles/tx-status)."""

import datetime as dt

import pytest

import hanjigap.web.vehicles_api as vehicles_mod
from test_api_status import add_tx, post

CAR = {"name": "패밀리 SUV", "purchased_on": "2023-03", "purchase_price": 32_000_000}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(vehicles_mod, "today", lambda _r: dt.date(2026, 10, 29))


def status(api, key, owner="sangbeom"):
    r = api.get(f"/api/vehicles/tx-status?owner={owner}&tx_key={key}")
    assert r.status_code == 200, r.json()
    return r.json()["items"]


def test_counted_by_default_rule(api, api_conn):
    car = post(api, "/api/vehicles", CAR)
    key = add_tx(api_conn, 1, -60_000, "OO주유소", "지출", "자동차", "주유")
    (s,) = status(api, key)
    assert s["vehicle"] == {"id": car["id"], "name": "패밀리 SUV", "owner": "sangbeom"}
    assert (s["counted"], s["cost_type"], s["cost_label"], s["tag"]) == (True, "fuel", "주유", None)
    assert "자동차" in s["reason"]


def test_include_and_exclude_tags(api, api_conn):
    car = post(api, "/api/vehicles", CAR)
    wash = add_tx(api_conn, 1, -8_000, "셀프세차장", "지출", "생활")
    (s,) = status(api, wash)
    assert (s["counted"], s["cost_type"]) == (False, None)
    api.put(f"/api/vehicles/{car['id']}/tags", json={"tx_key": wash, "include": True,
                                                     "cost_type": "세차"})  # fmt: skip
    (s,) = status(api, wash)
    assert (s["counted"], s["cost_type"], s["reason"], s["tag"]) == (
        True,
        "wash",
        "직접 붙임",
        "include",
    )
    fuel = add_tx(api_conn, 1, -60_000, "OO주유소", "지출", "자동차", "주유")
    api.put(f"/api/vehicles/{car['id']}/tags", json={"tx_key": fuel, "include": False})
    (s,) = status(api, fuel)
    assert (s["counted"], s["tag"]) == (False, "exclude")
    api.delete(f"/api/vehicles/{car['id']}/tags/sangbeom/{fuel}")
    assert status(api, fuel)[0]["counted"] is True


def test_only_cars_owned_that_month(api, api_conn):
    old = post(api, "/api/vehicles", {**CAR, "name": "옛 차"})
    api.post(f"/api/vehicles/{old['id']}/sold", json={"sold_on": "2026-05-10"})
    post(api, "/api/vehicles", {**CAR, "name": "새 차", "purchased_on": "2026-11"})
    key = add_tx(api_conn, 2, -60_000, "OO주유소", "지출", "자동차", "주유")  # 2026-09, 재희
    assert status(api, key, "jaehee") == []


def test_rejects(api, api_conn):
    assert api.get("/api/vehicles/tx-status?owner=sangbeom&tx_key=" + "0" * 32).status_code == 404
    assert api.get("/api/vehicles/tx-status?owner=nobody&tx_key=x").status_code == 400
    assert api.get("/api/vehicles/tx-status").status_code == 422
