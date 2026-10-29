"""웹에서 거래 고치기 (tx_edits)."""

import pytest

from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.db.tx_edits import clear_edit, edit_tx, get_edit
from hanjigap.db.validate import InputError

KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "e.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def sb(conn):
    m = add_member(conn, "sangbeom", "상범")
    conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (1, ?, 'zip', 0, 'applied')",
        ("a" * 64,),
    )
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, content, amount, batch_id, row_no) "
        "VALUES (1, ?, 1, 'b', '2026-09', '2026-09-10', '10:00:00', '지출', '식비', '식당', -1, "
        "1, 1)",
        (KEY,),
    )
    return m


def test_edit_and_revert_each_field(conn, sb):
    e = edit_tx(conn, sb, KEY, edited_by=sb, category="카페", note="  친구랑  ")
    assert (e.category, e.note, e.content, e.edited_by) == ("카페", "친구랑", None, sb.id)
    e = edit_tx(conn, sb, KEY, category="")
    assert (e.category, e.note) == (None, "친구랑")
    assert edit_tx(conn, sb, KEY, note="") is None  # 모두 되돌아가면 기록도 지움
    assert get_edit(conn, sb, KEY) is None


def test_keeps_row_with_older_fields(conn, sb):
    # 예전 방식으로 타입·뱅샐 메모를 고친 기록이 있으면 지우지 않는다
    conn.execute("INSERT INTO tx_overrides (member_id, tx_key, memo) VALUES (1, ?, '메모')", (KEY,))
    edit_tx(conn, sb, KEY, note="x")
    assert edit_tx(conn, sb, KEY, note="") is not None
    row = conn.execute("SELECT memo FROM tx_overrides").fetchone()
    assert row[0] == "메모"


def test_clear_edit(conn, sb):
    edit_tx(conn, sb, KEY, content="동네 식당")
    assert clear_edit(conn, sb, KEY)
    assert not clear_edit(conn, sb, KEY)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({}, "바꿀 내용이 없어요"),
        ({"tx_type": "수입"}, "고칠 수 없는 칸"),
        ({"memo": "x"}, "고칠 수 없는 칸"),
        ({"content": "가" * 101}, "100자 이하"),
    ],
)
def test_edit_rejects(conn, sb, changes, message):
    with pytest.raises(InputError, match=message):
        edit_tx(conn, sb, KEY, **changes)


def test_unknown_tx(conn, sb):
    with pytest.raises(InputError, match="거래를 찾을 수 없어요"):
        edit_tx(conn, sb, "f" * 32, note="x")
