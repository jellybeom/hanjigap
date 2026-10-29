"""고정비 API (F3b).

담당(owner) = 빠져나가는 계좌·카드의 주인.
    계좌·카드를 고르면 owner는 비워도 된다 (주인으로 정해짐). 골랐는데 owner가 다르면 400.
    계좌·카드 없이 직접 이체면 owner를 비울 때 로그인한 사람이 담당이다.
지우지 않고 마지막 달(end_month)로 마감한다. 마감 취소는 PATCH로 end_month에 ''.
관련 차량·집은 vehicle_id·property_id. 0을 보내면 뗀다.
그달 상태(냈음·안 냄 등)는 F4에서.
초안(GET /fixed-items/draft): 거래 하나로 등록 칸을 미리 채운다. 저장은 하지 않는다. (G6)
    나간 돈만. 결제수단이 계좌·카드에 연결돼 있으면 그 계좌·카드와 출금 방식까지 채운다.
    이미 그달 고정비와 짝지어진 거래면 already에 그 고정비를 알려 준다.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from hanjigap.db import fixed_items, holdings
from hanjigap.db.fixed_items import FixedItem
from hanjigap.db.members import get_member
from hanjigap.db.validate import InputError
from hanjigap.fixed.status import month_status
from hanjigap.web.common import changes, member_slugs, must_exist, owner_or_me, today
from hanjigap.web.deps import CurrentMember, Db

router = APIRouter(prefix="/api", tags=["fixed"])


class FixedIn(BaseModel):
    owner: str | None = None
    name: str
    category: str
    amount: int
    pay_day: int
    method: str
    start_month: str
    amount_mode: str = "fixed"
    cycle: str = "monthly"
    pay_month: int | None = None
    pay_account_id: int | None = None
    pay_card_id: int | None = None
    match_text: str | None = None
    match_days: int = 3
    end_month: str | None = None
    memo: str | None = None
    vehicle_id: int | None = None
    property_id: int | None = None


class FixedPatch(BaseModel):
    owner: str | None = None
    name: str | None = None
    category: str | None = None
    amount: int | None = None
    pay_day: int | None = None
    method: str | None = None
    start_month: str | None = None
    amount_mode: str | None = None
    cycle: str | None = None
    pay_month: int | None = None
    pay_account_id: int | None = None
    pay_card_id: int | None = None
    match_text: str | None = None
    match_days: int | None = None
    end_month: str | None = None
    memo: str | None = None
    vehicle_id: int | None = None
    property_id: int | None = None


class EndIn(BaseModel):
    end_month: str | None = None  # 비우면 이번 달


def _named(conn: sqlite3.Connection, table: str, item_id: int | None) -> dict | None:
    if item_id is None:
        return None
    row = conn.execute(f"SELECT id, name FROM {table} WHERE id = ?", (item_id,)).fetchone()
    return {"id": row["id"], "name": row["name"]} if row else None


def fixed_out(
    conn: sqlite3.Connection, i: FixedItem, slugs: dict[int, str], this_month: str
) -> dict:
    pay_account = pay_card = None
    if i.pay_account_id is not None:
        a = holdings.get_account(conn, i.pay_account_id)
        pay_account = {"id": a.id, "name": a.name, "owner": slugs[a.member_id]}
    if i.pay_card_id is not None:
        c = holdings.get_card(conn, i.pay_card_id)
        pay_card = {"id": c.id, "name": c.name, "owner": slugs[c.member_id]}
    return {
        "id": i.id,
        "owner": slugs[i.member_id],
        "name": i.name,
        "category": i.category,
        "category_label": i.category_label,
        "group": i.group,
        "amount": i.amount,
        "amount_mode": i.amount_mode,
        "amount_mode_label": fixed_items.AMOUNT_MODES[i.amount_mode],
        "cycle": i.cycle,
        "cycle_label": fixed_items.CYCLES[i.cycle],
        "pay_day": i.pay_day,
        "pay_month": i.pay_month,
        "schedule_label": i.schedule_label,
        "method": i.method,
        "method_label": i.method_label,
        "pay_account": pay_account,
        "pay_card": pay_card,
        "match_text": i.match_text,
        "match_days": i.match_days,
        "start_month": i.start_month,
        "end_month": i.end_month,
        "ended": i.end_month is not None and i.end_month < this_month,
        "memo": i.memo,
        "vehicle": _named(conn, "vehicles", i.vehicle_id),
        "property": _named(conn, "properties", i.property_id),
    }


def _detail(conn: sqlite3.Connection, request: Request, item_id: int) -> dict:
    item = fixed_items.get_fixed_item(conn, item_id)
    return fixed_out(conn, item, member_slugs(conn), _this_month(request))


def _this_month(request: Request) -> str:
    return today(request).isoformat()[:7]


@router.get("/fixed-items")
def list_fixed(
    request: Request,
    conn: Db,
    me: CurrentMember,
    owner: str | None = None,
    month: str | None = None,
    include_ended: bool = False,
) -> dict:
    """month를 주면 그달에 유효한 항목만 (매년 항목은 그 달만). 출금일 순."""
    member = owner_or_me(conn, owner, me) if owner else None
    items = fixed_items.list_fixed_items(
        conn, member, year_month=month, include_ended=include_ended
    )
    slugs, this_month = member_slugs(conn), _this_month(request)
    return {"items": [fixed_out(conn, i, slugs, this_month) for i in items]}


@router.post("/fixed-items", status_code=201)
def add_fixed(body: FixedIn, request: Request, conn: Db, me: CurrentMember) -> dict:
    values = body.model_dump(exclude={"owner"})
    if body.owner:
        member = get_member(conn, body.owner)
    elif body.pay_account_id or body.pay_card_id:
        member = None  # 계좌·카드 주인으로 정해진다
    else:
        member = me
    item = fixed_items.add_fixed_item(conn, member, **values)
    return _detail(conn, request, item.id)


# --- 거래로 초안 만들기 (G6) -------------------------------------------------------

# 뱅샐 분류 글자 → 고정비 분류 (맞는 게 없으면 기타)
_CATEGORY_HINTS = (
    ("통신", "telecom"),
    ("구독", "telecom"),
    ("보험", "insurance"),
    ("주거", "housing"),
    ("관리비", "housing"),
    ("월세", "housing"),
    ("저축", "saving"),
    ("투자", "saving"),
    ("대출", "loan"),
    ("용돈", "allowance"),
)


def _guess_category(*texts: str) -> str:
    joined = " ".join(t for t in texts if t)
    return next((code for word, code in _CATEGORY_HINTS if word in joined), "other")


@router.get("/fixed-items/draft")
def draft_from_tx(owner: str, tx_key: str, request: Request, conn: Db, _me: CurrentMember) -> dict:
    """거래 하나로 고정비 등록 칸을 채운 초안. 저장하지 않는다."""
    member = get_member(conn, owner)
    tx = conn.execute(
        "SELECT t.tx_date, t.year_month, t.amount, t.instrument, "
        "coalesce(o.content, t.content) AS content, coalesce(o.category, t.category) AS category, "
        "coalesce(o.subcategory, t.subcategory) AS subcategory FROM transactions t "
        "LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key "
        "WHERE t.member_id = ? AND t.tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    if tx is None:
        raise HTTPException(404, "거래를 찾을 수 없어요")
    if tx["amount"] >= 0:
        raise InputError("나간 돈만 고정비로 등록할 수 있어요")
    link = conn.execute(
        "SELECT target, account_id, card_id FROM instrument_links "
        "WHERE member_id = ? AND instrument = ?",
        (member.id, tx["instrument"]),
    ).fetchone()
    method, account_id, card_id = "manual", None, None
    if link is not None and link["target"] == "card":
        method, card_id = "card_auto", link["card_id"]
    elif link is not None and link["target"] == "account":
        method, account_id = "auto_transfer", link["account_id"]
    content = (tx["content"] or "").strip()
    report = month_status(conn, tx["year_month"], member=member, today=today(request))
    already = next((st.item for st in report.statuses if st.tx and st.tx.tx_key == tx_key), None)
    slugs = member_slugs(conn)
    return {
        "draft": {
            "owner": member.slug,
            "name": content[: fixed_items.NAME_MAX] or "고정비",
            "category": _guess_category(tx["category"], tx["subcategory"], content),
            "amount": -tx["amount"],
            "amount_mode": "fixed",
            "cycle": "monthly",
            "pay_day": int(tx["tx_date"][8:10]),
            "method": method,
            "pay_account_id": account_id,
            "pay_card_id": card_id,
            "match_text": content[: fixed_items.MATCH_TEXT_MAX] or None,
            "match_days": 3,
            "start_month": tx["year_month"],
        },
        "instrument": tx["instrument"],
        "instrument_linked": link is not None,
        "already": (
            None if already is None else fixed_out(conn, already, slugs, _this_month(request))
        ),
    }


@router.get("/fixed-items/{item_id}")
def get_fixed(item_id: int, request: Request, conn: Db, _me: CurrentMember) -> dict:
    must_exist(fixed_items.get_fixed_item, conn, item_id)
    return _detail(conn, request, item_id)


@router.patch("/fixed-items/{item_id}")
def update_fixed(
    item_id: int, body: FixedPatch, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    must_exist(fixed_items.get_fixed_item, conn, item_id)
    values = changes(body)
    owner = values.pop("owner", None)
    if owner:
        values["member"] = get_member(conn, owner)
    fixed_items.update_fixed_item(conn, item_id, **values)
    return _detail(conn, request, item_id)


@router.post("/fixed-items/{item_id}/end")
def end_fixed(
    item_id: int, request: Request, conn: Db, _me: CurrentMember, body: EndIn | None = None
) -> dict:
    must_exist(fixed_items.get_fixed_item, conn, item_id)
    month = (body.end_month if body else None) or _this_month(request)
    fixed_items.end_fixed_item(conn, item_id, month)
    return _detail(conn, request, item_id)
