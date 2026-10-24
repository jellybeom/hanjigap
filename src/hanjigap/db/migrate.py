"""스키마 버전 관리.

migrations/ 폴더의 'NNNN_이름.sql' 파일을 번호 순서대로 적용한다.
- DB의 schema_version 표에 적용한 번호와 파일 해시를 기록한다.
- 각 파일은 전부 적용되거나 전부 취소된다.
- 이미 적용된 파일의 내용이 바뀌었으면 멈춘다. (바꿀 내용은 새 번호 파일로 추가)
- 줄바꿈(CRLF/LF) 차이는 같은 내용으로 본다. 윈도우 git 설정에 따라 바뀔 수 있어서다.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    sha256     TEXT    NOT NULL,
    applied_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations(folder: Path | None = None) -> list[Migration]:
    """폴더(기본: 패키지 안 migrations/)의 SQL 파일을 번호 순으로 읽는다."""
    base = folder if folder is not None else resources.files("hanjigap.db") / "migrations"
    found: list[Migration] = []
    for entry in base.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        m = _NAME_RE.match(entry.name)
        if not m:
            raise MigrationError(f"마이그레이션 파일 이름 형식이 아닙니다: {entry.name}")
        sql = entry.read_text(encoding="utf-8").replace("\r\n", "\n")
        found.append(Migration(int(m[1]), m[2], sql))

    found.sort(key=lambda mg: mg.version)
    versions = [mg.version for mg in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"마이그레이션 번호가 1부터 빠짐없이 이어져야 합니다: {versions}")
    return found


def applied_versions(conn: sqlite3.Connection) -> dict[int, str]:
    conn.execute(_VERSION_TABLE)
    return {row[0]: row[1] for row in conn.execute("SELECT version, sha256 FROM schema_version")}


def current_version(conn: sqlite3.Connection) -> int:
    return max(applied_versions(conn), default=0)


def migrate(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> list[Migration]:
    """적용되지 않은 마이그레이션을 적용하고, 새로 적용한 목록을 돌려준다."""
    migrations = migrations if migrations is not None else load_migrations()
    applied = applied_versions(conn)
    known = {mg.version: mg for mg in migrations}

    for version, sha in applied.items():
        mg = known.get(version)
        if mg is None:
            raise MigrationError(f"DB에 적용된 버전 {version}의 파일이 없습니다")
        if mg.sha256 != sha:
            raise MigrationError(
                f"이미 적용된 {version:04d}_{mg.name}.sql 내용이 바뀌었습니다. "
                "바꿀 내용은 새 번호 파일로 추가하세요"
            )

    newly: list[Migration] = []
    for mg in migrations:
        if mg.version in applied:
            continue
        _apply(conn, mg)
        newly.append(mg)
    return newly


def _apply(conn: sqlite3.Connection, mg: Migration) -> None:
    record = (
        "INSERT INTO schema_version (version, name, sha256) "
        f"VALUES ({mg.version}, '{mg.name}', '{mg.sha256}');"
    )
    try:
        conn.executescript(f"BEGIN;\n{mg.sql}\n{record}\nCOMMIT;")
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise MigrationError(f"{mg.version:04d}_{mg.name}.sql 적용 실패: {exc}") from exc
