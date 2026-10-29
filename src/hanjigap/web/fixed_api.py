"""고정비 API (F3b).

담당(owner) = 빠져나가는 계좌·카드의 주인.
    계좌·카드를 고르면 owner는 비워도 된다 (주인으로 정해짐). 골랐는데 owner가 다르면 400.
    계좌·카드 없이 직접 이체면 owner를 비울 때 로그인한 사람이 담당이다.
지우지 않고 마지막 달(end_month)로 마감한다. 마감 취소는 PATCH로 end_month에 ''.
관련 차량·집은 vehicle_id·property_id. 0을 보내면 뗀다.
그달 상태(냈음·안 냄 등)는 F4에서.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request
from pydantic import BaseModel

from hanjigap.db import fixed_items, holdings
from hanjigap.db.fixed_items import FixedItem
from hanjigap.db.members import get_member
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
