"""J3a: 고정비 표(0004)와 등록·수정 함수."""

import sqlite3

import pytest

from hanjigap.db.connection import connect
from hanjigap.db.fixed_items import (
    FixedItemError,
    add_fixed_item,
    end_fixed_item,
    get_fixed_item,
    list_fixed_items,
    update_fixed_item,
)
from hanjigap.db.holdings import add_account, add_card
from hanjigap.db.members import add_member
from hanjigap.db.migrate import load_migrations, migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "f.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def setup(conn):
    sb = add_member(conn, "sangbeom", "상범")
    jh = add_member(conn, "jaehee", "재희")
    main = add_account(conn, sb, institution="A은행", name="주거래 통장", kind="입출금")
    salary = add_account(conn, jh, institution="B은행", name="급여 통장", kind="입출금")
    life = add_card(conn, sb, issuer="가 카드", name="생활 신용카드", kind="신용")
    return {"sb": sb, "jh": jh, "main": main, "salary": salary, "life": life}


def item(conn, s, **over):
    values = dict(
        name="상범 용돈",
        category="용돈",
        amount=300000,
        pay_day=22,
        method="자동이체",
        pay_account_id=s["main"].id,
        start_month="2026-01",
    )
    values.update(over)
    member = values.pop("member", None)
    return add_fixed_item(conn, member, **values)


# --- 등록 ----------------------------------------------------------------------


def test_add_monthly_auto_transfer(conn, setup):
    it = item(conn, setup, match_text=" 용돈 ")
    assert it.member_id == setup["sb"].id  # 계좌 주인이 담당
    assert (it.category, it.method, it.cycle, it.amount_mode) == (
        "allowance",
        "auto_transfer",
        "monthly",
        "fixed",
    )
    assert (it.match_text, it.match_days, it.group) == ("용돈", 3, "living")
    assert it.schedule_label == "매월 22일" and it.method_label == "자동이체"


def test_add_card_auto(conn, setup):
    it = item(
        conn,
        setup,
        name="운전자보험",
        category="insurance",
        amount=60000,
        pay_day=10,
        method="카드 자동결제",
        pay_account_id=None,
        pay_card_id=setup["life"].id,
    )
    assert it.pay_card_id == setup["life"].id and it.pay_account_id is None


def test_add_manual_without_account(conn, setup):
    it = item(
        conn,
        setup,
        name="처가 부모님 용돈",
        category="부모님",
        method="직접 이체",
        pay_account_id=None,
        member=setup["jh"],
    )
    assert it.member_id == setup["jh"].id


def test_saving_group(conn, setup):
    assert item(conn, setup, name="적금", category="저축").group == "saving"
    assert item(conn, setup, name="전세대출 이자", category="대출").group == "saving"


def test_yearly(conn, setup):
    it = item(
        conn, setup, name="자동차보험", category="보험", cycle="매년", pay_month=3, pay_day=14
    )
    assert it.schedule_label == "매년 3월 14일"
    assert it.active_in("2026-03") and not it.active_in("2026-04")


def test_monthly_ignores_pay_month(conn, setup):
    assert item(conn, setup, pay_month=5).pay_month is None


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"category": "식비"}, "분류를 알 수 없어요"),
        ({"method": "계좌이체"}, "출금 방식을 알 수 없어요"),
        ({"amount": 0}, "1원 이상"),
        ({"amount": 1.5}, "정수"),
        ({"pay_day": 32}, "출금일"),
        ({"cycle": "yearly"}, "출금 월을 입력"),
        ({"cycle": "yearly", "pay_month": 13}, "출금 월"),
        ({"start_month": "2026/1"}, "YYYY-MM"),
        ({"end_month": "2025-12"}, "시작 월보다 빨라요"),
        ({"match_days": 11}, "날짜 범위"),
        ({"memo": "계좌 110-123-4567890"}, "전체 계좌·카드번호"),
        ({"name": "가" * 51}, "50자"),
        ({"method": "카드 자동결제"}, "카드 자동결제는 카드를"),
        ({"pay_card_id": 1}, "하나만"),
        ({"pay_account_id": None}, "자동이체는 출금 계좌"),
    ],
)
def test_add_rejects(conn, setup, over, message):
    with pytest.raises(FixedItemError, match=message):
        item(conn, setup, **over)


def test_card_requires_card_auto(conn, setup):
    with pytest.raises(FixedItemError, match="카드 자동결제예요"):
        item(conn, setup, method="직접 이체", pay_account_id=None, pay_card_id=setup["life"].id)


def test_owner_must_match_account(conn, setup):
    with pytest.raises(FixedItemError, match="주인이어야"):
        item(conn, setup, member=setup["jh"])  # 상범 계좌인데 재희 담당
    with pytest.raises(FixedItemError, match="담당자를 정해"):
        item(conn, setup, method="직접 이체", pay_account_id=None)


def test_unknown_account(conn, setup):
    with pytest.raises(FixedItemError, match="계좌를 찾을 수 없어요"):
        item(conn, setup, pay_account_id=999)


# --- DB 제약 (함수를 거치지 않아도 지켜지는지) ------------------------------------


def insert(conn, **over):
    values = dict(
        member_id=1,
        name="x",
        category="other",
        amount=1,
        pay_day=1,
        method="manual",
        start_month="2026-01",
    )
    values.update(over)
    cols = ", ".join(values)
    marks = ", ".join("?" * len(values))
    conn.execute(f"INSERT INTO fixed_items ({cols}) VALUES ({marks})", tuple(values.values()))


@pytest.mark.parametrize(
    "over",
    [
        {"pay_account_id": 2},  # 재희 계좌 + 상범 담당
        {"pay_card_id": 1, "method": "manual"},
        {"method": "card_auto"},
        {"method": "auto_transfer"},
        {"cycle": "yearly"},
        {"pay_month": 3},
        {"amount": 0},
        {"start_month": "2026-13"},
        {"end_month": "2025-12"},
        {"memo": "1234567890"},
        {"category": "food"},
    ],
)
def test_db_constraints(conn, setup, over):
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, **over)


def test_db_accepts_valid_row(conn, setup):
    insert(conn, pay_card_id=setup["life"].id, method="card_auto")
    insert(conn, pay_account_id=setup["main"].id, method="manual")


# --- 조회 · 수정 · 마감 -----------------------------------------------------------


def test_list_order_and_filters(conn, setup):
    item(conn, setup, name="b", pay_day=26)
    item(conn, setup, name="a", pay_day=5)
    item(conn, setup, name="재희 용돈", pay_account_id=setup["salary"].id, pay_day=26)
    assert [i.name for i in list_fixed_items(conn)] == ["a", "b", "재희 용돈"]
    assert [i.name for i in list_fixed_items(conn, setup["jh"])] == ["재희 용돈"]


def test_list_by_month(conn, setup):
    item(conn, setup, name="old", start_month="2025-01", end_month="2026-03")
    item(conn, setup, name="new", start_month="2026-06")
    item(conn, setup, name="car", cycle="yearly", pay_month=9, start_month="2024-01")
    assert [i.name for i in list_fixed_items(conn, year_month="2026-09")] == ["car", "new"]
    assert [i.name for i in list_fixed_items(conn, year_month="2026-02")] == ["old"]
    assert [i.name for i in list_fixed_items(conn)] == ["car", "new"]  # 마감 안 된 것만
    assert len(list_fixed_items(conn, include_ended=True)) == 3


def test_update(conn, setup):
    it = item(conn, setup, match_text="용돈", memo="메모")
    it = update_fixed_item(conn, it.id, amount=350000, match_text="", memo="")
    assert (it.amount, it.match_text, it.memo) == (350000, None, None)


def test_update_payment_moves_owner(conn, setup):
    it = item(conn, setup)
    it = update_fixed_item(conn, it.id, pay_account_id=setup["salary"].id)
    assert it.member_id == setup["jh"].id  # 재희 계좌로 바꾸면 담당도 재희
    it = update_fixed_item(conn, it.id, method="직접 이체", pay_account_id=None)
    assert it.member_id == setup["jh"].id  # 계좌를 빼도 담당 유지
    it = update_fixed_item(
        conn, it.id, method="카드 자동결제", pay_card_id=setup["life"].id, pay_account_id=None
    )
    assert it.member_id == setup["sb"].id


def test_update_rejects(conn, setup):
    it = item(conn, setup)
    with pytest.raises(FixedItemError, match="바꿀 내용이 없어요"):
        update_fixed_item(conn, it.id)
    with pytest.raises(FixedItemError, match="바꿀 수 없는 칸"):
        update_fixed_item(conn, it.id, created_at="x")
    with pytest.raises(FixedItemError, match="찾을 수 없어요"):
        update_fixed_item(conn, 999, amount=1)
    with pytest.raises(FixedItemError, match="출금일"):
        update_fixed_item(conn, it.id, pay_day=0)
    assert get_fixed_item(conn, it.id).pay_day == 22  # 실패하면 그대로


def test_end(conn, setup):
    it = item(conn, setup, start_month="2026-01")
    it = end_fixed_item(conn, it.id, "2026-10")
    assert it.end_month == "2026-10"
    assert list_fixed_items(conn) == []
    assert it.active_in("2026-10") and not it.active_in("2026-11")
    with pytest.raises(FixedItemError, match="시작 월보다 빨라요"):
        end_fixed_item(conn, it.id, "2025-12")


def test_closed_account_still_referenced(conn, setup):
    # 계좌는 지우지 않으므로 고정비가 가리키는 계좌는 늘 남아 있다
    item(conn, setup)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute("DELETE FROM accounts WHERE id = ?", (setup["main"].id,))


def test_upgrade_from_version_3(tmp_path):
    c = connect(tmp_path / "v3.db")
    *old, v4 = load_migrations()[:4]
    migrate(c, old)
    c.execute("INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')")
    assert [m.name for m in migrate(c)][0] == v4.name
    assert c.execute("SELECT count(*) FROM members").fetchone()[0] == 1
    c.close()
