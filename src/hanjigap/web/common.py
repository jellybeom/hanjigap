"""여러 API가 함께 쓰는 도우미.

주인(owner)
    응답에는 구성원 번호 대신 slug를 쓴다. 등록할 때 owner를 비우면 로그인한 사람이 주인이다.
    두 사람 모두 모든 항목을 보고 고칠 수 있다.
없는 항목
    주소에 들어간 번호(/api/accounts/7)가 없으면 404.
    요청 내용 안에서 가리킨 항목(결제 계좌 등)이 없으면 400. (모듈 메시지 그대로)
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from hanjigap.db.members import Member, get_member, list_members
from hanjigap.db.validate import InputError
from hanjigap.web.deps import get_settings_from


def owner_or_me(conn: sqlite3.Connection, owner: str | None, me: Member) -> Member:
    """slug로 구성원 찾기. 비어 있으면 로그인한 사람. 없으면 400 (MemberError)."""
    if owner is None or not owner.strip():
        return me
    return get_member(conn, owner.strip())


def member_slugs(conn: sqlite3.Connection) -> dict[int, str]:
    return {m.id: m.slug for m in list_members(conn)}


def must_exist[T](getter: Callable[..., T], conn: sqlite3.Connection, item_id: int) -> T:
    """주소의 번호로 찾는다. 없으면 404."""
    try:
        return getter(conn, item_id)
    except InputError as exc:
        raise HTTPException(404, str(exc)) from None


def today(request: Request) -> dt.date:
    return dt.datetime.now(get_settings_from(request).tz).date()


def changes(model: Any) -> dict[str, Any]:
    """PATCH: 보낸 칸만. 바꿀 게 없으면 400."""
    values = model.model_dump(exclude_unset=True)
    if not values:
        raise InputError("바꿀 내용이 없어요")
    return values


def choices(codes: dict[str, str]) -> list[dict[str, str]]:
    return [{"code": k, "label": v} for k, v in codes.items()]
