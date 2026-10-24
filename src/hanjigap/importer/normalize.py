"""가계부 행 정규화와 값 검증.

B2(sheet)가 넘겨준 원래 값을 검사해서, 바뀌지 않는 거래 객체(LedgerRow)로 정리한다.

- 첫 오류에서 멈추지 않고 모든 행을 검사해 오류를 한꺼번에 모은다.
- 오류가 하나라도 있으면 그 파일은 다음 단계로 넘기지 않는다(호출하는 쪽에서 result.ok 확인).
- 오류·경고 메시지에는 문제가 된 값만 넣고 내용(가맹점명)은 넣지 않는다.
  이 메시지는 화면에만 보여주고 로그에는 건수만 남긴다.
- 날짜·시각은 모두 한국 시간 기준이며 시간대 정보 없이 다룬다.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from openpyxl.utils.datetime import from_excel

from hanjigap.importer.sheet import LedgerSheet

TxType = Literal["수입", "지출", "이체"]
TX_TYPES: frozenset[str] = frozenset({"수입", "지출", "이체"})
DEFAULT_CURRENCY = "KRW"
MIDNIGHT = dt.time(0, 0)

_DATE_RE = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_AMOUNT_JUNK = re.compile(r"[,\s원₩]")


@dataclass(frozen=True, slots=True)
class LedgerRow:
    row_no: int  # 엑셀 행 번호
    date: dt.date
    time: dt.time
    tx_type: TxType
    category: str
    subcategory: str
    content: str
    amount: int  # 부호 유지 (지출 음수, 수입 양수, 취소·환불은 지출 양수)
    currency: str
    instrument: str
    memo: str | None

    @property
    def occurred_at(self) -> dt.datetime:
        return dt.datetime.combine(self.date, self.time)


@dataclass(frozen=True, slots=True)
class RowIssue:
    row_no: int
    column: str
    message: str

    def __str__(self) -> str:
        return f"{self.row_no}행 {self.column}: {self.message}"


@dataclass
class NormalizeResult:
    rows: list[LedgerRow] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)
    errors: list[RowIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _Invalid(Exception):
    """한 칸의 값이 잘못됐을 때 (내부용)."""


def normalize(ledger: LedgerSheet) -> NormalizeResult:
    result = NormalizeResult()
    for row_no, record in ledger.iter_rows():
        row = _normalize_row(row_no, record, result)
        if row is not None:
            result.rows.append(row)
    return result


def _normalize_row(
    row_no: int, record: dict[str, Any], result: NormalizeResult
) -> LedgerRow | None:
    errors_before = len(result.errors)

    def check(column: str, parser, value):
        try:
            return parser(value)
        except _Invalid as exc:
            result.errors.append(RowIssue(row_no, column, str(exc)))
            return None

    date = check("날짜", _parse_date, record["날짜"])
    time = check("시간", _parse_time, record["시간"])
    tx_type = check("타입", _parse_type, record["타입"])
    amount = check("금액", _parse_amount, record["금액"])
    currency = check("화폐", _parse_currency, record["화폐"])

    if len(result.errors) > errors_before:
        return None

    if time is None:
        time = MIDNIGHT
        result.warnings.append(RowIssue(row_no, "시간", "비어 있어 00:00으로 처리"))
    if amount == 0:
        result.warnings.append(RowIssue(row_no, "금액", "0원"))
    if tx_type == "수입" and amount < 0:
        result.warnings.append(RowIssue(row_no, "금액", "수입인데 음수"))

    return LedgerRow(
        row_no=row_no,
        date=date,
        time=time,
        tx_type=tx_type,
        category=_text(record["대분류"]),
        subcategory=_text(record["소분류"]),
        content=_text(record["내용"]),
        amount=amount,
        currency=currency,
        instrument=_text(record["결제수단"]),
        memo=_text(record["메모"]) or None,
    )


# --- 열별 해석 -----------------------------------------------------------------


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_date(value: Any) -> dt.date:
    if _is_blank(value):
        raise _Invalid("비어 있습니다")
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        try:
            return from_excel(value).date()  # 서식 없는 엑셀 날짜 일련번호
        except (ValueError, OverflowError, TypeError) as exc:
            raise _Invalid(f"날짜로 해석할 수 없습니다 ({value!r})") from exc
    if isinstance(value, str):
        m = _DATE_RE.match(value.strip())
        if m:
            try:
                return dt.date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                pass
    raise _Invalid(f"날짜로 해석할 수 없습니다 ({value!r})")


def _parse_time(value: Any) -> dt.time | None:
    """비어 있으면 None (호출하는 쪽에서 00:00 + 경고)."""
    if _is_blank(value):
        return None
    if isinstance(value, dt.datetime):
        return value.time()
    if isinstance(value, dt.time):
        return value
    if isinstance(value, dt.timedelta) and dt.timedelta(0) <= value < dt.timedelta(days=1):
        return (dt.datetime.min + value).time()
    if isinstance(value, float) and 0 <= value < 1:  # 서식 없는 엑셀 시각(하루의 비율)
        seconds = round(value * 86400)
        return (dt.datetime.min + dt.timedelta(seconds=min(seconds, 86399))).time()
    if isinstance(value, str):
        m = _TIME_RE.match(value.strip())
        if m:
            try:
                return dt.time(int(m[1]), int(m[2]), int(m[3] or 0))
            except ValueError:
                pass
    raise _Invalid(f"시각으로 해석할 수 없습니다 ({value!r})")


def _parse_type(value: Any) -> TxType:
    text = _text(value)
    if text not in TX_TYPES:
        raise _Invalid(f"수입·지출·이체 중 하나여야 합니다 ({text!r})")
    return text  # type: ignore[return-value]


def _parse_amount(value: Any) -> int:
    if _is_blank(value):
        raise _Invalid("비어 있습니다")
    if isinstance(value, bool):
        raise _Invalid(f"숫자가 아닙니다 ({value!r})")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise _Invalid(f"숫자가 아닙니다 ({value!r})")
        if not value.is_integer():
            raise _Invalid(f"원 단위 아래 소수가 있습니다 ({value!r})")
        return int(value)
    if isinstance(value, str):
        text = _AMOUNT_JUNK.sub("", value).replace("−", "-")
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return _parse_amount(float(text))
        except ValueError:
            pass
    raise _Invalid(f"숫자가 아닙니다 ({value!r})")


def _parse_currency(value: Any) -> str:
    if _is_blank(value):
        return DEFAULT_CURRENCY
    code = _text(value).upper()
    if code != DEFAULT_CURRENCY:
        raise _Invalid(f"원화(KRW)가 아닌 거래입니다 ({code!r})")
    return code


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# --- 표시용 ---------------------------------------------------------------------


def group_issues(issues: Iterable[RowIssue], max_rows: int = 10) -> list[str]:
    """같은 종류의 경고를 묶는다. 예: '시간: 비어 있어 00:00으로 처리 3건 (12, 45, 78행)'"""
    groups: dict[tuple[str, str], list[int]] = {}
    for issue in issues:
        groups.setdefault((issue.column, issue.message), []).append(issue.row_no)
    lines = []
    for (column, message), rows in groups.items():
        shown = ", ".join(str(r) for r in rows[:max_rows])
        more = f" 외 {len(rows) - max_rows}건" if len(rows) > max_rows else ""
        lines.append(f"{column}: {message} {len(rows)}건 ({shown}행{more})")
    return lines
