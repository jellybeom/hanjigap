"""J6: 부동산 (집 · 계약 이력 · 시세 · 자산 가치 · 만기 알림 · 고정비 연결)."""

import datetime as dt
import sqlite3

import pytest

from hanjigap.db.connection import connect
from hanjigap.db.estates import (
    EstateError,
    add_contract,
    add_property,
    contracts,
    delete_valuation,
    end_contract,
    get_contract,
    get_property,
    list_properties,
    move_out,
    open_contract,
    property_fixed_items,
    renewal_info,
    set_valuation,
    update_contract,
    update_property,
    valuations,
    value_at,
)
from hanjigap.db.fixed_items import FixedItemError, add_fixed_item, update_fixed_item
from hanjigap.db.holdings import add_account
from hanjigap.db.members import add_member
from hanjigap.db.migrate import load_migrations, migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "e.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def people(conn):
    return add_member(conn, "sangbeom", "상범"), add_member(conn, "jaehee", "재희")


@pytest.fixture
def home(conn):
    return add_property(conn, name="우리집", address_memo="OO동 OO아파트")


@pytest.fixture
def jeonse(conn, people, home):
    return add_contract(
        conn,
        home.id,
        people[0],
        contract_type="전세",
        amount=300_000_000,
        start_on="2024-03-01",
        end_on="2026-02-28",
    )


# --- 집 -----------------------------------------------------------------------


def test_add_property_defaults(home):
    assert (home.kind, home.kind_label, home.active) == ("apartment", "아파트", True)
    assert home.address_memo == "OO동 OO아파트"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"name": " "}, "집 이름"),
        ({"name": "가" * 41}, "40자"),
        ({"name": "집", "kind": "성"}, "집 종류를 알 수 없어요"),
        ({"name": "집", "color": "red"}, "#rrggbb"),
        ({"name": "집", "memo": "계좌 110-123-456789"}, "끝 4자리"),
    ],
)
def test_add_property_rejects(conn, values, message):
    with pytest.raises(EstateError, match=message):
        add_property(conn, **values)


def test_property_name_unique(conn, home):
    with pytest.raises(EstateError, match="같은 이름"):
        add_property(conn, name="우리집")


def test_update_property(conn, home):
    p = update_property(conn, home.id, kind="빌라", memo="2층", color="#AA3300")
    assert (p.kind, p.memo, p.color) == ("villa", "2층", "#aa3300")
    p = update_property(conn, home.id, address_memo="", memo="")
    assert (p.address_memo, p.memo) == (None, None)
    with pytest.raises(EstateError, match="바꿀 수 없는 칸"):
        update_property(conn, home.id, moved_out_on="2026-01-01")
    with pytest.raises(EstateError, match="바꿀 내용이 없어요"):
        update_property(conn, home.id)


def test_get_unknown(conn):
    with pytest.raises(EstateError, match="집을 찾을 수 없어요"):
        get_property(conn, 1)
    with pytest.raises(EstateError, match="계약을 찾을 수 없어요"):
        get_contract(conn, 1)


# --- 계약 ---------------------------------------------------------------------


def test_add_jeonse(jeonse, people):
    assert (jeonse.contract_type, jeonse.type_label, jeonse.amount_label) == (
        "jeonse",
        "전세",
        "보증금",
    )
    assert jeonse.owner_member_id == people[0].id and jeonse.open
    assert jeonse.monthly_rent is None


def test_add_monthly_with_zero_deposit(conn, people, home):
    c = add_contract(
        conn,
        home.id,
        people[1],
        contract_type="월세",
        amount=0,
        monthly_rent=700_000,
        start_on="2024-03-01",
        end_on="2026-02-28",
    )
    assert (c.contract_type, c.amount, c.monthly_rent) == ("monthly", 0, 700_000)


def test_add_own_without_end(conn, people, home):
    c = add_contract(
        conn, home.id, people[0], contract_type="자가", amount=650_000_000, start_on="2027-05-20"
    )
    assert (c.end_on, c.amount_label) == (None, "매입가")


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"contract_type": "반전세"}, "계약 종류를 알 수 없어요"),
        ({"amount": 0}, "보증금을 입력하세요"),
        ({"amount": -1}, "보증금은 0원 이상"),
        ({"amount": "3억"}, "보증금은 0원 이상"),
        ({"monthly_rent": 500_000}, "월세 계약에만"),
        ({"end_on": None}, "전세 계약은 만기일"),
        ({"end_on": "2024-03-01"}, "만기일이 시작일보다 늦어야"),
        ({"start_on": "2024/03/01"}, "시작일 형식"),
        ({"contract_type": "월세", "monthly_rent": None}, "월세 금액을 입력"),
        ({"contract_type": "자가"}, "자가는 만기일이 없어요"),
        ({"contract_type": "자가", "end_on": None, "amount": 0}, "매입가를 입력하세요"),
        ({"memo": "1234567890"}, "끝 4자리"),
    ],
)
def test_add_contract_rejects(conn, people, home, values, message):
    base = {
        "contract_type": "전세",
        "amount": 300_000_000,
        "start_on": "2024-03-01",
        "end_on": "2026-02-28",
    }
    base.update(values)
    with pytest.raises(EstateError, match=message):
        add_contract(conn, home.id, people[0], **base)


def test_renewal_closes_previous(conn, people, home, jeonse):
    renewed = add_contract(
        conn,
        home.id,
        people[0],
        contract_type="전세",
        amount=320_000_000,
        start_on="2026-03-01",
        end_on="2028-02-29",
        memo="5% 증액 갱신",
    )
    history = contracts(conn, home.id)
    assert [c.id for c in history] == [jeonse.id, renewed.id]
    assert history[0].ended_on == "2026-02-28"  # 새 계약 전날
    assert open_contract(conn, home.id) == renewed


def test_new_contract_must_start_later(conn, people, home, jeonse):
    with pytest.raises(EstateError, match="지금 계약 시작일보다 늦어야"):
        add_contract(
            conn,
            home.id,
            people[0],
            contract_type="전세",
            amount=1,
            start_on="2024-03-01",
            end_on="2026-02-28",
        )
    assert open_contract(conn, home.id) == jeonse  # 이전 계약은 그대로


def test_one_open_contract_in_db(conn, people, home, jeonse):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO property_contracts (property_id, owner_member_id, contract_type, amount, "
            "start_on, end_on) VALUES (?, ?, 'jeonse', 1, '2026-03-01', '2028-02-29')",
            (home.id, people[0].id),
        )


@pytest.mark.parametrize(
    ("contract_type", "amount", "rent", "end_on"),
    [
        ("jeonse", 1, 100, "2026-02-28"),  # 전세에 월세
        ("monthly", 1, None, "2026-02-28"),  # 월세인데 월세 없음
        ("own", 1, None, "2026-02-28"),  # 자가에 만기
        ("jeonse", 1, None, None),  # 전세인데 만기 없음
        ("jeonse", 0, None, "2026-02-28"),  # 전세 보증금 0
        ("jeonse", 1, None, "2024-01-01"),  # 만기가 시작보다 빠름
    ],
)
def test_contract_checks_in_db(conn, people, home, contract_type, amount, rent, end_on):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO property_contracts (property_id, owner_member_id, contract_type, amount, "
            "monthly_rent, start_on, end_on) VALUES (?, ?, ?, ?, ?, '2024-03-01', ?)",
            (home.id, people[0].id, contract_type, amount, rent, end_on),
        )


def test_update_contract(conn, people, jeonse):
    c = update_contract(conn, jeonse.id, amount=310_000_000, owner=people[1], memo="정정")
    assert (c.amount, c.owner_member_id, c.memo) == (310_000_000, people[1].id, "정정")
    c = update_contract(conn, jeonse.id, contract_type="월세", monthly_rent=300_000)
    assert (c.contract_type, c.monthly_rent, c.end_on) == ("monthly", 300_000, "2026-02-28")
    c = update_contract(conn, jeonse.id, contract_type="전세")  # 월세 칸은 저절로 비움
    assert c.monthly_rent is None
    c = update_contract(conn, jeonse.id, contract_type="자가")  # 만기도 저절로 비움
    assert c.end_on is None
    with pytest.raises(EstateError, match="바꿀 수 없는 칸"):
        update_contract(conn, jeonse.id, ended_on="2026-01-01")


def test_update_start_after_ended_rejected(conn, people, home, jeonse):
    end_contract(conn, jeonse.id, "2025-12-31")
    with pytest.raises(EstateError, match="끝난 날보다 늦어요"):
        update_contract(conn, jeonse.id, start_on="2026-01-15", end_on="2027-01-14")


def test_end_and_reopen_contract(conn, people, home, jeonse):
    c = end_contract(conn, jeonse.id, "2026-02-28")
    assert not c.open and open_contract(conn, home.id) is None
    assert end_contract(conn, jeonse.id, None).open
    with pytest.raises(EstateError, match="시작일보다 빨라요"):
        end_contract(conn, jeonse.id, "2024-01-01")


def test_reopen_blocked_when_other_open(conn, people, home, jeonse):
    add_contract(
        conn,
        home.id,
        people[0],
        contract_type="전세",
        amount=1,
        start_on="2026-03-01",
        end_on="2028-02-29",
    )
    with pytest.raises(EstateError, match="이미 진행 중인 계약"):
        end_contract(conn, jeonse.id, None)


# --- 이사 나감 ------------------------------------------------------------------


def test_move_out_ends_contract(conn, people, home, jeonse):
    p = move_out(conn, home.id, "2026-02-28")
    assert not p.active and list_properties(conn) == []
    assert len(list_properties(conn, include_moved_out=True)) == 1
    assert get_contract(conn, jeonse.id).ended_on == "2026-02-28"
    with pytest.raises(EstateError, match="이사 나간 집이에요"):
        add_contract(
            conn,
            home.id,
            people[0],
            contract_type="자가",
            amount=1,
            start_on="2026-03-01",
        )
    with pytest.raises(EstateError, match="다시 진행 중으로 바꿀 수 없어요"):
        end_contract(conn, jeonse.id, None)
    p = move_out(conn, home.id, None)  # 표시만 취소, 계약은 끝난 채로
    assert p.active and get_contract(conn, jeonse.id).ended_on == "2026-02-28"


def test_move_out_before_contract_start(conn, home, jeonse):
    with pytest.raises(EstateError, match="계약 시작일보다 빨라요"):
        move_out(conn, home.id, "2024-01-01")
    assert get_property(conn, home.id).active


# --- 시세 · 자산 가치 ------------------------------------------------------------


def test_jeonse_value_is_deposit(conn, people, home, jeonse):
    v = value_at(conn, home, "2025-06")
    assert (v.value, v.basis, v.owner_member_id, v.label) == (
        300_000_000,
        "deposit",
        people[0].id,
        "전세 보증금",
    )
    assert value_at(conn, home, "2024-02") is None  # 계약 전


def test_value_follows_renewal(conn, people, home, jeonse):
    add_contract(
        conn,
        home.id,
        people[0],
        contract_type="전세",
        amount=320_000_000,
        start_on="2026-03-01",
        end_on="2028-02-29",
    )
    assert value_at(conn, home, "2026-02").value == 300_000_000
    assert value_at(conn, home, "2026-03").value == 320_000_000


def test_mid_month_renewal_uses_month_end(conn, people, home):
    add_contract(
        conn,
        home.id,
        people[0],
        contract_type="전세",
        amount=1,
        start_on="2024-03-15",
        end_on="2026-03-14",
    )
    add_contract(
        conn,
        home.id,
        people[0],
        contract_type="전세",
        amount=2,
        start_on="2026-03-15",
        end_on="2028-03-14",
    )
    assert value_at(conn, home, "2026-03").value == 2


def test_no_value_after_move_out(conn, home, jeonse):
    move_out(conn, home.id, "2025-12-20")
    assert value_at(conn, home, "2025-11").value == 300_000_000
    assert value_at(conn, home, "2025-12") is None  # 그 달 말에는 이미 나감


def test_own_value_uses_latest_valuation(conn, people, home):
    set_valuation(conn, home.id, "2026-01", 999)  # 사기 전 시세는 쓰지 않음
    add_contract(
        conn, home.id, people[0], contract_type="자가", amount=650_000_000, start_on="2027-05-20"
    )
    v = value_at(conn, home, "2027-06")
    assert (v.value, v.basis, v.label) == (650_000_000, "purchase", "2027년 5월 매입가")
    set_valuation(conn, home.id, "2027-09", 680_000_000, memo="같은 단지 실거래")
    set_valuation(conn, home.id, "2028-03", 700_000_000, source="실거래가")
    assert value_at(conn, home, "2027-08").value == 650_000_000
    v = value_at(conn, home, "2027-12")
    assert (v.value, v.label) == (680_000_000, "2027년 9월 직접 입력 시세")
    v = value_at(conn, home, "2028-05")
    assert (v.value, v.basis, v.label) == (700_000_000, "lookup", "2028년 3월 실거래가 시세")


def test_valuation_upsert_and_delete(conn, home):
    set_valuation(conn, home.id, "2026-09", 100)
    set_valuation(conn, home.id, "2026-09", 200, memo="고침")
    assert [(v.year_month, v.value, v.memo, v.source_label) for v in valuations(conn, home.id)] == [
        ("2026-09", 200, "고침", "직접 입력")
    ]
    assert delete_valuation(conn, home.id, "2026-09")
    assert not delete_valuation(conn, home.id, "2026-09")


@pytest.mark.parametrize(
    ("ym", "value", "source", "message"),
    [
        ("2026-9", 1, "manual", "YYYY-MM"),
        ("", 1, "manual", "시세 월을 입력"),
        ("2026-09", 0, "manual", "1원 이상"),
        ("2026-09", 1, "호가", "시세 출처를 알 수 없어요"),
    ],
)
def test_valuation_rejects(conn, home, ym, value, source, message):
    with pytest.raises(EstateError, match=message):
        set_valuation(conn, home.id, ym, value, source=source)


# --- 만기 알림 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "stage", "days_left"),
    [
        ("2025-08-27", "before", 185),
        ("2025-08-28", "notice", 184),  # 만기 6개월 전부터
        ("2025-12-28", "notice", 62),  # 2개월 전까지
        ("2025-12-29", "closing", 61),
        ("2026-02-28", "closing", 0),
        ("2026-03-01", "overdue", -1),
    ],
)
def test_renewal_stages(jeonse, today, stage, days_left):
    info = renewal_info(jeonse, dt.date.fromisoformat(today))
    assert (info.notice_from, info.notice_until) == ("2025-08-28", "2025-12-28")
    assert (info.stage, info.days_left) == (stage, days_left)


def test_renewal_month_end_clamp(conn, people, home):
    c = add_contract(
        conn,
        home.id,
        people[0],
        contract_type="전세",
        amount=1,
        start_on="2024-08-31",
        end_on="2026-08-31",
    )
    info = renewal_info(c, dt.date(2026, 1, 1))
    assert (info.notice_from, info.notice_until, info.stage_label) == (
        "2026-02-28",
        "2026-06-30",
        "갱신 의사 통지 기간 전",
    )


def test_no_renewal_for_own_or_ended(conn, people, home, jeonse):
    ended = end_contract(conn, jeonse.id, "2026-02-28")
    assert renewal_info(ended, dt.date(2026, 1, 1)) is None
    own = add_contract(
        conn, home.id, people[0], contract_type="자가", amount=1, start_on="2026-03-01"
    )
    assert renewal_info(own, dt.date(2026, 4, 1)) is None


# --- 고정비 연결 ------------------------------------------------------------------


def test_fixed_items_linked(conn, people, home):
    acc = add_account(conn, people[0], institution="A은행", name="주거래 통장", kind="입출금")
    fee = add_fixed_item(
        conn,
        None,
        name="관리비",
        category="주거",
        amount=250_000,
        amount_mode="변동",
        pay_day=25,
        method="자동이체",
        pay_account_id=acc.id,
        start_month="2024-03",
        property_id=home.id,
    )
    add_fixed_item(
        conn,
        people[1],
        name="용돈",
        category="용돈",
        amount=1,
        pay_day=1,
        method="직접 이체",
        start_month="2024-03",
    )
    assert [i.name for i in property_fixed_items(conn, home)] == ["관리비"]
    update_fixed_item(conn, fee.id, property_id=0)  # 떼기
    assert property_fixed_items(conn, home) == []
    with pytest.raises(FixedItemError, match="집을 찾을 수 없어요"):
        update_fixed_item(conn, fee.id, property_id=999)


def test_upgrade_from_version_7(tmp_path):
    c = connect(tmp_path / "v7.db")
    migrate(c, load_migrations()[:7])
    c.execute("INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')")
    c.execute(
        "INSERT INTO fixed_items (member_id, name, category, amount, pay_day, method, start_month) "
        "VALUES (1, '관리비', 'housing', 1, 25, 'manual', '2026-01')"
    )
    assert [m.name for m in migrate(c)][0] == "estates"
    assert c.execute("SELECT property_id FROM fixed_items").fetchone()[0] is None
    c.close()
