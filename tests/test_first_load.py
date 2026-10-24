import dataclasses

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.importer.fingerprint import assign_keys
from hanjigap.settle import apply as apply_mod
from hanjigap.settle.apply import apply_month
from hanjigap.settle.check import check_recent
from hanjigap.settle.first_load import FirstLoadError, first_load, plan_first_load
from hanjigap.settle.pipeline import ParsedExport, parse_export
from hanjigap.settle.preview import PreviewError

FILE_NAME = "2025-10-20~2026-10-20.xlsx"
PW = "test1234"
BASELINE = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 9)]


@pytest.fixture(scope="module")
def parsed(sample_bytes) -> ParsedExport:
    return parse_export(sample_bytes, None, FILE_NAME)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "f.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def members(conn):
    return add_member(conn, "sangbeom", "상범"), add_member(conn, "jaehee", "재희")


def states(conn, member):
    rows = conn.execute(
        "SELECT year_month, status, tx_count, batch_id FROM month_settlements "
        "WHERE member_id = ? ORDER BY year_month",
        (member.id,),
    )
    return [tuple(r) for r in rows]


def snapshot(conn):
    return {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
        for t in ("import_batches", "transactions", "month_settlements")
    }


# --- 계획 ----------------------------------------------------------------------


def test_plan_months_and_status(conn, members, parsed):
    plan = plan_first_load(conn, members[0], parsed, "2026-09")
    assert [m.year_month for m in plan.months] == [*BASELINE, "2026-09"]
    assert [m.status for m in plan.months] == ["baseline"] * 10 + ["settled"]
    assert plan.excluded == [("2025-10", "일부만 있음"), ("2026-10", "일부만 있음")]
    assert plan.baseline_count == 10
    assert plan.total_rows == sum(m.count for m in parsed.summary.months if not m.partial)


def test_plan_excludes_months_after_target(conn, members, parsed):
    plan = plan_first_load(conn, members[0], parsed, "2026-06")
    assert plan.months[-1].year_month == "2026-06" and plan.months[-1].status == "settled"
    assert ("2026-07", "정산 대상 이후") in plan.excluded
    assert ("2026-09", "정산 대상 이후") in plan.excluded


def test_plan_does_not_write(conn, members, parsed):
    before = conn.total_changes
    plan_first_load(conn, members[0], parsed, "2026-09")
    assert conn.total_changes == before


@pytest.mark.parametrize("ym", ["2026-10", "2025-10", "2026-11"])
def test_target_must_be_complete_month(conn, members, parsed, ym):
    with pytest.raises(PreviewError):
        plan_first_load(conn, members[0], parsed, ym)


# --- 저장 ----------------------------------------------------------------------


def test_first_load_saves_all_months(conn, members, parsed):
    result = first_load(conn, members[0], parsed, "2026-09")
    got = states(conn, members[0])
    assert [(ym, st) for ym, st, _, _ in got] == [(ym, "baseline") for ym in BASELINE] + [
        ("2026-09", "settled")
    ]
    by_month = {m.label: m.count for m in parsed.summary.months}
    assert all(count == by_month[ym] for ym, _, count, _ in got)
    assert {batch for *_, batch in got} == {result.batch_id}  # 업로드 기록 1건
    assert conn.execute("SELECT count(*) FROM import_batches").fetchone()[0] == 1
    total = conn.execute("SELECT count(*), sum(amount) FROM transactions").fetchone()
    loaded = [k.row for k in parsed.keyed if k.row.date.strftime("%Y-%m") in {*BASELINE, "2026-09"}]
    assert tuple(total) == (len(loaded), sum(r.amount for r in loaded))


def test_refuses_second_time(conn, members, parsed):
    first_load(conn, members[0], parsed, "2026-09")
    with pytest.raises(FirstLoadError, match="처음 한 번만"):
        first_load(conn, members[0], parsed, "2026-09")


def test_refuses_after_normal_settlement(conn, members, parsed):
    apply_month(conn, members[0], parsed, "2026-08")
    with pytest.raises(FirstLoadError, match="settle apply"):
        plan_first_load(conn, members[0], parsed, "2026-09")


def test_other_member_history_does_not_block(conn, members, parsed):
    sangbeom, jaehee = members
    first_load(conn, sangbeom, parsed, "2026-09")
    other = dataclasses.replace(
        parsed,
        keyed=assign_keys(
            [dataclasses.replace(r, amount=r.amount - 1) for r in parsed.normalized.rows]
        ),
        file_sha256="e" * 64,
    )
    first_load(conn, jaehee, other, "2026-09")
    assert len(states(conn, jaehee)) == 11 and len(states(conn, sangbeom)) == 11


@pytest.mark.parametrize("step", ["_upsert_rows", "_write_month_state"])
def test_failure_in_middle_rolls_back(conn, members, parsed, monkeypatch, step):
    before = snapshot(conn)
    original = getattr(apply_mod, step)
    calls = {"n": 0}

    def fail_on_fifth(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("중간 달에서 오류")
        return original(*args, **kwargs)

    monkeypatch.setattr(apply_mod, step, fail_on_fifth)
    with pytest.raises(RuntimeError):
        first_load(conn, members[0], parsed, "2026-09")
    assert snapshot(conn) == before and not conn.in_transaction


def test_next_month_flow_after_first_load(conn, members, parsed):
    me = members[0]
    first_load(conn, me, parsed, "2026-08")
    # 다음 달(9월) 정산이 정상 동작하고, 누락 점검이 8월(정산)·7월(기초)을 점검
    result = apply_month(conn, me, parsed, "2026-09")
    assert result.saved and result.added == 180
    checks = check_recent(conn, me, parsed, "2026-10")
    assert [(c.year_month, c.status) for c in checks] == [
        ("2026-09", "settled"),
        ("2026-08", "settled"),
    ]
    checks = check_recent(conn, me, parsed, "2026-09")
    assert [(c.year_month, c.status, c.clean) for c in checks] == [
        ("2026-08", "settled", True),
        ("2026-07", "baseline", True),
    ]


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
    return cli.main(["settle", "first-load", str(path), "--member", "sangbeom", *extra])


def test_cli_first_load(cli_env, capsys, monkeypatch):
    path, _ = cli_env
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: True)
    assert run(path, "--month", "2026-09") == 0
    out = capsys.readouterr().out
    assert "첫 적재     상범 · 파일 2025-10-20 ~ 2026-10-20 (파일 이름 기준)" in out
    assert "2026-09        180   정산 완료   ← 정산 대상" in out
    assert "2025-11        144   기초 데이터" in out
    assert "제외        2025-10 (일부만 있음), 2026-10 (일부만 있음)" in out
    assert "(기초 10개월 · 정산 1개월)" in out
    assert "업로드 기록 #1" in out


def test_cli_declined(cli_env, capsys, monkeypatch, isolated_env):
    path, _ = cli_env
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: False)
    assert run(path, "--month", "2026-09") == 1
    assert "취소했습니다" in capsys.readouterr().out
    c = connect(isolated_env / "data" / "hanjigap.db")
    assert c.execute("SELECT count(*) FROM month_settlements").fetchone()[0] == 0
    c.close()


def test_cli_second_run_refused_before_password(cli_env, capsys):
    path, asked = cli_env
    assert run(path, "--month", "2026-09", "--yes") == 0
    asked.clear()
    capsys.readouterr()
    assert run(path, "--month", "2026-09", "--yes") == 1
    assert asked == []  # 비밀번호를 묻지 않음
    assert "처음 한 번만" in capsys.readouterr().err


def test_cli_partial_target_refused_before_password(cli_env, capsys):
    path, asked = cli_env
    assert run(path, "--month", "2026-10") == 1
    assert asked == [] and "일부(10-01 ~ 10-20)만" in capsys.readouterr().err
