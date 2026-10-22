"""설정 로딩.

우선순위: 환경변수 > .env 파일 > 기본값.
상대 경로는 실행 위치(프로젝트 루트) 기준으로 절대 경로로 바꿔서 보관한다.
"""

from __future__ import annotations

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

    # H단계(Cloudflare Access)에서 사용
    cf_team_domain: str | None = None
    cf_access_aud: str | None = None

    @field_validator("data_dir", "db_path", "log_dir")
    @classmethod
    def _to_absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

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
