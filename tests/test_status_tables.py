"""J4b-1: 뱅샐현황의 재무·보험·투자·대출 표 읽기.

실제 파일(2026년 10월)과 같은 모양의 표를 가짜 값으로 만들어 시험한다.
병합된 칸은 openpyxl처럼 왼쪽 위 칸에만 값이 있다.
표를 엑셀 모양 그대로 한 줄씩 적어 두려고 이 파일만 줄 길이 검사를 끈다.
"""

# ruff: noqa: E501

import datetime as dt
import io

import pytest
from openpyxl import Workbook

from hanjigap import cli
from hanjigap.importer.status_tables import read_status_tables

D = dt.datetime


def build(rows_by_row: dict[int, list], title: str = "뱅샐현황") -> bytes:
    """{행 번호: [B열부터 값…]}"""
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r, values in rows_by_row.items():
        for j, v in enumerate(values, start=2):
            ws.cell(r, j, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def layout(changes: dict[int, list] | None = None) -> dict[int, list]:
    rows = {
        3: ["1.고객정보"],
        5: ["이름", "가짜이름"],
        37: ["3.재무현황"],
        38: ["데이터를 내보낸 시점의 자산과 부채 상태를 분석합니다."],
        39: ["자산", None, None, None, "부채"],
        40: ["항목", "상품명", None, "금액", "항목", "상품명", None, "금액"],
        41: ["자유입출금 자산", "주거래 통장", None, 1_500_000, "장기대출", "마이너스 통장", None, 240_410],
        42: [None, "비상금 통장", None, 2_000_000],
        43: ["투자성 자산", "위탁 계좌", None, 3_000_000],
        44: ["연금 자산", "IRP", None, 0],
        45: ["총자산", None, None, 6_500_000, "총부채", None, None, 240_410],
        46: ["순자산"],
        47: [6_259_590],
        97: ["4.보험현황"],
        98: ["사용자가 보유한 보험상품 현황을 분석합니다."],
        99: ["금융사", "보험명", None, "계약상태", "총납입금", "계약일자", "만기일자"],
        100: ["가 손해보험", "실손보험", None, "유지", 1_200_000, D(2020, 5, 1), D(2040, 5, 1)],
        101: ["나 생명", "종신보험", None, "유지", 9_000_000, D(2019, 1, 15), None],
        102: ["총계", None, "보유 보험 상품", None, "총 납입금"],
        103: [None, None, 2, None, 10_200_000],
        107: ["5.투자현황"],
        108: ["사용자가 보유한 투자상품 현황을 분석합니다."],
        109: ["투자상품종류", "금융사", "상품명", None, "투자원금", "평가금액", "수익률", "가입일자", "만기일자"],
        110: ["주식", "가 증권", "위탁 계좌", None, 2_000_000, 1_898_238, -5.08808177164441],
        111: [None, "가 증권", "해외 주식", None, 1_000_000, 1_101_762, 10.1762],
        112: ["총계", None, "보유 투자 상품", None, "총 투자 원금", "총 평가 금액", "총 수익률"],
        113: [None, None, 2, None, 3_000_000, 3_000_000, 0.0],
        121: ["6.대출현황"],
        122: ["사용자가 보유한 대출상품 현황을 분석합니다."],
        123: ["대출종류", "금융사", "상품명", None, "대출원금", "대출잔액", "대출금리", "대출신규일", "대출만기일"],
        124: ["은행 대출", "가 은행", "마이너스 통장", None, 10_000_000, 240_410, 5.55, D(2026, 10, 13), D(2027, 10, 13)],
        125: ["총계", None, "보유 대출 상품", None, "총 대출 원금", "총 대출 잔액"],
        126: [None, None, 1, None, 10_000_000, 240_410],
    }  # fmt: skip
    rows.update(changes or {})
    return rows


@pytest.fixture
def tables():
    return read_status_tables(build(layout()))


def test_all_tables_found(tables):
    assert tables.found == {"asset", "liability", "insurance", "investment", "loan"}
    assert tables.warnings == []
    assert tables.ok and len(tables.checks) == 11


def test_finance(tables):
    assets = tables.of("asset")
    assert [(h.group, h.name, h.amount) for h in assets] == [
        ("자유입출금 자산", "주거래 통장", 1_500_000),
        ("자유입출금 자산", "비상금 통장", 2_000_000),  # 비어 있는 항목은 위 값을 이어받음
        ("투자성 자산", "위탁 계좌", 3_000_000),
        ("연금 자산", "IRP", 0),
    ]
    assert [(h.group, h.name, h.amount) for h in tables.of("liability")] == [
        ("장기대출", "마이너스 통장", 240_410)
    ]
    assert (tables.total_assets, tables.total_liabilities, tables.net_worth) == (
        6_500_000,
        240_410,
        6_259_590,
    )
    assert assets[0].row_no == 41


def test_insurance(tables):
    a, b = tables.of("insurance")
    assert (a.institution, a.name, a.status, a.amount) == (
        "가 손해보험",
        "실손보험",
        "유지",
        1_200_000,
    )
    assert (a.start_date, a.end_date, b.end_date) == ("2020-05-01", "2040-05-01", None)
    assert tables.totals["insurance"] == {"보유보험상품": 2, "총납입금": 10_200_000}


def test_investment(tables):
    a, b = tables.of("investment")
    assert (a.group, a.institution, a.principal, a.amount) == (
        "주식",
        "가 증권",
        2_000_000,
        1_898_238,
    )
    assert a.rate == pytest.approx(-5.08808177164441)  # % 숫자 그대로
    assert b.group == "주식"  # 위 값을 이어받음
    assert (a.start_date, a.end_date) == (None, None)


def test_loan(tables):
    (loan,) = tables.of("loan")
    assert (loan.group, loan.principal, loan.amount, loan.rate) == (
        "은행 대출",
        10_000_000,
        240_410,
        5.55,
    )
    assert (loan.start_date, loan.end_date) == ("2026-10-13", "2027-10-13")
    assert tables.totals["loan"] == {
        "보유대출상품": 1,
        "총대출원금": 10_000_000,
        "총대출잔액": 240_410,
    }


def test_customer_info_never_read(tables):
    assert all("가짜이름" not in (h.name, h.group, h.institution) for h in tables.holdings)


def test_checks_detect_mismatch():
    t = read_status_tables(
        build(layout({45: ["총자산", None, None, 6_400_000, "총부채", None, None, 240_410]}))
    )
    failed = [str(c) for c in t.checks if not c.ok]
    assert failed == [
        "총자산: 뱅샐 6,400,000 / 한지갑 6,500,000",
        "순자산: 뱅샐 6,259,590 / 한지갑 6,159,590",
    ]


def test_count_check():
    t = read_status_tables(build(layout({126: [None, None, 2, None, 10_000_000, 240_410]})))
    assert [str(c) for c in t.checks if not c.ok] == ["대출 보유대출상품: 뱅샐 2 / 한지갑 1"]


def test_columns_found_by_name_not_position():
    rows = layout()
    rows[123] = ["금융사", "대출종류", "상품명", None, "대출잔액", "대출원금", "대출금리"]
    rows[124] = ["가 은행", "은행 대출", "마이너스 통장", None, 240_410, 10_000_000, 5.55]
    rows[125] = ["총계", None, "보유 대출 상품", None, "총 대출 잔액", "총 대출 원금"]
    rows[126] = [None, None, 1, None, 240_410, 10_000_000]
    (loan,) = read_status_tables(build(rows)).of("loan")
    assert (loan.institution, loan.group, loan.principal, loan.amount) == (
        "가 은행",
        "은행 대출",
        10_000_000,
        240_410,
    )


def test_missing_table_warns():
    rows = {k: v for k, v in layout().items() if not 97 <= k <= 103}
    t = read_status_tables(build(rows))
    assert "insurance" not in t.found
    assert "뱅샐현황에서 보험 표를 찾지 못했어요" in t.warnings


def test_missing_column_warns():
    rows = layout()
    rows[99] = ["금융사", "보험명", None, None, "총납입금"]
    t = read_status_tables(build(rows))
    assert "보험현황 표에 없는 열: 계약상태, 계약일자, 만기일자" in t.warnings
    assert len(t.of("insurance")) == 2


def test_no_status_sheet():
    t = read_status_tables(build({1: ["x"]}, title="가계부 내역"))
    assert t.found == set() and "시트가 없어" in t.warnings[0]


def test_sample_fixture_has_no_tables(sample_bytes):
    t = read_status_tables(sample_bytes)
    assert t.found == set() and t.holdings == []


# --- 명령어 ---------------------------------------------------------------------


def test_inspect_prints_summary(isolated_env, sample_bytes, capsys):
    path = isolated_env / "2025-10-20~2026-10-20.xlsx"
    path.write_bytes(sample_bytes)
    assert cli.main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert "뱅샐현황    건너뜀 (뱅샐현황에서 자산 표를 찾지 못했어요)" in out
