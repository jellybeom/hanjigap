"""웹 앱 만들기.

보안
    다른 사이트에서 보낸 쓰기 요청은 거부한다. (Origin·Sec-Fetch-Site 확인)
    모든 응답에 보안 헤더를 붙이고, API 응답은 브라우저에 저장하지 않게 한다.
    API 설명서(/docs)는 개발 모드에서만 연다.
로그
    요청 종류 · 경로 틀 · 결과 코드 · 걸린 시간만. (실제 경로 값·검색어·내용은 남기지 않음)
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response

from hanjigap import __version__
from hanjigap.config import Settings, get_settings
from hanjigap.web import system
from hanjigap.web.deps import UNSAFE_METHODS
from hanjigap.web.errors import SERVER_ERROR, error_response, register_errors, route_template

log = logging.getLogger("hanjigap.web")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
CROSS_SITE = "다른 사이트에서 온 요청은 받지 않아요"


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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    check_setup(settings)
    dev = settings.env == "dev"
    app = FastAPI(
        title="한지갑",
        version=__version__,
        docs_url="/docs" if dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings
    register_errors(app)
    app.include_router(system.router)

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
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        ms = (time.perf_counter() - started) * 1000
        log.info(
            "%s %s %d %.0fms", request.method, route_template(request), response.status_code, ms
        )
        return response

    return app


def _cross_site(request: Request) -> bool:
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False  # 브라우저가 아닌 요청 (명령어·테스트)
    return urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower()
