"""서버 상태 · 로그인한 사람 · 백업 (H1).

백업 화면에는 시각·종류·개수만 보낸다. 폴더 경로는 보내지 않는다. (미니PC 안의 정보)
되돌리기는 웹에서 하지 않는다. 미니PC에서 서버를 끄고 명령어로만.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hanjigap import __version__
from hanjigap.config import Settings
from hanjigap.db.backup import KINDS, BackupError, list_backups, make_backup
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


# --- 백업 ----------------------------------------------------------------------


def _backup_out(b) -> dict | None:
    if b is None:
        return None
    return {"at": b.at.isoformat(timespec="seconds"), "kind": b.kind, "label": KINDS[b.kind]}


def backup_state(settings: Settings) -> dict:
    items = list_backups(settings.backup_dir)
    copy_dir = settings.copy_dir
    copies = list_backups(copy_dir) if copy_dir else []
    return {
        "last": _backup_out(items[0] if items else None),
        "count": len(items),
        "counts": {k: sum(1 for b in items if b.kind == k) for k in KINDS},
        "copy": {
            "on": copy_dir is not None,
            "count": len(copies),
            "last": _backup_out(copies[0] if copies else None),
        },
    }


@router.get("/backups")
def backups(request: Request, _member: CurrentMember) -> dict:
    return backup_state(get_settings_from(request))


@router.post("/backups")
def backup_now(request: Request, _member: CurrentMember):
    settings = get_settings_from(request)
    try:
        result = make_backup(
            settings.db_path,
            settings.backup_dir,
            "manual",
            now=settings.now(),
            copy_dir=settings.copy_dir,
        )
    except BackupError:
        return error_response(500, "백업을 만들지 못했어요. 미니PC의 백업 폴더를 확인해 주세요")
    message = "백업했어요"
    if result.copied is False:
        message = "백업했어요. 다만 두 번째 사본은 복사하지 못했어요 (드라이브를 확인해 주세요)"
    return {**backup_state(settings), "message": message}
