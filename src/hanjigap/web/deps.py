"""요청마다 쓰는 준비물: DB 연결, 로그인한 구성원.

DB
    요청마다 새로 연결하고 끝나면 닫는다.
    쓰는 요청(POST·PUT·PATCH·DELETE)은 하나의 트랜잭션으로 묶는다.
    성공하면 응답을 보내기 '전에' 저장한다. 저장이 실패하면 오류로 답한다.
    중간에 오류가 나면 모두 취소한다.

로그인
    개발 모드: HANJIGAP_DEV_MEMBER에 적은 구성원으로 본다. (env=dev일 때만)
    운영 모드: Cloudflare Access 토큰 검증 (F2에서 추가). 그 전까지는 모두 거부한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from hanjigap.config import Settings
from hanjigap.db.connection import DatabaseError, connect_ready
from hanjigap.db.members import Member, MemberError, get_member

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DB_NOT_READY = "DB가 아직 준비되지 않았어요. 미니PC에서 'uv run hanjigap db init'을 실행하세요"


def get_settings_from(request: Request) -> Settings:
    return request.app.state.settings


def _db(request: Request) -> Iterator[sqlite3.Connection]:
    settings = get_settings_from(request)
    try:
        conn = connect_ready(settings.db_path)
    except DatabaseError:
        raise HTTPException(503, DB_NOT_READY) from None
    try:
        if request.method in UNSAFE_METHODS:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if conn.in_transaction:
            conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# scope="function": 경로 함수가 끝나면 바로 저장하고, 그다음에 응답을 보낸다.
Db = Annotated[sqlite3.Connection, Depends(_db, scope="function")]


def _current_member(request: Request, conn: Db) -> Member:
    settings = get_settings_from(request)
    if settings.env == "dev" and settings.dev_member:
        try:
            return get_member(conn, settings.dev_member)
        except MemberError:
            raise HTTPException(
                401, "개발 모드 구성원을 찾을 수 없어요. HANJIGAP_DEV_MEMBER를 확인하세요"
            ) from None
    raise HTTPException(401, "로그인 정보가 없어요")


CurrentMember = Annotated[Member, Depends(_current_member)]
