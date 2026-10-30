"""분류 규칙 API (F4a).

규칙은 위에서부터 본다. 새 규칙은 맨 위. 순서는 PUT /api/class-rules/order 로 통째로 바꾼다.
owner: 누구의 거래에 쓸지. 비우면 두 사람 모두. PATCH에서 ''를 보내면 '두 사람 모두'로 바꾼다.
조건 칸은 PATCH에서 ''(금액은 0)를 보내면 비운다.
미리보기(POST /api/class-rules/preview): 저장하지 않고, 이 조건에 맞는 거래 수와 최근 예시 3건. (G8)
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from hanjigap.db import class_rules
from hanjigap.db.class_rules import DIRECTIONS, RESULTS, ClassRule
from hanjigap.db.members import get_member
from hanjigap.web.common import member_slugs, must_exist
from hanjigap.web.deps import CurrentMember, Db

router = APIRouter(prefix="/api", tags=["class-rules"])


class RuleIn(BaseModel):
    result: str
    owner: str | None = None
    direction: str = "any"
    tx_type: str | None = None
    category: str | None = None
    subcategory: str | None = None
    pattern: str | None = None
    instrument: str | None = None
    amount: int | None = None
    result_category: str | None = None
    memo: str | None = None


class RulePatch(BaseModel):
    result: str | None = None
    owner: str | None = None
    direction: str | None = None
    tx_type: str | None = None
    category: str | None = None
    subcategory: str | None = None
    pattern: str | None = None
    instrument: str | None = None
    amount: int | None = None
    result_category: str | None = None
    memo: str | None = None
    active: bool | None = None


class PreviewIn(RuleIn):
    result: str | None = None  # 미리보기에서는 비워도 된다


class OrderIn(BaseModel):
    ids: list[int]


def rule_out(r: ClassRule, slugs: dict[int, str]) -> dict:
    return {
        "id": r.id,
        "position": r.position,
        "owner": None if r.member_id is None else slugs[r.member_id],
        "direction": r.direction,
        "direction_label": DIRECTIONS[r.direction],
        "tx_type": r.tx_type,
        "category": r.category,
        "subcategory": r.subcategory,
        "pattern": r.pattern,
        "instrument": r.instrument,
        "amount": r.amount,
        "result": r.result,
        "result_label": RESULTS[r.result],
        "result_category": r.result_category,
        "memo": r.memo,
        "active": r.active,
        "label": r.label,
    }


@router.get("/class-rules")
def list_rules(conn: Db, _me: CurrentMember) -> dict:
    slugs = member_slugs(conn)
    return {"items": [rule_out(r, slugs) for r in class_rules.list_rules(conn)]}


@router.post("/class-rules", status_code=201)
def add_rule(body: RuleIn, conn: Db, me: CurrentMember) -> dict:
    values = body.model_dump(exclude={"owner"})
    member = get_member(conn, body.owner) if body.owner else None
    r = class_rules.add_rule(conn, member=member, created_by=me, **values)
    return rule_out(r, member_slugs(conn))


@router.post("/class-rules/preview")
def preview(body: PreviewIn, conn: Db, _me: CurrentMember) -> dict:
    """저장하지 않는다. 조건에 맞는 거래 수 (직접 지정한 거래는 따로) · 최근 예시."""
    values = body.model_dump(exclude={"owner"})
    member = get_member(conn, body.owner) if body.owner else None
    p = class_rules.preview_rule(conn, member=member, **values)
    slugs = member_slugs(conn)
    return {
        **p,
        "examples": [
            {
                "owner": slugs[x["member_id"]],
                "tx_key": x["tx_key"],
                "date": x["tx_date"],
                "content": x["content"],
                "amount": x["amount"],
                "category": x["category"],
                "instrument": x["instrument"],
            }
            for x in p["examples"]
        ],
    }


@router.patch("/class-rules/{rule_id}")
def update_rule(rule_id: int, body: RulePatch, conn: Db, _me: CurrentMember) -> dict:
    must_exist(class_rules.get_rule, conn, rule_id)
    values = body.model_dump(exclude_unset=True)
    active = values.pop("active", None)
    if "owner" in values:
        owner = values.pop("owner")
        values["member"] = get_member(conn, owner) if owner else None
    if not values and active is None:
        class_rules.update_rule(conn, rule_id)  # '바꿀 내용이 없어요'
    if values:
        class_rules.update_rule(conn, rule_id, **values)
    if active is not None:
        class_rules.set_rule_active(conn, rule_id, active)
    return rule_out(class_rules.get_rule(conn, rule_id), member_slugs(conn))


@router.delete("/class-rules/{rule_id}")
def delete_rule(rule_id: int, conn: Db, _me: CurrentMember) -> dict:
    must_exist(class_rules.get_rule, conn, rule_id)
    class_rules.delete_rule(conn, rule_id)
    return {"deleted": True}


@router.put("/class-rules/order")
def reorder(body: OrderIn, conn: Db, _me: CurrentMember) -> dict:
    slugs = member_slugs(conn)
    return {"items": [rule_out(r, slugs) for r in class_rules.reorder_rules(conn, body.ids)]}
