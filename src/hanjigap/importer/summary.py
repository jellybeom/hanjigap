"""뱅샐 파일 기간과 월별 요약.

뱅샐 타입 기준의 단순 합계다. 수입·지출·이체 분류 규칙은 아직 적용하지 않는다.
요약 숫자도 금융 정보이므로 화면에만 보여주고 로그에는 남기지 않는다.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from hanjigap.importer.normalize import LedgerRow

_PERIOD_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s*~\s*(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date
    source: Literal["파일 이름", "거래 날짜"]

    def months(self) -> list[tuple[int, int]]:
        out, (y, m) = [], (self.start.year, self.start.month)
        while (y, m) <= (self.end.year, self.end.month):
            out.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out

    def is_partial(self, year: int, month: int) -> bool:
        """시작일이 1일이 아닌 첫 달, 종료일이 말일이 아닌 마지막 달."""
        if (year, month) == (self.start.year, self.start.month) and self.start.day != 1:
            return True
        last_day = calendar.monthrange(year, month)[1]
        return (year, month) == (self.end.year, self.end.month) and self.end.day != last_day


@dataclass
class MonthSummary:
    year: int
    month: int
    partial: bool
    count: int = 0
    income: int = 0  # 수입 합
    expense: int = 0  # 지출 합을 양수로 (결제 − 취소·환불)
    transfer_in: int = 0
    transfer_out: int = 0  # 양수로

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def add(self, row: LedgerRow) -> None:
        self.count += 1
        if row.tx_type == "수입":
            self.income += row.amount
        elif row.tx_type == "지출":
            self.expense -= row.amount
        elif row.amount >= 0:
            self.transfer_in += row.amount
        else:
            self.transfer_out -= row.amount


@dataclass
class FileSummary:
    period: Period | None
    months: list[MonthSummary]
    total: MonthSummary
    outside_period: int = 0  # 파일 기간 밖의 거래 수 (보통 0)
    warnings: list[str] = field(default_factory=list)

    @property
    def partial_months(self) -> list[str]:
        return [m.label for m in self.months if m.partial]

    @property
    def complete_months(self) -> list[str]:
        return [m.label for m in self.months if not m.partial]


def parse_period(name: str | None) -> Period | None:
    """'2025-10-20~2026-10-20.xlsx' 같은 이름에서 내보내기 기간을 읽는다."""
    if not name:
        return None
    m = _PERIOD_RE.search(name)
    if not m:
        return None
    try:
        start = dt.date(int(m[1]), int(m[2]), int(m[3]))
        end = dt.date(int(m[4]), int(m[5]), int(m[6]))
    except ValueError:
        return None
    return Period(start, end, "파일 이름") if start <= end else None


def summarize(rows: Sequence[LedgerRow], period: Period | None = None) -> FileSummary:
    if period is None and rows:
        dates = [r.date for r in rows]
        period = Period(min(dates), max(dates), "거래 날짜")

    months: dict[tuple[int, int], MonthSummary] = {}
    if period:
        for y, m in period.months():
            months[(y, m)] = MonthSummary(y, m, partial=period.is_partial(y, m))

    total = MonthSummary(0, 0, partial=False)
    outside = 0
    for row in rows:
        key = (row.date.year, row.date.month)
        if period and not (period.start <= row.date <= period.end):
            outside += 1
        if key not in months:  # 기간 밖 거래가 있는 달
            months[key] = MonthSummary(*key, partial=True)
        months[key].add(row)
        total.add(row)

    warnings = []
    if outside:
        warnings.append(f"파일 기간 밖의 거래가 {outside:,}건 있습니다")
    return FileSummary(
        period=period,
        months=[months[k] for k in sorted(months)],
        total=total,
        outside_period=outside,
        warnings=warnings,
    )
