"""뱅샐현황 시트의 '현금흐름현황' 표 읽기와 대조.

뱅샐이 직접 계산한 월별·항목별 합계를 읽어서, 한지갑 계산(뱅샐 타입 + 대분류 기준)과 비교한다.

표 구조 (2026년 10월 기준)
    헤더 행: '항목' 칸과 'YYYY-MM' 형식의 월 칸들
    '월수입 총계' 위의 항목 = 수입, '월수입 총계'와 '월지출 총계' 사이 = 지출
    총계 행은 수식이라 계산된 값이 저장돼 있지 않다 → 읽지 않고, 필요하면 항목을 더한다.

알려진 뱅샐 표시 문제
    그 달 대분류의 순지출이 음수(환불 > 지출)이면 뱅샐은 부호를 버리고 절댓값을 표시한다.
    이 경우는 '부호 표시 차이'로 따로 세고 정상으로 취급한다.

이 표는 참고 자료다. 못 읽어도 정산은 막지 않고 경고만 남긴다.
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from openpyxl import load_workbook

from hanjigap.importer.normalize import LedgerRow

STATUS_SHEET = "뱅샐현황"
INCOME_TOTAL = "월수입 총계"
EXPENSE_TOTAL = "월지출 총계"
HEADER_SEARCH_ROWS = 60
_YM_RE = re.compile(r"^\d{4}-\d{2}$")

Kind = Literal["income", "expense"]


@dataclass(frozen=True)
class CashflowItem:
    item: str
    kind: Kind
    amounts: dict[str, int]  # 연월 → 금액 (뱅샐 표시 그대로, 지출도 양수)


@dataclass(frozen=True)
class Cashflow:
    months: list[str]
    items: list[CashflowItem]

    def rows(self) -> list[tuple[str, Kind, str, int]]:
        """(연월, 종류, 항목, 금액) — DB 저장용"""
        return [
            (ym, it.kind, it.item, it.amounts.get(ym, 0)) for it in self.items for ym in self.months
        ]


@dataclass(frozen=True)
class Mismatch:
    year_month: str
    kind: Kind
    item: str
    banksalad: int | None  # None = 뱅샐 표에 없는 항목
    ours: int

    def __str__(self) -> str:
        bs = "표에 없음" if self.banksalad is None else f"{self.banksalad:,}"
        return f"{self.year_month} {self.item}: 뱅샐 {bs} / 한지갑 {self.ours:,}"


@dataclass
class CashflowComparison:
    months: int
    items: int
    matched: int = 0
    sign_only: int = 0  # 뱅샐 절댓값 표시 문제
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


# --- 읽기 ----------------------------------------------------------------------


def read_cashflow(xlsx: bytes) -> tuple[Cashflow | None, list[str]]:
    """(현금흐름 표, 경고). 표를 못 찾으면 (None, [이유])."""
    try:
        wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    except Exception:
        return None, ["엑셀을 읽을 수 없어 뱅샐 대조를 건너뜁니다"]
    try:
        if STATUS_SHEET not in wb.sheetnames:
            return None, [f"'{STATUS_SHEET}' 시트가 없어 뱅샐 대조를 건너뜁니다"]
        rows = list(wb[STATUS_SHEET].iter_rows(values_only=True))
    finally:
        wb.close()
    return _parse_table(rows)


def _parse_table(rows: list[tuple[Any, ...]]) -> tuple[Cashflow | None, list[str]]:
    header = None
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        cells = [str(v).strip() if v is not None else "" for v in row]
        if "항목" in cells and any(_YM_RE.match(c) for c in cells):
            header = i, cells
            break
    if header is None:
        return None, ["현금흐름현황 표를 찾을 수 없어 뱅샐 대조를 건너뜁니다"]

    start, cells = header
    item_col = cells.index("항목")
    month_cols = [(j, c) for j, c in enumerate(cells) if _YM_RE.match(c)]

    items: list[CashflowItem] = []
    kind: Kind | None = "income"
    for row in rows[start + 1 :]:
        name = row[item_col] if item_col < len(row) else None
        name = str(name).strip() if name is not None else ""
        if not name:
            break  # 표 끝
        if name == INCOME_TOTAL:
            kind = "expense"
            continue
        if name == EXPENSE_TOTAL:
            kind = None
            continue
        if kind is None:  # 순수입 총계 등 지출 총계 아래 행
            continue
        amounts = {ym: _to_int(row[j] if j < len(row) else None) for j, ym in month_cols}
        items.append(CashflowItem(name, kind, amounts))

    if kind == "income":
        return None, [f"현금흐름현황 표에 '{INCOME_TOTAL}' 행이 없어 뱅샐 대조를 건너뜁니다"]
    return Cashflow(months=[ym for _, ym in month_cols], items=items), []


def _to_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    text = str(value).replace(",", "").strip()
    if not text or text.startswith("="):  # 계산값 없는 수식
        return 0
    try:
        return round(float(text))
    except ValueError:
        return 0


# --- 대조 ----------------------------------------------------------------------


def our_cashflow(rows: Sequence[LedgerRow]) -> dict[tuple[str, Kind, str], int]:
    """한지갑 계산: 수입은 대분류별 합, 지출은 대분류별 합을 양수로 (결제 − 취소·환불)."""
    out: dict[tuple[str, Kind, str], int] = defaultdict(int)
    for r in rows:
        ym = f"{r.date.year:04d}-{r.date.month:02d}"
        if r.tx_type == "수입":
            out[(ym, "income", r.category)] += r.amount
        elif r.tx_type == "지출":
            out[(ym, "expense", r.category)] -= r.amount
    return out


def compare_cashflow(cashflow: Cashflow, rows: Sequence[LedgerRow]) -> CashflowComparison:
    ours = our_cashflow(rows)
    result = CashflowComparison(months=len(cashflow.months), items=len(cashflow.items))
    listed = {(it.kind, it.item) for it in cashflow.items}

    for it in cashflow.items:
        for ym in cashflow.months:
            bs = it.amounts.get(ym, 0)
            mine = ours.get((ym, it.kind, it.item), 0)
            if bs == mine:
                result.matched += 1
            elif mine < 0 and bs == -mine:
                result.sign_only += 1
            else:
                result.mismatches.append(Mismatch(ym, it.kind, it.item, bs, mine))

    months = set(cashflow.months)
    for (ym, kind, item), mine in sorted(ours.items()):
        if ym in months and (kind, item) not in listed and mine != 0:
            result.mismatches.append(Mismatch(ym, kind, item, None, mine))
    return result
