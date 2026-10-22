"""가져오기 오류.

모든 가져오기 오류는 ImportFailure 하나로 묶고, 오류 코드와 사용자용 한국어 메시지를 담는다.
웹 화면은 코드를 보고 다음 동작(예: 비밀번호 칸 다시 보여주기)을 정한다.
오류 메시지와 상세 내용에는 비밀번호나 거래 내용을 절대 넣지 않는다.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    NOT_EXCEL = "NOT_EXCEL"
    PASSWORD_REQUIRED = "PASSWORD_REQUIRED"
    WRONG_PASSWORD = "WRONG_PASSWORD"
    DECRYPT_FAILED = "DECRYPT_FAILED"


MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.FILE_TOO_LARGE: "파일이 너무 큽니다",
    ErrorCode.NOT_EXCEL: "엑셀 파일이 아닙니다",
    ErrorCode.PASSWORD_REQUIRED: "비밀번호가 필요한 파일입니다",
    ErrorCode.WRONG_PASSWORD: "비밀번호가 맞지 않습니다",
    ErrorCode.DECRYPT_FAILED: "파일이 손상되었거나 복호화에 실패했습니다",
}


class ImportFailure(Exception):
    def __init__(self, code: ErrorCode, detail: str | None = None) -> None:
        self.code = code
        self.message = MESSAGES[code]
        self.detail = detail
        super().__init__(f"{self.message} ({detail})" if detail else self.message)
