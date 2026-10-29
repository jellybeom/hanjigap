"""집 API (F3c): 집 · 계약 이력 · 시세.

집에는 주인이 없고 계약마다 주인(보증금·매입가를 낸 사람)이 있다. 비우면 로그인한 사람.
새 계약을 넣으면 진행 중이던 계약은 새 계약 전날로 끝난다 (갱신).
이사 나가면 집을 지우지 않고 이사 나간 날을 적는다. 진행 중인 계약도 그날로 끝난다.
집 상세에는 이번 달 자산 가치와 그 근거, 계약 이력(오래된 것부터), 만기 알림,
시세 기록, 연결된 고정비가 함께 들어간다.
계약·시세를 바꾸는 요청도 집 상세를 그대로 돌려준다. (화면이 다시 부르지 않아도 되게)
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request
from pydantic import BaseModel

from hanjigap.db import estates
from hanjigap.db.estates import Contract, Property
from hanjigap.db.members import get_member
from hanjigap.web.common import changes, member_slugs, must_exist, owner_or_me, today
from hanjigap.web.deps import CurrentMember, Db
from hanjigap.web.fixed_api import fixed_out

router = APIRouter(prefix="/api", tags=["estates"])


class PropertyIn(BaseModel):
    name: str
    kind: str = "apartment"
    address_memo: str | None = None
    color: str | None = None
    memo: str | None = None


class PropertyPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    address_memo: str | None = None
    color: str | None = None
    memo: str | None = None


class MoveOutIn(BaseModel):
    moved_out_on: str | None  # null이면 이사 나감 취소


class ContractIn(BaseModel):
    owner: str | None = None
    contract_type: str
    amount: int
    start_on: str
    end_on: str | None = None
    monthly_rent: int | None = None
    memo: str | None = None


class ContractPatch(BaseModel):
    owner: str | None = None
    contract_type: str | None = None
    amount: int | None = None
    start_on: str | None = None
    end_on: str | None = None
    monthly_rent: int | None = None
    memo: str | None = None


class EndContractIn(BaseModel):
    ended_on: str | None  # null이면 다시 진행 중으로


class ValuationIn(BaseModel):
    value: int
    source: str = "manual"
    memo: str | None = None


# --- 응답 형식 -------------------------------------------------------------------


def contract_out(c: Contract, slugs: dict[int, str], request: Request) -> dict:
    info = estates.renewal_info(c, today(request))
    return {
        "id": c.id,
        "property_id": c.property_id,
        "owner": slugs[c.owner_member_id],
        "contract_type": c.contract_type,
        "type_label": c.type_label,
        "amount": c.amount,
        "amount_label": c.amount_label,
        "monthly_rent": c.monthly_rent,
        "start_on": c.start_on,
        "end_on": c.end_on,
        "ended_on": c.ended_on,
        "open": c.open,
        "memo": c.memo,
        "renewal": (
            None
            if info is None
            else {
                "end_on": info.end_on,
                "days_left": info.days_left,
                "notice_from": info.notice_from,
                "notice_until": info.notice_until,
                "stage": info.stage,
                "stage_label": info.stage_label,
            }
        ),
    }


def property_out(
    conn: sqlite3.Connection, p: Property, slugs: dict[int, str], request: Request
) -> dict:
    current = estates.open_contract(conn, p.id)
    value = estates.value_at(conn, p, today(request).isoformat()[:7])
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "kind_label": p.kind_label,
        "address_memo": p.address_memo,
        "moved_out_on": p.moved_out_on,
        "active": p.active,
        "color": p.color,
        "memo": p.memo,
        "current_contract": None if current is None else contract_out(current, slugs, request),
        "value": (
            None
            if value is None
            else {
                "year_month": value.year_month,
                "value": value.value,
                "owner": slugs[value.owner_member_id],
                "basis": value.basis,
                "basis_month": value.basis_month,
                "label": value.label,
            }
        ),
    }


def _detail(conn: sqlite3.Connection, request: Request, property_id: int) -> dict:
    p = estates.get_property(conn, property_id)
    slugs = member_slugs(conn)
    out = property_out(conn, p, slugs, request)
    out["contracts"] = [contract_out(c, slugs, request) for c in estates.contracts(conn, p.id)]
    out["valuations"] = [
        {
            "year_month": v.year_month,
            "value": v.value,
            "source": v.source,
            "source_label": v.source_label,
            "memo": v.memo,
        }
        for v in estates.valuations(conn, p.id)
    ]
    this_month = today(request).isoformat()[:7]
    out["fixed_items"] = [
        fixed_out(conn, i, slugs, this_month) for i in estates.property_fixed_items(conn, p)
    ]
    return out


# --- 집 -----------------------------------------------------------------------


@router.get("/properties")
def list_properties(
    request: Request, conn: Db, _me: CurrentMember, include_moved_out: bool = False
) -> dict:
    slugs = member_slugs(conn)
    items = estates.list_properties(conn, include_moved_out=include_moved_out)
    return {"items": [property_out(conn, p, slugs, request) for p in items]}


@router.post("/properties", status_code=201)
def add_property(body: PropertyIn, request: Request, conn: Db, _me: CurrentMember) -> dict:
    p = estates.add_property(conn, **body.model_dump())
    return _detail(conn, request, p.id)


@router.get("/properties/{property_id}")
def get_property(property_id: int, request: Request, conn: Db, _me: CurrentMember) -> dict:
    must_exist(estates.get_property, conn, property_id)
    return _detail(conn, request, property_id)


@router.patch("/properties/{property_id}")
def update_property(
    property_id: int, body: PropertyPatch, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    must_exist(estates.get_property, conn, property_id)
    estates.update_property(conn, property_id, **changes(body))
    return _detail(conn, request, property_id)


@router.post("/properties/{property_id}/move-out")
def move_out(
    property_id: int, body: MoveOutIn, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    must_exist(estates.get_property, conn, property_id)
    estates.move_out(conn, property_id, body.moved_out_on)
    return _detail(conn, request, property_id)


# --- 계약 ---------------------------------------------------------------------


@router.post("/properties/{property_id}/contracts", status_code=201)
def add_contract(
    property_id: int, body: ContractIn, request: Request, conn: Db, me: CurrentMember
) -> dict:
    must_exist(estates.get_property, conn, property_id)
    values = body.model_dump(exclude={"owner"})
    estates.add_contract(conn, property_id, owner_or_me(conn, body.owner, me), **values)
    return _detail(conn, request, property_id)


@router.patch("/property-contracts/{contract_id}")
def update_contract(
    contract_id: int, body: ContractPatch, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    c = must_exist(estates.get_contract, conn, contract_id)
    values = changes(body)
    owner = values.pop("owner", None)
    if owner:
        values["owner"] = get_member(conn, owner)
    estates.update_contract(conn, contract_id, **values)
    return _detail(conn, request, c.property_id)


@router.post("/property-contracts/{contract_id}/end")
def end_contract(
    contract_id: int, body: EndContractIn, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    c = must_exist(estates.get_contract, conn, contract_id)
    estates.end_contract(conn, contract_id, body.ended_on)
    return _detail(conn, request, c.property_id)


# --- 시세 ---------------------------------------------------------------------


@router.put("/properties/{property_id}/valuations/{year_month}")
def set_valuation(
    property_id: int,
    year_month: str,
    body: ValuationIn,
    request: Request,
    conn: Db,
    _me: CurrentMember,
) -> dict:
    must_exist(estates.get_property, conn, property_id)
    estates.set_valuation(
        conn, property_id, year_month, body.value, source=body.source, memo=body.memo
    )
    return _detail(conn, request, property_id)


@router.delete("/properties/{property_id}/valuations/{year_month}")
def delete_valuation(
    property_id: int, year_month: str, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    must_exist(estates.get_property, conn, property_id)
    deleted = estates.delete_valuation(conn, property_id, year_month)
    return {"deleted": deleted, "property": _detail(conn, request, property_id)}
