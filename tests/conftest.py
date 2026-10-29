"""공통 테스트 설정.

모든 테스트는 임시 폴더에서 실행하고, 실제 .env와 HANJIGAP_ 환경변수의 영향을 받지 않게 한다.
"""

import io
import logging
import os
import struct
import zlib
from pathlib import Path

import pytest
from msoffcrypto.format.ooxml import OOXMLFile

from hanjigap.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_PASSWORD = "test1234"


@pytest.fixture(scope="session")
def sample_bytes() -> bytes:
    """익명화된 뱅샐 샘플 (암호 없음)."""
    return (FIXTURES / "sample.anon.xlsx").read_bytes()


def build_zip(entries: list[tuple[str, bytes]], password: str | None = None) -> bytes:
    """테스트용 zip 생성. password가 있으면 뱅샐과 같은 ZipCrypto로 잠근다.

    표준 라이브러리 zipfile은 잠긴 zip을 만들지 못하고 윈도우에는 기본 도구가 없어서 직접 만든다.
    """
    local_parts: list[bytes] = []
    central_parts: list[bytes] = []
    offset = 0
    dos_time, dos_date = 0, (2026 - 1980) << 9 | 10 << 5 | 20
    for name, raw in entries:
        name_b = name.encode("utf-8")
        crc = zlib.crc32(raw)
        comp = zlib.compressobj(6, zlib.DEFLATED, -15)
        body = comp.compress(raw) + comp.flush()
        flags = 0x800  # 이름 UTF-8
        if password:
            flags |= 0x1
            body = _zipcrypto_encrypt(password.encode("utf-8"), crc, body)
        common = struct.pack(
            "<HHHHHIII", 20, flags, 8, dos_time, dos_date, crc, len(body), len(raw)
        )
        local = struct.pack("<I", 0x04034B50) + common + struct.pack("<HH", len(name_b), 0)
        local += name_b + body
        central = (
            struct.pack("<IH", 0x02014B50, 20)
            + common
            + struct.pack("<HHHHHII", len(name_b), 0, 0, 0, 0, 0, offset)
            + name_b
        )
        local_parts.append(local)
        central_parts.append(central)
        offset += len(local)
    cd = b"".join(central_parts)
    eocd = struct.pack(
        "<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(cd), offset, 0
    )
    return b"".join(local_parts) + cd + eocd


def _zipcrypto_encrypt(password: bytes, crc: int, data: bytes) -> bytes:
    """PKWARE 전통 암호화(ZipCrypto)."""
    table = [_crc_entry(i) for i in range(256)]
    keys = [0x12345678, 0x23456789, 0x34567890]

    def crc_byte(c: int, b: int) -> int:
        return (c >> 8) ^ table[(c ^ b) & 0xFF]

    def update(b: int) -> None:
        keys[0] = crc_byte(keys[0], b)
        keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        keys[2] = crc_byte(keys[2], keys[1] >> 24)

    def stream_byte() -> int:
        t = (keys[2] | 2) & 0xFFFF
        return ((t * (t ^ 1)) >> 8) & 0xFF

    for b in password:
        update(b)
    header = bytes(range(11)) + bytes([crc >> 24])  # 마지막 바이트로 비밀번호 1차 검사
    out = bytearray()
    for b in header + data:
        out.append(b ^ stream_byte())
        update(b)
    return bytes(out)


def _crc_entry(n: int) -> int:
    for _ in range(8):
        n = (n >> 1) ^ 0xEDB88320 if n & 1 else n >> 1
    return n


@pytest.fixture(scope="session")
def make_zip():
    return build_zip


@pytest.fixture(scope="session")
def encrypted_sample_bytes(sample_bytes) -> bytes:
    """익명화 샘플을 SAMPLE_PASSWORD로 잠근 것. 테스트할 때마다 만들고 저장소에는 넣지 않는다."""
    out = io.BytesIO()
    OOXMLFile(io.BytesIO(sample_bytes)).encrypt(SAMPLE_PASSWORD, out)
    return out.getvalue()


def _reset_logger() -> None:
    logger = logging.getLogger("hanjigap")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("HANJIGAP_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    _reset_logger()
    yield tmp_path
    get_settings.cache_clear()
    _reset_logger()


# --- 웹 API 테스트 공용 (F3~) ---------------------------------------------------


@pytest.fixture
def api_db(tmp_path):
    """상범·재희가 등록된 DB 경로. 업로드 1건(batch id 1)도 만들어 둔다 (거래 넣기용)."""
    from hanjigap.db.connection import connect
    from hanjigap.db.members import add_member
    from hanjigap.db.migrate import migrate

    path = tmp_path / "data" / "hanjigap.db"
    path.parent.mkdir(exist_ok=True)
    c = connect(path)
    migrate(c)
    add_member(c, "sangbeom", "상범")
    add_member(c, "jaehee", "재희")
    c.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (1, ?, 'zip', 0, 'applied')",
        ("a" * 64,),
    )
    c.close()
    return path


@pytest.fixture
def api(tmp_path, api_db):
    """상범으로 로그인한 API 클라이언트 (개발 모드)."""
    from fastapi.testclient import TestClient

    from hanjigap.config import Settings
    from hanjigap.web import create_app

    settings = Settings(
        env="dev",
        dev_member="sangbeom",
        db_path=api_db,
        data_dir=api_db.parent,
        log_dir=tmp_path / "logs",
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def api_conn(api_db):
    """테스트에서 DB를 직접 보거나 넣을 때. 자동 저장(autocommit)."""
    from hanjigap.db.connection import connect

    c = connect(api_db)
    yield c
    c.close()
