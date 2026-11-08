"""한지갑에서 뺄 뱅샐현황 항목 API (0011).

각자 용돈 통장·마이너스 통장처럼 서로 터치하지 않는 개인 영역을 한지갑 집계에서 뺀다.
- 언제나 로그인한 본인 것만 다룬다. (owner를 받지 않는다. 상대의 규칙·항목은 보이지 않음)
- 고를 항목은 본인의 가장 최근 뱅샐현황 원본에서 보여 준다.
  뺀 항목은 자산·홈 화면 어디에도 안 나온다.
- 같은 것이 재무현황과 상세 표(대출현황·투자현황)에 함께 있으면 짝(pairs)으로 알려 함께 빼게 한다.
- 로그에는 개수만 남긴다. (상품명·금액은 남기지 않음)
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hanjigap.db import holding_excludes as hx
from hanjigap.db.members import Member
from hanjigap.db.snapshots import latest_snapshot
from hanjigap.db.validate import InputError
from hanjigap.importer.status_tables import SECTION_LABELS, Holding
from hanjigap.web.deps import CurrentMember, Db

log = logging.getLogger("hanjigap.web")
router = APIRouter(prefix="/api", tags=["excludes"])


class ItemIn(BaseModel):
    section: str
    group: str | None = None
    institution: str | None = None
    name: str


class ExcludeIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=10)


def _holding_out(h: Holding) -> dict:
    return {
        "section": h.section,
        "section_label": SECTION_LABELS[h.section],
        "group": h.group,
        "institution": h.institution,
        "name": h.name,
        "amount": h.amount,
    }


def _state(conn: sqlite3.Connection, me: Member) -> dict:
    snapshot = latest_snapshot(conn, me)  # 원본 (뺀 것도 포함) — 본인에게만 보여 줌
    rules = hx.list_rules(conn, me)
    keys = {r.key for r in rules}
    holdings = [h for h in snapshot.holdings if h.name] if snapshot else []
    by_key = {hx.key_of(h): h for h in holdings}
    candidates = sorted(
        (h for h in holdings if hx.key_of(h) not in keys),
        key=lambda h: hx.SECTION_ORDER.index(h.section),
    )
    index = {hx.key_of(h): i for i, h in enumerate(candidates)}
    return {
        "snapshot": (
            None if snapshot is None else {"as_of": snapshot.as_of, "month": snapshot.year_month}
        ),
        "items": [
            {
                "id": r.id,
                "section": r.section,
                "section_label": r.section_label,
                "group": r.group or None,
                "institution": r.institution or None,
                "name": r.name,
                "found": r.key in by_key,
                "amount": by_key[r.key].amount if r.key in by_key else None,
            }
            for r in rules
        ],
        "candidates": [
            {
                **_holding_out(h),
                "pairs": [index[hx.key_of(p)] for p in hx.pairs(candidates, h)],
            }
            for h in candidates
        ],
    }


@router.get("/holding-excludes")
def get_excludes(conn: Db, me: CurrentMember) -> dict:
    """내가 뺀 항목과, 뺄 수 있는 내 항목 (가장 최근 뱅샐현황 기준)"""
    return _state(conn, me)


@router.post("/holding-excludes")
def add_excludes(body: ExcludeIn, conn: Db, me: CurrentMember) -> dict:
    snapshot = latest_snapshot(conn, me)
    have = {hx.key_of(h) for h in snapshot.holdings} if snapshot else set()
    for item in body.items:
        key = (item.section, item.group or "", item.institution or "", item.name)
        if key not in have:
            raise InputError("내 최근 뱅샐현황에 없는 항목이에요. 화면을 새로 고쳐 주세요")
        hx.add_rule(conn, me, key)
    log.info("뺄 항목 추가: %d개", len(body.items))
    return _state(conn, me)


@router.delete("/holding-excludes/{rule_id}")
def remove_exclude(rule_id: int, conn: Db, me: CurrentMember) -> dict:
    if not hx.remove_rule(conn, me, rule_id):
        raise HTTPException(404, "내가 뺀 항목 중에 없어요")
    log.info("뺄 항목 다시 넣기: 1개")
    return _state(conn, me)
