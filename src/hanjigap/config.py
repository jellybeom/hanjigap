"""설정 로딩.

우선순위: 환경변수 > .env 파일 > 기본값.
상대 경로는 실행 위치(프로젝트 루트) 기준으로 절대 경로로 바꿔서 보관한다.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HANJIGAP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "prod"] = "dev"
    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/hanjigap.db")
    log_dir: Path = Path("./logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    timezone: str = "Asia/Seoul"
    port: int = 8000  # 웹 서버 포트 (이 컴퓨터 안에서만 열림)
    # 바깥 주소 (H2 트레이의 '한지갑 열기'): 예 https://hanjigap.example.com
    public_url: str | None = None

    # 백업 (H1): 기본 폴더와, 다른 드라이브 등에 하나 더 두는 두 번째 사본 폴더(선택)
    backup_dir: Path = Path("./data/backups")
    backup_copy_dir: Path | None = None

    # H단계(Cloudflare Access)에서 사용
    cf_team_domain: str | None = None
    cf_access_aud: str | None = None

    # 개발 모드 로그인: 이 구성원(slug)으로 로그인한 것처럼 본다. dev에서만, 이 컴퓨터에서만.
    dev_member: str | None = None

    @field_validator("data_dir", "db_path", "log_dir", "backup_dir")
    @classmethod
    def _to_absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("backup_copy_dir", mode="before")
    @classmethod
    def _optional_dir(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None  # .env에 'HANJIGAP_BACKUP_COPY_DIR=' 처럼 비워 두면 끈 것으로 본다
        return Path(value).expanduser().resolve()

    @field_validator("public_url", mode="before")
    @classmethod
    def _check_url(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        url = str(value).strip().rstrip("/") + "/"
        if not url.startswith(("https://", "http://")):
            raise ValueError("https://로 시작하는 주소여야 해요")
        return url

    @field_validator("port")
    @classmethod
    def _check_port(cls, value: int) -> int:
        if not 1024 <= value <= 65535:
            raise ValueError("포트는 1024~65535 사이여야 해요")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"알 수 없는 시간대: {value}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def copy_dir(self) -> Path | None:
        """두 번째 사본 폴더. 기본 백업 폴더와 같으면 없는 것으로 본다."""
        if self.backup_copy_dir and self.backup_copy_dir != self.backup_dir:
            return self.backup_copy_dir
        return None

    def now(self) -> datetime:
        """미니PC 시간대의 지금 (시간대 정보 없이). 백업 파일 이름 등에 쓴다."""
        return datetime.now(self.tz).replace(tzinfo=None)

    def ensure_dirs(self) -> list[Path]:
        """필요한 폴더를 만들고, 새로 만든 폴더 목록을 돌려준다."""
        created: list[Path] = []
        for path in (self.data_dir, self.log_dir, self.db_path.parent):
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                created.append(path)
        return created


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
