import datetime as dt
import io

import pytest
from openpyxl import Workbook

from hanjigap import cli
from hanjigap.importer.errors import ErrorCode, ImportFailure
from hanjigap.importer.sheet import KNOWN_COLUMNS, find_ledger

HEADER = ["날짜", "시간", "타입", "대분류", "소분류", "내용", "금액", "화폐", "결제수단", "메모"]
ROW = [dt.datetime(2026, 9, 1), dt.time(10, 0), "지출", "식비", "한식", "가맹점 0001", -9000]
ROW += ["KRW", "카드 A", None]


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fail(data: bytes) -> ImportFailure:
    with pytest.raises(ImportFailure) as exc:
        find_ledger(data)
    return exc.value


# --- 정상 ---------------------------------------------------------------------


def test_anonymized_sample(sample_bytes):
    ledger = find_ledger(sample_bytes)
    assert ledger.sheet_name == "가계부 내역"
    assert ledger.sheet_names == ["뱅샐현황", "가계부 내역"]
    assert ledger.header_row == 1
    assert ledger.data_rows == 1579
    assert ledger.warnings == []
    assert set(ledger.columns) == set(HEADER)
    assert ledger.columns["날짜"] == 0 and ledger.columns["금액"] == 6


def test_iter_rows_gives_row_numbers_and_names(sample_bytes):
    row_no, record = next(find_ledger(sample_bytes).iter_rows())
    assert row_no == 2  # 엑셀 2행 = 첫 거래
    assert set(record) == set(KNOWN_COLUMNS)
    assert record["타입"] in ("수입", "지출", "이체")
    assert isinstance(record["금액"], int)


def test_reordered_columns():
    order = [8, 6, 0, 1, 2, 3, 4, 5, 7, 9]
    data = make_xlsx({"가계부 내역": [[HEADER[i] for i in order], [ROW[i] for i in order]]})
    ledger = find_ledger(data)
    _, record = next(ledger.iter_rows())
    assert record["금액"] == -9000 and record["결제수단"] == "카드 A"
    assert ledger.warnings == []


def test_header_with_spaces_and_newlines():
    header = [
        " 날짜",
        "시간 ",
        "타입",
        "대분류",
        "소\n분류",
        "내용",
        "금 액",
        "화폐",
        "결제수단",
        "메모",
    ]
    ledger = find_ledger(make_xlsx({"가계부 내역": [header, ROW]}))
    assert ledger.data_rows == 1


def test_header_in_second_row():
    ledger = find_ledger(make_xlsx({"가계부 내역": [["뱅크샐러드 가계부"], HEADER, ROW]}))
    assert ledger.header_row == 2
    row_no, _ = next(ledger.iter_rows())
    assert row_no == 3


def test_blank_rows_skipped_numbers_kept():
    ledger = find_ledger(make_xlsx({"가계부 내역": [HEADER, ROW, [None] * 10, ["  "], ROW]}))
    assert [n for n, _ in ledger.iter_rows()] == [2, 5]


def test_header_only_is_not_error():
    assert find_ledger(make_xlsx({"가계부 내역": [HEADER]})).data_rows == 0


# --- 경고 ---------------------------------------------------------------------


def test_renamed_sheet_found_by_header():
    ledger = find_ledger(make_xlsx({"현황": [["x"]], "거래내역": [HEADER, ROW]}))
    assert ledger.sheet_name == "거래내역"
    assert any("시트 이름이 예상과 다릅니다" in w for w in ledger.warnings)


def test_optional_columns_missing():
    keep = [i for i, h in enumerate(HEADER) if h not in ("화폐", "메모")]
    data = make_xlsx({"가계부 내역": [[HEADER[i] for i in keep], [ROW[i] for i in keep]]})
    ledger = find_ledger(data)
    _, record = next(ledger.iter_rows())
    assert record["화폐"] is None and record["메모"] is None
    assert len([w for w in ledger.warnings if "선택 열이 없습니다" in w]) == 2


def test_unknown_column_warned_and_ignored():
    ledger = find_ledger(make_xlsx({"가계부 내역": [HEADER + ["태그"], ROW + ["여행"]]}))
    _, record = next(ledger.iter_rows())
    assert "태그" not in record
    assert any("태그" in w for w in ledger.warnings)


# --- 오류 ---------------------------------------------------------------------


def test_no_ledger_sheet():
    exc = _fail(make_xlsx({"뱅샐현황": [["이름"]], "기타": [["a", "b"]]}))
    assert exc.code == ErrorCode.LEDGER_SHEET_NOT_FOUND
    assert "뱅샐현황" in str(exc)


def test_missing_required_columns():
    header = [h for h in HEADER if h not in ("금액", "결제수단")]
    exc = _fail(make_xlsx({"가계부 내역": [header]}))
    assert exc.code == ErrorCode.HEADER_MISSING_COLUMNS
    assert "금액" in str(exc) and "결제수단" in str(exc)


def test_duplicate_columns():
    exc = _fail(make_xlsx({"가계부 내역": [HEADER + ["내용"], ROW + ["x"]]}))
    assert exc.code == ErrorCode.HEADER_DUPLICATE_COLUMNS
    assert "내용" in str(exc)


def test_broken_excel():
    exc = _fail(b"PK\x03\x04 broken")
    assert exc.code == ErrorCode.NOT_EXCEL


# --- inspect 명령 ------------------------------------------------------------


def test_inspect_shows_ledger(tmp_path, sample_bytes, capsys):
    path = tmp_path / "s.xlsx"
    path.write_bytes(sample_bytes)
    assert cli.main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert "거래 1,579행" in out and "필수 열 8개 + 선택 열 2개" in out
    assert "경고        없음" in out


def test_inspect_shows_warnings(tmp_path, capsys):
    path = tmp_path / "w.xlsx"
    path.write_bytes(make_xlsx({"거래내역": [HEADER + ["태그"], ROW + ["x"]]}))
    assert cli.main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert "경고        시트 이름이 예상과 다릅니다" in out
    assert "태그" in out


def test_inspect_reports_header_error(tmp_path, capsys):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(make_xlsx({"가계부 내역": [["날짜", "내용"]]}))
    assert cli.main(["inspect", str(path)]) == 1
    assert "필수 열이 없습니다" in capsys.readouterr().err
