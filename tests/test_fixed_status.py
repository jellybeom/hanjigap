"""J3b: 그달 고정비 상태 (거래와 맞춰 보기)."""

import datetime as dt
import hashlib
import itertools

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.fixed_items import add_fixed_item
from hanjigap.db.holdings import add_account, add_card, link_instrument
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.fixed.status import month_status

AFTER = dt.date(2026, 10, 28)  # 9월이 다 지난 날
_counter = itertools.count(1)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "s.db")
    migrate(c)
    yield c
    c.close()


def make_db(conn):
    sb = add_member(conn, "sangbeom", "상범")
    jh = add_member(conn, "jaehee", "재희")
    batch = conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (?, ?, 'zip', 0, 'applied')",
        (sb.id, "a" * 64),
    ).lastrowid
    for m in (sb, jh):
        for ym in ("2026-08", "2026-09"):
            conn.execute(
                "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count) "
                "VALUES (?, ?, 'settled', ?, 0)",
                (m.id, ym, batch),
            )
    main = add_account(conn, sb, institution="A은행", name="주거래 통장", kind="입출금")
    life = add_card(conn, sb, issuer="가 카드", name="생활 신용카드", kind="신용")
    salary = add_account(conn, jh, institution="B은행", name="급여 통장", kind="입출금")
    return {"sb": sb, "jh": jh, "batch": batch, "main": main, "life": life, "salary": salary}


@pytest.fixture
def db(conn):
    return make_db(conn)


def tx(conn, db, member, date, amount, content="", instrument="입출금통장", tx_type="이체"):
    n = next(_counter)
    key = hashlib.sha256(str(n).encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, "
        "tx_time, tx_type, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '09:00:00', ?, ?, ?, ?, ?, ?)",
        (
            member.id,
            key,
            f"b{n}",
            date[:7],
            date,
            tx_type,
            content,
            amount,
            instrument,
            db["batch"],
            n,
        ),
    )
    return key


def fixed(conn, db, **over):
    values = dict(
        name="상범 용돈",
        category="용돈",
        amount=300000,
        pay_day=22,
        method="자동이체",
        pay_account_id=db["main"].id,
        start_month="2026-01",
    )
    values.update(over)
    member = values.pop("member", None)
    return add_fixed_item(conn, member, **values)


def status_of(conn, name, ym="2026-09", today=AFTER, member=None):
    report = month_status(conn, ym, member=member, today=today)
    return next(s for s in report.statuses if s.item.name == name)


# --- 기본 ----------------------------------------------------------------------


def test_paid_on_the_day(conn, db):
    fixed(conn, db)
    tx(conn, db, db["sb"], "2026-09-22", -300000, "용돈 통장")
    s = status_of(conn, "상범 용돈")
    assert (s.status, s.label, s.expected_date, s.tx.date) == (
        "paid",
        "빠져나감",
        "2026-09-22",
        "2026-09-22",
    )


@pytest.mark.parametrize(("date", "expected"), [("2026-09-25", "paid"), ("2026-09-26", "missed")])
def test_date_window(conn, db, date, expected):
    fixed(conn, db)  # ±3일
    tx(conn, db, db["sb"], date, -300000)
    assert status_of(conn, "상범 용돈").status == expected


def test_month_without_31st(conn, db):
    fixed(conn, db, pay_day=31)
    tx(conn, db, db["sb"], "2026-09-30", -300000)
    s = status_of(conn, "상범 용돈")
    assert (s.expected_date, s.status) == ("2026-09-30", "paid")


def test_window_reaches_previous_month(conn, db):
    fixed(conn, db, pay_day=1)
    tx(conn, db, db["sb"], "2026-08-30", -300000)  # 주말이라 앞당겨진 자동이체
    assert status_of(conn, "상범 용돈").status == "paid"


def test_income_not_matched(conn, db):
    fixed(conn, db)
    tx(conn, db, db["sb"], "2026-09-22", 300000, tx_type="수입")
    assert status_of(conn, "상범 용돈").status == "missed"


def test_other_member_not_matched(conn, db):
    fixed(conn, db)
    tx(conn, db, db["jh"], "2026-09-22", -300000)
    assert status_of(conn, "상범 용돈").status == "missed"


# --- 금액 · 글자 -------------------------------------------------------------------


def test_amount_must_match_without_text(conn, db):
    fixed(conn, db)
    tx(conn, db, db["sb"], "2026-09-22", -310000)
    assert status_of(conn, "상범 용돈").status == "missed"


def test_text_match_with_different_amount(conn, db):
    fixed(
        conn,
        db,
        name="아파트 관리비",
        category="주거",
        amount=271000,
        pay_day=26,
        match_text="관리비",
    )
    tx(conn, db, db["sb"], "2026-09-26", -286000, "OO아파트관리비")
    s = status_of(conn, "아파트 관리비")
    assert (s.status, s.amount_gap) == ("different", 15000)


@pytest.mark.parametrize(("paid", "expected"), [(352000, "paid"), (353000, "different")])
def test_variable_tolerance(conn, db, paid, expected):
    fixed(
        conn,
        db,
        name="관리비",
        category="주거",
        amount=271000,
        amount_mode="변동",
        pay_day=26,
        match_text="관리비",
    )
    tx(conn, db, db["sb"], "2026-09-26", -paid, "관리비")
    assert status_of(conn, "관리비").status == expected


def test_variable_without_text_uses_tolerance(conn, db):
    fixed(conn, db, name="관리비", category="주거", amount=271000, amount_mode="변동", pay_day=26)
    tx(conn, db, db["sb"], "2026-09-26", -286000)
    tx(conn, db, db["sb"], "2026-09-26", -600000)  # 범위 밖이라 후보 아님
    s = status_of(conn, "관리비")
    assert (s.status, s.tx.paid) == ("paid", 286000)


def test_text_uses_web_override(conn, db):
    fixed(conn, db, match_text="용돈")
    key = tx(conn, db, db["sb"], "2026-09-22", -300000, "이체 홍길동")
    assert status_of(conn, "상범 용돈").status == "missed"
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, content) VALUES (?, ?, '용돈 이체')",
        (db["sb"].id, key),
    )
    assert status_of(conn, "상범 용돈").status == "paid"


# --- 출금 수단 -------------------------------------------------------------------


def test_instrument_filter_when_linked(conn, db):
    fixed(conn, db, name="운전자보험", category="보험", amount=60000, pay_day=10,
          method="카드 자동결제", pay_account_id=None, pay_card_id=db["life"].id)  # fmt: skip
    tx(conn, db, db["sb"], "2026-09-10", -60000, instrument="다른 카드", tx_type="지출")
    # 연결 전에는 결제수단을 따지지 않는다 → 다른 카드 거래도 잡힘
    assert status_of(conn, "운전자보험").status == "paid"
    tx_key = tx(conn, db, db["sb"], "2026-09-11", -60000, instrument="카드 A", tx_type="지출")
    link_instrument(conn, db["sb"], "카드 A", card_id=db["life"].id)
    s = status_of(conn, "운전자보험")
    assert (s.status, s.tx.tx_key, s.tx.instrument) == ("paid", tx_key, "카드 A")


def test_linked_but_no_matching_instrument(conn, db):
    fixed(conn, db)
    link_instrument(conn, db["sb"], "입출금통장", account_id=db["main"].id, allow_unknown=True)
    tx(conn, db, db["sb"], "2026-09-22", -300000, instrument="카드 A", tx_type="지출")
    assert status_of(conn, "상범 용돈").status == "missed"


# --- 짝짓기 ---------------------------------------------------------------------


def test_one_tx_one_item(conn, db):
    fixed(conn, db, name="본가 용돈", category="부모님", pay_day=22)
    fixed(conn, db, name="상범 용돈", pay_day=22)
    tx(conn, db, db["sb"], "2026-09-22", -300000)
    report = month_status(conn, "2026-09", today=AFTER)
    assert sorted(s.status for s in report.statuses) == ["missed", "paid"]


def test_closest_date_wins(conn, db):
    fixed(conn, db, name="본가 용돈", category="부모님", pay_day=20)
    fixed(conn, db, name="상범 용돈", pay_day=22)
    tx(conn, db, db["sb"], "2026-09-20", -300000)
    tx(conn, db, db["sb"], "2026-09-22", -300000)
    report = {s.item.name: s for s in month_status(conn, "2026-09", today=AFTER).statuses}
    assert report["본가 용돈"].tx.date == "2026-09-20"
    assert report["상범 용돈"].tx.date == "2026-09-22"


# --- 정산 전 · 예정 ---------------------------------------------------------------


def test_unsettled_month_is_pending(conn, db):
    fixed(conn, db, pay_day=5)
    s = status_of(conn, "상범 용돈", ym="2026-10", today=AFTER)
    assert (s.status, s.label) == ("pending", "정산 전")


def test_future_is_upcoming(conn, db):
    fixed(conn, db, pay_day=30)
    assert status_of(conn, "상범 용돈", ym="2026-10", today=AFTER).status == "upcoming"


def test_window_into_unsettled_month_is_pending(conn, db):
    fixed(conn, db, pay_day=30)  # 9/27 ~ 10/3, 10월은 아직 정산 전
    assert status_of(conn, "상범 용돈").status == "pending"


def test_yearly_only_in_its_month(conn, db):
    fixed(conn, db, name="자동차보험", category="보험", cycle="매년", pay_month=3, pay_day=14)
    assert month_status(conn, "2026-09", today=AFTER).statuses == []


def test_member_filter_and_totals(conn, db):
    fixed(conn, db)
    fixed(conn, db, name="적금", category="저축", amount=500000, pay_day=1)
    fixed(conn, db, name="재희 용돈", pay_account_id=db["salary"].id, pay_day=26)
    report = month_status(conn, "2026-09", today=AFTER)
    assert (report.total("living"), report.total("saving"), report.total()) == (
        600000,
        500000,
        1100000,
    )
    only = month_status(conn, "2026-09", member=db["jh"], today=AFTER)
    assert [s.item.name for s in only.statuses] == ["재희 용돈"]


# --- 명령어 ---------------------------------------------------------------------


@pytest.fixture
def cli_db(isolated_env, capsys):
    cli.main(["db", "init"])
    c = connect(isolated_env / "data" / "hanjigap.db")
    d = make_db(c)
    fixed(c, d)
    fixed(
        c, d, name="아파트 관리비", category="주거", amount=271000, pay_day=26, match_text="관리비"
    )
    fixed(c, d, name="재희 휴대폰", category="통신·구독", amount=43000, pay_day=21,
          method="직접 이체", pay_account_id=None, member=d["jh"])  # fmt: skip
    tx(c, d, d["sb"], "2026-09-22", -300000)
    tx(c, d, d["sb"], "2026-09-26", -286000, "관리비")
    c.close()
    capsys.readouterr()
    return isolated_env


def test_cli_check(cli_db, capsys):
    assert cli.main(["fixed", "check", "--month", "2026-09"]) == 0
    out = capsys.readouterr().out
    assert "2026-09 고정비 · 3건 · 빠져나감 1 · 금액 다름 1 · 안 빠져나감 1" in out
    assert "✓ 09-22 300,000원" in out
    assert "금액 다름 · 09-26 286,000원 (기준보다 +15,000원)" in out


def test_cli_check_member(cli_db, capsys):
    assert cli.main(["fixed", "check", "--month", "2026-09", "--member", "jaehee"]) == 0
    out = capsys.readouterr().out
    assert "2026-09 고정비 · 재희 · 1건 · 안 빠져나감 1" in out


def test_cli_check_empty_month(cli_db, capsys):
    assert cli.main(["fixed", "check", "--month", "2025-01"]) == 0
    assert "해당하는 고정비가 없습니다" in capsys.readouterr().out


def test_cli_logs_counts_only(cli_db, capsys):
    cli.main(["fixed", "check", "--month", "2026-09"])
    logs = "".join(p.read_text(encoding="utf-8") for p in (cli_db / "logs").glob("*.log*"))
    assert "fixed check 2026-09: 3건" in logs
    assert "관리비" not in logs
