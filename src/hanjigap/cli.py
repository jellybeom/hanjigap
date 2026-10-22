"""명령어.

python -m hanjigap version
python -m hanjigap check
python -m hanjigap inspect <뱅샐파일.xlsx>
"""

from __future__ import annotations

import argparse
import getpass
import io
import logging
import sys
from pathlib import Path

from openpyxl import load_workbook
from pydantic import ValidationError

from hanjigap import __version__
from hanjigap.config import Settings, get_settings
from hanjigap.importer.decrypt import DecryptedFile, decrypt_xlsx
from hanjigap.importer.errors import ErrorCode, ImportFailure
from hanjigap.logging_setup import setup_logging

PASSWORD_ATTEMPTS = 3

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


def _open_with_prompt(data: bytes) -> DecryptedFile:
    """잠긴 파일이면 비밀번호를 최대 PASSWORD_ATTEMPTS번 묻는다."""
    try:
        return decrypt_xlsx(data)
    except ImportFailure as exc:
        if exc.code != ErrorCode.PASSWORD_REQUIRED:
            raise

    for attempt in range(1, PASSWORD_ATTEMPTS + 1):
        try:
            return decrypt_xlsx(data, ask_password())
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
        sheets = load_workbook(io.BytesIO(opened.data), read_only=True).sheetnames
    except ImportFailure as exc:
        print(f"[실패] {exc.message}", file=sys.stderr)
        return 1

    print(f"파일 크기   {len(data) / 1024:,.0f} KB")
    print(f"암호화      {'예' if opened.was_encrypted else '아니오'}")
    print(f"시트        {', '.join(sheets)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hanjigap", description="한지갑 관리 명령어")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="버전 출력").set_defaults(func=cmd_version)
    sub.add_parser("check", help="설정 확인 및 폴더 생성").set_defaults(func=cmd_check)

    inspect = sub.add_parser("inspect", help="뱅샐 파일 열어보기 (암호화 여부, 시트 목록)")
    inspect.add_argument("file", type=Path, help="뱅샐 내보내기 엑셀 파일")
    inspect.set_defaults(func=cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
