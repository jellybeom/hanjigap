"""뱅크샐러드 엑셀 복호화.

입력과 출력 모두 메모리 안의 바이트다. 복호화된 파일은 디스크에 쓰지 않는다.
비밀번호는 이 함수 안에서만 쓰고 저장하거나 기록하지 않는다.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import msoffcrypto
from msoffcrypto import exceptions as mso_exc

from hanjigap.importer.errors import ErrorCode, ImportFailure

MAX_FILE_BYTES = 20 * 1024 * 1024  # 실제 뱅샐 파일은 수백 KB


@dataclass(frozen=True)
class DecryptedFile:
    data: bytes  # 잠금이 풀린 xlsx 바이트
    was_encrypted: bool


def decrypt_xlsx(data: bytes, password: str | None = None) -> DecryptedFile:
    """잠긴 xlsx는 비밀번호로 풀고, 잠기지 않은 xlsx는 그대로 돌려준다."""
    if len(data) > MAX_FILE_BYTES:
        raise ImportFailure(ErrorCode.FILE_TOO_LARGE, f"{len(data) // 1024:,} KB")
    if not data:
        raise ImportFailure(ErrorCode.NOT_EXCEL, "빈 파일")

    try:
        office = msoffcrypto.OfficeFile(io.BytesIO(data))
        encrypted = office.is_encrypted()
    except Exception as exc:  # 형식을 알 수 없는 파일
        raise ImportFailure(ErrorCode.NOT_EXCEL) from exc

    if not encrypted:
        _require_xlsx(data, ErrorCode.NOT_EXCEL)
        return DecryptedFile(data=data, was_encrypted=False)

    if not password:
        raise ImportFailure(ErrorCode.PASSWORD_REQUIRED)

    try:
        office.load_key(password=password, verify_password=True)
    except mso_exc.InvalidKeyError as exc:
        raise ImportFailure(ErrorCode.WRONG_PASSWORD) from exc
    except Exception as exc:
        raise ImportFailure(ErrorCode.DECRYPT_FAILED) from exc

    out = io.BytesIO()
    try:
        # 무결성 검사 실패도 InvalidKeyError로 오지만,
        # 비밀번호는 위에서 이미 검증됐으므로 파일 손상으로 본다
        office.decrypt(out, verify_integrity=True)
    except Exception as exc:
        raise ImportFailure(ErrorCode.DECRYPT_FAILED) from exc

    plain = out.getvalue()
    _require_xlsx(plain, ErrorCode.DECRYPT_FAILED)
    return DecryptedFile(data=plain, was_encrypted=True)


def _require_xlsx(data: bytes, code: ErrorCode) -> None:
    """xlsx(zip 안에 xl/workbook.xml)가 맞는지 확인한다."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "xl/workbook.xml" in zf.namelist():
                return
    except zipfile.BadZipFile:
        pass
    raise ImportFailure(code, "xlsx 형식이 아님")
