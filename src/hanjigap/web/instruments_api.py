"""뱅샐 결제수단 연결 API (F3a).

결제수단 이름은 사람마다 다르다 (각자의 뱅샐 파일에 나온 이름). 그래서 owner와 함께 다룬다.
이름에 '/' 같은 기호가 들어갈 수 있어 주소가 아니라 요청 내용으로 받는다.
결제수단 이름은 실제 카드·통장 이름이므로 로그에 남기지 않는다. (요청 로그는 경로 틀만)
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter
from pydantic import BaseModel

from hanjigap.db import holdings
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError, code
from hanjigap.web.common import owner_or_me
from hanjigap.web.deps import CurrentMember, Db

router = APIRouter(prefix="/api", tags=["instruments"])


class LinkIn(BaseModel):
    owner: str | None = None
    instrument: str
    target: str  # account | card | easy_pay (한글 이름도 받음)
    target_id: int | None = None  # 계좌·카드 번호. 간편결제는 비움


class UnlinkIn(BaseModel):
    owner: str | None = None
    instrument: str


def _link_ids(conn: sqlite3.Connection, member: Member) -> dict[str, int | None]:
    rows = conn.execute(
        "SELECT instrument, coalesce(account_id, card_id) AS target_id "
        "FROM instrument_links WHERE member_id = ?",
        (member.id,),
    )
    return {r["instrument"]: r["target_id"] for r in rows}


def _usage_out(u: holdings.InstrumentUsage, ids: dict[str, int | None]) -> dict:
    return {
        "instrument": u.instrument,
        "count": u.count,
        "first_date": u.first_date,
        "last_date": u.last_date,
        "expense": u.expense,
        "income": u.income,
        "transfer": u.transfer,
        "linked": u.linked,
        "target": u.target,
        "target_label": holdings.TARGET_LABELS.get(u.target or ""),
        "target_id": ids.get(u.instrument),
        "target_name": u.target_name,
        "link_label": u.link_label,
    }


def _one(conn: sqlite3.Connection, member: Member, instrument: str) -> dict:
    ids = _link_ids(conn, member)
    for u in holdings.instrument_usage(conn, member):
        if u.instrument == instrument:
            return _usage_out(u, ids)
    raise InputError("이 구성원의 거래에 그런 결제수단 이름이 없어요")  # 연결 직후라 생기지 않음


@router.get("/instruments")
def list_instruments(
    conn: Db, me: CurrentMember, owner: str | None = None, unlinked: bool = False
) -> dict:
    """그 사람 거래에 나온 결제수단 이름별 건수·기간·연결 상태. 건수 많은 순."""
    member = owner_or_me(conn, owner, me)
    ids = _link_ids(conn, member)
    items = [_usage_out(u, ids) for u in holdings.instrument_usage(conn, member)]
    if unlinked:
        items = [i for i in items if not i["linked"]]
    return {"owner": member.slug, "items": items}


@router.put("/instruments/link")
def link(body: LinkIn, conn: Db, me: CurrentMember) -> dict:
    member = owner_or_me(conn, body.owner, me)
    target = code(body.target, holdings.TARGET_LABELS, "연결 대상을")
    if target == "easy_pay":
        if body.target_id is not None:
            raise InputError("간편결제는 계좌·카드 번호 없이 연결해요")
        kwargs: dict = {"easy_pay": True}
    else:
        if body.target_id is None:
            raise InputError("연결할 계좌·카드를 고르세요")
        kwargs = {f"{target}_id": body.target_id}
    holdings.link_instrument(conn, member, body.instrument, linked_by=me, **kwargs)
    return _one(conn, member, body.instrument)


@router.post("/instruments/unlink")
def unlink(body: UnlinkIn, conn: Db, me: CurrentMember) -> dict:
    member = owner_or_me(conn, body.owner, me)
    return {"unlinked": holdings.unlink_instrument(conn, member, body.instrument)}
