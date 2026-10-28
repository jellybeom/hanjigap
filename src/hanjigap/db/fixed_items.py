"""고정비 등록 · 조회 · 수정 · 마감.

실제 입력은 웹 화면에서 한다. 이 모듈은 화면과 테스트가 함께 쓰는 함수다.
그달 상태 계산(거래와 맞춰 보기)은 J3b에서 따로 만든다.

규칙
    담당 = 빠져나가는 계좌·카드의 주인. 계좌·카드를 고르면 담당도 그 주인으로 정해진다.
    카드 자동결제 ⇔ 카드, 자동이체 ⇒ 계좌, 직접 이체는 계좌를 골라도 되고 안 골라도 된다.
    지우지 않고 마지막 달(end_month)로 마감한다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from hanjigap.db import validate
from hanjigap.db.holdings import get_account, get_card
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError

FixedItemError = InputError

CATEGORIES = {
    "allowance": "용돈",
    "housing": "주거",
    "insurance": "보험",
    "parents": "부모님",
    "telecom": "통신·구독",
    "saving": "저축",
    "loan": "대출",
    "other": "기타",
}
SAVING_CATEGORIES = frozenset({"saving", "loan"})  # '저축·상환'으로 묶는 분류
METHODS = {"auto_transfer": "자동이체", "card_auto": "카드 자동결제", "manual": "직접 이체"}
AMOUNT_MODES = {"fixed": "고정", "variable": "변동"}
CYCLES = {"monthly": "매월", "yearly": "매년"}
NAME_MAX = 50
MATCH_TEXT_MAX = 50


@dataclass(frozen=True)
class FixedItem:
    id: int
    member_id: int
    name: str
    category: str
    amount: int
    amount_mode: str
    cycle: str
    pay_day: int
    pay_month: int | None
    method: str
    pay_account_id: int | None
    pay_card_id: int | None
    match_text: str | None
    match_days: int
    start_month: str
    end_month: str | None
    memo: str | None

    @property
    def group(self) -> str:
        """'saving' = 저축·상환, 'living' = 생활 고정비"""
        return "saving" if self.category in SAVING_CATEGORIES else "living"

    @property
    def category_label(self) -> str:
        return CATEGORIES[self.category]

    @property
    def method_label(self) -> str:
        return METHODS[self.method]

    @property
    def schedule_label(self) -> str:
        if self.cycle == "yearly":
            return f"매년 {self.pay_month}월 {self.pay_day}일"
        return f"매월 {self.pay_day}일"

    def active_in(self, year_month: str) -> bool:
        """그달에 유효한 항목인가 (시작 달 ~ 마지막 달, 매년이면 그 달만)"""
        if year_month < self.start_month:
            return False
        if self.end_month is not None and year_month > self.end_month:
            return False
        return self.cycle == "monthly" or int(year_month[5:7]) == self.pay_month


_COLS = (
    "id, member_id, name, category, amount, amount_mode, cycle, pay_day, pay_month, method, "
    "pay_account_id, pay_card_id, match_text, match_days, start_month, end_month, memo"
)


def _row(r: sqlite3.Row) -> FixedItem:
    return FixedItem(**dict(r))


def add_fixed_item(
    conn: sqlite3.Connection,
    member: Member | None,
    *,
    name: str,
    category: str,
    amount: int,
    pay_day: int,
    method: str,
    start_month: str,
    amount_mode: str = "fixed",
    cycle: str = "monthly",
    pay_month: int | None = None,
    pay_account_id: int | None = None,
    pay_card_id: int | None = None,
    match_text: str | None = None,
    match_days: int = 3,
    end_month: str | None = None,
    memo: str | None = None,
) -> FixedItem:
    """member는 계좌·카드를 고르면 생략해도 된다 (주인으로 정해짐)."""
    values = {
        "name": name,
        "category": category,
        "amount": amount,
        "amount_mode": amount_mode,
        "cycle": cycle,
        "pay_day": pay_day,
        "pay_month": pay_month,
        "method": method,
        "pay_account_id": pay_account_id,
        "pay_card_id": pay_card_id,
        "match_text": match_text,
        "match_days": match_days,
        "start_month": start_month,
        "end_month": end_month,
        "memo": memo,
    }
    fields = _clean(values)
    _check_combination(fields)
    fields["member_id"] = _owner(conn, member, fields)
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    try:
        cur = conn.execute(
            f"INSERT INTO fixed_items ({cols}) VALUES ({marks})", tuple(fields.values())
        )
    except sqlite3.IntegrityError as exc:
        raise FixedItemError(f"고정비 정보가 올바르지 않아요 ({exc})") from None
    return get_fixed_item(conn, cur.lastrowid)


def get_fixed_item(conn: sqlite3.Connection, item_id: int) -> FixedItem:
    row = conn.execute(f"SELECT {_COLS} FROM fixed_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise FixedItemError(f"고정비를 찾을 수 없어요: {item_id}")
    return _row(row)


def list_fixed_items(
    conn: sqlite3.Connection,
    member: Member | None = None,
    *,
    year_month: str | None = None,
    include_ended: bool = False,
) -> list[FixedItem]:
    """year_month를 주면 그달에 유효한 항목만. 아니면 마감 안 된 항목 (include_ended면 전부).

    출금일 → 이름 순.
    """
    sql = f"SELECT {_COLS} FROM fixed_items"
    params: list[Any] = []
    if member is not None:
        sql += " WHERE member_id = ?"
        params.append(member.id)
    items = [_row(r) for r in conn.execute(sql + " ORDER BY pay_day, name, id", params)]
    if year_month is not None:
        ym = validate.year_month(year_month, "월")
        return [i for i in items if i.active_in(ym)]
    if not include_ended:
        return [i for i in items if i.end_month is None]
    return items


def update_fixed_item(conn: sqlite3.Connection, item_id: int, **changes: Any) -> FixedItem:
    """바꿀 칸만 넘긴다. match_text·memo는 ''면 지운다. 출금 수단을 바꾸면 담당도 따라 바뀐다."""
    current = get_fixed_item(conn, item_id)
    if not changes:
        raise FixedItemError("바꿀 내용이 없어요")
    unknown = set(changes) - set(_COLS.replace(" ", "").split(",")) - {"member"}
    if unknown:
        raise FixedItemError(f"바꿀 수 없는 칸이에요: {', '.join(sorted(unknown))}")
    member = changes.pop("member", None)
    merged = {k: v for k, v in current.__dict__.items() if k not in ("id", "member_id")}
    merged.update(changes)
    fields = _clean(merged)
    if member is None and fields["pay_account_id"] is None and fields["pay_card_id"] is None:
        member = _MemberRef(current.member_id)  # 직접 이체로 바꾸면 담당은 그대로
    _check_combination(fields)
    fields["member_id"] = _owner(conn, member, fields)
    fields["updated_at"] = validate.now_utc()
    sets = ", ".join(f"{k} = ?" for k in fields)
    try:
        conn.execute(f"UPDATE fixed_items SET {sets} WHERE id = ?", (*fields.values(), item_id))
    except sqlite3.IntegrityError as exc:
        raise FixedItemError(f"고정비 정보가 올바르지 않아요 ({exc})") from None
    return get_fixed_item(conn, item_id)


def end_fixed_item(conn: sqlite3.Connection, item_id: int, end_month: str) -> FixedItem:
    """그달까지만 유효하게 마감 (지우지 않음)."""
    return update_fixed_item(conn, item_id, end_month=end_month)


# --- 내부 ----------------------------------------------------------------------


@dataclass(frozen=True)
class _MemberRef:
    id: int


def _clean(v: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    name = validate.required(v["name"], "이름")
    if len(name) > NAME_MAX:
        raise FixedItemError(f"이름은 {NAME_MAX}자 이하로 입력하세요")
    out["name"] = name
    out["category"] = validate.code(v["category"], CATEGORIES, "분류를")
    amount = v["amount"]
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise FixedItemError("금액은 1원 이상의 정수로 입력하세요")
    out["amount"] = amount
    out["amount_mode"] = validate.code(v["amount_mode"], AMOUNT_MODES, "금액 방식을")
    out["cycle"] = validate.code(v["cycle"], CYCLES, "주기를")
    out["pay_day"] = validate.in_range(v["pay_day"], "출금일")
    if out["pay_day"] is None:
        raise FixedItemError("출금일을 입력하세요 (1~31)")
    if out["cycle"] == "yearly":
        if v.get("pay_month") is None:
            raise FixedItemError("매년 항목은 출금 월을 입력하세요 (1~12)")
        out["pay_month"] = validate.in_range(v["pay_month"], "출금 월", 1, 12)
    else:
        out["pay_month"] = None  # 매월이면 월은 쓰지 않는다
    out["method"] = validate.code(v["method"], METHODS, "출금 방식을")
    out["pay_account_id"] = v.get("pay_account_id") or None
    out["pay_card_id"] = v.get("pay_card_id") or None
    text = validate.optional_text(v.get("match_text"), "거래 찾는 글자")
    if text is not None and len(text) > MATCH_TEXT_MAX:
        raise FixedItemError(f"거래 찾는 글자는 {MATCH_TEXT_MAX}자 이하로 입력하세요")
    out["match_text"] = text
    out["match_days"] = validate.in_range(v.get("match_days", 3), "날짜 범위", 0, 10)
    start = validate.year_month(v["start_month"], "시작 월")
    if start is None:
        raise FixedItemError("시작 월을 입력하세요 (예: 2026-10)")
    out["start_month"] = start
    out["end_month"] = validate.year_month(v.get("end_month"), "마지막 월")
    if out["end_month"] is not None and out["end_month"] < start:
        raise FixedItemError("마지막 월이 시작 월보다 빨라요")
    out["memo"] = validate.optional_text(v.get("memo"), "메모")
    return out


def _owner(conn: sqlite3.Connection, member: Any, fields: dict[str, Any]) -> int:
    """담당자 id. 계좌·카드를 골랐으면 그 주인이어야 한다."""
    if fields["pay_account_id"] is not None:
        owner = get_account(conn, fields["pay_account_id"]).member_id
    elif fields["pay_card_id"] is not None:
        owner = get_card(conn, fields["pay_card_id"]).member_id
    else:
        if member is None:
            raise FixedItemError("출금 계좌·카드가 없으면 담당자를 정해 주세요")
        return member.id
    if member is not None and member.id != owner:
        raise FixedItemError("담당자는 출금 계좌·카드의 주인이어야 해요")
    return owner


def _check_combination(f: dict[str, Any]) -> None:
    if f["pay_account_id"] is not None and f["pay_card_id"] is not None:
        raise FixedItemError("출금 계좌와 카드 중 하나만 고르세요")
    if f["method"] == "card_auto" and f["pay_card_id"] is None:
        raise FixedItemError("카드 자동결제는 카드를 골라 주세요")
    if f["method"] != "card_auto" and f["pay_card_id"] is not None:
        raise FixedItemError("카드를 고르면 출금 방식은 카드 자동결제예요")
    if f["method"] == "auto_transfer" and f["pay_account_id"] is None:
        raise FixedItemError("자동이체는 출금 계좌를 골라 주세요")
