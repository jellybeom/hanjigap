import datetime as dt

import pytest

from hanjigap import cli
from hanjigap.importer.normalize import LedgerRow, normalize
from hanjigap.importer.sheet import find_ledger
from hanjigap.importer.summary import Period, parse_period, summarize


def tx(date, amount, tx_type="지출", time=dt.time(12, 0), row_no=2) -> LedgerRow:
    return LedgerRow(
        row_no=row_no,
        date=date,
        time=time,
        tx_type=tx_type,
        category="",
        subcategory="",
        content="",
        amount=amount,
        currency="KRW",
        instrument="카드 A",
        memo=None,
    )


D = dt.date


# --- 기간 읽기 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "2025-10-20~2026-10-20.xlsx",
        "뱅크샐러드_2025-10-20~2026-10-20.zip",
        "2025-10-20 ~ 2026-10-20",
    ],
)
def test_parse_period(name):
    p = parse_period(name)
    assert p == Period(D(2025, 10, 20), D(2026, 10, 20), "파일 이름")


@pytest.mark.parametrize(
    "name",
    [None, "", "뱅샐.zip", "2026-10-20.xlsx", "2026-13-01~2026-14-01", "2026-10-20~2025-10-20"],
)
def test_parse_period_none(name):
    assert parse_period(name) is None


def test_period_months_cross_year():
    p = Period(D(2025, 11, 5), D(2026, 2, 1), "파일 이름")
    assert p.months() == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


@pytest.mark.parametrize(
    ("start", "end", "partial"),
    [
        (D(2025, 10, 20), D(2026, 10, 20), {(2025, 10), (2026, 10)}),
        (D(2025, 10, 1), D(2026, 10, 31), set()),  # 1일 시작, 말일 끝
        (D(2025, 11, 1), D(2026, 2, 28), set()),  # 2월 말일
        (D(2024, 2, 1), D(2024, 2, 28), {(2024, 2)}),  # 윤년이라 28일은 말일 아님
        (D(2025, 11, 1), D(2026, 11, 1), {(2026, 11)}),  # 11월 1일 내보내기
    ],
)
def test_partial_months(start, end, partial):
    p = Period(start, end, "파일 이름")
    assert {ym for ym in p.months() if p.is_partial(*ym)} == partial


# --- 합계 ----------------------------------------------------------------------


def test_month_sums():
    rows = [
        tx(D(2026, 9, 1), 3_500_000, "수입"),
        tx(D(2026, 9, 2), -30_000),
        tx(D(2026, 9, 3), -92_700),
        tx(D(2026, 9, 3), 92_700),  # 취소: 지출에서 차감
        tx(D(2026, 9, 4), 100_000, "이체"),
        tx(D(2026, 9, 5), -1_800_000, "이체"),
    ]
    s = summarize(rows, Period(D(2026, 9, 1), D(2026, 9, 30), "파일 이름"))
    (m,) = s.months
    assert (m.label, m.count, m.partial) == ("2026-09", 6, False)
    assert (m.income, m.expense, m.transfer_in, m.transfer_out) == (
        3_500_000,
        30_000,
        100_000,
        1_800_000,
    )
    assert s.total.count == 6 and s.total.expense == 30_000


def test_refund_bigger_than_spending_goes_negative():
    s = summarize([tx(D(2026, 9, 1), 50_000)], Period(D(2026, 9, 1), D(2026, 9, 30), "파일 이름"))
    assert s.months[0].expense == -50_000


def test_month_boundary():
    rows = [
        tx(D(2026, 8, 31), -1, time=dt.time(23, 59, 59)),
        tx(D(2026, 9, 1), -2, time=dt.time(0, 0)),
    ]
    s = summarize(rows, Period(D(2026, 8, 1), D(2026, 9, 30), "파일 이름"))
    assert [(m.label, m.expense) for m in s.months] == [("2026-08", 1), ("2026-09", 2)]


def test_empty_months_listed():
    s = summarize([tx(D(2026, 9, 1), -1)], Period(D(2026, 7, 1), D(2026, 9, 30), "파일 이름"))
    assert [(m.label, m.count) for m in s.months] == [
        ("2026-07", 0),
        ("2026-08", 0),
        ("2026-09", 1),
    ]


def test_period_from_dates_when_no_name():
    s = summarize([tx(D(2026, 9, 5), -1), tx(D(2026, 7, 10), -1)])
    assert s.period == Period(D(2026, 7, 10), D(2026, 9, 5), "거래 날짜")
    assert s.partial_months == ["2026-07", "2026-09"]
    assert s.complete_months == ["2026-08"]


def test_outside_period_warned():
    s = summarize([tx(D(2026, 6, 30), -1)], Period(D(2026, 7, 1), D(2026, 7, 31), "파일 이름"))
    assert s.outside_period == 1 and s.warnings
    assert s.months[0].label == "2026-06" and s.months[0].partial


def test_no_rows_no_period():
    s = summarize([])
    assert s.period is None and s.months == [] and s.total.count == 0


# --- 익명화 샘플 ------------------------------------------------------------------


def test_sample_summary(sample_bytes):
    rows = normalize(find_ledger(sample_bytes)).rows
    s = summarize(rows, parse_period("2025-10-20~2026-10-20.xlsx"))
    assert len(s.months) == 13
    assert sum(m.count for m in s.months) == s.total.count == 1579
    assert s.partial_months == ["2025-10", "2026-10"]
    assert sum(m.expense for m in s.months) == s.total.expense
    assert s.total.income + s.total.transfer_in - s.total.expense - s.total.transfer_out == sum(
        r.amount for r in rows
    )


# --- inspect 명령 ----------------------------------------------------------------


def test_inspect_prints_table(tmp_path, sample_bytes, capsys):
    path = tmp_path / "2025-10-20~2026-10-20.xlsx"
    path.write_bytes(sample_bytes)
    assert cli.main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert "기간        2025-10-20 ~ 2026-10-20 (파일 이름 기준, 13개월)" in out
    assert "2025-10*" in out and "2026-10*" in out and "2026-09 " in out
    assert "합계       1,579" in out
    assert "일부 기간만 포함된 달" in out


def test_inspect_period_from_dates(tmp_path, sample_bytes, capsys):
    path = tmp_path / "뱅샐.xlsx"
    path.write_bytes(sample_bytes)
    assert cli.main(["inspect", str(path)]) == 0
    assert "(거래 날짜 기준, 13개월)" in capsys.readouterr().out
