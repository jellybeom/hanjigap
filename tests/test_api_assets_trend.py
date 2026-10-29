"""G5a: 자산 화면용 /api/assets (사람별 보기 · 기록이 있는 달 · 12개월 추이 · 묶음)."""

import datetime as dt

import pytest

import hanjigap.web.ledger_api as ledger_mod
import hanjigap.web.status_api as status_mod

TODAY = dt.date(2026, 10, 29)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    for mod in (ledger_mod, status_mod):
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
