"""DB 백업 · 보관 정리 · 되돌리기 (H1).

언제
    서버가 켜질 때와 하루 한 번 (한 시간마다 '오늘 백업이 있나' 확인 → 꺼져 있던 날도 켜지면 채움)
    정산을 저장하기 직전 · 설정 화면이나 명령어로 직접 · 되돌리기 직전
어떻게
    SQLite 백업 기능으로 복사한다. 서버가 켜져 있어도(WAL) 한 시점의 온전한 사본이 된다.
    임시 이름(.partial)으로 만들고 무결성 검사를 통과해야 진짜 이름으로 바꾼다.
보관
    매일 14개 + 매달 첫 매일 백업 12개월치, 정산 직전 12개, 직접 10개, 되돌리기 직전 5개.
    이름이 'hanjigap-날짜-시각-종류.db'인 파일만 정리한다. 폴더의 다른 파일은 건드리지 않는다.
두 번째 사본
    설정한 폴더(다른 드라이브 등)에 같은 파일을 하나 더 복사하고 같은 규칙으로 정리한다.
    사본 복사가 실패해도 백업 자체는 성공으로 본다. (로그에 남김)
로그
    종류 · 결과 · 개수만. 경로나 내용은 남기지 않는다.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("hanjigap.backup")

KINDS = {"daily": "매일", "settle": "정산 직전", "manual": "직접", "restore": "되돌리기 직전"}
KEEP = {"daily": 14, "settle": 12, "manual": 10, "restore": 5}
KEEP_MONTHS = 12  # 매달 첫 '매일' 백업을 몇 달치 남길지
NAME_RE = re.compile(r"^hanjigap-(\d{8})-(\d{6})-(daily|settle|manual|restore)\.db$")


class BackupError(Exception):
    """백업·되돌리기를 할 수 없음. 메시지는 그대로 보여 준다."""


@dataclass(frozen=True)
class Backup:
    path: Path
    at: datetime  # 미니PC 시간대 기준 (시간대 정보 없이)
    kind: str
    size: int

    @property
    def label(self) -> str:
        return KINDS[self.kind]


@dataclass(frozen=True)
class BackupResult:
    backup: Backup
    copied: bool | None  # None: 두 번째 사본 폴더를 정하지 않음


# --- 목록 ----------------------------------------------------------------------


def parse_name(path: Path) -> tuple[datetime, str] | None:
    m = NAME_RE.match(path.name)
    if not m:
        return None
    try:
        at = datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return at, m[3]


def list_backups(folder: Path) -> list[Backup]:
    """최신 순. 폴더가 없으면 빈 목록."""
    if not folder.is_dir():
        return []
    found: list[Backup] = []
    for path in folder.iterdir():
        parsed = parse_name(path)
        if parsed and path.is_file():
            found.append(Backup(path, parsed[0], parsed[1], path.stat().st_size))
    return sorted(found, key=lambda b: (b.at, b.path.name), reverse=True)


# --- 만들기 --------------------------------------------------------------------


def make_backup(
    db_path: Path,
    folder: Path,
    kind: str,
    *,
    now: datetime,
    copy_dir: Path | None = None,
) -> BackupResult:
    if kind not in KINDS:
        raise ValueError(kind)
    if not db_path.is_file():
        raise BackupError(f"DB 파일이 없어요: {db_path}")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"백업 폴더를 만들 수 없어요: {folder}") from exc

    target = _free_name(folder, now, kind)
    partial = target.with_name(target.name + ".partial")
    try:
        _copy_db(db_path, partial)
        os.replace(partial, target)
    except (sqlite3.Error, OSError) as exc:
        partial.unlink(missing_ok=True)
        log.error("백업 실패: %s (%s)", kind, type(exc).__name__)
        raise BackupError("백업을 만들지 못했어요") from exc

    backup = Backup(target, _at_of(target), kind, target.stat().st_size)
    prune(folder)
    copied = _copy_out(backup, copy_dir) if copy_dir else None
    log.info("백업 완료: %s (보관 %d개, 사본 %s)", kind, len(list_backups(folder)), copied)
    return BackupResult(backup, copied)


def ensure_daily(db_path: Path, folder: Path, *, now: datetime, copy_dir: Path | None = None):
    """오늘 '매일' 백업이 없으면 만든다. 만들었으면 결과, 이미 있으면 None."""
    today = now.date()
    if any(b.kind == "daily" and b.at.date() == today for b in list_backups(folder)):
        return None
    return make_backup(db_path, folder, "daily", now=now, copy_dir=copy_dir)


def _free_name(folder: Path, now: datetime, kind: str) -> Path:
    """같은 초에 두 번 만들면 1초씩 밀어서 겹치지 않게."""
    at = now.replace(microsecond=0)
    while True:
        path = folder / f"hanjigap-{at:%Y%m%d-%H%M%S}-{kind}.db"
        if not path.exists() and not path.with_name(path.name + ".partial").exists():
            return path
        at += timedelta(seconds=1)


def _at_of(path: Path) -> datetime:
    parsed = parse_name(path)
    assert parsed is not None
    return parsed[0]


def _copy_db(source: Path, target: Path) -> None:
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            # 원본의 WAL 표시까지 복사되므로, 파일 하나로 완결되게 일반 방식으로 바꿔 둔다
            dst.execute("PRAGMA journal_mode = DELETE")
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise sqlite3.DatabaseError("integrity_check")
        finally:
            dst.close()
    finally:
        src.close()


def _copy_out(backup: Backup, copy_dir: Path) -> bool:
    try:
        copy_dir.mkdir(parents=True, exist_ok=True)
        partial = copy_dir / (backup.path.name + ".partial")
        shutil.copyfile(backup.path, partial)
        os.replace(partial, copy_dir / backup.path.name)
        prune(copy_dir)
        return True
    except OSError as exc:
        log.warning("두 번째 사본 복사 실패 (%s)", type(exc).__name__)
        return False


# --- 정리 ----------------------------------------------------------------------


def keepers(backups: list[Backup]) -> set[Path]:
    keep: set[Path] = set()
    for kind, count in KEEP.items():
        keep.update(b.path for b in [b for b in backups if b.kind == kind][:count])
    firsts: dict[str, Backup] = {}
    for b in sorted((b for b in backups if b.kind == "daily"), key=lambda b: b.at):
        firsts.setdefault(f"{b.at:%Y-%m}", b)
    for month in sorted(firsts, reverse=True)[:KEEP_MONTHS]:
        keep.add(firsts[month].path)
    return keep


def prune(folder: Path) -> list[Path]:
    """보관 규칙 밖의 백업을 지우고 지운 목록을 돌려준다."""
    backups = list_backups(folder)
    keep = keepers(backups)
    removed = []
    for b in backups:
        if b.path not in keep:
            try:
                b.path.unlink(missing_ok=True)
                removed.append(b.path)
            except OSError as exc:  # 윈도우에서 다른 프로그램이 열고 있으면 다음에 다시
                log.warning("오래된 백업 정리 실패 (%s)", type(exc).__name__)
    return removed


# --- 되돌리기 -------------------------------------------------------------------


def backup_version(path: Path) -> int:
    """한지갑 백업 파일의 스키마 버전. 한지갑 DB가 아니거나 깨졌으면 BackupError."""
    if not path.is_file():
        raise BackupError(f"파일이 없어요: {path}")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("백업 파일이 깨져 있어요")
            row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
            members = conn.execute("SELECT count(*) FROM members").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError("한지갑 백업 파일이 아니에요") from exc
    if not row or row[0] is None or members < 1:
        raise BackupError("한지갑 백업 파일이 아니에요")
    return int(row[0])


def restore(
    db_path: Path,
    source: Path,
    folder: Path,
    *,
    now: datetime,
    latest_version: int,
) -> tuple[Backup | None, int]:
    """source의 내용으로 DB를 되돌린다. 먼저 지금 DB를 '되돌리기 직전'으로 백업한다.

    돌려주는 값: (되돌리기 직전 백업, 되돌린 파일의 스키마 버전)
    서버가 꺼져 있을 때만 불러야 한다. (확인은 부르는 쪽에서)
    """
    version = backup_version(source)
    if version > latest_version:
        raise BackupError(
            f"이 백업은 더 새 버전의 한지갑에서 만들었어요 (스키마 {version} > {latest_version}). "
            "먼저 한지갑을 업데이트하세요"
        )
    if db_path.exists() and source.resolve() == db_path.resolve():
        raise BackupError("지금 쓰는 DB 파일 자체는 되돌리기에 쓸 수 없어요")
    before = None
    if db_path.exists():
        before = make_backup(db_path, folder, "restore", now=now).backup
    try:
        _restore_into(source, db_path)
    except sqlite3.Error as exc:
        log.error("되돌리기 실패 (%s)", type(exc).__name__)
        raise BackupError("되돌리지 못했어요. 지금 DB는 '되돌리기 직전' 백업에 있어요") from exc
    log.info("되돌리기 완료 (스키마 %d)", version)
    return before, version


def _restore_into(source: Path, db_path: Path) -> None:
    """SQLite 백업 기능으로 덮어쓴다. WAL 파일까지 함께 맞춰 주므로 파일을 직접 바꾸지 않는다."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(db_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
