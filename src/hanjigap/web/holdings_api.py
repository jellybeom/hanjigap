"""계좌 · 카드 API (F3a).

목록은 주인(owner)·해지 포함 여부로 거를 수 있다. 해지는 지우지 않고 해지일을 적는다.
수정(PATCH)은 보낸 칸만 바꾼다. 선택 칸은 ''를 보내면 지운다.
등록할 때 link(뱅샐 결제수단 이름)를 주면 같은 트랜잭션에서 그 이름을 새 계좌·카드에 연결한다.
    (화면의 '새 카드로 등록하고 연결'. 연결이 실패하면 등록도 취소된다)
    카드의 결제일·결제 계좌는 0, 이용기간은 시작일·몇 달 전을 둘 다 0으로 보내면 지운다.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request
from pydantic import BaseModel

from hanjigap.db import holdings
from hanjigap.db.holdings import Account, Card
from hanjigap.web.common import changes, member_slugs, must_exist, owner_or_me, today
from hanjigap.web.deps import CurrentMember, Db

router = APIRouter(prefix="/api", tags=["holdings"])


# --- 입력 형식 -------------------------------------------------------------------


class AccountIn(BaseModel):
    owner: str | None = None
    link: str | None = None  # 등록하면서 연결할 뱅샐 결제수단 이름
    institution: str
    name: str
    kind: str
    last4: str | None = None
    opened_on: str | None = None
    color: str | None = None
    memo: str | None = None
    number_hint: str | None = None


class AccountPatch(BaseModel):
    institution: str | None = None
    name: str | None = None
    kind: str | None = None
    last4: str | None = None
    opened_on: str | None = None
    color: str | None = None
    memo: str | None = None
    number_hint: str | None = None


class CardIn(BaseModel):
    owner: str | None = None
    link: str | None = None  # 등록하면서 연결할 뱅샐 결제수단 이름
    issuer: str
    name: str
    kind: str
    last4: str | None = None
    pay_day: int | None = None
    period_start_day: int | None = None
    period_month_offset: int | None = None
    pay_account_id: int | None = None
    annual_fee: int | None = None
    color: str | None = None
    memo: str | None = None
    number_hint: str | None = None


class CardPatch(BaseModel):
    issuer: str | None = None
    name: str | None = None
    kind: str | None = None
    last4: str | None = None
    pay_day: int | None = None
    period_start_day: int | None = None
    period_month_offset: int | None = None
    pay_account_id: int | None = None
    annual_fee: int | None = None
    color: str | None = None
    memo: str | None = None
    number_hint: str | None = None


class CloseIn(BaseModel):
    closed_on: str | None = None  # 비우면 오늘


# --- 응답 형식 -------------------------------------------------------------------


def account_out(a: Account, slugs: dict[int, str], linked: list[str] | int) -> dict:
    out = {
        "id": a.id,
        "owner": slugs[a.member_id],
        "institution": a.institution,
        "name": a.name,
        "kind": a.kind,
        "kind_label": a.kind_label,
        "last4": a.number_last4,
        "opened_on": a.opened_on,
        "closed_on": a.closed_on,
        "active": a.active,
        "color": a.color,
        "memo": a.memo,
        "number_hint": a.number_hint,
    }
    if isinstance(linked, int):
        out["linked_count"] = linked
    else:
        out["linked_count"] = len(linked)
        out["linked_instruments"] = linked
    return out


def card_out(
    conn: sqlite3.Connection, c: Card, slugs: dict[int, str], linked: list[str] | int
) -> dict:
    pay_account = None
    if c.pay_account_id is not None:
        acc = holdings.get_account(conn, c.pay_account_id)
        pay_account = {"id": acc.id, "name": acc.name, "owner": slugs[acc.member_id]}
    out = {
        "id": c.id,
        "owner": slugs[c.member_id],
        "issuer": c.issuer,
        "name": c.name,
        "kind": c.kind,
        "kind_label": c.kind_label,
        "last4": c.number_last4,
        "pay_day": c.pay_day,
        "period_start_day": c.period_start_day,
        "period_month_offset": c.period_month_offset,
        "period_label": c.period_label,
        "pay_account": pay_account,
        "annual_fee": c.annual_fee,
        "closed_on": c.closed_on,
        "active": c.active,
        "color": c.color,
        "memo": c.memo,
        "number_hint": c.number_hint,
    }
    if isinstance(linked, int):
        out["linked_count"] = linked
    else:
        out["linked_count"] = len(linked)
        out["linked_instruments"] = linked
    return out


def _linked_names(conn: sqlite3.Connection, column: str, item_id: int) -> list[str]:
    rows = conn.execute(
        f"SELECT instrument FROM instrument_links WHERE {column} = ? ORDER BY instrument",
        (item_id,),
    )
    return [r[0] for r in rows]


def _account_detail(conn: sqlite3.Connection, account_id: int) -> dict:
    a = holdings.get_account(conn, account_id)
    return account_out(a, member_slugs(conn), _linked_names(conn, "account_id", a.id))


def _card_detail(conn: sqlite3.Connection, card_id: int) -> dict:
    c = holdings.get_card(conn, card_id)
    return card_out(conn, c, member_slugs(conn), _linked_names(conn, "card_id", c.id))


# --- 계좌 ----------------------------------------------------------------------


@router.get("/accounts")
def list_accounts(
    conn: Db, me: CurrentMember, owner: str | None = None, include_closed: bool = False
) -> dict:
    member = owner_or_me(conn, owner, me) if owner else None
    slugs = member_slugs(conn)
    counts = holdings.links_by_target(conn, "accounts")
    items = holdings.list_accounts(conn, member, include_closed=include_closed)
    return {"items": [account_out(a, slugs, counts.get(a.id, 0)) for a in items]}


@router.post("/accounts", status_code=201)
def add_account(body: AccountIn, conn: Db, me: CurrentMember) -> dict:
    values = body.model_dump(exclude={"owner", "link"})
    member = owner_or_me(conn, body.owner, me)
    a = holdings.add_account(conn, member, **values)
    if body.link:
        holdings.link_instrument(conn, member, body.link, account_id=a.id, linked_by=me)
    return _account_detail(conn, a.id)


@router.get("/accounts/{account_id}")
def get_account(account_id: int, conn: Db, _me: CurrentMember) -> dict:
    must_exist(holdings.get_account, conn, account_id)
    return _account_detail(conn, account_id)


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountPatch, conn: Db, _me: CurrentMember) -> dict:
    must_exist(holdings.get_account, conn, account_id)
    holdings.update_account(conn, account_id, **changes(body))
    return _account_detail(conn, account_id)


@router.post("/accounts/{account_id}/close")
def close_account(
    account_id: int, request: Request, conn: Db, _me: CurrentMember, body: CloseIn | None = None
) -> dict:
    must_exist(holdings.get_account, conn, account_id)
    day = (body.closed_on if body else None) or today(request).isoformat()
    holdings.close_account(conn, account_id, day)
    return _account_detail(conn, account_id)


@router.post("/accounts/{account_id}/reopen")
def reopen_account(account_id: int, conn: Db, _me: CurrentMember) -> dict:
    must_exist(holdings.get_account, conn, account_id)
    holdings.reopen_account(conn, account_id)
    return _account_detail(conn, account_id)


# --- 카드 ----------------------------------------------------------------------


@router.get("/cards")
def list_cards(
    conn: Db, me: CurrentMember, owner: str | None = None, include_closed: bool = False
) -> dict:
    member = owner_or_me(conn, owner, me) if owner else None
    slugs = member_slugs(conn)
    counts = holdings.links_by_target(conn, "cards")
    items = holdings.list_cards(conn, member, include_closed=include_closed)
    return {"items": [card_out(conn, c, slugs, counts.get(c.id, 0)) for c in items]}


@router.post("/cards", status_code=201)
def add_card(body: CardIn, conn: Db, me: CurrentMember) -> dict:
    values = body.model_dump(exclude={"owner", "link"})
    member = owner_or_me(conn, body.owner, me)
    c = holdings.add_card(conn, member, **values)
    if body.link:
        holdings.link_instrument(conn, member, body.link, card_id=c.id, linked_by=me)
    return _card_detail(conn, c.id)


@router.get("/cards/{card_id}")
def get_card(card_id: int, conn: Db, _me: CurrentMember) -> dict:
    must_exist(holdings.get_card, conn, card_id)
    return _card_detail(conn, card_id)


@router.patch("/cards/{card_id}")
def update_card(card_id: int, body: CardPatch, conn: Db, _me: CurrentMember) -> dict:
    must_exist(holdings.get_card, conn, card_id)
    holdings.update_card(conn, card_id, **changes(body))
    return _card_detail(conn, card_id)


@router.post("/cards/{card_id}/close")
def close_card(
    card_id: int, request: Request, conn: Db, _me: CurrentMember, body: CloseIn | None = None
) -> dict:
    must_exist(holdings.get_card, conn, card_id)
    day = (body.closed_on if body else None) or today(request).isoformat()
    holdings.close_card(conn, card_id, day)
    return _card_detail(conn, card_id)


@router.post("/cards/{card_id}/reopen")
def reopen_card(card_id: int, conn: Db, _me: CurrentMember) -> dict:
    must_exist(holdings.get_card, conn, card_id)
    holdings.reopen_card(conn, card_id)
    return _card_detail(conn, card_id)
