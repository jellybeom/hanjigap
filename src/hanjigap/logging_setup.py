"""로깅 설정.

원칙: 로그에는 건수·기간·처리 결과만 남긴다.
거래 내용, 금액, 이름 등 금융 정보는 어떤 레벨에서도 기록하지 않는다.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from hanjigap.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_FILE_NAME = "hanjigap.log"
MAX_BYTES = 1_000_000  # 1MB
BACKUP_COUNT = 5


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger("hanjigap")
    if root.handlers:  # 중복 설정 방지
        return

    root.setLevel(settings.log_level)
    formatter = logging.Formatter(LOG_FORMAT)

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        settings.log_dir / LOG_FILE_NAME,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if settings.env == "dev":
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
