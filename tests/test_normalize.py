import dataclasses
import datetime as dt
import io
from collections import Counter

import pytest
from openpyxl import Workbook

from hanjigap import cli
from hanjigap.importer.normalize import (
    LedgerRow,
    RowIssue,
    group_issues,
    normalize,
)
from hanjigap.importer.sheet import find_ledger

HEADER = ["날짜", "시간", "타입", "대분류", "소분류", "내용", "금액", "화폐", "결제수단", "메모"]
D = dt.datetime(2026, 9, 1)
T = dt.time(10, 30, 15)


def row(**over):
    base = {
        "날짜": D,
        "시간": T,
        "타입": "지출",
        "대분류": "식비",
        "소분류": "한식",
        "내용": "가맹점 0001",
        "금액": -9000,
        "화폐": "KRW",
        "결제수단": "카드 A",
        "메모": None,
    }
    base.update(over)
    return [base[h] for h in HEADER]


def run(*rows, header=HEADER):
    wb = Workbook()
    ws = wb.active
    ws.title = "가계부 내역"
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return normalize(find_ledger(buf.getvalue()))


def one(**over) -> LedgerRow:
    result = run(row(**over))
    assert result.ok, [str(e) for e in result.errors]
    return result.rows[0]


def error_of(**over) -> RowIssue:
    result = run(row(**over))
    assert not result.ok and not result.rows
    return result.errors[0]


# --- 익명화 샘플 ---------------------------------------------------------------


def test_sample_all_rows(sample_bytes):
    result = normalize(find_ledger(sample_bytes))
    assert result.ok and result.warnings == []
    assert len(result.rows) == 1579
    assert Counter(r.tx_type for r in result.rows) == {"지출": 1307, "이체": 223, "수입": 49}
    assert all(r.currency == "KRW" for r in result.rows)
    assert sum(1 for r in result.rows if r.time == dt.time(0, 0)) == 52
    assert sum(1 for r in result.rows if r.memo) == 3
    assert result.rows[0].row_no == 2


def test_basic_fields():
    r = one(내용="  가맹점 0001  ", 메모="  ")
    assert r.date == dt.date(2026, 9, 1) and r.time == T
    assert r.occurred_at == dt.datetime(2026, 9, 1, 10, 30, 15)
    assert r.content == "가맹점 0001" and r.memo is None
    assert r.amount == -9000 and r.tx_type == "지출"


def test_row_is_immutable():
    r = one()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.amount = 0  # type: ignore[misc]


# --- 날짜 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["2026-09-01", "2026.09.01", "2026/9/1", " 2026-09-01 00:00:00", dt.date(2026, 9, 1)]
)
def test_date_formats(value):
    assert one(날짜=value).date == dt.date(2026, 9, 1)


@pytest.mark.parametrize("value", [None, "", "어제", "2026-13-01", "2026-02-30"])
def test_date_invalid(value):
    assert error_of(날짜=value).column == "날짜"


# --- 시간 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10:30", dt.time(10, 30)),
        ("9:05:07", dt.time(9, 5, 7)),
        (dt.datetime(1899, 12, 30, 23, 59, 59), dt.time(23, 59, 59)),
        (0.5, dt.time(12, 0)),
        (dt.time(0, 0), dt.time(0, 0)),
    ],
)
def test_time_formats(value, expected):
    assert one(시간=value).time == expected


def test_time_blank_is_midnight_with_warning():
    result = run(row(시간=None), row(시간="  "))
    assert result.ok and [r.time for r in result.rows] == [dt.time(0, 0)] * 2
    assert [w.row_no for w in result.warnings] == [2, 3]
    assert "00:00" in result.warnings[0].message


def test_time_midnight_value_has_no_warning():
    assert run(row(시간=dt.time(0, 0))).warnings == []


@pytest.mark.parametrize("value", ["25:00", "오전 10시", "10-30"])
def test_time_invalid(value):
    assert error_of(시간=value).column == "시간"


# --- 타입 -----------------------------------------------------------------------


@pytest.mark.parametrize("value", ["수입", "지출", "이체", " 이체 "])
def test_types(value):
    assert one(타입=value).tx_type == value.strip()


@pytest.mark.parametrize("value", [None, "", "입금", "기타"])
def test_type_invalid(value):
    assert error_of(타입=value).column == "타입"


# --- 금액 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-9000, -9000), (12000.0, 12000), ("-1,000", -1000), ("1,234원", 1234), ("−500", -500)],
)
def test_amount_formats(value, expected):
    assert one(금액=value).amount == expected


@pytest.mark.parametrize("value", [None, "", "abc", 1000.5, "12.3", True])
def test_amount_invalid(value):
    assert error_of(금액=value).column == "금액"


def test_amount_warnings():
    result = run(
        row(금액=0), row(타입="수입", 금액=-100), row(금액=500)
    )  # 마지막은 환불: 경고 없음
    assert result.ok
    assert [(w.row_no, w.message) for w in result.warnings] == [(2, "0원"), (3, "수입인데 음수")]


# --- 화폐 -----------------------------------------------------------------------


def test_currency_default_and_case():
    assert one(화폐=None).currency == "KRW"
    assert one(화폐="krw").currency == "KRW"


def test_currency_missing_column_defaults_to_krw():
    header = [h for h in HEADER if h != "화폐"]
    values = [v for h, v in zip(HEADER, row(), strict=True) if h != "화폐"]
    result = run(values, header=header)
    assert result.ok and result.rows[0].currency == "KRW"


def test_foreign_currency_stops():
    err = error_of(화폐="USD")
    assert err.column == "화폐" and "USD" in err.message


# --- 텍스트 ---------------------------------------------------------------------


def test_blank_text_fields_allowed():
    r = one(대분류=None, 소분류="", 내용=None, 결제수단=None)
    assert (r.category, r.subcategory, r.content, r.instrument) == ("", "", "", "")


def test_number_in_text_field():
    assert one(내용=12345).content == "12345"


# --- 오류 모으기 ------------------------------------------------------------------


def test_all_errors_collected_not_first_only():
    result = run(row(금액="abc"), row(), row(날짜=None, 타입="??"), row(화폐="JPY"))
    assert not result.ok
    assert len(result.rows) == 1  # 정상 행 1개
    assert [(e.row_no, e.column) for e in result.errors] == [
        (2, "금액"),
        (4, "날짜"),
        (4, "타입"),
        (5, "화폐"),
    ]


def test_error_message_has_value_not_content():
    err = error_of(금액="abc", 내용="비밀가맹점")
    assert str(err) == "2행 금액: 숫자가 아닙니다 ('abc')"
    assert "비밀가맹점" not in str(err)


def test_group_issues():
    issues = [RowIssue(n, "시간", "비어 있어 00:00으로 처리") for n in range(2, 15)]
    lines = group_issues(issues, max_rows=3)
    assert lines == ["시간: 비어 있어 00:00으로 처리 13건 (2, 3, 4행 외 10건)"]


# --- inspect 명령 ----------------------------------------------------------------


def _write(tmp_path, *rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "가계부 내역"
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    path = tmp_path / "t.xlsx"
    wb.save(path)
    return path


def test_inspect_success_line(tmp_path, capsys):
    assert cli.main(["inspect", str(_write(tmp_path, row(), row(시간=None)))]) == 0
    out = capsys.readouterr().out
    assert "정규화      2행 성공, 오류 0건, 경고 1건" in out
    assert "시간: 비어 있어 00:00으로 처리 1건 (3행)" in out


def test_inspect_fails_on_errors(tmp_path, capsys):
    rows = [row(금액="x")] * 25
    assert cli.main(["inspect", str(_write(tmp_path, *rows))]) == 1
    captured = capsys.readouterr()
    assert "오류 25건" in captured.out
    assert "2행 금액" in captured.err and "외 5건" in captured.err
    assert "사용할 수 없습니다" in captured.err
