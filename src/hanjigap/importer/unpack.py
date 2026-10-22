"""뱅크샐러드 내보내기 파일 열기.

뱅샐은 엑셀을 비밀번호 걸린 zip(ZipCrypto)으로 보낸다. 사용자가 압축을 풀어둔 엑셀도 받는다.
파일 종류는 확장자가 아니라 내용으로 판별한다.

    zip 안에 .xlsx가 있음  → zip 비밀번호로 꺼냄 → 엑셀이 또 잠겨 있으면 같은 비밀번호로 풂
    엑셀(xlsx)            → decrypt_xlsx 그대로
    그 외                 → NOT_EXCEL

모든 처리는 메모리 안에서만 한다. 디스크에 풀지 않는다.
"""

from __future__ import annotations

import io
import zipfile
import zlib
from dataclasses import dataclass
from typing import Literal

from hanjigap.importer import decrypt as _decrypt
from hanjigap.importer.decrypt import decrypt_xlsx
from hanjigap.importer.errors import ErrorCode, ImportFailure

ZIP_MAGIC = b"PK\x03\x04"
XLSX_MARKER = "xl/workbook.xml"
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 압축 폭탄 방지
ZIP_AES_METHOD = 99
_READ_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class OpenedExport:
    xlsx: bytes  # 잠금이 모두 풀린 xlsx 바이트
    container: Literal["zip", "xlsx"]
    zip_encrypted: bool
    xlsx_encrypted: bool
    inner_name: str | None  # zip 안의 엑셀 파일 이름

    @property
    def lock_label(self) -> str:
        locks = [
            name
            for name, on in (
                ("zip 비밀번호", self.zip_encrypted),
                ("엑셀 비밀번호", self.xlsx_encrypted),
            )
            if on
        ]
        return " + ".join(locks) if locks else "없음"


def open_export(data: bytes, password: str | None = None) -> OpenedExport:
    if len(data) > _decrypt.MAX_FILE_BYTES:
        raise ImportFailure(ErrorCode.FILE_TOO_LARGE, f"{len(data) // 1024:,} KB")

    archive = _as_export_zip(data)
    if archive is None:
        opened = decrypt_xlsx(data, password)
        return OpenedExport(
            xlsx=opened.data,
            container="xlsx",
            zip_encrypted=False,
            xlsx_encrypted=opened.was_encrypted,
            inner_name=None,
        )

    with archive:
        info = _pick_excel(archive)
        zip_encrypted = bool(info.flag_bits & 0x1)
        inner = _read_member(archive, info, password if zip_encrypted else None)

    opened = decrypt_xlsx(inner, password)
    return OpenedExport(
        xlsx=opened.data,
        container="zip",
        zip_encrypted=zip_encrypted,
        xlsx_encrypted=opened.was_encrypted,
        inner_name=_display_name(info),
    )


def _as_export_zip(data: bytes) -> zipfile.ZipFile | None:
    """뱅샐식 zip(엑셀을 담은 압축파일)이면 열어서 돌려준다. xlsx 자체도 zip이라 구분이 필요하다."""
    if not data.startswith(ZIP_MAGIC):
        return None
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    if XLSX_MARKER in archive.namelist():  # 그냥 엑셀 파일
        archive.close()
        return None
    return archive


def _pick_excel(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    candidates = [
        info
        for info in archive.infolist()
        if not info.is_dir()
        and not info.filename.startswith("__MACOSX/")
        and info.filename.lower().endswith(".xlsx")
        and not info.filename.rsplit("/", 1)[-1].startswith(("~$", "._"))
    ]
    if not candidates:
        raise ImportFailure(ErrorCode.ZIP_NO_EXCEL)
    if len(candidates) > 1:
        raise ImportFailure(ErrorCode.ZIP_MULTIPLE_EXCEL, f"{len(candidates)}개")
    info = candidates[0]
    if info.file_size > MAX_UNCOMPRESSED_BYTES:
        raise ImportFailure(ErrorCode.FILE_TOO_LARGE, "압축 해제 크기 초과")
    if info.compress_type == ZIP_AES_METHOD:
        raise ImportFailure(ErrorCode.ZIP_UNSUPPORTED, "AES")
    return info


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, password: str | None) -> bytes:
    encrypted = bool(info.flag_bits & 0x1)
    if encrypted and not password:
        raise ImportFailure(ErrorCode.PASSWORD_REQUIRED)

    last_wrong: ImportFailure | None = None
    for pwd in _password_candidates(password) if encrypted else [None]:
        try:
            return _read_limited(archive, info, pwd)
        except RuntimeError as exc:  # "Bad password for file"
            last_wrong = ImportFailure(ErrorCode.WRONG_PASSWORD)
            last_wrong.__cause__ = exc
        except (zipfile.BadZipFile, zlib.error) as exc:
            # ZipCrypto는 틀린 비밀번호가 1/256 확률로 첫 검사를 통과한다.
            # 그 뒤 압축 해제나 CRC 검사에서 걸린다.
            if not encrypted:
                raise ImportFailure(ErrorCode.DECRYPT_FAILED) from exc
            last_wrong = ImportFailure(ErrorCode.WRONG_PASSWORD)
            last_wrong.__cause__ = exc
        except NotImplementedError as exc:
            raise ImportFailure(ErrorCode.ZIP_UNSUPPORTED) from exc
    assert last_wrong is not None
    raise last_wrong


def _read_limited(archive: zipfile.ZipFile, info: zipfile.ZipInfo, pwd: bytes | None) -> bytes:
    """헤더에 적힌 크기를 믿지 않고, 실제로 읽은 양으로 크기 제한을 건다."""
    buf = io.BytesIO()
    with archive.open(info, pwd=pwd) as member:
        while chunk := member.read(_READ_CHUNK):
            buf.write(chunk)
            if buf.tell() > MAX_UNCOMPRESSED_BYTES:
                raise ImportFailure(ErrorCode.FILE_TOO_LARGE, "압축 해제 크기 초과")
    return buf.getvalue()


def _password_candidates(password: str | None) -> list[bytes]:
    """ZipCrypto 비밀번호는 바이트라 인코딩이 정해져 있지 않다.

    한글 비밀번호면 UTF-8과 cp949 두 방식을 모두 시도한다.
    """
    assert password
    candidates = [password.encode("utf-8")]
    try:
        cp949 = password.encode("cp949")
    except UnicodeEncodeError:
        cp949 = None
    if cp949 and cp949 not in candidates:
        candidates.append(cp949)
    return candidates


def _display_name(info: zipfile.ZipInfo) -> str:
    """UTF-8 표시가 없는 zip 파일 이름은 한국어 윈도우 방식(cp949)일 수 있다."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name
