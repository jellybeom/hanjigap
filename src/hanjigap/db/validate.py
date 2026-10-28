"""사람이 입력한 값 검사 (계좌·카드·고정비 등 공용).

원칙
    자유 입력 칸은 공백·-·.·/를 뺐을 때 숫자가 10자리 넘게 이어지면 거부한다.
    (전체 계좌·카드번호 방지)
    빈 문자열은 '지우기'로 본다. (선택 칸)
    종류 값은 영어 코드로 저장하지만 한글 이름으로도 받는다.
"""

from __future__ import annotations

import datetime as dt
import re

_SEPARATORS_RE = re.compile(r"[\s\-./]")
_LONG_NUMBER_RE = re.compile(r"\d{10,}")
_LAST4_RE = re.compile(r"^\d{4}$")
_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_YM_RE = re.compile(r"^\d{4}-\d{2}$")
TEXT_MAX = 100


class InputError(Exception):
    """입력값이 올바르지 않음. 메시지는 그대로 사용자에게 보여 준다."""


def now_utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def required(value: str | None, label: str) -> str:
    value = (value or "").strip()
    if not value:
        raise InputError(f"{label}을(를) 입력하세요")
    if len(value) > TEXT_MAX:
        raise InputError(f"{label}은(는) {TEXT_MAX}자 이하로 입력하세요")
    reject_long_number(value, label)
    return value


def optional_text(value: str | None, label: str) -> str | None:
    """빈 문자열은 '지우기'(None)로 본다."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > TEXT_MAX:
        raise InputError(f"{label}은(는) {TEXT_MAX}자 이하로 입력하세요")
    reject_long_number(value, label)
    return value


def reject_long_number(value: str, label: str) -> None:
    if _LONG_NUMBER_RE.search(_SEPARATORS_RE.sub("", value)):
        raise InputError(
            f"{label}에 전체 계좌·카드번호로 보이는 숫자가 있어요. 한지갑은 끝 4자리만 저장해요"
        )


def last4(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not _LAST4_RE.match(value):
        raise InputError("번호는 끝 4자리 숫자만 입력하세요 (예: 0937)")
    return value


def color(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    m = _COLOR_RE.match(value.strip())
    if not m:
        raise InputError("색은 #rrggbb 형식으로 입력하세요 (예: #143d30)")
    return "#" + m[1].lower()


def parse_date(value: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise InputError(f"{label} 형식이 아니에요: {value} (예: 2026-10-28)") from None


def date_or_month(value: str | None, label: str) -> str | None:
    """'YYYY-MM-DD' 또는 'YYYY-MM'. '.'도 받는다 (2015.03)."""
    if value is None or not value.strip():
        return None
    value = value.strip().replace(".", "-")
    if _YM_RE.match(value):
        parse_date(value + "-01", label)
        return value
    parse_date(value, label)
    return value


def year_month(value: str | None, label: str) -> str | None:
    """'YYYY-MM'만. '.'도 받는다."""
    if value is None or not value.strip():
        return None
    value = value.strip().replace(".", "-")
    if not _YM_RE.match(value):
        raise InputError(f"{label}은(는) YYYY-MM 형식이에요 (예: 2026-10)")
    parse_date(value + "-01", label)
    return value


def code(value: str | None, codes: dict[str, str], what: str) -> str:
    """영어 코드 또는 한글 이름 모두 받는다. (입출금 → checking)

    what은 조사까지 붙여 넘긴다. 예) '계좌 종류를', '출금 방식을'
    """
    v = (value or "").strip()
    if v.lower() in codes:
        return v.lower()
    for key, label in codes.items():
        if v.lower() == label.lower():
            return key
    choices = ", ".join(f"{label}({key})" for key, label in codes.items())
    raise InputError(f"{what} 알 수 없어요: {value}. 가능한 값: {choices}")


def in_range(value: int | None, label: str, low: int = 1, high: int = 31) -> int | None:
    if value is None:
        return None
    if not low <= value <= high:
        raise InputError(f"{label}은(는) {low}~{high} 사이여야 해요")
    return value
