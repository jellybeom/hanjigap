"""자동차 API (F3b): 차량 · 직접 넣는 시세 · 유지비 규칙 · 거래 붙이기·빼기.

차를 등록하면 기본 유지비 규칙도 같은 트랜잭션에서 만든다.
차 상세에는 이번 달(판 차는 판 달) 추정 시세와 계산식, 기준값, 연결된 고정비가 함께 들어간다.
유지비 규칙의 owner는 '누구의 거래에 적용할지'다. 비우면 모든 구성원.
    결제수단 규칙은 사람마다 이름이 달라서 owner가 꼭 필요하다.
유지비 합계·시세 추이 그래프는 F4에서.
거래 하나가 차마다 유지비로 들어가는지(GET /vehicles/tx-status)는
    거래 상세의 '차 유지비'에 쓴다. (G7a)
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from hanjigap.db import vehicle_costs, vehicles
from hanjigap.db.members import get_member
from hanjigap.db.vehicle_costs import CostRule
from hanjigap.db.vehicles import Vehicle
from hanjigap.web.common import changes, member_slugs, must_exist, owner_or_me, today
from hanjigap.web.deps import CurrentMember, Db
from hanjigap.web.fixed_api import fixed_out

router = APIRouter(prefix="/api", tags=["vehicles"])


class VehicleIn(BaseModel):
    owner: str | None = None
    name: str
    purchased_on: str
    purchase_price: int
    model_year: int | None = None
    depreciation_pct: float = vehicles.DEFAULT_DEPRECIATION
    bs_asset_name: str | None = None
    inspection_due_on: str | None = None
    color: str | None = None
    memo: str | None = None


class VehiclePatch(BaseModel):
    name: str | None = None
    purchased_on: str | None = None
    purchase_price: int | None = None
    model_year: int | None = None
    depreciation_pct: float | None = None
    bs_asset_name: str | None = None
    inspection_due_on: str | None = None
    color: str | None = None
    memo: str | None = None


class SoldIn(BaseModel):
    sold_on: str | None  # null이면 매각 취소


class ValuationIn(BaseModel):
    value: int
    memo: str | None = None


class RuleIn(BaseModel):
    kind: str
    cost_type: str
    category: str | None = None
    subcategory: str | None = None
    pattern: str | None = None
    owner: str | None = None  # 비우면 모든 구성원


class RulePatch(BaseModel):
    active: bool


class TagIn(BaseModel):
    owner: str | None = None  # 거래 주인. 비우면 로그인한 사람
    tx_key: str
    include: bool
    cost_type: str | None = None


# --- 응답 형식 -------------------------------------------------------------------


def _estimate_out(conn: sqlite3.Connection, v: Vehicle, this_month: str) -> dict | None:
    month = min(this_month, v.sold_on[:7]) if v.sold_on else this_month
    e = vehicles.estimate(conn, v, month)
    if e is None:
        return None
    return {
        "year_month": e.year_month,
        "value": e.value,
        "formula": e.formula,
        "anchor": {
            "year_month": e.anchor.year_month,
            "value": e.anchor.value,
            "source": e.anchor.source,
            "label": e.anchor.label,
        },
        "months": e.months,
        "rate_pct": e.rate_pct,
        "monthly_pct": round(e.monthly_pct, 2),
    }


def vehicle_out(
    conn: sqlite3.Connection, v: Vehicle, slugs: dict[int, str], request: Request
) -> dict:
    day = today(request)
    days_left = None
    if v.inspection_due_on:
        days_left = (dt.date.fromisoformat(v.inspection_due_on) - day).days
    return {
        "id": v.id,
        "owner": slugs[v.owner_member_id],
        "name": v.name,
        "model_year": v.model_year,
        "purchased_on": v.purchased_on,
        "purchase_price": v.purchase_price,
        "depreciation_pct": v.depreciation_pct,
        "bs_asset_name": v.bs_asset_name,
        "inspection_due_on": v.inspection_due_on,
        "inspection_days_left": days_left,
        "sold_on": v.sold_on,
        "active": v.active,
        "color": v.color,
        "memo": v.memo,
        "estimate": _estimate_out(conn, v, day.isoformat()[:7]),
    }


def _detail(conn: sqlite3.Connection, request: Request, vehicle_id: int) -> dict:
    v = vehicles.get_vehicle(conn, vehicle_id)
    slugs = member_slugs(conn)
    out = vehicle_out(conn, v, slugs, request)
    out["anchors"] = [
        {
            "year_month": a.year_month,
            "value": a.value,
            "source": a.source,
            "source_label": vehicles.SOURCE_LABELS[a.source],
            "label": a.label,
        }
        for a in vehicles.anchors(conn, v)
    ]
    out["valuations"] = [
        dict(r)
        for r in conn.execute(
            "SELECT year_month, value, memo FROM vehicle_valuations WHERE vehicle_id = ? "
            "ORDER BY year_month",
            (v.id,),
        )
    ]
    this_month = today(request).isoformat()[:7]
    out["fixed_items"] = [
        fixed_out(conn, i, slugs, this_month) for i in vehicles.vehicle_fixed_items(conn, v)
    ]
    return out


def rule_out(r: CostRule, slugs: dict[int, str]) -> dict:
    return {
        "id": r.id,
        "vehicle_id": r.vehicle_id,
        "kind": r.kind,
        "kind_label": vehicle_costs.RULE_KINDS[r.kind],
        "owner": slugs[r.member_id] if r.member_id is not None else None,
        "category": r.category,
        "subcategory": r.subcategory,
        "pattern": r.pattern,
        "cost_type": r.cost_type,
        "cost_type_label": vehicle_costs.COST_TYPES[r.cost_type],
        "active": r.active,
        "label": r.label,
    }


# --- 차량 ----------------------------------------------------------------------


@router.get("/vehicles")
def list_vehicles(
    request: Request, conn: Db, _me: CurrentMember, include_sold: bool = False
) -> dict:
    slugs = member_slugs(conn)
    items = vehicles.list_vehicles(conn, include_sold=include_sold)
    return {"items": [vehicle_out(conn, v, slugs, request) for v in items]}


@router.post("/vehicles", status_code=201)
def add_vehicle(body: VehicleIn, request: Request, conn: Db, me: CurrentMember) -> dict:
    values = body.model_dump(exclude={"owner"})
    v = vehicles.add_vehicle(conn, owner_or_me(conn, body.owner, me), **values)
    vehicle_costs.create_default_rules(conn, v)
    return _detail(conn, request, v.id)


# --- 거래 하나의 유지비 여부 (G7a) ----------------------------------------------------


@router.get("/vehicles/tx-status")
def tx_status(owner: str, tx_key: str, conn: Db, _me: CurrentMember) -> dict:
    """그 거래가 차마다 유지비로 들어가는지. 그 달에 가지고 있던 차만 (산 달 ~ 판 달)."""
    member = get_member(conn, owner)
    row = conn.execute(
        "SELECT year_month FROM transactions WHERE member_id = ? AND tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "거래를 찾을 수 없어요")
    ym = row["year_month"]
    slugs = member_slugs(conn)
    items = []
    for v in vehicles.list_vehicles(conn, include_sold=True):
        if ym < v.purchase_month or (v.sold_on and ym > v.sold_on[:7]):
            continue
        report = vehicle_costs.vehicle_costs(conn, v, ym, ym)
        hit = next(
            (i for i in report.items if i.member_id == member.id and i.tx_key == tx_key), None
        )
        tag = conn.execute(
            "SELECT action FROM vehicle_tx_tags WHERE vehicle_id = ? AND member_id = ? "
            "AND tx_key = ?",
            (v.id, member.id, tx_key),
        ).fetchone()
        items.append(
            {
                "vehicle": {"id": v.id, "name": v.name, "owner": slugs[v.owner_member_id]},
                "counted": hit is not None,
                "cost_type": hit.cost_type if hit else None,
                "cost_label": hit.cost_label if hit else None,
                "reason": hit.reason if hit else None,
                "tag": tag["action"] if tag else None,
            }
        )
    return {"items": items}


@router.get("/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: int, request: Request, conn: Db, _me: CurrentMember) -> dict:
    must_exist(vehicles.get_vehicle, conn, vehicle_id)
    return _detail(conn, request, vehicle_id)


@router.patch("/vehicles/{vehicle_id}")
def update_vehicle(
    vehicle_id: int, body: VehiclePatch, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    must_exist(vehicles.get_vehicle, conn, vehicle_id)
    vehicles.update_vehicle(conn, vehicle_id, **changes(body))
    return _detail(conn, request, vehicle_id)


@router.post("/vehicles/{vehicle_id}/sold")
def mark_sold(
    vehicle_id: int, body: SoldIn, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    must_exist(vehicles.get_vehicle, conn, vehicle_id)
    vehicles.mark_sold(conn, vehicle_id, body.sold_on)
    return _detail(conn, request, vehicle_id)


# --- 직접 넣는 시세 ---------------------------------------------------------------


@router.put("/vehicles/{vehicle_id}/valuations/{year_month}")
def set_valuation(
    vehicle_id: int,
    year_month: str,
    body: ValuationIn,
    request: Request,
    conn: Db,
    _me: CurrentMember,
) -> dict:
    must_exist(vehicles.get_vehicle, conn, vehicle_id)
    vehicles.set_valuation(conn, vehicle_id, year_month, body.value, body.memo)
    return _detail(conn, request, vehicle_id)


@router.delete("/vehicles/{vehicle_id}/valuations/{year_month}")
def delete_valuation(
    vehicle_id: int, year_month: str, request: Request, conn: Db, _me: CurrentMember
) -> dict:
    must_exist(vehicles.get_vehicle, conn, vehicle_id)
    deleted = vehicles.delete_valuation(conn, vehicle_id, year_month)
    return {"deleted": deleted, "vehicle": _detail(conn, request, vehicle_id)}


# --- 유지비 규칙 ------------------------------------------------------------------


@router.get("/vehicles/{vehicle_id}/cost-rules")
def list_rules(
    vehicle_id: int, conn: Db, _me: CurrentMember, include_inactive: bool = True
) -> dict:
    v = must_exist(vehicles.get_vehicle, conn, vehicle_id)
    slugs = member_slugs(conn)
    rules = vehicle_costs.list_rules(conn, v, include_inactive=include_inactive)
    return {"items": [rule_out(r, slugs) for r in rules]}


@router.post("/vehicles/{vehicle_id}/cost-rules", status_code=201)
def add_rule(vehicle_id: int, body: RuleIn, conn: Db, _me: CurrentMember) -> dict:
    v = must_exist(vehicles.get_vehicle, conn, vehicle_id)
    member = get_member(conn, body.owner) if body.owner else None
    r = vehicle_costs.add_rule(
        conn,
        v,
        body.kind,
        body.cost_type,
        category=body.category,
        subcategory=body.subcategory,
        pattern=body.pattern,
        member=member,
    )
    return rule_out(r, member_slugs(conn))


@router.patch("/vehicle-cost-rules/{rule_id}")
def set_rule_active(rule_id: int, body: RulePatch, conn: Db, _me: CurrentMember) -> dict:
    must_exist(vehicle_costs.get_rule, conn, rule_id)
    r = vehicle_costs.set_rule_active(conn, rule_id, body.active)
    return rule_out(r, member_slugs(conn))


@router.delete("/vehicle-cost-rules/{rule_id}")
def delete_rule(rule_id: int, conn: Db, _me: CurrentMember) -> dict:
    must_exist(vehicle_costs.get_rule, conn, rule_id)
    vehicle_costs.delete_rule(conn, rule_id)
    return {"deleted": True}


# --- 거래 붙이기·빼기 -------------------------------------------------------------


def _tags(conn: sqlite3.Connection, vehicle_id: int, slugs: dict[int, str]) -> list[dict]:
    rows = conn.execute(
        "SELECT member_id, tx_key, action, cost_type, tagged_by, tagged_at "
        "FROM vehicle_tx_tags WHERE vehicle_id = ? ORDER BY tagged_at, tx_key",
        (vehicle_id,),
    )
    return [
        {
            "owner": slugs[r["member_id"]],
            "tx_key": r["tx_key"],
            "include": r["action"] == "include",
            "cost_type": r["cost_type"],
            "tagged_by": slugs.get(r["tagged_by"]),
            "tagged_at": r["tagged_at"],
        }
        for r in rows
    ]


@router.get("/vehicles/{vehicle_id}/tags")
def list_tags(vehicle_id: int, conn: Db, _me: CurrentMember) -> dict:
    must_exist(vehicles.get_vehicle, conn, vehicle_id)
    return {"items": _tags(conn, vehicle_id, member_slugs(conn))}


@router.put("/vehicles/{vehicle_id}/tags")
def tag_tx(vehicle_id: int, body: TagIn, conn: Db, me: CurrentMember) -> dict:
    v = must_exist(vehicles.get_vehicle, conn, vehicle_id)
    member = owner_or_me(conn, body.owner, me)
    vehicle_costs.tag_tx(
        conn,
        v,
        member,
        body.tx_key,
        include=body.include,
        cost_type=body.cost_type,
        tagged_by=me,
    )
    slugs = member_slugs(conn)
    return next(
        t
        for t in _tags(conn, vehicle_id, slugs)
        if t["owner"] == member.slug and t["tx_key"] == body.tx_key
    )


@router.delete("/vehicles/{vehicle_id}/tags/{owner}/{tx_key}")
def untag_tx(vehicle_id: int, owner: str, tx_key: str, conn: Db, _me: CurrentMember) -> dict:
    v = must_exist(vehicles.get_vehicle, conn, vehicle_id)
    return {"deleted": vehicle_costs.untag_tx(conn, v, get_member(conn, owner), tx_key)}


# --- 유지비 · 시세 추이 (F4b) -------------------------------------------------------


def _shift_month(ym: str, delta: int) -> str:
    total = int(ym[:4]) * 12 + int(ym[5:7]) - 1 + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


@router.get("/vehicles/{vehicle_id}/costs")
def costs(
    vehicle_id: int,
    request: Request,
    conn: Db,
    _me: CurrentMember,
    start: str | None = Query(None, alias="from"),
    end: str | None = Query(None, alias="to"),
) -> dict:
    """유지비. 기간을 비우면 지난달까지 12개월. (산 달·판 달 밖은 잘라 낸다)"""
    v = must_exist(vehicles.get_vehicle, conn, vehicle_id)
    last = _shift_month(today(request).isoformat()[:7], -1)
    end = end or last
    start = start or _shift_month(end, -11)
    report = vehicle_costs.vehicle_costs(conn, v, start, end)
    slugs = member_slugs(conn)
    return {
        "from": report.start_month,
        "to": report.end_month,
        "total": report.total,
        "months": report.months,
        "monthly_average": report.monthly_average,
        "by_type": [
            {"cost_type": k, "label": vehicle_costs.COST_TYPES[k], "amount": a}
            for k, a in report.by_type().items()
        ],
        "by_month": [{"month": k, "amount": a} for k, a in report.by_month().items()],
        "items": [
            {
                "owner": slugs[i.member_id],
                "tx_key": i.tx_key,
                "date": i.date,
                "content": i.content,
                "instrument": i.instrument,
                "category": i.category,
                "subcategory": i.subcategory,
                "cost": i.cost,
                "cost_type": i.cost_type,
                "cost_label": i.cost_label,
                "reason": i.reason,
                "manual": i.manual,
            }
            for i in sorted(report.items, key=lambda i: i.date, reverse=True)
        ],
    }


@router.get("/vehicles/{vehicle_id}/value-series")
def value_series(
    vehicle_id: int, request: Request, conn: Db, _me: CurrentMember, until: str | None = None
) -> dict:
    """산 달부터 until(비우면 이번 달)까지 달마다 추정 시세. 기준값이 있는 달은 anchor=true."""
    v = must_exist(vehicles.get_vehicle, conn, vehicle_id)
    end = until or today(request).isoformat()[:7]
    if v.sold_on:
        end = min(end, v.sold_on[:7])
    series = vehicles.value_series(conn, v, end)
    return {"items": [{"month": m, "value": val, "anchor": a} for m, val, a in series]}
