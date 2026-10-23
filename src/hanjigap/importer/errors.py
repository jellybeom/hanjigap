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
    ZIP_NO_EXCEL = "ZIP_NO_EXCEL"
    ZIP_MULTIPLE_EXCEL = "ZIP_MULTIPLE_EXCEL"
    ZIP_UNSUPPORTED = "ZIP_UNSUPPORTED"
    LEDGER_SHEET_NOT_FOUND = "LEDGER_SHEET_NOT_FOUND"
    HEADER_MISSING_COLUMNS = "HEADER_MISSING_COLUMNS"
    HEADER_DUPLICATE_COLUMNS = "HEADER_DUPLICATE_COLUMNS"


MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.FILE_TOO_LARGE: "파일이 너무 큽니다",
    ErrorCode.NOT_EXCEL: "엑셀 또는 뱅샐 압축파일이 아닙니다",
    ErrorCode.PASSWORD_REQUIRED: "비밀번호가 필요한 파일입니다",
    ErrorCode.WRONG_PASSWORD: "비밀번호가 맞지 않습니다",
    ErrorCode.DECRYPT_FAILED: "파일이 손상되었거나 복호화에 실패했습니다",
    ErrorCode.ZIP_NO_EXCEL: "압축파일 안에 엑셀 파일이 없습니다",
    ErrorCode.ZIP_MULTIPLE_EXCEL: "압축파일 안에 엑셀 파일이 여러 개 있습니다",
    ErrorCode.ZIP_UNSUPPORTED: "지원하지 않는 압축 암호화 방식입니다",
    ErrorCode.LEDGER_SHEET_NOT_FOUND: "가계부 내역 시트를 찾을 수 없습니다",
    ErrorCode.HEADER_MISSING_COLUMNS: "필수 열이 없습니다",
    ErrorCode.HEADER_DUPLICATE_COLUMNS: "같은 이름의 열이 여러 개 있습니다",
}


class ImportFailure(Exception):
    def __init__(self, code: ErrorCode, detail: str | None = None) -> None:
        self.code = code
        self.message = MESSAGES[code]
        self.detail = detail
        super().__init__(f"{self.message} ({detail})" if detail else self.message)
