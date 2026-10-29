import sqlite3

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.migrate import (
    Migration,
    MigrationError,
    applied_versions,
    current_version,
    load_migrations,
    migrate,
)

TABLES = {
    "members",
    "import_batches",
    "month_settlements",
    "transactions",
    "tx_overrides",
    "bs_cashflow",
    "accounts",
    "cards",
    "instrument_links",
    "fixed_items",
    "bs_snapshots",
    "bs_holdings",
    "vehicles",
    "vehicle_valuations",
    "vehicle_cost_rules",
    "vehicle_tx_tags",
    "properties",
    "property_contracts",
    "property_valuations",
}
KEY = "0123456789abcdef0123456789abcdef"
LATEST = len(load_migrations())


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


def _tables(c) -> set[str]:
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return {r[0] for r in rows} - {"schema_version"}


def _seed(c) -> tuple[int, int]:
    """구성원 1명과 업로드 1건을 만들고 (member_id, batch_id)를 돌려준다."""
    mid = c.execute(
        "INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')"
    ).lastrowid
    bid = c.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (?, ?, 'zip', 0, 'applied')",
        (mid, "a" * 64),
    ).lastrowid
    return mid, bid


def _tx(c, mid, bid, **over):
    values = dict(
        member_id=mid,
        tx_key=KEY,
        seq=1,
        base_key="2026-09-20|14:10:17|-100000|입출금통장",
        year_month="2026-09",
        tx_date="2026-09-20",
        tx_time="14:10:17",
        tx_type="이체",
        amount=-100000,
        batch_id=bid,
        row_no=2,
    )
    values.update(over)
    cols = ", ".join(values)
    marks = ", ".join("?" * len(values))
    c.execute(f"INSERT INTO transactions ({cols}) VALUES ({marks})", tuple(values.values()))


# --- 마이그레이션 ------------------------------------------------------------


def test_init_creates_tables(conn):
    assert _tables(conn) == TABLES
    assert current_version(conn) == LATEST
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_transactions_month", "idx_transactions_batch"} <= indexes


def test_migrate_twice_is_noop(conn):
    assert migrate(conn) == []
    assert current_version(conn) == LATEST


def test_settings_applied(conn, tmp_path):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_bundled_migrations_are_numbered():
    migs = load_migrations()
    assert [m.version for m in migs] == list(range(1, len(migs) + 1))
    assert migs[0].name == "init"


def test_failed_migration_rolls_back(tmp_path):
    c = connect(tmp_path / "f.db")
    good = load_migrations()
    bad = Migration(
        len(good) + 1, "broken", "CREATE TABLE extra (x INTEGER) STRICT;\nTHIS IS NOT SQL;"
    )
    migrate(c, good)
    with pytest.raises(MigrationError, match=f"{bad.version:04d}_broken.sql 적용 실패"):
        migrate(c, [*good, bad])
    assert current_version(c) == LATEST
    assert "extra" not in _tables(c)  # 앞부분도 취소됨
    assert not c.in_transaction


def test_changed_applied_migration_detected(tmp_path):
    c = connect(tmp_path / "x.db")
    migrate(c)
    original = load_migrations()[0]
    tampered = Migration(1, original.name, original.sql + "\n-- 수정")
    with pytest.raises(MigrationError, match="내용이 바뀌었습니다"):
        migrate(c, [tampered, *load_migrations()[1:]])


def test_line_endings_do_not_count_as_change(tmp_path):
    folder = tmp_path / "mig"
    folder.mkdir()
    (folder / "0001_init.sql").write_bytes(b"CREATE TABLE a (x INTEGER) STRICT;\n")
    c = connect(tmp_path / "le.db")
    migrate(c, load_migrations(folder))
    (folder / "0001_init.sql").write_bytes(b"CREATE TABLE a (x INTEGER) STRICT;\r\n")
    assert migrate(c, load_migrations(folder)) == []


def test_missing_migration_file_detected(tmp_path):
    c = connect(tmp_path / "m.db")
    migrate(c)
    with pytest.raises(MigrationError, match="파일이 없습니다"):
        migrate(c, [])


@pytest.mark.parametrize("names", [["0001_a.sql", "0003_b.sql"], ["0002_a.sql"]])
def test_gap_in_numbers_rejected(tmp_path, names):
    for n in names:
        (tmp_path / n).write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="빠짐없이"):
        load_migrations(tmp_path)


def test_bad_file_name_rejected(tmp_path):
    (tmp_path / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="이름 형식"):
        load_migrations(tmp_path)


def test_applied_versions_recorded(conn):
    assert list(applied_versions(conn)) == list(range(1, LATEST + 1))


# --- 제약 조건 ---------------------------------------------------------------


def test_valid_transaction_inserts(conn):
    mid, bid = _seed(conn)
    _tx(conn, mid, bid)
    assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1


def test_duplicate_member_key_rejected(conn):
    mid, bid = _seed(conn)
    _tx(conn, mid, bid)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _tx(conn, mid, bid, row_no=3)


def test_same_key_other_member_ok(conn):
    mid, bid = _seed(conn)
    other = conn.execute("INSERT INTO members (slug, display_name) VALUES ('jaehee', '재희')")
    _tx(conn, mid, bid)
    _tx(conn, other.lastrowid, bid)


def test_foreign_key_enforced(conn):
    mid, bid = _seed(conn)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _tx(conn, 999, bid)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _tx(conn, mid, 999)


@pytest.mark.parametrize(
    "over",
    [
        {"tx_type": "입금"},
        {"tx_date": "2026-02-30", "year_month": "2026-02"},
        {"tx_date": "2026/09/20"},
        {"tx_time": "25:00:00"},
        {"tx_time": "9:05:00"},
        {"year_month": "2026-08"},  # 날짜와 연월 불일치
        {"currency": "USD"},
        {"tx_key": "short"},
        {"tx_key": "Z" * 32},
        {"seq": 0},
        {"amount": "1,000"},  # STRICT: 문자 금액 거부
        {"amount": 1.5},
    ],
)
def test_invalid_transaction_rejected(conn, over):
    mid, bid = _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _tx(conn, mid, bid, **over)


@pytest.mark.parametrize("slug", ["A", "상범", "with space", "x"])
def test_member_slug_rules(conn, slug):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO members (slug, display_name) VALUES (?, '이름')", (slug,))


@pytest.mark.parametrize(
    ("year_month", "status"),
    [("2026-13", "settled"), ("2026-9", "settled"), ("2026-09", "done")],
)
def test_month_settlement_rules(conn, year_month, status):
    mid, bid = _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (mid, year_month, status, bid),
        )


def test_month_settlement_unique(conn):
    mid, bid = _seed(conn)
    sql = (
        "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count) "
        "VALUES (?, '2026-10', 'settled', ?, 0)"
    )
    conn.execute(sql, (mid, bid))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (mid, bid))


def test_batch_period_order_checked(conn):
    mid, _ = _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status, "
            "period_start, period_end) "
            "VALUES (?, ?, 'zip', 0, 'applied', '2026-10-20', '2025-10-20')",
            (mid, "b" * 64),
        )


def test_override_survives_without_transaction(conn):
    # 거래가 재정산으로 지워져도 웹 수정 내용은 지문으로 남아 있어야 한다
    mid, bid = _seed(conn)
    _tx(conn, mid, bid)
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, category) VALUES (?, ?, '저축')", (mid, KEY)
    )
    conn.execute("DELETE FROM transactions")
    row = conn.execute("SELECT category FROM tx_overrides WHERE tx_key = ?", (KEY,)).fetchone()
    assert row["category"] == "저축"


def test_cashflow_deleted_with_batch(conn):
    mid, bid = _seed(conn)
    conn.execute(
        "INSERT INTO bs_cashflow (batch_id, member_id, year_month, item, amount) "
        "VALUES (?, ?, '2026-09', '급여', 3500000)",
        (bid, mid),
    )
    conn.execute("DELETE FROM import_batches WHERE id = ?", (bid,))
    assert conn.execute("SELECT count(*) FROM bs_cashflow").fetchone()[0] == 0


# --- 명령어 ------------------------------------------------------------------


def test_cli_db_init_and_status(isolated_env, capsys):
    assert cli.main(["db", "status"]) == 1
    assert "db init" in capsys.readouterr().err

    assert cli.main(["db", "init"]) == 0
    out = capsys.readouterr().out
    assert "새로 만듦" in out and "0001_init.sql" in out and f"스키마 버전 {LATEST}" in out
    assert (isolated_env / "data" / "hanjigap.db").is_file()

    assert cli.main(["db", "init"]) == 0
    assert "이미 최신" in capsys.readouterr().out

    assert cli.main(["db", "status"]) == 0
    out = capsys.readouterr().out
    assert f"표          {len(TABLES)}개" in out
    for table in TABLES:
        assert f"{table}" in out
    assert out.count("0행") == len(TABLES)
