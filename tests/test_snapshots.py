"""J4b-2: 뱅샐현황 잔액 기록(0005)을 정산할 때 저장."""

import io

import pytest
from openpyxl import load_workbook

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import load_migrations, migrate
from hanjigap.db.snapshots import latest_snapshot
from hanjigap.settle import apply as apply_mod
from hanjigap.settle.apply import apply_month
from hanjigap.settle.first_load import first_load
from hanjigap.settle.pipeline import parse_export

FILE_NAME = "2025-10-20~2026-10-20.xlsx"


def with_status_tables(sample: bytes, changes: dict | None = None) -> bytes:
    """익명화 샘플의 뱅샐현황 시트에 재무·보험·투자·대출 표(가짜 값, 계산식 총계)를 붙인다."""
    # 같은 표 모양을 재사용. 함수 안에서 가져와 import 정렬이 환경마다 달라지지 않게 한다.
    from test_status_tables import formula_layout

    wb = load_workbook(io.BytesIO(sample))
    ws = wb["뱅샐현황"]
    for r, values in formula_layout(changes).items():
        if r < 37:  # 고객정보·현금흐름 자리는 샘플 그대로
            continue
        for j, v in enumerate(values, start=2):
            ws.cell(r, j, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def parsed(sample_bytes):
    return parse_export(with_status_tables(sample_bytes), None, FILE_NAME)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "snap.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def me(conn):
    return add_member(conn, "sangbeom", "상범")


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_parsed_has_tables(parsed):
    assert parsed.status is not None and parsed.status.ok
    assert len(parsed.status.holdings) == 10


def test_first_load_saves_snapshot(conn, me, parsed):
    first_load(conn, me, parsed, "2026-09")
    snap = latest_snapshot(conn, me)
    assert (snap.as_of, snap.year_month) == ("2026-10-20", "2026-09")  # 내보낸 날 / 정산한 달
    assert (snap.total_assets, snap.total_liabilities, snap.net_worth, snap.checks_ok) == (
        6_500_000,
        240_410,
        6_259_590,
        True,
    )
    assert [h.name for h in snap.of("asset")] == ["주거래 통장", "비상금 통장", "위탁 계좌", "IRP"]
    (loan,) = snap.of("loan")
    assert (loan.principal, loan.amount, loan.rate, loan.end_date) == (
        10_000_000,
        240_410,
        5.55,
        "2027-10-13",
    )
    inv = snap.of("investment")[0]
    assert inv.rate == pytest.approx(-5.08808177164441)


def test_apply_saves_snapshot(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-09")
    assert count(conn, "bs_snapshots") == 1 and count(conn, "bs_holdings") == 10


def test_no_change_resettle_saves_nothing(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-09")
    apply_month(conn, me, parsed, "2026-09")  # 변경 없음 → 저장하지 않음
    assert count(conn, "bs_snapshots") == 1


def test_latest_wins(conn, me, sample_bytes, parsed):
    first_load(conn, me, parsed, "2026-08")
    other = parse_export(
        with_status_tables(sample_bytes, {44: ["연금 자산", "IRP", None, 500_000]}), None, FILE_NAME
    )
    apply_month(conn, me, other, "2026-09")
    assert latest_snapshot(conn, me).net_worth == 6_759_590  # 9월 (나중 것)
    assert latest_snapshot(conn, me, "2026-08").net_worth == 6_259_590
    assert latest_snapshot(conn, me, "2026-07") is None


def test_rolled_back_with_settlement(conn, me, parsed, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("월 상태 저장 실패")

    monkeypatch.setattr(apply_mod, "_write_month_state", boom)
    with pytest.raises(RuntimeError):
        apply_month(conn, me, parsed, "2026-09")
    assert count(conn, "bs_snapshots") == 0 and count(conn, "bs_holdings") == 0


def test_deleted_with_batch(conn, me, parsed):
    result = apply_month(conn, me, parsed, "2026-09")
    conn.execute("DELETE FROM month_settlements")
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM bs_cashflow")
    conn.execute("DELETE FROM import_batches WHERE id = ?", (result.batch_id,))
    assert count(conn, "bs_snapshots") == 0 and count(conn, "bs_holdings") == 0


def test_mismatch_still_saved_with_flag(conn, me, sample_bytes):
    bad = parse_export(
        with_status_tables(
            sample_bytes,
            {45: ["총자산", None, None, "=SUM(E41:E42)", "총부채", None, None, "=SUM(I41:I44)"]},
        ),
        None,
        FILE_NAME,
    )
    apply_month(conn, me, bad, "2026-09")
    snap = latest_snapshot(conn, me)
    assert snap is not None and not snap.checks_ok


def test_sample_without_tables_saves_nothing(conn, me, sample_bytes):
    apply_month(conn, me, parse_export(sample_bytes, None, FILE_NAME), "2026-09")
    assert count(conn, "bs_snapshots") == 0
    assert latest_snapshot(conn, me) is None


def test_upgrade_from_version_4(tmp_path):
    c = connect(tmp_path / "v4.db")
    migs = load_migrations()
    migrate(c, migs[:4])
    c.execute("INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')")
    assert [m.name for m in migrate(c)][0] == "bs_snapshots"
    assert c.execute("SELECT count(*) FROM members").fetchone()[0] == 1
    c.close()


# --- 명령어 ---------------------------------------------------------------------


def test_preview_shows_status_line(isolated_env, sample_bytes, capsys):
    path = isolated_env / FILE_NAME
    path.write_bytes(with_status_tables(sample_bytes))
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    capsys.readouterr()
    code = cli.main(["settle", "preview", str(path), "--member", "sangbeom", "--month", "2026-09"])
    out = capsys.readouterr().out
    assert code == 0
    assert (
        "뱅샐현황    자산 4개 · 부채 1개 · 보험 2개 · 투자 2개 · 대출 1개 · 합계 검증 일치" in out
    )
    assert "주거래 통장" not in out  # 상품명은 보여 주지 않는다
