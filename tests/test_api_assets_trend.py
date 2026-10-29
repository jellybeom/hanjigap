"""G5a: 자산 화면용 /api/assets (사람별 보기 · 기록이 있는 달 · 12개월 추이 · 묶음)."""

import datetime as dt

import pytest

import hanjigap.web.ledger_api as ledger_mod
import hanjigap.web.status_api as status_mod
import hanjigap.web.vehicles_api as vehicles_mod

TODAY = dt.date(2026, 10, 29)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for mod in (ledger_mod, status_mod, vehicles_mod):
        monkeypatch.setattr(mod, "today", lambda _r: TODAY)


def snapshot(conn, member_id, ym, net, holdings=()):
    # test_api_status와 같은 모양의 가짜 재무현황
    from test_api_status import snapshot as make

    make(conn, member_id, ym, net, holdings)


@pytest.fixture
def data(api_conn):
    snapshot(api_conn, 1, "2026-08", 1_000_000)
    snapshot(api_conn, 1, "2026-09", 1_200_000, [
        ("asset", "자유입출금 자산", "예시 통장", 700_000),
        ("investment", "주식", "예시 주식", 500_000),
        ("loan", "신용대출", "예시 대출", 300_000),
    ])  # fmt: skip
    snapshot(api_conn, 2, "2026-08", 500_000)


def test_all_members(api, data):
    a = api.get("/api/assets").json()
    assert (a["owner"], a["month"], a["months"]) == (None, None, ["2026-08", "2026-09"])
    assert a["net_worth"] == 1_200_000 + 500_000  # 사람마다 가장 최근 기록
    assert [m["banksalad"]["year_month"] for m in a["members"]] == ["2026-09", "2026-08"]
    assert a["trend_end"] == "2026-09" and len(a["trend"]) == 12
    assert a["trend"][-1] == {"month": "2026-09", "net_worth": 1_200_000}  # 재희 9월 기록 없음
    assert a["trend"][-2] == {"month": "2026-08", "net_worth": 1_500_000}
    assert a["trend"][0] == {"month": "2025-10", "net_worth": None}
    assert a["previous_net_worth"] == 1_500_000
    b = a["breakdown"]
    assert (b["has_banksalad"], b["investment"]["value"], b["loans"]["balance"]) == (
        True,
        500_000,
        300_000,
    )


def test_one_member(api, data):
    a = api.get("/api/assets?owner=jaehee").json()
    assert (a["owner"], a["months"], a["net_worth"]) == ("jaehee", ["2026-08"], 500_000)
    assert [m["owner"] for m in a["members"]] == ["jaehee"]
    assert a["trend_end"] == "2026-08" and a["trend"][-1]["net_worth"] == 500_000


def test_month(api, data):
    a = api.get("/api/assets?month=2026-08").json()
    assert (a["month"], a["trend_end"], a["net_worth"]) == ("2026-08", "2026-08", 1_500_000)
    assert a["previous_net_worth"] is None


def test_no_records(api):
    a = api.get("/api/assets").json()
    assert (a["months"], a["net_worth"], a["trend_end"]) == ([], 0, "2026-09")  # 지난달
    assert all(t["net_worth"] is None for t in a["trend"])
    assert a["breakdown"]["has_banksalad"] is False


@pytest.mark.parametrize("query", ["owner=nobody", "month=2026-13"])
def test_rejects(api, query):
    assert api.get(f"/api/assets?{query}").status_code == 400


# --- 차·집 값은 그 사람의 뱅샐 기록과 같은 달로 (한 시점) ------------------------------------


CAR = {"name": "패밀리 SUV", "purchased_on": "2023-03", "purchase_price": 32_000_000}


def test_latest_view_values_car_at_snapshot_month(api, data):
    car = api.post("/api/vehicles", json={**CAR, "owner": "sangbeom"}).json()
    api.post(f"/api/vehicles/{car['id']}/sold", json={"sold_on": "2026-10-15"})
    a = api.get("/api/assets").json()
    sb = a["members"][0]
    # 상범의 마지막 기록은 9월 → 차도 9월 말 값 (9월 말엔 아직 가지고 있었음)
    assert sb["value_month"] == "2026-09" and len(sb["vehicles"]) == 1
    assert (
        sb["vehicles"][0]["value"]
        == api.get(f"/api/vehicles/{car['id']}/value-series").json()["items"][-2]["value"]
    )
    # 10월을 콕 집어 보면 판 달이라 빠진다
    assert api.get("/api/assets?month=2026-10").json()["members"][0]["vehicles"] == []


def test_car_bought_after_last_record(api, data):
    car = api.post(
        "/api/vehicles", json={**CAR, "owner": "jaehee", "purchased_on": "2026-10"}
    ).json()
    a = api.get("/api/assets").json()
    jh = a["members"][1]
    # 재희의 마지막 기록은 8월 → 10월에 산 차는 아직 안 들어간다
    assert jh["value_month"] == "2026-08" and jh["vehicles"] == []
    october = api.get("/api/assets?owner=jaehee&month=2026-10").json()
    assert october["members"][0]["vehicles"][0]["id"] == car["id"]


def test_no_records_at_all_uses_this_month(api):
    api.post("/api/vehicles", json={**CAR, "owner": "sangbeom"})
    a = api.get("/api/assets").json()
    assert a["value_month"] == "2026-10"
    assert [m["value_month"] for m in a["members"]] == ["2026-10", "2026-10"]
    assert len(a["members"][0]["vehicles"]) == 1
