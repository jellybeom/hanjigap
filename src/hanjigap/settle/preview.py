"""정산 미리보기.

정산할 월의 파일 거래와 DB 거래를 지문으로 비교해서 신규·변경·삭제·동일을 알려준다.
DB는 읽기만 하고 절대 바꾸지 않는다.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal
from zoneinfo import ZoneInfo

from hanjigap.db.members import Member
from hanjigap.importer.fingerprint import KeyedRow
from hanjigap.importer.summary import MonthSummary, Period, parse_period
from hanjigap.settle.pipeline import ParsedExport

# 지문에 들어가지 않아서 같은 지문이어도 바뀔 수 있는 항목 (DB 열, 화면 이름)
COMPARED_FIELDS: tuple[tuple[str, str], ...] = (
    ("tx_type", "타입"),
    ("category", "대분류"),
    ("subcategory", "소분류"),
    ("content", "내용"),
    ("memo", "메모"),
    ("currency", "화폐"),
)
DEFAULT_TZ = ZoneInfo("Asia/Seoul")
OWNER_CHECK_MIN_ROWS = 5  # 이보다 적으면 소유자 추정을 하지 않는다

_YM_RE = re.compile(r"^(\d{4})-(\d{2})$")


class PreviewError(Exception):
    pass


@dataclass(frozen=True)
class StoredTx:
    tx_key: str
    tx_date: str
    tx_time: str
    tx_type: str
    category: str
    subcategory: str
    content: str
    amount: int
    instrument: str
    memo: str | None
    currency: str


@dataclass(frozen=True)
class FieldChange:
    label: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ChangedTx:
    stored: StoredTx
    new: KeyedRow
    changes: tuple[FieldChange, ...]


@dataclass(frozen=True)
class MonthState:
    status: Literal["baseline", "settled"]
    settled_at: str  # UTC ISO


@dataclass
class MonthPreview:
    member: Member
    year_month: str
    previous: MonthState | None
    month: MonthSummary
    rows: list[KeyedRow]  # 이 달의 파일 거래
    added: list[KeyedRow] = field(default_factory=list)
    changed: list[ChangedTx] = field(default_factory=list)
    removed: list[StoredTx] = field(default_factory=list)
    unchanged: int = 0
    removed_with_override: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    @property
    def kind(self) -> Literal["new", "resettle", "baseline_to_settled"]:
        if self.previous is None:
            return "new"
        return "baseline_to_settled" if self.previous.status == "baseline" else "resettle"

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


# --- 월 계산 ------------------------------------------------------------------


def local_date(utc_iso: str, tz: ZoneInfo = DEFAULT_TZ) -> str:
    """DB의 UTC 기록 시각을 한국 날짜로."""
    stamp = dt.datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    return stamp.astimezone(tz).date().isoformat()


def previous_month(today: dt.date) -> str:
    first = today.replace(day=1)
    last_month = first - dt.timedelta(days=1)
    return f"{last_month.year:04d}-{last_month.month:02d}"


def parse_year_month(value: str) -> tuple[int, int]:
    m = _YM_RE.match(value.strip())
    if not m or not 1 <= int(m[2]) <= 12:
        raise PreviewError(f"월은 YYYY-MM 형식이어야 합니다: {value}")
    return int(m[1]), int(m[2])


def check_period(period: Period | None, year: int, month: int) -> None:
    """정산 가능한 달인지 (파일 기간 안, 완전한 달) 확인한다."""
    ym = f"{year:04d}-{month:02d}"
    if period is None:
        raise PreviewError("파일에 거래가 없어 기간을 알 수 없습니다")
    if (year, month) not in period.months():
        raise PreviewError(f"{ym}은 파일 기간({period.start} ~ {period.end})에 없습니다")
    if period.is_partial(year, month):
        last = calendar.monthrange(year, month)[1]
        start = max(period.start, dt.date(year, month, 1))
        end = min(period.end, dt.date(year, month, last))
        raise PreviewError(
            f"{ym}은 파일에 일부({start:%m-%d} ~ {end:%m-%d})만 있습니다. "
            "그 달이 끝난 뒤 내보낸 파일로 정산하세요"
        )


def precheck_month(file_name: str | None, year_month: str) -> None:
    """비밀번호를 묻기 전에, 파일 이름에 적힌 기간으로 먼저 확인한다.

    이름에서 기간을 읽을 수 없으면 아무것도 하지 않는다(파일을 연 뒤 다시 확인).
    """
    year, month = parse_year_month(year_month)
    period = parse_period(file_name)
    if period is not None:
        check_period(period, year, month)


def _check_month(parsed: ParsedExport, year: int, month: int) -> MonthSummary:
    check_period(parsed.summary.period, year, month)
    return next(m for m in parsed.summary.months if (m.year, m.month) == (year, month))


# --- 미리보기 -----------------------------------------------------------------


def preview_month(
    conn: sqlite3.Connection,
    member: Member,
    parsed: ParsedExport,
    year_month: str,
    *,
    tz: ZoneInfo = DEFAULT_TZ,
) -> MonthPreview:
    year, month = parse_year_month(year_month)
    ym = f"{year:04d}-{month:02d}"
    month_summary = _check_month(parsed, year, month)
    rows = [k for k in parsed.keyed if (k.row.date.year, k.row.date.month) == (year, month)]

    preview = MonthPreview(
        member=member,
        year_month=ym,
        previous=_month_state(conn, member.id, ym),
        month=month_summary,
        rows=rows,
    )

    stored = {s.tx_key: s for s in _stored_rows(conn, member.id, ym)}
    in_file = {k.tx_key: k for k in rows}
    for key, new in in_file.items():
        old = stored.get(key)
        if old is None:
            preview.added.append(new)
            continue
        changes = _diff(old, new)
        if changes:
            preview.changed.append(ChangedTx(old, new, changes))
        else:
            preview.unchanged += 1
    preview.removed = [s for k, s in stored.items() if k not in in_file]
    preview.removed.sort(key=lambda s: (s.tx_date, s.tx_time), reverse=True)

    if preview.removed:
        preview.removed_with_override = _keys_with_override(
            conn, member.id, [s.tx_key for s in preview.removed]
        )
        if preview.removed_with_override:
            preview.warnings.append(
                f"웹에서 고친 거래 {len(preview.removed_with_override)}건이 삭제 대상입니다 "
                "(고친 내용은 지워지지 않고 남습니다)"
            )

    preview.warnings += _duplicate_upload_warnings(conn, member, parsed.file_sha256, tz)
    owner_warning = _owner_warning(conn, member, ym, list(in_file))
    if owner_warning:
        preview.warnings.append(owner_warning)
    return preview


def _diff(old: StoredTx, new: KeyedRow) -> tuple[FieldChange, ...]:
    out = []
    for col, label in COMPARED_FIELDS:
        before = getattr(old, col)
        after = getattr(new.row, col)
        if (before or None) != (after or None):
            out.append(FieldChange(label, before, after))
    return tuple(out)


def _month_state(conn: sqlite3.Connection, member_id: int, ym: str) -> MonthState | None:
    row = conn.execute(
        "SELECT status, settled_at FROM month_settlements WHERE member_id = ? AND year_month = ?",
        (member_id, ym),
    ).fetchone()
    return MonthState(row["status"], row["settled_at"]) if row else None


def _stored_rows(conn: sqlite3.Connection, member_id: int, ym: str) -> list[StoredTx]:
    rows = conn.execute(
        "SELECT tx_key, tx_date, tx_time, tx_type, category, subcategory, content, amount, "
        "instrument, memo, currency FROM transactions WHERE member_id = ? AND year_month = ?",
        (member_id, ym),
    )
    return [StoredTx(*tuple(r)) for r in rows]


def _keys_with_override(conn: sqlite3.Connection, member_id: int, keys: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(keys), 500):
        chunk = keys[i : i + 500]
        marks = ",".join("?" * len(chunk))
        found |= {
            r[0]
            for r in conn.execute(
                f"SELECT tx_key FROM tx_overrides WHERE member_id = ? AND tx_key IN ({marks})",
                (member_id, *chunk),
            )
        }
    return found


def _duplicate_upload_warnings(
    conn: sqlite3.Connection, member: Member, sha: str, tz: ZoneInfo
) -> list[str]:
    rows = conn.execute(
        "SELECT b.member_id, b.uploaded_at, m.display_name FROM import_batches b "
        "JOIN members m ON m.id = b.member_id "
        "WHERE b.file_sha256 = ? AND b.status = 'applied' ORDER BY b.uploaded_at DESC",
        (sha,),
    ).fetchall()
    warnings = []
    mine = [r for r in rows if r["member_id"] == member.id]
    others = [r for r in rows if r["member_id"] != member.id]
    if mine:
        warnings.append(
            f"이 파일은 {local_date(mine[0]['uploaded_at'], tz)}에 이미 올린 파일입니다"
        )
    if others:
        warnings.append(f"같은 파일이 {others[0]['display_name']}의 파일로 이미 올라가 있습니다")
    return warnings


def _owner_warning(
    conn: sqlite3.Connection, member: Member, ym: str, keys: list[str]
) -> str | None:
    """이 달 거래의 절반 이상이 다른 구성원의 지문과 같으면 소유자 실수를 의심한다."""
    if len(keys) < OWNER_CHECK_MIN_ROWS:
        return None
    best_name, best = None, 0
    for other in conn.execute(
        "SELECT id, display_name FROM members WHERE id != ?", (member.id,)
    ).fetchall():
        matched = 0
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            marks = ",".join("?" * len(chunk))
            matched += conn.execute(
                f"SELECT count(*) FROM transactions WHERE member_id = ? AND year_month = ? "
                f"AND tx_key IN ({marks})",
                (other["id"], ym, *chunk),
            ).fetchone()[0]
        if matched > best:
            best_name, best = other["display_name"], matched
    if best * 2 >= len(keys):
        return (
            f"이 달 거래 {len(keys)}건 중 {best}건이 {best_name}의 거래와 같습니다. "
            f"{best_name}의 파일일 수 있습니다"
        )
    return None
