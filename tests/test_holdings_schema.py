"""0003 마이그레이션: 계좌 · 카드 · 뱅샐 결제수단 연결 표의 제약 조건."""

import sqlite3

import pytest

from hanjigap.db.connection import connect
from hanjigap.db.migrate import current_version, load_migrations, migrate

TEN_DIGITS = "1234567890"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "h.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def members(conn):
    sb = conn.execute("INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')")
    jh = conn.execute("INSERT INTO members (slug, display_name) VALUES ('jaehee', '재희')")
    return sb.lastrowid, jh.lastrowid


def add_account(conn, member_id, **over):
    values = dict(member_id=member_id, institution="A은행", name="주거래 통장", kind="checking")
    values.update(over)
    cols = ", ".join(values)
    marks = ", ".join("?" * len(values))
    return conn.execute(
        f"INSERT INTO accounts ({cols}) VALUES ({marks})", tuple(values.values())
    ).lastrowid


def add_card(conn, member_id, **over):
    values = dict(member_id=member_id, issuer="가 카드", name="생활 신용카드", kind="credit")
    values.update(over)
    cols = ", ".join(values)
    marks = ", ".join("?" * len(values))
    return conn.execute(
        f"INSERT INTO cards ({cols}) VALUES ({marks})", tuple(values.values())
    ).lastrowid


def link(conn, member_id, instrument, target, account_id=None, card_id=None):
    conn.execute(
        "INSERT INTO instrument_links (member_id, instrument, target, account_id, card_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (member_id, instrument, target, account_id, card_id),
    )


# --- 계좌 ----------------------------------------------------------------------


def test_account_full_row(conn, members):
    sb, _ = members
    aid = add_account(
        conn,
        sb,
        number_last4="1234",
        opened_on="2015-03",
        color="#143d30",
        memo="생활비 · 카드대금 출금",
        number_hint="A은행 앱 > 계좌 조회",
    )
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone()
    assert row["closed_on"] is None and row["created_at"].endswith("Z")


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "입출금"},  # 영어 코드만
        {"number_last4": "123"},
        {"number_last4": "12345"},
        {"number_last4": "12a4"},
        {"number_last4": 937},  # 숫자로 넣으면 앞자리 0이 사라져 3자리가 된다 → 거부
        {"opened_on": "2015/03"},
        {"opened_on": "2015-13"},
        {"opened_on": "2015-02-30"},
        {"closed_on": "2026-10"},  # 해지일은 날짜만
        {"opened_on": "2020-05-01", "closed_on": "2019-01-01"},
        {"color": "#ABCDEF"},  # 소문자만
        {"color": "red"},
        {"memo": f"번호 {TEN_DIGITS}"},
        {"number_hint": f"110{TEN_DIGITS}"},
        {"institution": ""},
        {"name": ""},
    ],
)
def test_account_rejects(conn, members, over):
    with pytest.raises(sqlite3.IntegrityError):
        add_account(conn, members[0], **over)


@pytest.mark.parametrize(
    "over",
    [
        {"opened_on": "2015-03-02"},
        {"opened_on": "2015-03", "closed_on": "2015-03-31"},
        {"number_hint": "123-456-789"},  # 9자리까지는 DB가 막지 않는다 (나머지는 저장 함수가 검사)
    ],
)
def test_account_accepts(conn, members, over):
    add_account(conn, members[0], **over)


def test_account_name_unique_per_member(conn, members):
    sb, jh = members
    add_account(conn, sb)
    add_account(conn, jh)  # 다른 사람은 같은 이름 가능
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        add_account(conn, sb, institution="B은행")


def test_account_needs_member(conn):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        add_account(conn, 999)


# --- 카드 ----------------------------------------------------------------------


def test_card_full_row(conn, members):
    sb, jh = members
    acc = add_account(conn, jh, name="급여 통장")
    add_card(
        conn,
        sb,
        number_last4="4821",
        pay_day=14,
        period_start_day=1,
        period_month_offset=-1,
        pay_account_id=acc,  # 다른 사람 계좌로 결제되는 카드도 허용
        annual_fee=15000,
        color="#3f7f61",
    )


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "신용"},
        {"number_last4": "48210"},
        {"pay_day": 0},
        {"pay_day": 32},
        {"period_start_day": 1},  # 둘은 함께 있어야 한다
        {"period_month_offset": -1},
        {"period_start_day": 1, "period_month_offset": -3},
        {"period_start_day": 1, "period_month_offset": 1},
        {"annual_fee": -1},
        {"pay_account_id": 999},
        {"number_hint": f"카드 {TEN_DIGITS}"},
        {"color": "#12345"},
    ],
)
def test_card_rejects(conn, members, over):
    with pytest.raises(sqlite3.IntegrityError):
        add_card(conn, members[0], **over)


def test_no_column_for_full_card_number(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cards)")}
    assert not cols & {"number", "card_number", "cvc", "cvv", "expiry", "expires_on"}
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    assert not cols & {"number", "account_number"}


# --- 결제수단 연결 ---------------------------------------------------------------


def test_links_to_own_account_and_card(conn, members):
    sb, _ = members
    acc = add_account(conn, sb)
    card = add_card(conn, sb)
    link(conn, sb, "주거래 통장", "account", account_id=acc)
    link(conn, sb, "생활 신용카드", "card", card_id=card)
    link(conn, sb, "카카오페이", "easy_pay")
    assert conn.execute("SELECT count(*) FROM instrument_links").fetchone()[0] == 3


def test_cannot_link_other_members_account(conn, members):
    sb, jh = members
    acc = add_account(conn, jh, name="급여 통장")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        link(conn, sb, "급여 통장", "account", account_id=acc)


def test_cannot_link_other_members_card(conn, members):
    sb, jh = members
    card = add_card(conn, jh, name="체크카드", kind="check")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        link(conn, sb, "체크카드", "card", card_id=card)


@pytest.mark.parametrize(
    ("target", "use_account", "use_card"),
    [
        ("account", False, False),  # 계좌 연결인데 계좌가 없음
        ("account", True, True),  # 둘 다 채움
        ("card", True, False),
        ("easy_pay", True, False),  # 간편결제는 둘 다 비어야 함
        ("none", False, False),
    ],
)
def test_target_must_match_columns(conn, members, target, use_account, use_card):
    sb, _ = members
    acc = add_account(conn, sb)
    card = add_card(conn, sb)
    with pytest.raises(sqlite3.IntegrityError):
        link(conn, sb, "이름", target, acc if use_account else None, card if use_card else None)


def test_one_link_per_name(conn, members):
    sb, jh = members
    link(conn, sb, "네이버페이", "easy_pay")
    link(conn, jh, "네이버페이", "easy_pay")  # 사람마다 따로
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        link(conn, sb, "네이버페이", "easy_pay")


def test_linked_account_cannot_be_deleted(conn, members):
    sb, _ = members
    acc = add_account(conn, sb)
    link(conn, sb, "주거래 통장", "account", account_id=acc)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute("DELETE FROM accounts WHERE id = ?", (acc,))


def test_empty_instrument_rejected(conn, members):
    with pytest.raises(sqlite3.IntegrityError):
        link(conn, members[0], "", "easy_pay")


# --- 업그레이드 -------------------------------------------------------------------


def test_upgrade_keeps_existing_data(tmp_path):
    c = connect(tmp_path / "old.db")
    v1, v2, *rest = load_migrations()
    migrate(c, [v1, v2])
    c.execute("INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')")
    applied = migrate(c)
    assert [m.name for m in applied] == [m.name for m in rest]
    assert current_version(c) == len(load_migrations())
    assert c.execute("SELECT display_name FROM members").fetchone()[0] == "상범"
    add_account(c, 1)
    c.close()
