"""계좌 · 카드 · 뱅샐 결제수단 연결.

- 결제수단 이름별 사용 현황 (instrument_usage)
- 계좌·카드 등록·수정·해지 표시 (지우지 않는다)
- 뱅샐 결제수단 이름 → 계좌·카드·간편결제 연결

원칙
    계좌번호·카드번호는 끝 4자리만 받는다.
    자유 입력 칸(메모, 번호 보는 곳)은 공백·-·.·/를 뺐을 때 숫자가 10자리 넘게 이어지면 거부한다.
    (DB도 같은 검사를 하지만 DB는 구분 기호가 섞인 번호까지는 못 막는다)
    결제수단 이름은 실제 카드·통장 이름이라 로그에는 남기지 않는다. (부르는 쪽 책임)
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from hanjigap.db.members import Member

TARGET_LABELS = {"account": "계좌", "card": "카드", "easy_pay": "간편결제"}

ACCOUNT_KINDS = {
    "checking": "입출금",
    "savings": "적금",
    "deposit": "예금",
    "parking": "파킹통장",
    "securities": "증권",
    "isa": "ISA",
    "pension": "연금저축",
    "irp": "IRP",
    "fund": "펀드",
    "other": "기타",
}
CARD_KINDS = {"credit": "신용", "check": "체크", "hipass": "하이패스", "other": "기타"}

_SEPARATORS_RE = re.compile(r"[\s\-./]")
_LONG_NUMBER_RE = re.compile(r"\d{10,}")
_LAST4_RE = re.compile(r"^\d{4}$")
_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_YM_RE = re.compile(r"^\d{4}-\d{2}$")
TEXT_MAX = 100


class HoldingsError(Exception):
    pass


# --- 결제수단 사용 현황 -----------------------------------------------------------


@dataclass(frozen=True)
class InstrumentUsage:
    instrument: str  # 뱅샐 '결제수단' 값 그대로 ('' 가능)
    count: int
    first_date: str
    last_date: str
    expense: int  # 타입별 건수
    income: int
    transfer: int
    target: str | None  # None = 연결 안 됨
    target_name: str | None  # 연결된 계좌·카드 이름

    @property
    def linked(self) -> bool:
        return self.target is not None

    @property
    def link_label(self) -> str:
        if self.target is None:
            return "-"
        label = TARGET_LABELS[self.target]
        return f"{label} · {self.target_name}" if self.target_name else label


def instrument_usage(conn: sqlite3.Connection, member: Member) -> list[InstrumentUsage]:
    """그 사람 거래에 나온 결제수단 이름별 건수·기간·타입별 건수·연결 상태. 건수 많은 순."""
    rows = conn.execute(
        """
        SELECT t.instrument                       AS instrument,
               count(*)                           AS n,
               min(t.tx_date)                     AS first_date,
               max(t.tx_date)                     AS last_date,
               sum(t.tx_type = '지출')            AS expense,
               sum(t.tx_type = '수입')            AS income,
               sum(t.tx_type = '이체')            AS transfer,
               l.target                           AS target,
               coalesce(a.name, c.name)           AS target_name
        FROM transactions t
        LEFT JOIN instrument_links l
               ON l.member_id = t.member_id AND l.instrument = t.instrument
        LEFT JOIN accounts a ON a.id = l.account_id
        LEFT JOIN cards c    ON c.id = l.card_id
        WHERE t.member_id = ?
        GROUP BY t.instrument
        ORDER BY n DESC, t.instrument
        """,
        (member.id,),
    ).fetchall()
    return [
        InstrumentUsage(
            instrument=r["instrument"],
            count=r["n"],
            first_date=r["first_date"],
            last_date=r["last_date"],
            expense=r["expense"],
            income=r["income"],
            transfer=r["transfer"],
            target=r["target"],
            target_name=r["target_name"],
        )
        for r in rows
    ]


# --- 입력 검사 ------------------------------------------------------------------


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _required(value: str | None, label: str) -> str:
    value = (value or "").strip()
    if not value:
        raise HoldingsError(f"{label}을(를) 입력하세요")
    if len(value) > TEXT_MAX:
        raise HoldingsError(f"{label}은(는) {TEXT_MAX}자 이하로 입력하세요")
    _reject_long_number(value, label)
    return value


def _optional_text(value: str | None, label: str) -> str | None:
    """빈 문자열은 '지우기'(None)로 본다."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > TEXT_MAX:
        raise HoldingsError(f"{label}은(는) {TEXT_MAX}자 이하로 입력하세요")
    _reject_long_number(value, label)
    return value


def _reject_long_number(value: str, label: str) -> None:
    if _LONG_NUMBER_RE.search(_SEPARATORS_RE.sub("", value)):
        raise HoldingsError(
            f"{label}에 전체 계좌·카드번호로 보이는 숫자가 있어요. " "한지갑은 끝 4자리만 저장해요"
        )


def _last4(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not _LAST4_RE.match(value):
        raise HoldingsError("번호는 끝 4자리 숫자만 입력하세요 (예: 0937)")
    return value


def _color(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    m = _COLOR_RE.match(value.strip())
    if not m:
        raise HoldingsError("색은 #rrggbb 형식으로 입력하세요 (예: #143d30)")
    return "#" + m[1].lower()


def _opened_on(value: str | None) -> str | None:
    """'YYYY-MM-DD' 또는 'YYYY-MM'. '.'도 받는다 (2015.03)."""
    if value is None or not value.strip():
        return None
    value = value.strip().replace(".", "-")
    if _YM_RE.match(value):
        _parse_date(value + "-01", "개설일")
        return value
    _parse_date(value, "개설일")
    return value


def _parse_date(value: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise HoldingsError(f"{label} 형식이 아니에요: {value} (예: 2026-10-28)") from None


def _kind(value: str, kinds: dict[str, str], what: str) -> str:
    """영어 코드 또는 한글 이름 모두 받는다. (입출금 → checking)"""
    v = (value or "").strip()
    if v.lower() in kinds:
        return v.lower()
    for code, label in kinds.items():
        if v.lower() == label.lower():
            return code
    choices = ", ".join(f"{label}({code})" for code, label in kinds.items())
    raise HoldingsError(f"{what} 종류를 알 수 없어요: {value}. 가능한 값: {choices}")


def _day(value: int | None, label: str, low: int = 1, high: int = 31) -> int | None:
    if value is None:
        return None
    if not low <= value <= high:
        raise HoldingsError(f"{label}은(는) {low}~{high} 사이여야 해요")
    return value


# --- 계좌 ----------------------------------------------------------------------


@dataclass(frozen=True)
class Account:
    id: int
    member_id: int
    institution: str
    name: str
    kind: str
    number_last4: str | None
    opened_on: str | None
    closed_on: str | None
    color: str | None
    memo: str | None
    number_hint: str | None

    @property
    def kind_label(self) -> str:
        return ACCOUNT_KINDS[self.kind]

    @property
    def active(self) -> bool:
        return self.closed_on is None


_ACCOUNT_COLS = (
    "id, member_id, institution, name, kind, number_last4, opened_on, closed_on, color, memo, "
    "number_hint"
)


def _row_to_account(r: sqlite3.Row) -> Account:
    return Account(**dict(r))


def _account_fields(**raw: Any) -> dict[str, Any]:
    """넘어온 값만 검사해서 DB 칸 이름으로 돌려준다. (None = 바꾸지 않음)"""
    out: dict[str, Any] = {}
    if raw.get("institution") is not None:
        out["institution"] = _required(raw["institution"], "기관")
    if raw.get("name") is not None:
        out["name"] = _required(raw["name"], "계좌 이름")
    if raw.get("kind") is not None:
        out["kind"] = _kind(raw["kind"], ACCOUNT_KINDS, "계좌")
    if raw.get("last4") is not None:
        out["number_last4"] = _last4(raw["last4"])
    if raw.get("opened_on") is not None:
        out["opened_on"] = _opened_on(raw["opened_on"])
    if raw.get("color") is not None:
        out["color"] = _color(raw["color"])
    if raw.get("memo") is not None:
        out["memo"] = _optional_text(raw["memo"], "메모")
    if raw.get("number_hint") is not None:
        out["number_hint"] = _optional_text(raw["number_hint"], "번호 보는 곳")
    return out


def add_account(
    conn: sqlite3.Connection,
    member: Member,
    *,
    institution: str,
    name: str,
    kind: str,
    last4: str | None = None,
    opened_on: str | None = None,
    color: str | None = None,
    memo: str | None = None,
    number_hint: str | None = None,
) -> Account:
    fields = _account_fields(
        institution=institution,
        name=name,
        kind=kind,
        last4=last4,
        opened_on=opened_on,
        color=color,
        memo=memo,
        number_hint=number_hint,
    )
    if "institution" not in fields or "name" not in fields or "kind" not in fields:
        raise HoldingsError("기관, 계좌 이름, 종류는 꼭 입력하세요")
    fields["member_id"] = member.id
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    try:
        cur = conn.execute(
            f"INSERT INTO accounts ({cols}) VALUES ({marks})", tuple(fields.values())
        )
    except sqlite3.IntegrityError as exc:
        raise _integrity_error(exc, "계좌") from None
    return get_account(conn, cur.lastrowid)


def get_account(conn: sqlite3.Connection, account_id: int) -> Account:
    row = conn.execute(
        f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if row is None:
        raise HoldingsError(f"계좌를 찾을 수 없어요: {account_id}")
    return _row_to_account(row)


def list_accounts(
    conn: sqlite3.Connection, member: Member | None = None, *, include_closed: bool = False
) -> list[Account]:
    where, params = [], []
    if member is not None:
        where.append("member_id = ?")
        params.append(member.id)
    if not include_closed:
        where.append("closed_on IS NULL")
    sql = f"SELECT {_ACCOUNT_COLS} FROM accounts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY member_id, closed_on IS NOT NULL, id"
    return [_row_to_account(r) for r in conn.execute(sql, params)]


def update_account(conn: sqlite3.Connection, account_id: int, **raw: Any) -> Account:
    """바꿀 칸만 넘긴다. 메모·번호 보는 곳·끝자리·개설일·색은 ''를 넘기면 지운다."""
    get_account(conn, account_id)
    fields = _account_fields(**raw)
    if not fields:
        raise HoldingsError("바꿀 내용이 없어요")
    _update(conn, "accounts", account_id, fields, "계좌")
    return get_account(conn, account_id)


def close_account(conn: sqlite3.Connection, account_id: int, closed_on: str) -> Account:
    acc = get_account(conn, account_id)
    day = _parse_date(closed_on, "해지일").isoformat()
    if acc.opened_on and day < acc.opened_on:
        raise HoldingsError("해지일이 개설일보다 빨라요")
    _update(conn, "accounts", account_id, {"closed_on": day}, "계좌")
    return get_account(conn, account_id)


def reopen_account(conn: sqlite3.Connection, account_id: int) -> Account:
    get_account(conn, account_id)
    _update(conn, "accounts", account_id, {"closed_on": None}, "계좌")
    return get_account(conn, account_id)


# --- 카드 ----------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    id: int
    member_id: int
    issuer: str
    name: str
    kind: str
    number_last4: str | None
    pay_day: int | None
    period_start_day: int | None
    period_month_offset: int | None
    pay_account_id: int | None
    annual_fee: int | None
    closed_on: str | None
    color: str | None
    memo: str | None
    number_hint: str | None

    @property
    def kind_label(self) -> str:
        return CARD_KINDS[self.kind]

    @property
    def active(self) -> bool:
        return self.closed_on is None

    @property
    def period_label(self) -> str:
        """이용기간을 사람이 읽는 말로. 비어 있으면 '전월 1일 ~ 말일'로 본다."""
        if self.kind == "check":
            return "바로 출금"
        start = self.period_start_day or 1
        offset = self.period_month_offset if self.period_month_offset is not None else -1
        month = {0: "당월", -1: "전월", -2: "전전월"}[offset]
        if start == 1:
            return f"{month} 1일 ~ 말일"
        end_month = {0: "익월", -1: "당월", -2: "전월"}[offset]
        return f"{month} {start}일 ~ {end_month} {start - 1}일"


_CARD_COLS = (
    "id, member_id, issuer, name, kind, number_last4, pay_day, period_start_day, "
    "period_month_offset, pay_account_id, annual_fee, closed_on, color, memo, number_hint"
)


def _row_to_card(r: sqlite3.Row) -> Card:
    return Card(**dict(r))


def _card_fields(conn: sqlite3.Connection, **raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if raw.get("issuer") is not None:
        out["issuer"] = _required(raw["issuer"], "카드사")
    if raw.get("name") is not None:
        out["name"] = _required(raw["name"], "카드 이름")
    if raw.get("kind") is not None:
        out["kind"] = _kind(raw["kind"], CARD_KINDS, "카드")
    if raw.get("last4") is not None:
        out["number_last4"] = _last4(raw["last4"])
    if raw.get("pay_day") is not None:  # 0 = 지우기 (체크카드 등)
        out["pay_day"] = _day(raw["pay_day"], "결제일") if raw["pay_day"] else None
    start, offset = raw.get("period_start_day"), raw.get("period_month_offset")
    if (start is None) != (offset is None):
        raise HoldingsError("이용기간은 시작일과 몇 달 전을 함께 입력하세요 (예: 1일, -1)")
    if start is not None:
        if start == 0 and offset == 0:  # 둘 다 0 = 지우기 (기본값 '전월 1일 ~ 말일')
            out["period_start_day"] = out["period_month_offset"] = None
        else:
            out["period_start_day"] = _day(start, "이용기간 시작일")
            out["period_month_offset"] = _day(offset, "이용기간 몇 달 전", -2, 0)
    if raw.get("pay_account_id") is not None:
        if raw["pay_account_id"] == 0:  # 0 = 지우기
            out["pay_account_id"] = None
        else:
            out["pay_account_id"] = get_account(conn, raw["pay_account_id"]).id
    if raw.get("annual_fee") is not None:
        if raw["annual_fee"] < 0:
            raise HoldingsError("연회비는 0 이상이어야 해요")
        out["annual_fee"] = raw["annual_fee"]
    if raw.get("color") is not None:
        out["color"] = _color(raw["color"])
    if raw.get("memo") is not None:
        out["memo"] = _optional_text(raw["memo"], "메모")
    if raw.get("number_hint") is not None:
        out["number_hint"] = _optional_text(raw["number_hint"], "번호 보는 곳")
    return out


def add_card(
    conn: sqlite3.Connection,
    member: Member,
    *,
    issuer: str,
    name: str,
    kind: str,
    last4: str | None = None,
    pay_day: int | None = None,
    period_start_day: int | None = None,
    period_month_offset: int | None = None,
    pay_account_id: int | None = None,
    annual_fee: int | None = None,
    color: str | None = None,
    memo: str | None = None,
    number_hint: str | None = None,
) -> Card:
    fields = _card_fields(
        conn,
        issuer=issuer,
        name=name,
        kind=kind,
        last4=last4,
        pay_day=pay_day,
        period_start_day=period_start_day,
        period_month_offset=period_month_offset,
        pay_account_id=pay_account_id,
        annual_fee=annual_fee,
        color=color,
        memo=memo,
        number_hint=number_hint,
    )
    if "issuer" not in fields or "name" not in fields or "kind" not in fields:
        raise HoldingsError("카드사, 카드 이름, 종류는 꼭 입력하세요")
    fields["member_id"] = member.id
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    try:
        cur = conn.execute(f"INSERT INTO cards ({cols}) VALUES ({marks})", tuple(fields.values()))
    except sqlite3.IntegrityError as exc:
        raise _integrity_error(exc, "카드") from None
    return get_card(conn, cur.lastrowid)


def get_card(conn: sqlite3.Connection, card_id: int) -> Card:
    row = conn.execute(f"SELECT {_CARD_COLS} FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        raise HoldingsError(f"카드를 찾을 수 없어요: {card_id}")
    return _row_to_card(row)


def list_cards(
    conn: sqlite3.Connection, member: Member | None = None, *, include_closed: bool = False
) -> list[Card]:
    where, params = [], []
    if member is not None:
        where.append("member_id = ?")
        params.append(member.id)
    if not include_closed:
        where.append("closed_on IS NULL")
    sql = f"SELECT {_CARD_COLS} FROM cards"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY member_id, closed_on IS NOT NULL, id"
    return [_row_to_card(r) for r in conn.execute(sql, params)]


def update_card(conn: sqlite3.Connection, card_id: int, **raw: Any) -> Card:
    """바꿀 칸만 넘긴다. 결제일·결제 계좌는 0, 이용기간은 (0, 0)을 넘기면 지운다."""
    get_card(conn, card_id)
    fields = _card_fields(conn, **raw)
    if not fields:
        raise HoldingsError("바꿀 내용이 없어요")
    _update(conn, "cards", card_id, fields, "카드")
    return get_card(conn, card_id)


def close_card(conn: sqlite3.Connection, card_id: int, closed_on: str) -> Card:
    get_card(conn, card_id)
    day = _parse_date(closed_on, "해지일").isoformat()
    _update(conn, "cards", card_id, {"closed_on": day}, "카드")
    return get_card(conn, card_id)


def reopen_card(conn: sqlite3.Connection, card_id: int) -> Card:
    get_card(conn, card_id)
    _update(conn, "cards", card_id, {"closed_on": None}, "카드")
    return get_card(conn, card_id)


# --- 결제수단 연결 ---------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentLink:
    member_id: int
    instrument: str
    target: str
    account_id: int | None
    card_id: int | None


def link_instrument(
    conn: sqlite3.Connection,
    member: Member,
    instrument: str,
    *,
    account_id: int | None = None,
    card_id: int | None = None,
    easy_pay: bool = False,
    linked_by: Member | None = None,
    allow_unknown: bool = False,
) -> InstrumentLink:
    """결제수단 이름을 계좌·카드·간편결제 중 하나에 연결한다. 이미 있으면 바꾼다."""
    if sum((account_id is not None, card_id is not None, easy_pay)) != 1:
        raise HoldingsError("계좌, 카드, 간편결제 중 하나만 고르세요")
    if not instrument:
        raise HoldingsError("결제수단 이름을 입력하세요")
    if not allow_unknown and not _instrument_exists(conn, member, instrument):
        raise HoldingsError(
            "이 구성원의 거래에 그런 결제수단 이름이 없어요. "
            "'instrument list'에서 이름을 그대로 복사해 주세요"
        )
    if account_id is not None:
        target = "account"
        if get_account(conn, account_id).member_id != member.id:
            raise HoldingsError("다른 사람의 계좌에는 연결할 수 없어요")
    elif card_id is not None:
        target = "card"
        if get_card(conn, card_id).member_id != member.id:
            raise HoldingsError("다른 사람의 카드에는 연결할 수 없어요")
    else:
        target = "easy_pay"
    conn.execute(
        """
        INSERT INTO instrument_links (member_id, instrument, target, account_id, card_id,
                                      linked_by, linked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (member_id, instrument) DO UPDATE SET
            target = excluded.target, account_id = excluded.account_id,
            card_id = excluded.card_id, linked_by = excluded.linked_by,
            linked_at = excluded.linked_at
        """,
        (
            member.id,
            instrument,
            target,
            account_id,
            card_id,
            linked_by.id if linked_by else None,
            _now(),
        ),
    )
    return InstrumentLink(member.id, instrument, target, account_id, card_id)


def unlink_instrument(conn: sqlite3.Connection, member: Member, instrument: str) -> bool:
    cur = conn.execute(
        "DELETE FROM instrument_links WHERE member_id = ? AND instrument = ?",
        (member.id, instrument),
    )
    return cur.rowcount > 0


def links_by_target(conn: sqlite3.Connection, table: str) -> dict[int, int]:
    """계좌·카드 id → 연결된 결제수단 이름 수. table은 'accounts' 또는 'cards'."""
    column = {"accounts": "account_id", "cards": "card_id"}[table]
    rows = conn.execute(
        f"SELECT {column} AS id, count(*) AS n FROM instrument_links "
        f"WHERE {column} IS NOT NULL GROUP BY {column}"
    )
    return {r["id"]: r["n"] for r in rows}


def _instrument_exists(conn: sqlite3.Connection, member: Member, instrument: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM transactions WHERE member_id = ? AND instrument = ? LIMIT 1",
        (member.id, instrument),
    ).fetchone()
    return row is not None


# --- 공통 ----------------------------------------------------------------------


def _update(
    conn: sqlite3.Connection, table: str, row_id: int, fields: dict[str, Any], what: str
) -> None:
    fields = {**fields, "updated_at": _now()}
    sets = ", ".join(f"{k} = ?" for k in fields)
    try:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id))
    except sqlite3.IntegrityError as exc:
        raise _integrity_error(exc, what) from None


def _integrity_error(exc: sqlite3.IntegrityError, what: str) -> HoldingsError:
    text = str(exc)
    if "UNIQUE" in text:
        return HoldingsError(f"같은 사람에게 같은 이름의 {what}이(가) 이미 있어요")
    if "FOREIGN KEY" in text:
        return HoldingsError(f"{what}과(와) 연결된 항목을 찾을 수 없어요")
    return HoldingsError(f"{what} 정보가 올바르지 않아요 ({text})")
