"""구성원 · 별칭 API (F4a).

별칭은 부부 이체 자동 인식에 쓴다 (실명·애칭 등). 설정 화면에서 추가·삭제한다.
이메일은 담지 않는다. (로그인 확인용으로 DB에만)
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hanjigap.db.members import (
    Member,
    MemberError,
    add_aliases,
    get_member,
    list_aliases,
    list_members,
    remove_aliases,
)
from hanjigap.web.deps import CurrentMember, Db

router = APIRouter(prefix="/api", tags=["members"])


class AliasesIn(BaseModel):
    add: list[str] = []
    remove: list[str] = []


def member_out(conn: sqlite3.Connection, m: Member) -> dict:
    aliases = list_aliases(conn, m.slug)
    return {
        "slug": m.slug,
        "name": m.display_name,
        "aliases": aliases,
        "search_names": [m.display_name, *aliases],
    }


@router.get("/members")
def members(conn: Db, _me: CurrentMember) -> dict:
    return {"items": [member_out(conn, m) for m in list_members(conn)]}


@router.put("/members/{slug}/aliases")
def update_aliases(slug: str, body: AliasesIn, conn: Db, _me: CurrentMember) -> dict:
    try:
        member = get_member(conn, slug)
    except MemberError as exc:
        raise HTTPException(404, str(exc)) from None
    if body.remove:
        remove_aliases(conn, slug, body.remove)
    if body.add:
        add_aliases(conn, slug, body.add)
    return member_out(conn, member)
