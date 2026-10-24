import dataclasses

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.importer.fingerprint import assign_keys
from hanjigap.settle import apply as apply_mod
from hanjigap.settle.apply import apply_month
from hanjigap.settle.pipeline import ParsedExport, parse_export
from hanjigap.settle.preview import PreviewError, precheck_month

FILE_NAME = "2025-10-20~2026-10-20.xlsx"
YM = "2026-09"
PW = "test1234"


@pytest.fixture(scope="module")
def parsed(sample_bytes) -> ParsedExport:
    return parse_export(sample_bytes, None, FILE_NAME)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "a.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def members(conn):
    return add_member(conn, "sangbeom", "상범"), add_member(conn, "jaehee", "재희")


def edited(parsed: ParsedExport, change) -> ParsedExport:
    """거래 일부를 바꾼 파일을 흉내 낸다. change(rows) → rows"""
    rows = change(list(parsed.normalized.rows))
    return dataclasses.replace(parsed, keyed=assign_keys(rows), file_sha256="b" * 64)


def sept(parsed):
    return [r for r in parsed.normalized.rows if (r.date.year, r.date.month) == (2026, 9)]


def count(conn, table, where="1=1", params=()):
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", params).fetchone()[0]


def snapshot(conn):
    """DB 전체 내용 (롤백 확인용)."""
    out = {}
    for t in ("import_batches", "transactions", "month_settlements", "tx_overrides"):
        out[t] = [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
    return out


# --- 저장 ----------------------------------------------------------------------


def test_apply_to_empty_db(conn, members, parsed):
    result = apply_month(conn, members[0], parsed, YM)
    assert result.saved and result.batch_id == 1
    assert (result.added, result.changed, result.removed) == (180, 0, 0)
    assert count(conn, "transactions") == 180
    state = conn.execute("SELECT * FROM month_settlements").fetchone()
    assert (state["year_month"], state["status"], state["tx_count"]) == (YM, "settled", 180)
    batch = conn.execute("SELECT * FROM import_batches").fetchone()
    assert batch["file_sha256"] == parsed.file_sha256
    assert (batch["period_start"], batch["period_end"], batch["period_source"]) == (
        "2025-10-20",
        "2026-10-20",
        "filename",
    )
    assert batch["row_count"] == 1579 and batch["status"] == "applied"


def test_stored_values_match_file(conn, members, parsed):
    apply_month(conn, members[0], parsed, YM)
    stored = {
        r["tx_key"]: r
        for r in conn.execute("SELECT * FROM transactions WHERE year_month = ?", (YM,))
    }
    for k in parsed.keyed:
        if (k.row.date.year, k.row.date.month) != (2026, 9):
            continue
        r = stored[k.tx_key]
        assert (r["tx_date"], r["tx_time"]) == (k.row.date.isoformat(), k.row.time.isoformat())
        assert (r["amount"], r["content"], r["instrument"]) == (
            k.row.amount,
            k.row.content,
            k.row.instrument,
        )
    assert sum(r["amount"] for r in stored.values()) == sum(r.amount for r in sept(parsed))


def test_same_file_twice_is_noop(conn, members, parsed):
    apply_month(conn, members[0], parsed, YM)
    before = snapshot(conn)
    second = apply_month(conn, members[0], parsed, YM)
    assert not second.saved and second.batch_id is None
    assert second.preview.unchanged == 180
    assert snapshot(conn) == before


def test_resettle_updates_deletes_and_keeps_ids(conn, members, parsed):
    apply_month(conn, members[0], parsed, YM)
    ids_before = dict(conn.execute("SELECT tx_key, id FROM transactions"))
    rows = sept(parsed)
    target, gone = rows[0], rows[1]

    def change(all_rows):
        out = [r for r in all_rows if r is not gone]
        return [dataclasses.replace(r, category="급여") if r is target else r for r in out]

    result = apply_month(conn, members[0], edited(parsed, change), YM)
    assert result.saved and (result.added, result.changed, result.removed) == (0, 1, 1)
    assert count(conn, "transactions") == 179
    ids_after = dict(conn.execute("SELECT tx_key, id FROM transactions"))
    assert all(ids_after[k] == ids_before[k] for k in ids_after)  # 남은 거래 id 그대로
    assert (
        conn.execute(
            "SELECT category FROM transactions WHERE content = ? AND tx_date = ?",
            (target.content, target.date.isoformat()),
        ).fetchone()[0]
        == "급여"
    )
    assert count(conn, "import_batches") == 2
    state = conn.execute("SELECT batch_id, tx_count FROM month_settlements").fetchone()
    assert tuple(state) == (2, 179)


def test_overrides_survive_resettle(conn, members, parsed):
    apply_month(conn, members[0], parsed, YM)
    key = conn.execute("SELECT tx_key FROM transactions LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, category) VALUES (?, ?, '저축')",
        (members[0].id, key),
    )
    changed = edited(parsed, lambda rows: [dataclasses.replace(r, memo="m") for r in rows])
    assert apply_month(conn, members[0], changed, YM).changed == 180
    assert count(conn, "tx_overrides") == 1


def test_baseline_converted_even_without_changes(conn, members, parsed):
    apply_month(conn, members[0], parsed, YM)
    conn.execute("UPDATE month_settlements SET status = 'baseline'")
    result = apply_month(conn, members[0], parsed, YM)
    assert result.saved and result.preview.kind == "baseline_to_settled"
    assert conn.execute("SELECT status FROM month_settlements").fetchone()[0] == "settled"


def test_other_months_and_members_untouched(conn, members, parsed):
    sangbeom, jaehee = members
    apply_month(conn, sangbeom, parsed, "2026-08")
    apply_month(conn, jaehee, parsed, YM)
    aug, j = count(conn, "transactions", "year_month = '2026-08'"), count(
        conn, "transactions", "member_id = ?", (jaehee.id,)
    )
    # 상범 9월을 빈 달로 재정산해도 8월과 재희 데이터는 그대로
    apply_month(conn, sangbeom, parsed, YM)
    apply_month(
        conn, sangbeom, edited(parsed, lambda rows: [r for r in rows if r not in sept(parsed)]), YM
    )
    assert count(conn, "transactions", "member_id = ? AND year_month = ?", (sangbeom.id, YM)) == 0
    assert count(conn, "transactions", "year_month = '2026-08'") == aug
    assert count(conn, "transactions", "member_id = ?", (jaehee.id,)) == j


@pytest.mark.parametrize("step", ["_upsert_rows", "_delete_rows", "_write_month_state"])
def test_failure_rolls_back_everything(conn, members, parsed, monkeypatch, step):
    apply_month(conn, members[0], parsed, YM)
    rows = sept(parsed)
    changed = edited(
        parsed,
        lambda all_rows: [
            dataclasses.replace(r, category="X") if r is rows[0] else r
            for r in all_rows
            if r is not rows[1]
        ],
    )
    before = snapshot(conn)

    def boom(*a, **k):
        raise RuntimeError("저장 중 오류")

    monkeypatch.setattr(apply_mod, step, boom)
    with pytest.raises(RuntimeError):
        apply_month(conn, members[0], changed, YM)
    assert snapshot(conn) == before
    assert not conn.in_transaction


def test_invalid_month_writes_nothing(conn, members, parsed):
    before = snapshot(conn)
    with pytest.raises(PreviewError):
        apply_month(conn, members[0], parsed, "2026-10")
    assert snapshot(conn) == before and not conn.in_transaction


# --- 비밀번호 전 기간 확인 -----------------------------------------------------------


def test_precheck_month():
    precheck_month(FILE_NAME, YM)  # 통과
    with pytest.raises(PreviewError, match="일부"):
        precheck_month(FILE_NAME, "2026-10")
    with pytest.raises(PreviewError, match="파일 기간"):
        precheck_month(FILE_NAME, "2026-11")
    precheck_month("뱅샐.zip", "2026-10")  # 이름에 기간이 없으면 검사하지 않음
    with pytest.raises(PreviewError, match="YYYY-MM"):
        precheck_month(FILE_NAME, "2026-9")


def test_peek_inner_name(make_zip, sample_bytes):
    from hanjigap.importer.unpack import peek_inner_name

    assert peek_inner_name(make_zip([(FILE_NAME, sample_bytes)], PW)) == FILE_NAME
    assert peek_inner_name(sample_bytes) is None  # 그냥 엑셀
    assert peek_inner_name(b"garbage") is None


# --- 명령어 ---------------------------------------------------------------------


@pytest.fixture
def cli_env(isolated_env, make_zip, sample_bytes, capsys, monkeypatch):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    path = isolated_env / "뱅샐.zip"
    path.write_bytes(make_zip([(FILE_NAME, sample_bytes)], PW))
    asked = []
    monkeypatch.setattr(cli, "ask_password", lambda: asked.append(1) or PW)
    capsys.readouterr()
    return path, asked


def run(path, *extra):
    return cli.main(["settle", "apply", str(path), "--member", "sangbeom", "--month", YM, *extra])


def test_cli_apply_confirm_yes(cli_env, capsys, monkeypatch):
    path, _ = cli_env
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: True)
    assert run(path) == 0
    out = capsys.readouterr().out
    assert "저장        신규 180건 추가 · 변경 0건 · 삭제 0건" in out
    assert "월 상태     2026-09 상범 · 정산 완료" in out and "업로드 기록 #1" in out


def test_cli_apply_declined(cli_env, capsys, monkeypatch, isolated_env):
    path, _ = cli_env
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: False)
    assert run(path) == 1
    assert "취소했습니다" in capsys.readouterr().out
    conn = connect(isolated_env / "data" / "hanjigap.db")
    assert count(conn, "transactions") == 0 and count(conn, "import_batches") == 0
    conn.close()


def test_cli_apply_yes_flag_and_repeat(cli_env, capsys, monkeypatch):
    path, _ = cli_env
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: pytest.fail("묻지 않아야 함"))
    assert run(path, "--yes") == 0
    capsys.readouterr()
    assert run(path, "--yes") == 0
    assert "변경 사항이 없어 저장하지 않았습니다" in capsys.readouterr().out


def test_cli_partial_month_rejected_before_password(cli_env, capsys):
    path, asked = cli_env
    code = cli.main(["settle", "apply", str(path), "--member", "sangbeom", "--month", "2026-10"])
    assert code == 1 and asked == []  # 비밀번호를 묻지 않음
    assert "일부(10-01 ~ 10-20)만" in capsys.readouterr().err


def test_cli_preview_still_works(cli_env, capsys):
    path, _ = cli_env
    code = cli.main(["settle", "preview", str(path), "--member", "sangbeom", "--month", YM])
    assert code == 0
    assert "DB는 바뀌지 않았습니다" in capsys.readouterr().out


def test_console_shows_no_info_logs(cli_env, capsys, monkeypatch):
    path, _ = cli_env
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: True)
    run(path)
    captured = capsys.readouterr()
    assert "INFO" not in captured.out + captured.err
