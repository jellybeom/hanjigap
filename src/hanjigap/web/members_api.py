"""구성원 · 별칭 API (F4a).

별칭은 부부 이체 자동 인식에 쓴다 (실명·애칭 등). 설정 화면에서 추가·삭제한다.
이름(화면에 보이는 이름)은 PATCH로 바꾼다. (G8)
이메일은 담지도 바꾸지도 않는다. 로그인 열쇠라서 미니PC 명령어(member update)로만 바꾼다.
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
    update_member,
)
from hanjigap.web.deps import CurrentMember, Db

router = APIRouter(prefix="/api", tags=["members"])


class MemberPatch(BaseModel):
    name: str


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


def _path_member(conn: sqlite3.Connection, slug: str) -> Member:
    try:
        return get_member(conn, slug)
    except MemberError as exc:
        raise HTTPException(404, str(exc)) from None


@router.patch("/members/{slug}")
def rename(slug: str, body: MemberPatch, conn: Db, _me: CurrentMember) -> dict:
    """화면에 보이는 이름만 바꾼다 (이메일은 여기서 바꾸지 않는다)."""
    _path_member(conn, slug)
    return member_out(conn, update_member(conn, slug, display_name=body.name))


@router.put("/members/{slug}/aliases")
def update_aliases(slug: str, body: AliasesIn, conn: Db, _me: CurrentMember) -> dict:
    member = _path_member(conn, slug)
    if body.remove:
        remove_aliases(conn, slug, body.remove)
    if body.add:
        add_aliases(conn, slug, body.add)
    return member_out(conn, member)
