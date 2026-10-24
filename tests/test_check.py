import dataclasses
import datetime as dt

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.importer.fingerprint import assign_keys
from hanjigap.settle.apply import apply_month
from hanjigap.settle.check import check_all, check_recent
from hanjigap.settle.pipeline import ParsedExport, parse_export

FILE_NAME = "2025-10-20~2026-10-20.xlsx"
PW = "test1234"


@pytest.fixture(scope="module")
def parsed(sample_bytes) -> ParsedExport:
    return parse_export(sample_bytes, None, FILE_NAME)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "c.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def me(conn):
    return add_member(conn, "sangbeom", "상범")


def month(parsed, ym):
    y, m = map(int, ym.split("-"))
    return [r for r in parsed.normalized.rows if (r.date.year, r.date.month) == (y, m)]


def variant(parsed, rows, sha="b" * 64) -> ParsedExport:
    return dataclasses.replace(parsed, keyed=assign_keys(rows), file_sha256=sha)


def settle_old_version(conn, me, parsed, ym, change):
    """예전 파일로 정산해 둔 상태를 만든다. change(rows) → 예전 파일의 rows"""
    old = variant(parsed, change(list(parsed.normalized.rows)), sha="0" * 64)
    assert apply_month(conn, me, old, ym).saved


def count_all(conn):
    return sum(
        conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("transactions", "import_batches", "month_settlements", "tx_overrides")
    )


# --- 점검 결과 ------------------------------------------------------------------


def test_nothing_to_check_on_empty_db(conn, me, parsed):
    assert check_recent(conn, me, parsed, "2026-09") == []
    assert check_all(conn, me, parsed) == []


def test_clean_month(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-08")
    (c,) = check_recent(conn, me, parsed, "2026-09")
    assert c.year_month == "2026-08" and c.status == "settled" and c.clean


def test_late_transactions_detected(conn, me, parsed):
    late = month(parsed, "2026-08")[:2]
    settle_old_version(conn, me, parsed, "2026-08", lambda rows: [r for r in rows if r not in late])
    (c,) = check_recent(conn, me, parsed, "2026-09")
    assert not c.clean
    assert sorted(k.row.row_no for k in c.added) == sorted(r.row_no for r in late)
    assert not c.removed and not c.shifted


def test_removed_and_changed_detected(conn, me, parsed):
    aug = month(parsed, "2026-08")
    apply_month(conn, me, parsed, "2026-08")
    gone, edited = aug[0], aug[1]
    newer = [
        dataclasses.replace(r, category="급여") if r is edited else r
        for r in parsed.normalized.rows
        if r is not gone
    ]
    (c,) = check_recent(conn, me, variant(parsed, newer), "2026-09")
    assert [s.content for s in c.removed] == [gone.content]
    assert [ch.new.row.content for ch in c.changed] == [edited.content]


def test_time_shift_paired(conn, me, parsed):
    target = month(parsed, "2026-08")[3]
    settle_old_version(
        conn,
        me,
        parsed,
        "2026-08",
        lambda rows: [
            dataclasses.replace(r, time=dt.time(0, 0)) if r is target else r for r in rows
        ],
    )
    (c,) = check_recent(conn, me, parsed, "2026-09")
    assert not c.added and not c.removed  # '사라짐 + 새로 생김'으로 보이지 않음
    (shift,) = c.shifted
    assert shift.old.tx_time == "00:00:00"
    assert shift.new.row.time == target.time and not shift.has_override


def test_time_shift_with_override_warns(conn, me, parsed):
    target = month(parsed, "2026-08")[3]
    settle_old_version(
        conn,
        me,
        parsed,
        "2026-08",
        lambda rows: [
            dataclasses.replace(r, time=dt.time(0, 0)) if r is target else r for r in rows
        ],
    )
    key = conn.execute(
        "SELECT tx_key FROM transactions "
        "WHERE tx_time = '00:00:00' AND content = ? AND tx_date = ?",
        (target.content, target.date.isoformat()),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, category) VALUES (?, ?, 'x')", (me.id, key)
    )
    (c,) = check_recent(conn, me, parsed, "2026-09")
    assert c.shifted[0].has_override
    assert any("연결이 끊깁니다" in w for w in c.warnings)


def test_different_content_not_paired(conn, me, parsed):
    target = month(parsed, "2026-08")[3]
    settle_old_version(
        conn,
        me,
        parsed,
        "2026-08",
        lambda rows: [
            dataclasses.replace(r, time=dt.time(0, 0), content="다른 거래") if r is target else r
            for r in rows
        ],
    )
    (c,) = check_recent(conn, me, parsed, "2026-09")
    assert len(c.added) == 1 and len(c.removed) == 1 and not c.shifted


def test_only_two_previous_months(conn, me, parsed):
    for ym in ("2026-05", "2026-06", "2026-07", "2026-08"):
        apply_month(conn, me, parsed, ym)
    assert [c.year_month for c in check_recent(conn, me, parsed, "2026-09")] == [
        "2026-08",
        "2026-07",
    ]
    assert [c.year_month for c in check_all(conn, me, parsed)] == [
        "2026-08",
        "2026-07",
        "2026-06",
        "2026-05",
    ]


def test_year_boundary(conn, me, parsed):
    apply_month(conn, me, parsed, "2025-12")
    apply_month(conn, me, parsed, "2025-11")
    assert [c.year_month for c in check_recent(conn, me, parsed, "2026-01")] == [
        "2025-12",
        "2025-11",
    ]


def test_months_not_in_db_or_partial_skipped(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-08")
    # 2025-10은 파일에 일부만 있음: DB에 있더라도 점검하지 않음
    conn.execute(
        "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count) "
        "VALUES (?, '2025-10', 'baseline', 1, 0)",
        (me.id,),
    )
    assert [c.year_month for c in check_all(conn, me, parsed)] == ["2026-08"]
    assert check_recent(conn, me, parsed, "2025-11") == []


def test_baseline_months_checked(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-08")
    conn.execute("UPDATE month_settlements SET status = 'baseline'")
    (c,) = check_recent(conn, me, parsed, "2026-09")
    assert c.status == "baseline"


def test_check_does_not_write(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-08")
    before, changes = count_all(conn), conn.total_changes
    check_all(conn, me, parsed)
    check_recent(conn, me, parsed, "2026-09")
    assert count_all(conn) == before and conn.total_changes == changes


# --- 명령어 ---------------------------------------------------------------------


@pytest.fixture
def cli_env(isolated_env, make_zip, sample_bytes, parsed, capsys, monkeypatch):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    path = isolated_env / "뱅샐.zip"
    path.write_bytes(make_zip([(FILE_NAME, sample_bytes)], PW))
    monkeypatch.setattr(cli, "ask_password", lambda: PW)
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: True)
    capsys.readouterr()
    return path


def _store_old_august(isolated_env, parsed):
    conn = connect(isolated_env / "data" / "hanjigap.db")
    me = conn.execute("SELECT id, slug, display_name, email FROM members").fetchone()
    from hanjigap.db.members import Member

    member = Member(*tuple(me))
    late = month(parsed, "2026-08")[:2]
    settle_old_version(
        conn, member, parsed, "2026-08", lambda rows: [r for r in rows if r not in late]
    )
    conn.close()


def test_cli_preview_shows_check(cli_env, isolated_env, parsed, capsys):
    _store_old_august(isolated_env, parsed)
    code = cli.main(
        ["settle", "preview", str(cli_env), "--member", "sangbeom", "--month", "2026-09"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "누락 점검   2026-08: 새로 생긴 거래 2건 → 재정산 권장" in out
    assert '재정산: settle apply "뱅샐.zip" --member sangbeom --month 2026-08' in out


def test_cli_preview_without_history(cli_env, capsys):
    cli.main(["settle", "preview", str(cli_env), "--member", "sangbeom", "--month", "2026-09"])
    assert "누락 점검   점검할 이전 정산 달 없음" in capsys.readouterr().out


def test_cli_apply_shows_check_before_confirm(cli_env, isolated_env, parsed, capsys, monkeypatch):
    _store_old_august(isolated_env, parsed)
    seen = []
    monkeypatch.setattr(
        cli, "ask_confirm", lambda prompt: seen.append(capsys.readouterr().out) or False
    )
    cli.main(["settle", "apply", str(cli_env), "--member", "sangbeom", "--month", "2026-09"])
    assert "2026-08: 새로 생긴 거래 2건" in seen[0]


def test_cli_check_command(cli_env, isolated_env, parsed, capsys):
    _store_old_august(isolated_env, parsed)
    cli.main(
        ["settle", "apply", str(cli_env), "--member", "sangbeom", "--month", "2026-09", "--yes"]
    )
    capsys.readouterr()
    assert cli.main(["settle", "check", str(cli_env), "--member", "sangbeom"]) == 0
    out = capsys.readouterr().out
    assert "누락 점검   2026-09: 변동 없음" in out
    assert "2026-08: 새로 생긴 거래 2건" in out
    assert "DB는 바뀌지 않았습니다" in out


def test_cli_check_empty(cli_env, capsys):
    assert cli.main(["settle", "check", str(cli_env), "--member", "sangbeom"]) == 0
    assert "정산 달이 없습니다" in capsys.readouterr().out
