"""공통 테스트 설정.

모든 테스트는 임시 폴더에서 실행하고, 실제 .env와 HANJIGAP_ 환경변수의 영향을 받지 않게 한다.
"""

import io
import logging
import os
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
