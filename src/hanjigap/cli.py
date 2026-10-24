"""명령어.

python -m hanjigap version
python -m hanjigap check
python -m hanjigap inspect <뱅샐파일.zip 또는 .xlsx>
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from hanjigap import __version__
from hanjigap.config import Settings, get_settings
from hanjigap.importer.errors import ErrorCode, ImportFailure
from hanjigap.importer.fingerprint import assign_keys, key_stats
from hanjigap.importer.normalize import group_issues, normalize
from hanjigap.importer.sheet import REQUIRED_COLUMNS, find_ledger
from hanjigap.importer.unpack import OpenedExport, open_export
from hanjigap.logging_setup import setup_logging

PASSWORD_ATTEMPTS = 3
MAX_ERRORS_SHOWN = 20

log = logging.getLogger("hanjigap.cli")


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"hanjigap {__version__}")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        print("설정 오류:", file=sys.stderr)
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"])
            print(f"  - {field}: {err['msg']}", file=sys.stderr)
        return 1

    created = settings.ensure_dirs()
    setup_logging(settings)

    _print_settings(settings)
    for path in created:
        print(f"[생성] {path}")
    print("설정 확인 완료")
    log.info("check 완료 (생성된 폴더 %d개)", len(created))
    return 0


def _print_settings(settings: Settings) -> None:
    rows = [
        ("env", settings.env),
        ("data_dir", settings.data_dir),
        ("db_path", settings.db_path),
        ("log_dir", settings.log_dir),
        ("log_level", settings.log_level),
        ("timezone", settings.timezone),
        ("cf_team_domain", settings.cf_team_domain or "(미설정)"),
        ("cf_access_aud", "(설정됨)" if settings.cf_access_aud else "(미설정)"),
    ]
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        print(f"{key:<{width}}  {value}")


def ask_password() -> str:
    """화면에 보이지 않게 비밀번호를 입력받는다. (테스트에서 바꿔 끼울 수 있게 분리)"""
    return getpass.getpass("비밀번호: ")


def _open_with_prompt(data: bytes) -> OpenedExport:
    """잠긴 파일이면 비밀번호를 최대 PASSWORD_ATTEMPTS번 묻는다."""
    try:
        return open_export(data)
    except ImportFailure as exc:
        if exc.code != ErrorCode.PASSWORD_REQUIRED:
            raise

    for attempt in range(1, PASSWORD_ATTEMPTS + 1):
        try:
            return open_export(data, ask_password())
        except ImportFailure as exc:
            if exc.code != ErrorCode.WRONG_PASSWORD or attempt == PASSWORD_ATTEMPTS:
                raise
            print(f"{exc.message} ({attempt}/{PASSWORD_ATTEMPTS})", file=sys.stderr)
    raise AssertionError("도달하지 않음")


def cmd_inspect(args: argparse.Namespace) -> int:
    path: Path = args.file
    if not path.is_file():
        print(f"파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 1

    data = path.read_bytes()
    try:
        opened = _open_with_prompt(data)
    except ImportFailure as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1

    if opened.container == "zip":
        print(f"형식        zip (안의 파일: {opened.inner_name})")
        size = f"{len(data) / 1024:,.0f} KB → 엑셀 {len(opened.xlsx) / 1024:,.0f} KB"
    else:
        print("형식        xlsx")
        size = f"{len(data) / 1024:,.0f} KB"
    print(f"잠금        {opened.lock_label}")
    print(f"파일 크기   {size}")

    try:
        ledger = find_ledger(opened.xlsx)
    except ImportFailure as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1

    required = len(REQUIRED_COLUMNS)
    optional = len(ledger.optional_found)
    print(f"시트        {', '.join(ledger.sheet_names)}")
    print(
        f"가계부      '{ledger.sheet_name}' 시트, 헤더 {ledger.header_row}행, "
        f"거래 {ledger.data_rows:,}행, 필수 열 {required}개 + 선택 열 {optional}개 확인"
    )

    result = normalize(ledger)
    print(
        f"정규화      {len(result.rows):,}행 성공, "
        f"오류 {len(result.errors):,}건, 경고 {len(result.warnings):,}건"
    )
    if result.ok:
        stats = key_stats(assign_keys(result.rows))
        uniq = "모두 고유" if stats.all_unique else f"고유 {stats.unique:,}건"
        groups = f" (같은 조건 묶음 {stats.groups}개는 순번으로 구분)" if stats.groups else ""
        print(f"지문        {stats.total:,}건 {uniq}{groups}")
    _print_block("경고", [*ledger.warnings, *group_issues(result.warnings)], empty="없음")

    if not result.ok:
        shown = [str(e) for e in result.errors[:MAX_ERRORS_SHOWN]]
        if len(result.errors) > MAX_ERRORS_SHOWN:
            shown.append(f"외 {len(result.errors) - MAX_ERRORS_SHOWN:,}건")
        _print_block("오류", shown, file=sys.stderr)
        print("[실패] 오류가 있어 이 파일은 사용할 수 없습니다", file=sys.stderr)
        return 1
    return 0


def _print_block(title: str, lines: list[str], empty: str | None = None, file=None) -> None:
    """'경고        첫 줄' 다음 줄부터는 들여쓰기로 맞춘다."""
    file = file or sys.stdout
    if not lines:
        if empty is not None:
            print(f"{title}        {empty}", file=file)
        return
    for i, line in enumerate(lines):
        label = f"{title}        " if i == 0 else " " * 12
        print(f"{label}{line}", file=file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hanjigap", description="한지갑 관리 명령어")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="버전 출력").set_defaults(func=cmd_version)
    sub.add_parser("check", help="설정 확인 및 폴더 생성").set_defaults(func=cmd_check)

    inspect = sub.add_parser("inspect", help="뱅샐 파일 열어보기 (형식, 잠금, 시트 목록)")
    inspect.add_argument("file", type=Path, help="뱅샐 내보내기 파일 (zip 또는 xlsx)")
    inspect.set_defaults(func=cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
