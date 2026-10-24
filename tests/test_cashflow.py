import datetime as dt
import io
import sqlite3

import pytest
from openpyxl import Workbook, load_workbook

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import load_migrations, migrate
from hanjigap.importer.cashflow import (
    Cashflow,
    CashflowItem,
    compare_cashflow,
    read_cashflow,
)
from hanjigap.importer.normalize import LedgerRow
from hanjigap.settle import apply as apply_mod
from hanjigap.settle.apply import apply_month
from hanjigap.settle.first_load import first_load
from hanjigap.settle.pipeline import ParsedExport, parse_export
from hanjigap.tools.anonymize import write_cashflow_table

FILE_NAME = "2025-10-20~2026-10-20.xlsx"


@pytest.fixture(scope="module")
def parsed(sample_bytes) -> ParsedExport:
    return parse_export(sample_bytes, None, FILE_NAME)


def tx(ym, tx_type, category, amount) -> LedgerRow:
    y, m = map(int, ym.split("-"))
    return LedgerRow(
        row_no=2,
        date=dt.date(y, m, 1),
        time=dt.time(12),
        tx_type=tx_type,
        category=category,
        subcategory="",
        content="",
        amount=amount,
        currency="KRW",
        instrument="",
        memo=None,
    )


def status_xlsx(rows: list[list]) -> bytes:
    """뱅샐현황 시트만 있는 엑셀. rows는 B열부터의 값."""
    wb = Workbook()
    ws = wb.active
    ws.title = "뱅샐현황"
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row, start=2):
            ws.cell(i, j, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


TABLE = [
    ["2.현금흐름현황"],
    [],
    ["항목", "총계", "월평균", "2026-08", "2026-09"],
    ["급여", "=SUM(E4:F4)", "=C4/2", 3_000_000, 3_100_000],
    ["금융수입", None, None, 100, None],
    ["월수입 총계", None, None, "=SUM(E4:E5)", "=SUM(F4:F5)"],
    ["식비", None, None, 50_000, 60_000],
    ["생활", None, None, 10_000, 20_000],
    ["월지출 총계", None, None, "=SUM(E7:E8)", "=SUM(F7:F8)"],
    ["순수입 총계", None, None, "=E6-E9", "=F6-F9"],
    [],
    ["3.재무현황"],
]


# --- 읽기 ----------------------------------------------------------------------


def test_read_table():
    cf, warnings = read_cashflow(status_xlsx(TABLE))
    assert warnings == []
    assert cf.months == ["2026-08", "2026-09"]
    assert [(i.item, i.kind) for i in cf.items] == [
        ("급여", "income"),
        ("금융수입", "income"),
        ("식비", "expense"),
        ("생활", "expense"),
    ]
    assert cf.items[0].amounts == {"2026-08": 3_000_000, "2026-09": 3_100_000}
    assert cf.items[1].amounts["2026-09"] == 0  # 빈 칸은 0


def test_rows_for_db():
    cf, _ = read_cashflow(status_xlsx(TABLE))
    rows = cf.rows()
    assert len(rows) == 4 * 2
    assert ("2026-09", "expense", "생활", 20_000) in rows


def test_sample_table(sample_bytes):
    cf, warnings = read_cashflow(sample_bytes)
    assert warnings == [] and len(cf.months) == 13 and len(cf.items) == 20
    assert sum(1 for i in cf.items if i.kind == "income") == 4


@pytest.mark.parametrize(
    ("data_rows", "message"),
    [
        ([["항목", "금액"], ["급여", 1]], "표를 찾을 수 없어"),
        ([["항목", "2026-09"], ["급여", 1], ["식비", 2]], "'월수입 총계' 행이 없어"),
    ],
)
def test_bad_tables(data_rows, message):
    cf, warnings = read_cashflow(status_xlsx(data_rows))
    assert cf is None and message in warnings[0]


def test_no_status_sheet():
    wb = Workbook()
    wb.active.title = "가계부 내역"
    buf = io.BytesIO()
    wb.save(buf)
    cf, warnings = read_cashflow(buf.getvalue())
    assert cf is None and "시트가 없어" in warnings[0]


# --- 대조 ----------------------------------------------------------------------


def cashflow(*items):
    return Cashflow(
        months=["2026-09"], items=[CashflowItem(n, k, {"2026-09": v}) for n, k, v in items]
    )


def test_compare_match_sign_and_mismatch():
    cf = cashflow(
        ("급여", "income", 3_000_000),
        ("식비", "expense", 50_000),
        ("생활", "expense", 181_728),  # 실제 순지출 −181,728 (환불 > 지출) → 뱅샐 절댓값 표시
        ("교통", "expense", 9_999),
    )
    rows = [
        tx("2026-09", "수입", "급여", 3_000_000),
        tx("2026-09", "지출", "식비", -50_000),
        tx("2026-09", "지출", "생활", -342_230),
        tx("2026-09", "지출", "생활", 523_958),
        tx("2026-09", "지출", "교통", -10_000),
        tx("2026-09", "이체", "저축", -1_000_000),  # 이체는 대조 대상 아님
    ]
    c = compare_cashflow(cf, rows)
    assert (c.months, c.items, c.matched, c.sign_only) == (1, 4, 2, 1)
    assert [str(m) for m in c.mismatches] == ["2026-09 교통: 뱅샐 9,999 / 한지갑 10,000"]
    assert not c.ok


def test_category_missing_from_table():
    c = compare_cashflow(cashflow(("식비", "expense", 0)), [tx("2026-09", "지출", "미분류", -500)])
    assert [str(m) for m in c.mismatches] == ["2026-09 미분류: 뱅샐 표에 없음 / 한지갑 500"]


def test_months_outside_table_ignored():
    c = compare_cashflow(cashflow(("식비", "expense", 0)), [tx("2026-07", "지출", "식비", -500)])
    assert c.ok and c.matched == 1


def test_sample_comparison(parsed):
    c = parsed.comparison
    assert (c.months, c.items) == (13, 20)
    assert c.matched + c.sign_only == 260 and c.sign_only == 8 and c.ok


def test_anonymizer_reproduces_sign_quirk():
    wb = Workbook()
    ws = wb.active
    ws.title = "뱅샐현황"
    rows = [
        [dt.datetime(2026, 9, 1), dt.time(1), "지출", "생활", "", "a", -100, "KRW", "", None],
        [dt.datetime(2026, 9, 2), dt.time(1), "지출", "생활", "", "b", 300, "KRW", "", None],
        [dt.datetime(2026, 9, 3), dt.time(1), "수입", "급여", "", "c", 1000, "KRW", "", None],
    ]
    write_cashflow_table(ws, rows, ["2026-09"], [("급여", "income"), ("생활", "expense")])
    buf = io.BytesIO()
    wb.save(buf)
    cf, _ = read_cashflow(buf.getvalue())
    assert {i.item: i.amounts["2026-09"] for i in cf.items} == {"급여": 1000, "생활": 200}


# --- 저장 ----------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "cf.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def me(conn):
    return add_member(conn, "sangbeom", "상범")


def stored(conn):
    return conn.execute("SELECT count(*), count(DISTINCT batch_id) FROM bs_cashflow").fetchone()


def test_apply_stores_cashflow(conn, me, parsed):
    result = apply_month(conn, me, parsed, "2026-09")
    assert tuple(stored(conn)) == (260, 1)
    row = conn.execute(
        "SELECT kind, amount FROM bs_cashflow WHERE batch_id = ? AND year_month = '2026-09' "
        "AND item = '급여'",
        (result.batch_id,),
    ).fetchone()
    assert row["kind"] == "income"
    assert (
        row["amount"]
        == next(i for i in parsed.cashflow.items if i.item == "급여").amounts["2026-09"]
    )


def test_no_duplicate_on_same_file(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-09")
    apply_month(conn, me, parsed, "2026-09")  # 변경 없음 → 저장하지 않음
    assert tuple(stored(conn)) == (260, 1)


def test_first_load_stores_cashflow_once(conn, me, parsed):
    first_load(conn, me, parsed, "2026-09")
    assert tuple(stored(conn)) == (260, 1)


def test_cashflow_rolls_back_with_settlement(conn, me, parsed, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("월 상태 저장 실패")

    monkeypatch.setattr(apply_mod, "_write_month_state", boom)
    with pytest.raises(RuntimeError):
        apply_month(conn, me, parsed, "2026-09")
    assert tuple(stored(conn))[0] == 0


def test_settlement_without_table_still_works(conn, me, parsed):
    import dataclasses

    no_table = dataclasses.replace(parsed, cashflow=None, comparison=None)
    assert apply_month(conn, me, no_table, "2026-09").saved
    assert tuple(stored(conn))[0] == 0


# --- 스키마 0002 -------------------------------------------------------------------


def test_upgrade_from_version_1(tmp_path):
    c = connect(tmp_path / "v1.db")
    v1, *rest = load_migrations()
    migrate(c, [v1])
    c.execute("INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')")
    c.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (1, ?, 'zip', 0, 'applied')",
        ("a" * 64,),
    )
    c.execute(
        "INSERT INTO bs_cashflow (batch_id, member_id, year_month, item, amount) "
        "VALUES (1, 1, '2026-09', '식비', 100)"
    )
    assert [m.name for m in migrate(c)] == [m.name for m in rest]
    assert c.execute("SELECT kind FROM bs_cashflow").fetchone()[0] == "expense"  # 기존 행은 기본값
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO bs_cashflow (batch_id, member_id, year_month, kind, item, amount) "
            "VALUES (1, 1, '2026-09', 'other', '급여', 1)"
        )
    c.close()


# --- 명령어 ---------------------------------------------------------------------


def test_inspect_shows_comparison(tmp_path, sample_bytes, capsys):
    path = tmp_path / FILE_NAME
    path.write_bytes(sample_bytes)
    assert cli.main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert "뱅샐 대조   13개월 × 20항목: 일치 252 · 부호 표시 차이 8 · 불일치 0" in out


def test_inspect_without_table(tmp_path, sample_bytes, capsys):
    wb = load_workbook(io.BytesIO(sample_bytes))
    del wb["뱅샐현황"]
    path = tmp_path / FILE_NAME
    wb.save(path)
    assert cli.main(["inspect", str(path)]) == 0
    assert "뱅샐 대조   건너뜀 ('뱅샐현황' 시트가 없어" in capsys.readouterr().out


def test_preview_shows_mismatch_details(isolated_env, sample_bytes, capsys):
    wb = load_workbook(io.BytesIO(sample_bytes))
    ws = wb["뱅샐현황"]
    target = next(r for r in ws.iter_rows() if r[1].value == "식비")
    sept_col = next(c.column for c in ws[11] if c.value == "2026-09")
    ws.cell(target[0].row, sept_col).value = 1  # 뱅샐 값을 일부러 틀리게
    path = isolated_env / FILE_NAME
    wb.save(path)
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    capsys.readouterr()
    assert (
        cli.main(["settle", "preview", str(path), "--member", "sangbeom", "--month", "2026-09"])
        == 0
    )
    out = capsys.readouterr().out
    assert "불일치 1" in out
    assert "대조 차이   2026-09 식비: 뱅샐 1 / 한지갑 " in out
