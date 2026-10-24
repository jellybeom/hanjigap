"""누락 점검.

이미 정산한 달(정산 완료·기초 데이터)을 새 파일과 다시 비교해서,
늦게 들어온 거래나 사라진 거래가 있는지 알려준다. DB는 읽기만 하고, 자동으로 재정산하지 않는다.

시각이 바뀐 거래: 지문에는 시각이 들어가므로 시각만 바뀌어도 '사라짐 + 새로 생김'으로 보인다.
날짜·금액·결제수단·타입·내용이 같은 짝이면 같은 거래로 보고 '시각이 바뀐 거래'로 묶는다.
(예: 00:00이던 하이패스가 나중에 실제 시각으로 바뀌는 경우)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from hanjigap.db.members import Member
from hanjigap.importer.fingerprint import KeyedRow
from hanjigap.settle.pipeline import ParsedExport
from hanjigap.settle.preview import (
    DEFAULT_TZ,
    ChangedTx,
    StoredTx,
    parse_year_month,
    preview_month,
)

RECENT_MONTHS = 2


@dataclass(frozen=True)
class TimeShift:
    old: StoredTx
    new: KeyedRow
    has_override: bool  # 웹 수정 내용이 옛 지문에 붙어 있음 → 재정산하면 연결이 끊긴다


@dataclass
class MonthCheck:
    year_month: str
    status: str  # baseline | settled
    added: list[KeyedRow] = field(default_factory=list)
    removed: list[StoredTx] = field(default_factory=list)
    changed: list[ChangedTx] = field(default_factory=list)
    shifted: list[TimeShift] = field(default_factory=list)
    removed_with_override: set[str] = field(default_factory=set)

    @property
    def clean(self) -> bool:
        return not (self.added or self.removed or self.changed or self.shifted)

    @property
    def warnings(self) -> list[str]:
        out = []
        cut = [s for s in self.shifted if s.has_override]
        if cut:
            out.append(
                f"{self.year_month}: 시각이 바뀐 거래 {len(cut)}건에 웹 수정 내용이 있습니다. "
                "재정산하면 연결이 끊깁니다 (고친 내용은 남습니다)"
            )
        gone = [s for s in self.removed if s.tx_key in self.removed_with_override]
        if gone:
            out.append(f"{self.year_month}: 사라진 거래 {len(gone)}건에 웹 수정 내용이 있습니다")
        return out


def _shift_ym(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _settled_months(conn: sqlite3.Connection, member_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT year_month, status FROM month_settlements WHERE member_id = ? ORDER BY year_month",
        (member_id,),
    )
    return {r["year_month"]: r["status"] for r in rows}


def _complete_in_file(parsed: ParsedExport) -> set[str]:
    period = parsed.summary.period
    if period is None:
        return set()
    return {f"{y:04d}-{m:02d}" for y, m in period.months() if not period.is_partial(y, m)}


def check_month(
    conn: sqlite3.Connection,
    member: Member,
    parsed: ParsedExport,
    year_month: str,
    status: str,
    *,
    tz: ZoneInfo = DEFAULT_TZ,
) -> MonthCheck:
    preview = preview_month(conn, member, parsed, year_month, tz=tz)
    added, removed, shifted = _pair_time_shifts(preview.added, preview.removed)
    keyed_overrides = preview.removed_with_override
    return MonthCheck(
        year_month=preview.year_month,
        status=status,
        added=added,
        removed=removed,
        changed=list(preview.changed),
        shifted=[
            TimeShift(old, new, has_override=old.tx_key in keyed_overrides) for old, new in shifted
        ],
        removed_with_override=keyed_overrides,
    )


def check_recent(
    conn: sqlite3.Connection,
    member: Member,
    parsed: ParsedExport,
    target_month: str,
    *,
    months_back: int = RECENT_MONTHS,
    tz: ZoneInfo = DEFAULT_TZ,
) -> list[MonthCheck]:
    """정산 대상 월 바로 앞의 최대 months_back개월 중, DB에 있고 파일에 완전히 들어 있는 달."""
    year, month = parse_year_month(target_month)
    settled = _settled_months(conn, member.id)
    complete = _complete_in_file(parsed)
    out = []
    for back in range(1, months_back + 1):
        y, m = _shift_ym(year, month, -back)
        ym = f"{y:04d}-{m:02d}"
        if ym in settled and ym in complete:
            out.append(check_month(conn, member, parsed, ym, settled[ym], tz=tz))
    return out


def check_all(
    conn: sqlite3.Connection, member: Member, parsed: ParsedExport, *, tz: ZoneInfo = DEFAULT_TZ
) -> list[MonthCheck]:
    """DB에 있는 달 중 파일에 완전히 들어 있는 모든 달 (최근 달부터)."""
    settled = _settled_months(conn, member.id)
    complete = _complete_in_file(parsed)
    months = sorted((ym for ym in settled if ym in complete), reverse=True)
    return [check_month(conn, member, parsed, ym, settled[ym], tz=tz) for ym in months]


def _same_except_time(old: StoredTx, new: KeyedRow) -> bool:
    r = new.row
    return (
        old.tx_date == r.date.isoformat()
        and old.amount == r.amount
        and old.instrument == r.instrument
        and old.tx_type == r.tx_type
        and old.content == r.content
        and old.tx_time != r.time.isoformat(timespec="seconds")
    )


def _pair_time_shifts(
    added: list[KeyedRow], removed: list[StoredTx]
) -> tuple[list[KeyedRow], list[StoredTx], list[tuple[StoredTx, KeyedRow]]]:
    left_added = list(added)
    left_removed: list[StoredTx] = []
    pairs: list[tuple[StoredTx, KeyedRow]] = []
    for old in sorted(removed, key=lambda s: (s.tx_date, s.tx_time)):
        match = next((k for k in left_added if _same_except_time(old, k)), None)
        if match is None:
            left_removed.append(old)
        else:
            left_added.remove(match)
            pairs.append((old, match))
    return left_added, left_removed, pairs
