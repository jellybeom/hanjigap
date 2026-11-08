"""0011: 한지갑에서 뺄 뱅샐현황 항목 (각자 용돈 통장·마이너스 통장).

샘플 표(test_status_tables.layout)
    자산 주거래 통장 1,500,000 · 비상금 통장 2,000,000 · 위탁 계좌 3,000,000 · IRP 0
    부채 마이너스 통장 240,410 / 대출현황 가 은행 마이너스 통장 240,410 / 투자 2개
"""

import pytest

from hanjigap.db import holding_excludes as hx
from hanjigap.db.members import get_member
from hanjigap.db.snapshots import latest_snapshot
from test_api_uploads import fixed_today, upload  # noqa: F401 (날짜 고정 fixture)
from test_settle_status_only import tables_file

MINUS = {"section": "liability", "group": "장기대출", "institution": None, "name": "마이너스 통장"}
LOAN = {"section": "loan", "group": "은행 대출", "institution": "가 은행", "name": "마이너스 통장"}
MAIN = {"section": "asset", "group": "자유입출금 자산", "institution": None, "name": "주거래 통장"}


def as_member(api, slug):
    api.app.state.settings.dev_member = slug


@pytest.fixture
def settled(api, sample_bytes):
    """상범 9월을 재무현황 표가 있는 파일로 정산"""
    p = upload(api, tables_file(sample_bytes)).json()
    assert api.post(f"/api/uploads/{p['upload_id']}/apply").json()["saved"]
    return p


# --- 계산 ----------------------------------------------------------------------


def test_apply_excludes_recalculates_totals(api, api_conn, settled):
    snap = latest_snapshot(api_conn, get_member(api_conn, "sangbeom"))
    keys = {
        ("asset", "자유입출금 자산", "", "주거래 통장"),
        ("liability", "장기대출", "", "마이너스 통장"),
        ("loan", "은행 대출", "가 은행", "마이너스 통장"),
    }
    out = hx.apply_excludes(snap, keys)
    assert (out.total_assets, out.total_liabilities, out.net_worth) == (5_000_000, 0, 5_000_000)
    assert [h.name for h in out.holdings if h.section in ("asset", "liability", "loan")] == [
        "비상금 통장",
        "위탁 계좌",
        "IRP",
    ]
    assert hx.apply_excludes(snap, set()) is snap  # 뺄 게 없으면 그대로
    assert hx.apply_excludes(None, keys) is None


# --- API ------------------------------------------------------------------------


def test_candidates_and_pairs(api, settled):
    body = api.get("/api/holding-excludes").json()
    assert body["items"] == [] and body["snapshot"]["month"] == "2026-09"
    cands = body["candidates"]
    assert [c["section"] for c in cands][:2] == ["asset", "asset"]  # 자산 → 부채 → 투자 → 대출 순
    minus = next(c for c in cands if c["section"] == "liability")
    (pair,) = minus["pairs"]
    assert cands[pair]["section"] == "loan" and cands[pair]["amount"] == 240_410
    loan = cands[pair]
    assert [cands[i]["section"] for i in loan["pairs"]] == ["liability"]  # 거꾸로도 짝


def test_exclude_hides_everywhere(api, settled):
    before = api.get("/api/assets").json()
    r = api.post("/api/holding-excludes", json={"items": [MINUS, LOAN, MAIN]})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert [(i["name"], i["found"]) for i in body["items"]] == [
        ("마이너스 통장", True),
        ("마이너스 통장", True),
        ("주거래 통장", True),
    ]
    assert not any(c["name"] in ("마이너스 통장", "주거래 통장") for c in body["candidates"])

    after = api.get("/api/assets").json()
    assert after["net_worth"] == before["net_worth"] - 1_500_000 + 240_410
    text = str(after)
    assert "마이너스 통장" not in text and "주거래 통장" not in text  # 흐리게도 안 보냄
    assert after["breakdown"]["loans"] == {"balance": 0, "count": 0}
    home = api.get("/api/home").json()
    assert "마이너스 통장" not in str(home) and "주거래 통장" not in str(home)
    # 추이(지난 기록)에도 같은 규칙
    assert after["trend"][-1]["net_worth"] == after["net_worth"]


def test_include_again(api, settled):
    before = api.get("/api/assets").json()["net_worth"]
    rule_id = api.post("/api/holding-excludes", json={"items": [MAIN]}).json()["items"][0]["id"]
    assert api.get("/api/assets").json()["net_worth"] == before - 1_500_000
    body = api.delete(f"/api/holding-excludes/{rule_id}").json()
    assert body["items"] == [] and any(c["name"] == "주거래 통장" for c in body["candidates"])
    assert api.get("/api/assets").json()["net_worth"] == before


def test_only_my_own(api, settled):
    rule_id = api.post("/api/holding-excludes", json={"items": [MAIN]}).json()["items"][0]["id"]
    as_member(api, "jaehee")
    mine = api.get("/api/holding-excludes").json()
    assert mine == {"snapshot": None, "items": [], "candidates": []}  # 상범 것은 안 보임
    assert api.delete(f"/api/holding-excludes/{rule_id}").status_code == 404
    r = api.post("/api/holding-excludes", json={"items": [MINUS]})  # 내 기록에 없는 항목
    assert r.status_code == 400 and "내 최근 뱅샐현황에 없는" in r.json()["error"]
    # 재희가 봐도 상범의 뺀 항목은 자산 화면에 없다
    assert "주거래 통장" not in str(api.get("/api/assets", params={"owner": "sangbeom"}).json())


def test_validation(api, settled):
    bad = {**MAIN, "name": "없는 통장"}
    assert api.post("/api/holding-excludes", json={"items": [bad]}).status_code == 400
    assert api.post("/api/holding-excludes", json={"items": []}).status_code == 422
    # 같은 것을 두 번 빼도 하나
    api.post("/api/holding-excludes", json={"items": [MAIN]})
    body = api.post("/api/holding-excludes", json={"items": [MAIN]}).json()
    assert len(body["items"]) == 1


def test_next_upload_counts_auto_excluded(api, sample_bytes, settled):
    api.post("/api/holding-excludes", json={"items": [MINUS, LOAN]})
    p = upload(api, tables_file(sample_bytes)).json()
    assert p["auto_excluded"] == 2
    assert "마이너스 통장" not in str(p)  # 이름은 보내지 않음


def test_missing_rule_is_marked(api, api_conn, settled):
    me = get_member(api_conn, "sangbeom")
    hx.add_rule(api_conn, me, ("asset", "자유입출금 자산", "", "해지한 통장"))
    (item,) = api.get("/api/holding-excludes").json()["items"]
    assert (item["name"], item["found"], item["amount"]) == ("해지한 통장", False, None)
