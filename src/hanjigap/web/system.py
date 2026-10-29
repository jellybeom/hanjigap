"""서버 상태 · 로그인한 사람."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hanjigap import __version__
from hanjigap.db.connection import DatabaseError, connect_ready
from hanjigap.db.members import list_members
from hanjigap.web.deps import DB_NOT_READY, CurrentMember, Db, get_settings_from
from hanjigap.web.errors import error_response

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(request: Request) -> JSONResponse:
    """서버와 DB가 준비됐는지. 로그인 없이 볼 수 있으므로 버전 외 정보는 담지 않는다."""
    try:
        connect_ready(get_settings_from(request).db_path).close()
    except DatabaseError:
        return error_response(503, DB_NOT_READY)
    return JSONResponse({"status": "ok", "version": __version__})


@router.get("/me")
def me(member: CurrentMember, conn: Db) -> dict:
    """로그인한 사람과 구성원 목록 (화면의 이름·담당 표시용). 이메일은 담지 않는다."""
    return {
        "member": {"slug": member.slug, "name": member.display_name},
        "members": [{"slug": m.slug, "name": m.display_name} for m in list_members(conn)],
    }
