"""웹에서 뱅샐 파일 올리기 (F5).

흐름
    1. POST /api/uploads            파일 + 비밀번호 → 미리보기 (DB는 바꾸지 않음)
    2. POST /api/uploads/{id}/apply 정산 확정 (저장 직전에 미리보기를 다시 계산)
       이미 정산한 달에 거래가 그대로면 재무현황 표만 저장한다 (그 달 기록이 없거나 다를 때).
       DELETE /api/uploads/{id}     취소
    처음 올리는 사람(정산 기록 없음)이면 두 가지 중 고른다. (확정할 때 {"mode": ...})
        settle      이 달만 넣기 (기본). 그 달 거래와 그 시점 재무현황으로 시작한다.
                    지난 달은 나중에 한 달씩 정산할 수 있다.
        first_load  지난 달도 함께 넣기. 파일의 완전한 지난 달을 '기초 데이터'로 함께 넣는다.
        미리보기는 '이 달만' 기준이고, 'history'에 지난 달도 넣을 때의 내용을 함께 준다.

안전장치
    비밀번호는 파일을 여는 데만 쓰고 저장·기록하지 않는다.
    파일은 디스크에 쓰지 않는다. 읽은 결과만 메모리에 15분 두고, 확정·취소·시간 초과면 지운다.
        (업로드 파일이 임시 파일로 디스크에 넘어가지 않게 메모리 한도를 올려 둔다. app.py)
    확정은 올린 사람만 할 수 있다. 20MB 이하 zip·xlsx만 받는다.
    로그에는 건수·결과만 남긴다. (파일 이름·내용·금액은 남기지 않음)
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from hanjigap.db.backup import BackupError, make_backup
from hanjigap.db.members import Member, get_member
from hanjigap.importer.errors import ImportFailure
from hanjigap.ledger.classify import classify
from hanjigap.settle.apply import apply_month, status_needs_saving
from hanjigap.settle.check import check_recent
from hanjigap.settle.first_load import FirstLoadError, first_load, plan_first_load
from hanjigap.settle.pipeline import ExportInvalid, ParsedExport, parse_export
from hanjigap.settle.preview import PreviewError, preview_month
from hanjigap.web.deps import CurrentMemberRaw, RawDb, get_settings_from
from hanjigap.web.errors import error_response
from hanjigap.web.ledger_api import month_or_last

log = logging.getLogger("hanjigap.web")
router = APIRouter(prefix="/api", tags=["uploads"])

MAX_BYTES = 20 * 1024 * 1024
KEEP_SECONDS = 15 * 60
MAX_PENDING = 4
ALLOWED_SUFFIXES = (".zip", ".xlsx")
BACKUP_FAILED = "정산 전에 백업을 만들지 못해서 멈췄어요. 미니PC의 백업 폴더를 확인해 주세요"
GONE = "올린 파일을 찾을 수 없어요. 시간이 지났거나 서버가 다시 켜졌을 수 있어요. 다시 올려 주세요"


# --- 메모리 보관 -----------------------------------------------------------------


@dataclass
class Pending:
    id: str
    member_id: int
    uploaded_by: int
    parsed: ParsedExport
    month: str
    mode: str  # settle | first_load
    created: float
    first_time: bool = False  # 처음 올리는 사람 → 확정할 때 first_load도 고를 수 있음


@dataclass
class UploadStore:
    """읽은 파일을 잠깐 메모리에 둔다. 서버를 다시 켜면 사라진다."""

    clock: Callable[[], float] = time.monotonic
    items: dict[str, Pending] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, member_id: int, uploaded_by: int, parsed: ParsedExport, month: str,mode: str, first_time: bool = False) -> Pending:  # fmt: skip
        with self.lock:
            self._purge()
            while len(self.items) >= MAX_PENDING:
                oldest = min(self.items.values(), key=lambda p: p.created)
                del self.items[oldest.id]
            pending = Pending(secrets.token_urlsafe(16), member_id, uploaded_by, parsed, month,
                              mode, self.clock(), first_time)  # fmt: skip
            self.items[pending.id] = pending
            return pending

    def get(self, upload_id: str, me: Member) -> Pending:
        with self.lock:
            self._purge()
            pending = self.items.get(upload_id)
        if pending is None or pending.uploaded_by != me.id:
            raise HTTPException(404, GONE)
        return pending

    def drop(self, upload_id: str) -> bool:
        with self.lock:
            return self.items.pop(upload_id, None) is not None

    def _purge(self) -> None:
        now = self.clock()
        for key in [k for k, p in self.items.items() if now - p.created > KEEP_SECONDS]:
            del self.items[key]


def store_of(request: Request) -> UploadStore:
    return request.app.state.uploads


# --- 미리보기 --------------------------------------------------------------------


def _fail(exc: Exception) -> JSONResponse:
    if isinstance(exc, ImportFailure):
        return error_response(400, exc.message, code=str(exc.code))
    if isinstance(exc, ExportInvalid):
        rows = sorted({e.row_no for e in exc.result.errors})[:5]
        more = " …" if len(exc.result.errors) > 5 else ""
        message = (
            f"파일에 읽을 수 없는 행이 {len(exc.result.errors):,}건 있어요 "
            f"(엑셀 {', '.join(map(str, rows))}행{more})"
        )
        return error_response(400, message, code="INVALID_ROWS")
    return error_response(400, str(exc), code="CANNOT_SETTLE")


def _file_out(parsed: ParsedExport) -> dict:
    period = parsed.summary.period
    return {
        "period_start": period.start.isoformat() if period else None,
        "period_end": period.end.isoformat() if period else None,
        "rows": len(parsed.keyed),
        "status_tables": (
            None
            if parsed.status is None
            else {"found": bool(parsed.status.found), "ok": parsed.status.ok}
        ),
    }


def _month_out(summary) -> dict:
    return {
        "count": summary.count,
        "income": summary.income,
        "expense": summary.expense,
        "transfer_in": summary.transfer_in,
        "transfer_out": summary.transfer_out,
    }


def _settle_preview(conn, member: Member, parsed: ParsedExport, month: str, tz) -> dict:
    preview = preview_month(conn, member, parsed, month, tz=tz)
    warnings = list(preview.warnings)
    for check in check_recent(conn, member, parsed, month, tz=tz):
        if not check.clean:
            n = len(check.added) + len(check.removed) + len(check.changed) + len(check.shifted)
            warnings.append(f"이미 정산한 {check.year_month}과 다른 거래가 {n}건 있어요")
        warnings += check.warnings
    status_only = False
    if preview.kind == "resettle" and not preview.has_changes:
        status_only = status_needs_saving(conn, member, parsed, month)
        if not status_only:
            warnings.append("이미 정산한 달이고 바뀐 거래가 없어요. 확정해도 저장할 게 없어요")
    return {
        "kind": preview.kind,
        "will_save": preview.kind != "resettle" or preview.has_changes or status_only,
        "status_only": status_only,
        "month_summary": _month_out(preview.month),
        "changes": {
            "added": len(preview.added),
            "changed": len(preview.changed),
            "removed": len(preview.removed),
            "unchanged": preview.unchanged,
        },
        "warnings": warnings,
    }


def _first_load_preview(conn, member: Member, parsed: ParsedExport, month: str, tz) -> dict:
    plan = plan_first_load(conn, member, parsed, month, tz=tz)
    target = next(m for m in plan.months if m.year_month == plan.target_month)
    warnings = [w for m in plan.months for w in m.preview.warnings]
    return {
        "kind": "first_load",
        "will_save": True,
        "status_only": False,
        "month_summary": _month_out(target.preview.month),
        "first_load": {
            "months": [
                {"month": m.year_month, "status": m.status, "count": m.count} for m in plan.months
            ],
            "excluded": [{"month": ym, "reason": why} for ym, why in plan.excluded],
            "total_rows": plan.total_rows,
        },
        "warnings": list(dict.fromkeys(warnings)),
    }


def _history_option(conn, member: Member, parsed: ParsedExport, month: str, tz) -> dict | None:
    """처음 올릴 때 '지난 달도 함께 넣기'를 고르면 들어갈 달들. 넣을 지난 달이 없으면 None."""
    body = _first_load_preview(conn, member, parsed, month, tz)
    info = body["first_load"]
    if not any(m["status"] == "baseline" for m in info["months"]):
        return None
    return {**info, "warnings": body["warnings"]}


def _has_settlements(conn, member: Member) -> bool:
    row = conn.execute(
        "SELECT 1 FROM month_settlements WHERE member_id = ? LIMIT 1", (member.id,)
    ).fetchone()
    return row is not None


@router.post("/uploads", status_code=201)
def create_upload(
    request: Request,
    conn: RawDb,
    me: CurrentMemberRaw,
    file: Annotated[UploadFile, File()],
    password: Annotated[str | None, Form()] = None,
    owner: Annotated[str | None, Form()] = None,
    month: Annotated[str | None, Form()] = None,
):
    """파일을 읽어 미리보기를 돌려준다. DB는 바꾸지 않는다."""
    member = get_member(conn, owner) if owner else me
    ym = month_or_last(request, month)
    name = file.filename or ""
    if not name.lower().endswith(ALLOWED_SUFFIXES):
        return error_response(
            400, "뱅샐에서 받은 zip 또는 xlsx 파일을 골라 주세요", code="NOT_EXCEL"
        )
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return error_response(400, "파일이 너무 커요 (20MB까지)", code="FILE_TOO_LARGE")
    tz = get_settings_from(request).tz
    try:
        parsed = parse_export(data, password or None, name)
        first_time = not _has_settlements(conn, member)
        mode, body = "settle", _settle_preview(conn, member, parsed, ym, tz)
        if first_time:  # 지난 달도 함께 넣을 때의 내용 (고를 수 있게)
            body["history"] = _history_option(conn, member, parsed, ym, tz)
    except (ImportFailure, ExportInvalid, PreviewError, FirstLoadError) as exc:
        log.info("웹 업로드 거부: %s", type(exc).__name__)
        return _fail(exc)
    finally:
        del data  # 원본 바이트는 바로 버린다 (읽은 결과만 잠깐 보관)
    pending = store_of(request).put(member.id, me.id, parsed, ym, mode, first_time)
    log.info("웹 업로드 미리보기: %s %s %d건 (처음=%s)", mode, ym, len(parsed.keyed), first_time)
    return {
        "upload_id": pending.id,
        "owner": member.slug,
        "mode": mode,
        "first_time": first_time,
        "month": ym,
        "file": _file_out(parsed),
        "expires_in": KEEP_SECONDS,
        **body,
    }


# --- 확정 · 취소 -----------------------------------------------------------------


class ApplyIn(BaseModel):
    # 처음 올리는 사람만 고를 수 있다. 비우면 미리보기 때의 방식(이 달만)
    mode: Literal["settle", "first_load"] | None = None


@router.post("/uploads/{upload_id}/apply")
def apply_upload(upload_id: str, request: Request, conn: RawDb, me: CurrentMemberRaw,body: ApplyIn | None = None):  # fmt: skip
    store = store_of(request)
    pending = store.get(upload_id, me)
    mode = (body.mode if body else None) or pending.mode
    if mode != pending.mode and not pending.first_time:
        return error_response(
            400, "처음 올리는 파일만 '지난 달도 함께 넣기'를 고를 수 있어요", code="CANNOT_SETTLE"
        )
    slug = conn.execute("SELECT slug FROM members WHERE id = ?", (pending.member_id,)).fetchone()
    member = get_member(conn, slug[0])
    settings = get_settings_from(request)
    tz = settings.tz
    try:  # 정산이 DB를 크게 바꾸므로 저장 직전에 백업 (실패하면 정산하지 않는다)
        make_backup(settings.db_path, settings.backup_dir, "settle", now=settings.now(),
                    copy_dir=settings.copy_dir)  # fmt: skip
    except BackupError:
        return error_response(503, BACKUP_FAILED)
    try:
        if mode == "first_load":
            result = first_load(conn, member, pending.parsed, pending.month, uploaded_by=me.id,
                                tz=tz)  # fmt: skip
            saved, status_only, counts = True, False, {"rows": result.plan.total_rows}
        else:
            applied = apply_month(conn, member, pending.parsed, pending.month,
                                  uploaded_by=me.id, tz=tz)  # fmt: skip
            saved = applied.saved
            status_only = applied.status_only
            counts = {
                "added": applied.added,
                "changed": applied.changed,
                "removed": applied.removed,
            }
    except (PreviewError, FirstLoadError) as exc:
        log.info("웹 업로드 확정 거부: %s", type(exc).__name__)
        return _fail(exc)
    store.drop(upload_id)
    report = classify(conn, pending.month, member=member)
    log.info("웹 업로드 확정: %s %s 저장=%s", mode, pending.month, saved)
    if status_only:
        message = "거래는 그대로라서 재무현황(순자산)만 저장했어요"
    elif saved:
        message = "정산했어요"
    else:
        message = "바뀐 거래가 없어 저장하지 않았어요"
    return {
        "saved": saved,
        "status_only": status_only,
        "message": message,
        "owner": member.slug,
        "mode": mode,
        "month": pending.month,
        **counts,
        "summary": {
            "income": report.income,
            "expense": report.expense,
            "saving": report.saving,
            "net": report.net,
            "unconfirmed": report.counts()["unconfirmed"],
        },
    }


@router.delete("/uploads/{upload_id}")
def cancel_upload(upload_id: str, request: Request, me: CurrentMemberRaw) -> dict:
    store = store_of(request)
    store.get(upload_id, me)
    return {"deleted": store.drop(upload_id)}
