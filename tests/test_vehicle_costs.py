"""J5b: 자동차 유지비 (규칙 · 거래별 붙이기·빼기 · 기간별 합계)."""

import hashlib
import itertools
import sqlite3

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member, get_member
from hanjigap.db.migrate import load_migrations, migrate
from hanjigap.db.vehicle_costs import (
    CostError,
    add_rule,
    create_default_rules,
    delete_rule,
    list_rules,
    set_rule_active,
    tag_tx,
    untag_tx,
    vehicle_costs,
)
from hanjigap.db.vehicles import add_vehicle, mark_sold
from hanjigap.settle.first_load import first_load
from hanjigap.settle.pipeline import parse_export

_n = itertools.count(1)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "c.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def env(conn):
    sb = add_member(conn, "sangbeom", "상범")
    jh = add_member(conn, "jaehee", "재희")
    batch = conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (?, ?, 'zip', 0, 'applied')",
        (sb.id, "a" * 64),
    ).lastrowid
    car = add_vehicle(
        conn, jh, name="패밀리 SUV", purchased_on="2023-03", purchase_price=32_000_000
    )
    create_default_rules(conn, car)
    return {"sb": sb, "jh": jh, "batch": batch, "car": car}


def tx(conn, env, member, date, amount, category="", sub="", content="", tx_type="지출",instrument="카드 A"):  # fmt: skip
    n = next(_n)
    key = hashlib.sha256(f"k{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, ?, ?, ?, ?, ?, ?, ?)",
        (member.id, key, f"b{n}", date[:7], date, tx_type, category, sub, content, amount,
         instrument, env["batch"], n),
    )  # fmt: skip
    return key


def report(conn, env, start="2026-09", end="2026-09"):
    return vehicle_costs(conn, env["car"], start, end)


# --- 기본 규칙 -------------------------------------------------------------------


def test_default_rules_created_once(conn, env):
    rules = list_rules(conn, env["car"])
    assert len(rules) == 9
    assert [r.label for r in rules][:2] == ["분류 자동차 › 주유", "분류 자동차 › 통행료"]
    assert rules[-1].label == "글자 「자동차세」"
    assert create_default_rules(conn, env["car"]) == []


def test_default_rules_pick_car_costs(conn, env):
    sb, jh = env["sb"], env["jh"]
    tx(conn, env, sb, "2026-09-02", -70_000, "자동차", "주유", "주유소")
    tx(conn, env, jh, "2026-09-21", -60_000, "자동차", "주유", "주유소")  # 두 사람 거래 모두
    tx(conn, env, sb, "2026-09-28", -12_300, "자동차", "통행료", "고속도로")
    tx(conn, env, sb, "2026-09-12", -40_000, "금융", "세금/과태료", "속도위반 과태료")
    tx(conn, env, sb, "2026-09-15", -240_000, "자동차", "할부/리스", "자동차 할부")  # 유지비 아님
    tx(conn, env, sb, "2026-09-16", -50_000, "식비", "한식", "식당")
    r = report(conn, env)
    assert r.total == 182_300
    assert r.by_type() == {"fuel": 130_000, "fine": 40_000, "toll": 12_300}
    reasons = {i.content: i.reason for i in r.items}
    assert reasons["속도위반 과태료"] == "규칙: 글자 「과태료」"
    assert reasons["주유소"] == "규칙: 분류 자동차 › 주유"


def test_refund_reduces_cost(conn, env):
    tx(conn, env, env["sb"], "2026-09-02", -70_000, "자동차", "주유")
    tx(conn, env, env["sb"], "2026-09-03", 10_000, "자동차", "주유")  # 취소·환불
    assert report(conn, env).total == 60_000


def test_transfers_and_income_ignored(conn, env):
    tx(conn, env, env["sb"], "2026-09-02", -70_000, "자동차", "주유", tx_type="이체")
    assert report(conn, env).items == []


def test_web_override_used(conn, env):
    key = tx(conn, env, env["sb"], "2026-09-02", -25_000, "생활", "생활서비스", "손세차")
    assert report(conn, env).items == []
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, category, subcategory) "
        "VALUES (?, ?, '자동차', '세차')",
        (env["sb"].id, key),
    )
    assert report(conn, env).by_type() == {"wash": 25_000}


# --- 규칙 관리 -------------------------------------------------------------------


def test_add_text_and_instrument_rules(conn, env):
    sb = env["sb"]
    tx(conn, env, sb, "2026-09-05", -20_000, "생활", "생활서비스", "OO카센터 엔진오일")
    tx(conn, env, sb, "2026-09-06", -9_000, "생활", "편의점", "충전", instrument="하이패스카드")
    add_rule(conn, env["car"], "글자", "정비", pattern="카센터")
    add_rule(conn, env["car"], "결제수단", "통행료", pattern="하이패스카드", member=sb)
    assert report(conn, env).by_type() == {"repair": 20_000, "toll": 9_000}


def test_instrument_rule_needs_member(conn, env):
    with pytest.raises(CostError, match="누구의 결제수단"):
        add_rule(conn, env["car"], "instrument", "toll", pattern="하이패스카드")


def test_instrument_rule_only_for_that_member(conn, env):
    add_rule(conn, env["car"], "instrument", "toll", pattern="하이패스카드", member=env["sb"])
    tx(conn, env, env["jh"], "2026-09-06", -9_000, "생활", "편의점", instrument="하이패스카드")
    assert report(conn, env).items == []


def test_category_rule_whole_category(conn, env):
    add_rule(conn, env["car"], "category", "other", category="자동차")  # 소분류 없음 = 전체
    tx(conn, env, env["sb"], "2026-09-15", -5_000, "자동차", "새 소분류")
    assert report(conn, env).by_type() == {"other": 5_000}


def test_rule_order_category_first(conn, env):
    add_rule(conn, env["car"], "text", "other", pattern="주유")
    tx(conn, env, env["sb"], "2026-09-02", -70_000, "자동차", "주유", "주유소")
    assert report(conn, env).items[0].cost_type == "fuel"  # 분류 규칙이 글자 규칙보다 먼저


def test_inactive_and_deleted_rules(conn, env):
    fuel = list_rules(conn, env["car"])[0]
    tx(conn, env, env["sb"], "2026-09-02", -70_000, "자동차", "주유")
    set_rule_active(conn, fuel.id, False)
    assert report(conn, env).items == []
    set_rule_active(conn, fuel.id, True)
    assert report(conn, env).total == 70_000
    delete_rule(conn, fuel.id)
    assert report(conn, env).items == []
    with pytest.raises(CostError, match="찾을 수 없어요"):
        delete_rule(conn, fuel.id)


@pytest.mark.parametrize(
    ("args", "kwargs", "message"),
    [
        (("색깔", "fuel"), {"pattern": "x"}, "규칙 종류를 알 수 없어요"),
        (("text", "간식"), {"pattern": "x"}, "비용 종류를 알 수 없어요"),
        (("text", "fuel"), {"pattern": " "}, "찾을 글자"),
        (("category", "fuel"), {}, "분류"),
    ],
)
def test_rule_rejects(conn, env, args, kwargs, message):
    with pytest.raises(CostError, match=message):
        add_rule(conn, env["car"], *args, **kwargs)


# --- 거래별 붙이기·빼기 ------------------------------------------------------------


def test_exclude_and_include(conn, env):
    sb = env["sb"]
    fuel = tx(conn, env, sb, "2026-09-02", -70_000, "자동차", "주유", "렌터카 주유")
    other = tx(conn, env, sb, "2026-09-03", -30_000, "생활", "생활서비스", "블랙박스 설치")
    tag_tx(conn, env["car"], sb, fuel, include=False)  # 다른 차에 넣은 기름
    tag_tx(conn, env["car"], sb, other, include=True, cost_type="정비")
    r = report(conn, env)
    assert [(i.content, i.cost_type, i.reason, i.manual) for i in r.items] == [
        ("블랙박스 설치", "repair", "직접 붙임", True)
    ]
    assert untag_tx(conn, env["car"], sb, fuel)
    assert report(conn, env).total == 100_000


def test_include_without_type_is_other(conn, env):
    key = tx(conn, env, env["sb"], "2026-09-03", -30_000, "생활", "생활서비스")
    tag_tx(conn, env["car"], env["sb"], key, include=True)
    assert report(conn, env).items[0].cost_type == "other"


def test_include_works_for_transfers(conn, env):
    key = tx(conn, env, env["sb"], "2026-09-03", -250_000, "금융", "", "자동차세", tx_type="이체")
    tag_tx(conn, env["car"], env["sb"], key, include=True, cost_type="세금")
    assert report(conn, env).by_type() == {"tax": 250_000}


def test_tag_unknown_tx(conn, env):
    with pytest.raises(CostError, match="거래를 찾을 수 없어요"):
        tag_tx(conn, env["car"], env["sb"], "0" * 32, include=True)


def test_tag_constraint_in_db(conn, env):
    key = tx(conn, env, env["sb"], "2026-09-03", -1, "x")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO vehicle_tx_tags (vehicle_id, member_id, tx_key, action, cost_type) "
            "VALUES (?, ?, ?, 'exclude', 'fuel')",
            (env["car"].id, env["sb"].id, key),
        )


# --- 기간 ----------------------------------------------------------------------


def test_period_totals(conn, env):
    for ym, amount in (("2026-07", 100), ("2026-08", 200), ("2026-09", 300)):
        tx(conn, env, env["sb"], f"{ym}-10", -amount, "자동차", "주유")
    r = report(conn, env, "2026-07", "2026-09")
    assert (r.total, r.months, r.monthly_average) == (600, 3, 200)
    assert r.by_month() == {"2026-07": 100, "2026-08": 200, "2026-09": 300}


def test_period_limited_to_ownership(conn, env):
    tx(conn, env, env["sb"], "2023-02-10", -100, "자동차", "주유")  # 사기 전
    tx(conn, env, env["sb"], "2026-11-10", -100, "자동차", "주유")  # 판 뒤
    tx(conn, env, env["sb"], "2026-10-10", -100, "자동차", "주유")
    sold = mark_sold(conn, env["car"].id, "2026-10-20")
    r = vehicle_costs(conn, sold, "2023-01", "2026-12")
    assert (r.start_month, r.end_month, r.total) == ("2023-03", "2026-10", 100)


def test_bad_period(conn, env):
    with pytest.raises(CostError, match="기간"):
        report(conn, env, "2026-09", "2026-07")


def test_upgrade_from_version_6(tmp_path):
    c = connect(tmp_path / "v6.db")
    migrate(c, load_migrations()[:6])
    assert [m.name for m in migrate(c)][0] == "vehicle_costs"
    c.close()


# --- 명령어 ---------------------------------------------------------------------


def test_cli_preview_costs(isolated_env, sample_bytes, capsys):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    c = connect(isolated_env / "data" / "hanjigap.db")
    parsed = parse_export(sample_bytes, None, "2025-10-20~2026-10-20.xlsx")
    first_load(c, get_member(c, "sangbeom"), parsed, "2026-09")
    c.close()
    capsys.readouterr()
    assert cli.main(["vehicle", "preview-costs", "--from", "2026-07", "--to", "2026-09"]) == 0
    out = capsys.readouterr().out
    assert "자동차 비용 미리보기 (기본 규칙, 모든 구성원) 2026-07 ~ 2026-09 · 거래 45건" in out
    assert "주유" in out and "월별" in out
    assert cli.main(["vehicle", "preview-costs", "--from", "2026-09", "--to", "2026-07"]) == 1
