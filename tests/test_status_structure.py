"""J4a: '뱅샐현황' 시트 구조 보기 (값은 숨김)."""

import datetime as dt
import io

import pytest
from openpyxl import Workbook

from hanjigap import cli
from hanjigap.importer.status_structure import describe_status_sheet, safe_header

SECRET_NAME = "홍길동"
SECRET_AMOUNT = 12345678
SECRET_PRODUCT = "OO은행 슈퍼적금 2호"


def workbook(rows: list[list], title: str = "뱅샐현황") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row, start=2):  # 뱅샐처럼 B열부터
            ws.cell(i, j, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ROWS = [
    ["1.고객정보"],
    [],
    ["이름", SECRET_NAME],
    ["생년월일", dt.datetime(1990, 1, 1)],
    [],
    ["2.현금흐름현황"],
    [],
    ["항목", "2026-08", "2026-09"],
    ["급여", 3000000, 3100000],
    ["식비", 50000, None],
    [],
    ["3.재무현황"],
    [],
    ["구분", "항목", "상품명", "금액", "기준일"],
    ["자산", "현금성자산", SECRET_PRODUCT, SECRET_AMOUNT, dt.datetime(2026, 10, 1)],
    ["자산", "투자성자산", "주식 위탁", 2000000, dt.datetime(2026, 10, 1)],
    ["부채", "대출", "전세자금대출", -88400000, None],
    [],
    ["4.보험현황"],
    [],
    ["보험명", "월 납입액", "가입일 2020"],
]


@pytest.fixture
def structure():
    return describe_status_sheet(workbook(ROWS))


def test_sections_found(structure):
    assert structure.found
    assert [s.title for s in structure.sections] == [
        "1.고객정보",
        "2.현금흐름현황",
        "3.재무현황",
        "4.보험현황",
    ]


def test_private_section_skipped(structure):
    sec = structure.sections[0]
    assert sec.private and sec.columns == [] and (sec.title_row, sec.last_row) == (1, 4)


def test_finance_columns(structure):
    sec = structure.sections[2]
    assert (sec.header_row, sec.data_rows, sec.span) == (14, 3, "B12:F17")
    cols = {c.header: c for c in sec.columns}
    assert cols["구분"].labels == ["자산", "부채"]
    assert cols["항목"].labels == ["현금성자산", "투자성자산", "대출"]
    assert cols["상품명"].labels is None  # 숫자 섞인 이름이 있으면 목록을 보여 주지 않는다
    assert (cols["금액"].numbers, cols["금액"].labels) == (3, None)
    assert (cols["기준일"].dates, cols["기준일"].blanks) == (2, 1)


def test_header_only_section(structure):
    sec = structure.sections[3]
    assert sec.data_rows == 0
    assert [c.header for c in sec.columns] == ["보험명", "월 납입액", "〈숫자 포함〉"]


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("2026-09", "2026-09"),
        ("금액", "금액"),
        ("가입일 2020", "〈숫자 포함〉"),
        (12345, "〈숫자 포함〉"),
        (None, ""),
        ("가" * 30, "가" * 20),
    ],
)
def test_safe_header(value, shown):
    assert safe_header(value) == shown


def test_name_columns_hidden():
    rows = [
        ["1.보험현황"],
        ["보험사", "보험명"],
        ["가 손해보험", "실손보험"],
        ["나 생명", "종신보험"],
    ]
    cols = describe_status_sheet(workbook(rows)).sections[0].columns
    assert cols[0].labels == ["가 손해보험", "나 생명"]
    assert cols[1].labels is None


def test_too_many_labels_hidden():
    rows = [["1.투자현황"], ["종목"], *[[f"종목{chr(0xAC00 + i)}"] for i in range(13)]]
    sec = describe_status_sheet(workbook(rows)).sections[0]
    assert sec.columns[0].labels is None


def test_no_status_sheet():
    st = describe_status_sheet(workbook([["x"]], title="가계부 내역"))
    assert not st.found and st.sheet_names == ["가계부 내역"]


def test_sample_fixture(sample_bytes):
    st = describe_status_sheet(sample_bytes)
    assert [s.title for s in st.sections] == ["2.현금흐름현황"]
    assert st.sections[0].columns[0].header == "항목"


# --- 명령어 ---------------------------------------------------------------------


def test_cli_hides_values(isolated_env, capsys):
    path = isolated_env / "뱅샐.xlsx"
    path.write_bytes(workbook(ROWS))
    assert cli.main(["inspect-status", str(path)]) == 0
    out = capsys.readouterr().out
    assert "[1] 1.고객정보  (1~4행) → 개인정보라 내용은 건너뜀" in out
    assert "[3] 3.재무현황  B12:F17 · 머리글 14행 · 데이터 3행" in out
    assert "값: 자산, 부채" in out
    for secret in (SECRET_NAME, f"{SECRET_AMOUNT}", f"{SECRET_AMOUNT:,}", SECRET_PRODUCT, "1990"):
        assert secret not in out


def test_cli_missing_file(isolated_env, capsys):
    assert cli.main(["inspect-status", str(isolated_env / "없음.zip")]) == 1
    assert "찾을 수 없습니다" in capsys.readouterr().err
