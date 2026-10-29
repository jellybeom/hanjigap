"""오류 응답 형식: 항상 {"error": "한글 메시지"}.

입력이 틀리면 400 (모듈이 내는 메시지를 그대로 보여 준다)
형식이 틀리면 422 (+ 어느 칸인지 fields)
예상하지 못한 오류는 500 (app.py 미들웨어에서). 로그에는 오류 종류와 경로 틀만 남긴다.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hanjigap.db.members import MemberError
from hanjigap.db.validate import InputError

SERVER_ERROR = "서버에서 문제가 생겼어요. 잠시 뒤 다시 해보세요"
NOT_FOUND = "없는 주소예요"
METHOD_NOT_ALLOWED = "이 주소에서 할 수 없는 요청이에요"


def error_response(status: int, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def route_template(request: Request) -> str:
    """로그용 경로 틀 (/api/accounts/{id}). 실제 값·검색어는 넣지 않는다."""
    route = request.scope.get("route")
    return getattr(route, "path", "(없는 경로)")


def register_errors(app: FastAPI) -> None:
    @app.exception_handler(InputError)
    async def _input(_request: Request, exc: InputError) -> JSONResponse:
        return error_response(400, str(exc))

    @app.exception_handler(MemberError)
    async def _member(_request: Request, exc: MemberError) -> JSONResponse:
        return error_response(400, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404 and exc.detail == "Not Found":
            message = NOT_FOUND
        elif exc.status_code == 405:
            message = METHOD_NOT_ALLOWED
        else:
            message = str(exc.detail)
        return error_response(exc.status_code, message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {
                ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path"))
                for e in exc.errors()
            }
        )
        return error_response(422, "입력 형식이 올바르지 않아요", fields=[f for f in fields if f])
