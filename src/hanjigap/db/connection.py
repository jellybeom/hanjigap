"""DB 연결.

모든 연결에 같은 설정을 건다.
- foreign_keys: 없는 구성원·업로드를 가리키는 행을 거부
- WAL: 웹이 읽는 중에도 정산 저장 가능
- busy_timeout: 잠깐 잠겨 있으면 기다렸다가 진행
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIN_SQLITE = (3, 37, 0)  # STRICT 표 지원


class DatabaseError(Exception):
    pass


def connect(path: Path | str) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        need = ".".join(map(str, MIN_SQLITE))
        raise DatabaseError(f"SQLite {need} 이상이 필요합니다 (현재 {sqlite3.sqlite_version})")

    conn = sqlite3.connect(path, isolation_level=None)  # 트랜잭션은 코드에서 명시적으로 연다
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn
