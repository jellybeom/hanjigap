"""공통 테스트 설정.

모든 테스트는 임시 폴더에서 실행하고, 실제 .env와 HANJIGAP_ 환경변수의 영향을 받지 않게 한다.
"""

import logging
import os

import pytest

from hanjigap.config import get_settings


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
