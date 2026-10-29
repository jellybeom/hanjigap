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

    # 트랜잭션은 코드에서 명시적으로 연다.
    # check_same_thread=False: 웹 서버는 연결을 준비한 스레드와 요청을 처리하는 스레드가 다를 수
    # 있다. 연결은 요청마다 따로 만들고 동시에 두 곳에서 쓰지 않으므로 안전하다.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def connect_ready(path: Path | str) -> sqlite3.Connection:
    """DB가 있고 스키마가 최신일 때만 연결을 돌려준다. (명령어·웹에서 사용)"""
    from hanjigap.db.migrate import applied_versions, load_migrations

    if not Path(path).exists():
        raise DatabaseError(f"DB 파일이 없습니다: {path} — 먼저 'db init'을 실행하세요")
    conn = connect(path)
    latest = max((m.version for m in load_migrations()), default=0)
    current = max(applied_versions(conn), default=0)
    if current != latest:
        conn.close()
        raise DatabaseError(
            f"DB 스키마가 최신이 아닙니다 (현재 {current}, 최신 {latest}) "
            "— 먼저 'db init'을 실행하세요"
        )
    return conn
