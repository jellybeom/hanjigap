"""첫 적재.

처음 한 번, 파일 하나로 과거 완전한 달들은 '기초 데이터'로, 정산 대상 월은 '정산 완료'로 넣는다.

- 그 구성원에게 정산 기록이 하나도 없을 때만 실행된다.
- 정산 대상 월보다 뒤의 달, 파일에 일부만 있는 달은 넣지 않는다.
- 모든 달을 하나의 트랜잭션으로 저장한다. 하나라도 실패하면 아무것도 들어가지 않는다.
- 업로드 기록은 1건만 만들고 모든 달이 그 기록을 가리킨다.
- 실제 쓰기는 일반 정산과 같은 write_month()를 쓴다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Literal
from zoneinfo import ZoneInfo

from hanjigap.db.members import Member
from hanjigap.settle import apply as _apply
from hanjigap.settle.pipeline import ParsedExport
from hanjigap.settle.preview import (
    DEFAULT_TZ,
    MonthPreview,
    PreviewError,
    check_period,
    parse_year_month,
    preview_month,
)


class FirstLoadError(Exception):
    pass


@dataclass(frozen=True)
class PlannedMonth:
    year_month: str
    status: Literal["baseline", "settled"]
    preview: MonthPreview

    @property
    def count(self) -> int:
        return len(self.preview.rows)


@dataclass
class FirstLoadPlan:
    member: Member
    target_month: str
    months: list[PlannedMonth]
    excluded: list[tuple[str, str]] = field(default_factory=list)  # (연월, 이유)

    @property
    def total_rows(self) -> int:
        return sum(m.count for m in self.months)

    @property
    def baseline_count(self) -> int:
        return sum(1 for m in self.months if m.status == "baseline")


@dataclass(frozen=True)
class FirstLoadResult:
    batch_id: int
    plan: FirstLoadPlan


def ensure_first_load_allowed(conn: sqlite3.Connection, member: Member) -> None:
    """그 구성원에게 정산 기록이 하나도 없어야 한다."""
    row = conn.execute(
        "SELECT 1 FROM month_settlements WHERE member_id = ? LIMIT 1", (member.id,)
    ).fetchone()
    if row is not None:
        raise FirstLoadError(
            f"{member.display_name}에게는 이미 정산 기록이 있습니다. "
            "첫 적재는 처음 한 번만 할 수 있습니다. 매월 정산은 'settle apply'를 쓰세요"
        )


def plan_first_load(
    conn: sqlite3.Connection,
    member: Member,
    parsed: ParsedExport,
    target_month: str,
    *,
    tz: ZoneInfo = DEFAULT_TZ,
) -> FirstLoadPlan:
    """어떤 달을 어떤 상태로 넣을지 계산한다. DB는 바꾸지 않는다."""
    ensure_first_load_allowed(conn, member)
    year, month = parse_year_month(target_month)
    target = f"{year:04d}-{month:02d}"
    period = parsed.summary.period
    check_period(period, year, month)  # 정산 대상 월은 반드시 완전한 달

    months: list[PlannedMonth] = []
    excluded: list[tuple[str, str]] = []
    for y, m in period.months():
        ym = f"{y:04d}-{m:02d}"
        if period.is_partial(y, m):
            excluded.append((ym, "일부만 있음"))
            continue
        if ym > target:
            excluded.append((ym, "정산 대상 이후"))
            continue
        status: Literal["baseline", "settled"] = "settled" if ym == target else "baseline"
        try:
            preview = preview_month(conn, member, parsed, ym, tz=tz)
        except PreviewError as exc:  # 이론상 없음 (위에서 걸렀음)
            raise FirstLoadError(str(exc)) from exc
        months.append(PlannedMonth(ym, status, preview))
    return FirstLoadPlan(member=member, target_month=target, months=months, excluded=excluded)


def first_load(
    conn: sqlite3.Connection,
    member: Member,
    parsed: ParsedExport,
    target_month: str,
    *,
    uploaded_by: int | None = None,
    tz: ZoneInfo = DEFAULT_TZ,
) -> FirstLoadResult:
    conn.execute("BEGIN IMMEDIATE")
    try:
        plan = plan_first_load(conn, member, parsed, target_month, tz=tz)
        batch_id = _apply.insert_batch(conn, member, parsed, uploaded_by)
        _apply.insert_cashflow(conn, member, batch_id, parsed)
        for planned in plan.months:
            _apply.write_month(conn, member, planned.preview, batch_id, planned.status)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return FirstLoadResult(batch_id=batch_id, plan=plan)
