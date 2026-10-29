"""부동산: 집 · 계약 이력 · 시세 기록 · 자산 가치 계산 (J6).

실제 입력은 웹 화면에서 한다. 이 모듈은 화면과 테스트가 함께 쓰는 함수다.

계약
    전세·월세: amount = 보증금, end_on = 만기일. 월세는 monthly_rent도 넣는다.
    자가(매매): amount = 매입가, 만기 없음.
    주인 = 보증금·매입가를 낸 사람 (계약마다 정한다).
    한 집에 진행 중인 계약은 하나. 새 계약을 넣으면 이전 계약은 새 계약 전날로 끝난다.

자산 가치 (저장하지 않고 볼 때마다 계산, 그 달 말 기준)
    전세·월세 = 보증금 (돌려받을 돈)
    자가 = 그 달까지의 마지막 시세 (계약 시작 이후 것만), 없으면 매입가

만기 알림
    주택임대차보호법상 임차인은 만기 6개월 전부터 2개월 전까지 갱신을 요구할 수 있다.
    그 기간을 '갱신 의사를 알리는 기간'으로 표시한다.
"""

from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from hanjigap.db import validate
from hanjigap.db.fixed_items import FixedItem, list_fixed_items
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError

EstateError = InputError

PROPERTY_KINDS = {
    "apartment": "아파트",
    "villa": "빌라",
    "officetel": "오피스텔",
    "house": "주택",
    "other": "기타",
}
CONTRACT_TYPES = {"jeonse": "전세", "monthly": "월세", "own": "자가"}
VALUATION_SOURCES = {"manual": "직접 입력", "lookup": "실거래가"}
NAME_MAX = 40
NOTICE_FROM_MONTHS = 6  # 만기 6개월 전부터
NOTICE_UNTIL_MONTHS = 2  # 2개월 전까지
RENEWAL_STAGES = {
    "before": "갱신 의사 통지 기간 전",
    "notice": "갱신 의사를 알리는 기간",
    "closing": "만기 2개월 이내",
    "overdue": "만기 지남 · 계약 정리 필요",
}


@dataclass(frozen=True)
class Property:
    id: int
    name: str
    kind: str
    address_memo: str | None
    moved_out_on: str | None
    color: str | None
    memo: str | None

    @property
    def kind_label(self) -> str:
        return PROPERTY_KINDS[self.kind]

    @property
    def active(self) -> bool:
        return self.moved_out_on is None


@dataclass(frozen=True)
class Contract:
    id: int
    property_id: int
    owner_member_id: int
    contract_type: str
    amount: int
    monthly_rent: int | None
    start_on: str
    end_on: str | None
    ended_on: str | None
    memo: str | None

    @property
    def type_label(self) -> str:
        return CONTRACT_TYPES[self.contract_type]

    @property
    def amount_label(self) -> str:
        return "매입가" if self.contract_type == "own" else "보증금"

    @property
    def open(self) -> bool:
        return self.ended_on is None

    def active_on(self, day: str) -> bool:
        return self.start_on <= day and (self.ended_on is None or self.ended_on >= day)


@dataclass(frozen=True)
class Valuation:
    year_month: str
    value: int
    source: str
    memo: str | None

    @property
    def source_label(self) -> str:
        return VALUATION_SOURCES[self.source]


@dataclass(frozen=True)
class PropertyValue:
    year_month: str
    value: int
    contract: Contract
    basis: str  # deposit | purchase | manual | lookup
    basis_month: str

    @property
    def owner_member_id(self) -> int:
        return self.contract.owner_member_id

    @property
    def label(self) -> str:
        """화면의 '이 금액은 이렇게 정했어요' 칸"""
        if self.basis == "deposit":
            return f"{self.contract.type_label} 보증금"
        y, m = self.basis_month.split("-")
        if self.basis == "purchase":
            return f"{y}년 {int(m)}월 매입가"
        return f"{y}년 {int(m)}월 {VALUATION_SOURCES[self.basis]} 시세"


@dataclass(frozen=True)
class RenewalInfo:
    end_on: str
    days_left: int  # 만기까지 남은 날 (지났으면 음수)
    notice_from: str
    notice_until: str
    stage: str  # before | notice | closing | overdue

    @property
    def stage_label(self) -> str:
        return RENEWAL_STAGES[self.stage]


_PROPERTY_COLS = "id, name, kind, address_memo, moved_out_on, color, memo"
_CONTRACT_COLS = (
    "id, property_id, owner_member_id, contract_type, amount, monthly_rent, start_on, end_on, "
    "ended_on, memo"
)


# --- 집 -----------------------------------------------------------------------


def add_property(
    conn: sqlite3.Connection,
    *,
    name: str,
    kind: str = "apartment",
    address_memo: str | None = None,
    color: str | None = None,
    memo: str | None = None,
) -> Property:
    fields = _clean_property(
        {"name": name, "kind": kind, "address_memo": address_memo, "color": color, "memo": memo}
    )
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    try:
        cur = conn.execute(
            f"INSERT INTO properties ({cols}) VALUES ({marks})", tuple(fields.values())
        )
    except sqlite3.IntegrityError as exc:
        raise _property_integrity(exc) from None
    return get_property(conn, cur.lastrowid)


def get_property(conn: sqlite3.Connection, property_id: int) -> Property:
    row = conn.execute(
        f"SELECT {_PROPERTY_COLS} FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    if row is None:
        raise EstateError(f"집을 찾을 수 없어요: {property_id}")
    return Property(**dict(row))


def list_properties(conn: sqlite3.Connection, *, include_moved_out: bool = False) -> list[Property]:
    sql = f"SELECT {_PROPERTY_COLS} FROM properties"
    if not include_moved_out:
        sql += " WHERE moved_out_on IS NULL"
    return [Property(**dict(r)) for r in conn.execute(sql + " ORDER BY id")]


def update_property(conn: sqlite3.Connection, property_id: int, **changes: Any) -> Property:
    """바꿀 칸만. 선택 칸은 ''를 넘기면 지운다. 이사 나감은 move_out으로."""
    current = get_property(conn, property_id)
    if not changes:
        raise EstateError("바꿀 내용이 없어요")
    allowed = {"name", "kind", "address_memo", "color", "memo"}
    unknown = set(changes) - allowed
    if unknown:
        raise EstateError(f"바꿀 수 없는 칸이에요: {', '.join(sorted(unknown))}")
    merged = {k: v for k, v in current.__dict__.items() if k in allowed}
    merged.update(changes)
    fields = _clean_property(merged)
    fields["updated_at"] = validate.now_utc()
    sets = ", ".join(f"{k} = ?" for k in fields)
    try:
        conn.execute(f"UPDATE properties SET {sets} WHERE id = ?", (*fields.values(), property_id))
    except sqlite3.IntegrityError as exc:
        raise _property_integrity(exc) from None
    return get_property(conn, property_id)


def move_out(conn: sqlite3.Connection, property_id: int, moved_out_on: str | None) -> Property:
    """이사 나간 날 표시. 진행 중인 계약도 그날로 끝낸다. None이면 표시만 취소 (계약은 그대로)."""
    get_property(conn, property_id)
    day = validate.parse_date(moved_out_on, "이사 나간 날").isoformat() if moved_out_on else None
    current = open_contract(conn, property_id)
    if day is not None and current is not None and day < current.start_on:
        raise EstateError("이사 나간 날이 지금 계약 시작일보다 빨라요")
    with _savepoint(conn):
        if day is not None and current is not None:
            _set_ended(conn, current.id, day)
        conn.execute(
            "UPDATE properties SET moved_out_on = ?, updated_at = ? WHERE id = ?",
            (day, validate.now_utc(), property_id),
        )
    return get_property(conn, property_id)


# --- 계약 ---------------------------------------------------------------------


def add_contract(
    conn: sqlite3.Connection,
    property_id: int,
    owner: Member,
    *,
    contract_type: str,
    amount: int,
    start_on: str,
    end_on: str | None = None,
    monthly_rent: int | None = None,
    memo: str | None = None,
) -> Contract:
    """새 계약. 진행 중인 계약이 있으면 새 계약 전날로 끝낸다 (갱신·재계약)."""
    prop = get_property(conn, property_id)
    if not prop.active:
        raise EstateError("이사 나간 집이에요. 계약을 넣으려면 먼저 이사 나감을 취소하세요")
    fields = _clean_contract(
        {
            "contract_type": contract_type,
            "amount": amount,
            "start_on": start_on,
            "end_on": end_on,
            "monthly_rent": monthly_rent,
            "memo": memo,
        }
    )
    previous = open_contract(conn, property_id)
    if previous is not None and fields["start_on"] <= previous.start_on:
        raise EstateError("새 계약 시작일은 지금 계약 시작일보다 늦어야 해요")
    fields["property_id"] = property_id
    fields["owner_member_id"] = owner.id
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    with _savepoint(conn):
        if previous is not None:
            day_before = dt.date.fromisoformat(fields["start_on"]) - dt.timedelta(days=1)
            _set_ended(conn, previous.id, day_before.isoformat())
        try:
            cur = conn.execute(
                f"INSERT INTO property_contracts ({cols}) VALUES ({marks})",
                tuple(fields.values()),
            )
        except sqlite3.IntegrityError as exc:
            raise EstateError(f"계약 정보가 올바르지 않아요 ({exc})") from None
    return get_contract(conn, cur.lastrowid)


def get_contract(conn: sqlite3.Connection, contract_id: int) -> Contract:
    row = conn.execute(
        f"SELECT {_CONTRACT_COLS} FROM property_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if row is None:
        raise EstateError(f"계약을 찾을 수 없어요: {contract_id}")
    return Contract(**dict(row))


def contracts(conn: sqlite3.Connection, property_id: int) -> list[Contract]:
    """계약 이력 (오래된 것부터)."""
    rows = conn.execute(
        f"SELECT {_CONTRACT_COLS} FROM property_contracts WHERE property_id = ? "
        "ORDER BY start_on, id",
        (property_id,),
    )
    return [Contract(**dict(r)) for r in rows]


def open_contract(conn: sqlite3.Connection, property_id: int) -> Contract | None:
    row = conn.execute(
        f"SELECT {_CONTRACT_COLS} FROM property_contracts "
        "WHERE property_id = ? AND ended_on IS NULL",
        (property_id,),
    ).fetchone()
    return None if row is None else Contract(**dict(row))


def update_contract(conn: sqlite3.Connection, contract_id: int, **changes: Any) -> Contract:
    """잘못 넣은 계약 고치기. owner(Member)로 주인도 바꿀 수 있다. 끝내기는 end_contract로."""
    current = get_contract(conn, contract_id)
    if not changes:
        raise EstateError("바꿀 내용이 없어요")
    allowed = {"contract_type", "amount", "monthly_rent", "start_on", "end_on", "memo"}
    unknown = set(changes) - allowed - {"owner"}
    if unknown:
        raise EstateError(f"바꿀 수 없는 칸이에요: {', '.join(sorted(unknown))}")
    owner = changes.pop("owner", None)
    merged = {k: v for k, v in current.__dict__.items() if k in allowed}
    merged.update(changes)
    if "contract_type" in changes and changes["contract_type"] is not None:
        # 종류를 바꾸면 그 종류에 맞지 않는 칸은 따로 넘기지 않은 한 비운다
        kind = validate.code(changes["contract_type"], CONTRACT_TYPES, "계약 종류를")
        if kind != "monthly" and "monthly_rent" not in changes:
            merged["monthly_rent"] = None
        if kind == "own" and "end_on" not in changes:
            merged["end_on"] = None
    fields = _clean_contract(merged)
    if current.ended_on is not None and current.ended_on < fields["start_on"]:
        raise EstateError("시작일이 계약이 끝난 날보다 늦어요")
    if owner is not None:
        fields["owner_member_id"] = owner.id
    fields["updated_at"] = validate.now_utc()
    sets = ", ".join(f"{k} = ?" for k in fields)
    try:
        conn.execute(
            f"UPDATE property_contracts SET {sets} WHERE id = ?", (*fields.values(), contract_id)
        )
    except sqlite3.IntegrityError as exc:
        raise EstateError(f"계약 정보가 올바르지 않아요 ({exc})") from None
    return get_contract(conn, contract_id)


def end_contract(conn: sqlite3.Connection, contract_id: int, ended_on: str | None) -> Contract:
    """계약이 끝난 날 표시 (None이면 다시 진행 중으로). 지우지 않는다."""
    c = get_contract(conn, contract_id)
    day = validate.parse_date(ended_on, "끝난 날").isoformat() if ended_on else None
    if day is not None and day < c.start_on:
        raise EstateError("끝난 날이 계약 시작일보다 빨라요")
    if day is None and get_property(conn, c.property_id).moved_out_on is not None:
        raise EstateError("이사 나간 집의 계약은 다시 진행 중으로 바꿀 수 없어요")
    try:
        _set_ended(conn, contract_id, day)
    except sqlite3.IntegrityError:
        raise EstateError("이 집에는 이미 진행 중인 계약이 있어요") from None
    return get_contract(conn, contract_id)


def renewal_info(contract: Contract, today: dt.date) -> RenewalInfo | None:
    """만기 알림. 자가이거나 이미 끝난 계약이면 None."""
    if contract.end_on is None or contract.ended_on is not None:
        return None
    end = dt.date.fromisoformat(contract.end_on)
    notice_from = _shift_months(end, -NOTICE_FROM_MONTHS)
    notice_until = _shift_months(end, -NOTICE_UNTIL_MONTHS)
    if today > end:
        stage = "overdue"
    elif today > notice_until:
        stage = "closing"
    elif today >= notice_from:
        stage = "notice"
    else:
        stage = "before"
    return RenewalInfo(
        contract.end_on,
        (end - today).days,
        notice_from.isoformat(),
        notice_until.isoformat(),
        stage,
    )


# --- 시세 ---------------------------------------------------------------------


def set_valuation(
    conn: sqlite3.Connection,
    property_id: int,
    year_month: str,
    value: int,
    *,
    source: str = "manual",
    memo: str | None = None,
) -> Valuation:
    """그 달 시세를 넣는다. 이미 있으면 바꾼다. 자가 계약일 때 자산 가치에 쓰인다."""
    get_property(conn, property_id)
    ym = validate.year_month(year_month, "시세 월")
    if ym is None:
        raise EstateError("시세 월을 입력하세요 (예: 2026-09)")
    _positive_int(value, "시세")
    src = validate.code(source, VALUATION_SOURCES, "시세 출처를")
    text = validate.optional_text(memo, "메모")
    conn.execute(
        "INSERT INTO property_valuations (property_id, year_month, value, source, memo) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (property_id, year_month) DO UPDATE SET "
        "value = excluded.value, source = excluded.source, memo = excluded.memo",
        (property_id, ym, value, src, text),
    )
    return Valuation(ym, value, src, text)


def delete_valuation(conn: sqlite3.Connection, property_id: int, year_month: str) -> bool:
    cur = conn.execute(
        "DELETE FROM property_valuations WHERE property_id = ? AND year_month = ?",
        (property_id, year_month),
    )
    return cur.rowcount > 0


def valuations(conn: sqlite3.Connection, property_id: int) -> list[Valuation]:
    rows = conn.execute(
        "SELECT year_month, value, source, memo FROM property_valuations "
        "WHERE property_id = ? ORDER BY year_month",
        (property_id,),
    )
    return [Valuation(**dict(r)) for r in rows]


# --- 자산 가치 -------------------------------------------------------------------


def value_at(conn: sqlite3.Connection, prop: Property, year_month: str) -> PropertyValue | None:
    """그 달 말 기준 자산 가치. 그때 진행 중인 계약이 없으면 None."""
    ym = validate.year_month(year_month, "월")
    if ym is None:
        raise EstateError("월을 입력하세요 (예: 2026-09)")
    month_end = _month_end(ym)
    # 그 달 말일에 진행 중인 계약 (말일에 끝난 계약도 포함). 갱신은 옛 계약이 새 계약 전날 끝나서
    # '말일에 끝남'과 '말일에 이사 나감'을 계약만으로는 가를 수 없다. 갱신이 끊기지 않게 포함한다.
    active = [c for c in contracts(conn, prop.id) if c.active_on(month_end)]
    if not active:
        return None
    c = active[-1]
    if c.contract_type != "own":
        return PropertyValue(ym, c.amount, c, "deposit", c.start_on[:7])
    start_month = c.start_on[:7]
    usable = [v for v in valuations(conn, prop.id) if start_month <= v.year_month <= ym]
    if usable:
        last = usable[-1]
        return PropertyValue(ym, last.value, c, last.source, last.year_month)
    return PropertyValue(ym, c.amount, c, "purchase", start_month)


def property_fixed_items(conn: sqlite3.Connection, prop: Property) -> list[FixedItem]:
    """이 집에 연결된 고정비 (관리비·월세 등). 마감된 것 포함."""
    return [i for i in list_fixed_items(conn, include_ended=True) if i.property_id == prop.id]


# --- 내부 ----------------------------------------------------------------------


@contextmanager
def _savepoint(conn: sqlite3.Connection) -> Iterator[None]:
    """여러 줄을 함께 바꿀 때 하나라도 실패하면 모두 취소한다."""
    conn.execute("SAVEPOINT estates")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO estates")
        conn.execute("RELEASE estates")
        raise
    conn.execute("RELEASE estates")


def _set_ended(conn: sqlite3.Connection, contract_id: int, day: str | None) -> None:
    conn.execute(
        "UPDATE property_contracts SET ended_on = ?, updated_at = ? WHERE id = ?",
        (day, validate.now_utc(), contract_id),
    )


def _clean_property(v: dict[str, Any]) -> dict[str, Any]:
    name = validate.required(v["name"], "집 이름")
    if len(name) > NAME_MAX:
        raise EstateError(f"집 이름은 {NAME_MAX}자 이하로 입력하세요")
    return {
        "name": name,
        "kind": validate.code(v.get("kind") or "apartment", PROPERTY_KINDS, "집 종류를"),
        "address_memo": validate.optional_text(v.get("address_memo"), "주소 메모"),
        "color": validate.color(v.get("color")),
        "memo": validate.optional_text(v.get("memo"), "메모"),
    }


def _clean_contract(v: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    kind = validate.code(v["contract_type"], CONTRACT_TYPES, "계약 종류를")
    out["contract_type"] = kind
    topic, obj = ("매입가는", "매입가를") if kind == "own" else ("보증금은", "보증금을")
    amount = v["amount"]
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise EstateError(f"{topic} 0원 이상의 정수로 입력하세요")
    if amount == 0 and kind != "monthly":
        raise EstateError(f"{obj} 입력하세요")
    out["amount"] = amount
    rent = v.get("monthly_rent") or None
    if kind == "monthly":
        if rent is None:
            raise EstateError("월세 계약은 월세 금액을 입력하세요")
        _positive_int(rent, "월세")
    elif rent is not None:
        raise EstateError("월세 금액은 월세 계약에만 넣어요")
    out["monthly_rent"] = rent
    start = validate.parse_date(str(v["start_on"] or "").strip(), "시작일").isoformat()
    out["start_on"] = start
    end_raw = v.get("end_on")
    end = (
        validate.parse_date(end_raw.strip(), "만기일").isoformat()
        if isinstance(end_raw, str) and end_raw.strip()
        else None
    )
    if kind == "own":
        if end is not None:
            raise EstateError("자가는 만기일이 없어요")
    else:
        if end is None:
            raise EstateError(f"{CONTRACT_TYPES[kind]} 계약은 만기일을 입력하세요")
        if end <= start:
            raise EstateError("만기일이 시작일보다 늦어야 해요")
    out["end_on"] = end
    out["memo"] = validate.optional_text(v.get("memo"), "메모")
    return out


def _positive_int(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EstateError(f"{label}는 1원 이상의 정수로 입력하세요")


def _month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def _shift_months(day: dt.date, months: int) -> dt.date:
    """달을 옮긴다. 그 달에 같은 날이 없으면 말일로 (8/31 − 6개월 = 2/28)."""
    total = day.year * 12 + day.month - 1 + months
    y, m = divmod(total, 12)
    m += 1
    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def _property_integrity(exc: sqlite3.IntegrityError) -> EstateError:
    if "UNIQUE" in str(exc):
        return EstateError("같은 이름의 집이 이미 있어요")
    return EstateError(f"집 정보가 올바르지 않아요 ({exc})")
