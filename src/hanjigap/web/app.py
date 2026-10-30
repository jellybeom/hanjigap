"""웹 앱 만들기.

화면
    / 는 화면 틀(index.html), /static/ 은 화면 파일(CSS·JS·아이콘). 빌드 과정 없이 그대로 보낸다.
보안
    다른 사이트에서 보낸 쓰기 요청은 거부한다. (Origin·Sec-Fetch-Site 확인)
    모든 응답에 보안 헤더를 붙이고, API 응답은 브라우저에 저장하지 않게 한다.
    화면에는 콘텐츠 보안 정책(CSP)을 건다: 이 사이트의 파일만 실행·연결할 수 있다.
    API 설명서(/docs)는 개발 모드에서만 연다. (/docs는 바깥 스크립트를 써서 CSP에서 뺀다)
백업
    serve로 켤 때만(auto_backup) 켜자마자, 그리고 한 시간마다 '오늘 백업이 있나' 확인해서 채운다.
로그
    요청 종류 · 경로 틀 · 결과 코드 · 걸린 시간만. (실제 경로 값·검색어·내용은 남기지 않음)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser

from hanjigap import __version__
from hanjigap.config import Settings, get_settings
from hanjigap.db.backup import BackupError, ensure_daily
from hanjigap.web import (
    class_rules_api,
    estates_api,
    fixed_api,
    holdings_api,
    instruments_api,
    ledger_api,
    members_api,
    options,
    status_api,
    system,
    uploads_api,
    vehicles_api,
)
from hanjigap.web.auth import AccessVerifier
from hanjigap.web.deps import UNSAFE_METHODS
from hanjigap.web.errors import SERVER_ERROR, error_response, register_errors, route_template

log = logging.getLogger("hanjigap.web")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
CROSS_SITE = "다른 사이트에서 온 요청은 받지 않아요"
STATIC_DIR = Path(__file__).parent / "static"
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
DOCS_PATHS = ("/docs", "/openapi.json")
BACKUP_CHECK_SECONDS = 3600

# 윈도우는 파일 종류를 레지스트리에서 읽어 .js가 text/plain이 될 수 있다 (모듈 스크립트가 막힘)
for _type, _ext in (
    ("text/javascript", ".js"),
    ("text/css", ".css"),
    ("image/svg+xml", ".svg"),
    ("application/manifest+json", ".webmanifest"),
):
    mimetypes.add_type(_type, _ext)


# 올린 파일이 임시 파일(디스크)로 넘어가지 않게, 업로드 한도보다 크게 메모리에 둔다.
MultiPartParser.spool_max_size = uploads_api.MAX_BYTES + 1024 * 1024


class SetupError(Exception):
    """서버를 켤 수 없는 설정. 메시지는 그대로 보여 준다."""


def check_setup(settings: Settings) -> None:
    if settings.env == "prod":
        if settings.dev_member:
            raise SetupError("운영 모드(prod)에서는 HANJIGAP_DEV_MEMBER를 쓸 수 없어요")
        if not (settings.cf_team_domain and settings.cf_access_aud):
            raise SetupError(
                "운영 모드(prod)에는 HANJIGAP_CF_TEAM_DOMAIN과 HANJIGAP_CF_ACCESS_AUD가 필요해요"
            )


def create_app(settings: Settings | None = None, *, auto_backup: bool = False) -> FastAPI:
    """auto_backup: 매일 백업을 돌린다. (serve에서만 켠다. 테스트·명령어에서는 끈 채로)"""
    settings = settings or get_settings()
    check_setup(settings)
    dev = settings.env == "dev"

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(daily_backups(settings)) if auto_backup else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="한지갑",
        version=__version__,
        docs_url="/docs" if dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.verifier = _verifier(settings)
    app.state.uploads = uploads_api.UploadStore()
    register_errors(app)
    routers = (
        system,
        options,
        members_api,
        holdings_api,
        instruments_api,
        fixed_api,
        vehicles_api,
        estates_api,
        ledger_api,
        class_rules_api,
        status_api,
        uploads_api,
    )
    for module in routers:
        app.include_router(module.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    @app.middleware("http")
    async def _guard_and_log(request: Request, call_next) -> Response:
        started = time.perf_counter()
        if request.method in UNSAFE_METHODS and _cross_site(request):
            response: Response = error_response(403, CROSS_SITE)
        else:
            try:
                response = await call_next(request)
            except Exception as exc:  # 예상하지 못한 오류도 같은 형식·헤더로 답한다
                log.error(
                    "처리 실패 %s %s (%s)",
                    request.method,
                    route_template(request),
                    type(exc).__name__,
                )
                response = error_response(500, SERVER_ERROR)
        response.headers.update(SECURITY_HEADERS)
        path = request.url.path
        if not path.startswith(DOCS_PATHS):
            response.headers["Content-Security-Policy"] = CSP
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # 고친 화면이 바로 보이게 (매번 확인)
        ms = (time.perf_counter() - started) * 1000
        log.info(
            "%s %s %d %.0fms", request.method, route_template(request), response.status_code, ms
        )
        return response

    return app


async def daily_backups(settings: Settings, *, interval: float = BACKUP_CHECK_SECONDS) -> None:
    """켜자마자 한 번, 그 뒤로 interval초마다 오늘의 '매일' 백업이 있는지 보고 없으면 만든다."""
    while True:
        try:
            await asyncio.to_thread(
                ensure_daily,
                settings.db_path,
                settings.backup_dir,
                now=settings.now(),
                copy_dir=settings.copy_dir,
            )
        except BackupError:
            pass  # 이유는 backup 모듈이 로그에 남겼다. 다음 확인 때 다시 해 본다
        except Exception as exc:  # 백업 문제로 서버가 멈추지 않게
            log.error("매일 백업 확인 실패 (%s)", type(exc).__name__)
        await asyncio.sleep(interval)


def _verifier(settings: Settings) -> AccessVerifier | None:
    if not (settings.cf_team_domain and settings.cf_access_aud):
        return None
    try:
        return AccessVerifier.create(settings.cf_team_domain, settings.cf_access_aud)
    except ValueError:
        raise SetupError(
            "HANJIGAP_CF_TEAM_DOMAIN 형식이 아니에요 "
            "(예: myteam 또는 myteam.cloudflareaccess.com)"
        ) from None


def _cross_site(request: Request) -> bool:
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False  # 브라우저가 아닌 요청 (명령어·테스트)
    return urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower()
