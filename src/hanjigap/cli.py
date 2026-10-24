"""명령어.

python -m hanjigap version
python -m hanjigap check
python -m hanjigap inspect <뱅샐파일.zip 또는 .xlsx>
python -m hanjigap db init | status
python -m hanjigap member add | list | update
python -m hanjigap settle preview <뱅샐파일> --member <slug> [--month YYYY-MM]
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import logging
import sys
import unicodedata
from pathlib import Path

from pydantic import ValidationError

from hanjigap import __version__
from hanjigap.config import Settings, get_settings
from hanjigap.db.connection import DatabaseError, connect, connect_ready
from hanjigap.db.members import (
    MemberError,
    add_member,
    get_member,
    list_members,
    mask_email,
    update_member,
)
from hanjigap.db.migrate import MigrationError, current_version, migrate
from hanjigap.importer.errors import ErrorCode, ImportFailure
from hanjigap.importer.fingerprint import assign_keys, key_stats
from hanjigap.importer.normalize import group_issues, normalize
from hanjigap.importer.sheet import REQUIRED_COLUMNS, find_ledger
from hanjigap.importer.summary import FileSummary, MonthSummary, parse_period, summarize
from hanjigap.importer.unpack import OpenedExport, open_export
from hanjigap.logging_setup import setup_logging
from hanjigap.settle.pipeline import ExportInvalid, parse_opened
from hanjigap.settle.preview import (
    MonthPreview,
    PreviewError,
    StoredTx,
    preview_month,
    previous_month,
)

PASSWORD_ATTEMPTS = 3
MAX_ERRORS_SHOWN = 20

log = logging.getLogger("hanjigap.cli")


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"hanjigap {__version__}")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    settings = _load_settings()
    if settings is None:
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
    summary = None
    if result.ok:
        stats = key_stats(assign_keys(result.rows))
        uniq = "모두 고유" if stats.all_unique else f"고유 {stats.unique:,}건"
        groups = f" (같은 조건 묶음 {stats.groups}개는 순번으로 구분)" if stats.groups else ""
        print(f"지문        {stats.total:,}건 {uniq}{groups}")

        name = opened.inner_name if opened.container == "zip" else path.name
        summary = summarize(result.rows, parse_period(name))
        if summary.period:
            p = summary.period
            print(f"기간        {p.start} ~ {p.end} ({p.source} 기준, {len(summary.months)}개월)")

    warnings = [*ledger.warnings, *group_issues(result.warnings)]
    if summary:
        warnings += summary.warnings
    _print_block("경고", warnings, empty="없음")

    if not result.ok:
        _print_row_errors(result.errors)
        return 1

    if summary and summary.months:
        print()
        _print_month_table(summary)
    return 0


_TABLE_COLUMNS = ("월", "건수", "수입", "지출", "이체 들어옴", "이체 나감")
_TABLE_WIDTHS = (9, 7, 13, 13, 13, 13)


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, right: bool) -> str:
    gap = " " * max(0, width - _display_width(text))
    return gap + text if right else text + gap


def _print_month_table(summary: FileSummary) -> None:
    def line(cells: list[str]) -> str:
        parts = [
            _pad(cell, width, right=i > 0)
            for i, (cell, width) in enumerate(zip(cells, _TABLE_WIDTHS, strict=True))
        ]
        return "".join(parts).rstrip()

    def cells(label: str, m: MonthSummary) -> list[str]:
        nums = (m.count, m.income, m.expense, m.transfer_in, m.transfer_out)
        return [label, *(f"{n:,}" for n in nums)]

    print(line(list(_TABLE_COLUMNS)))
    for m in summary.months:
        print(line(cells(m.label + ("*" if m.partial else ""), m)))
    print(line(cells("합계", summary.total)))
    print()
    if summary.partial_months:
        print("* 일부 기간만 포함된 달 (정산 대상으로 쓰면 안 됨)")
    print("※ 뱅샐 타입 기준 단순 합계. 지출 = 결제 − 취소·환불. 분류 규칙 적용 전.")


def _print_row_errors(errors) -> None:
    shown = [str(e) for e in errors[:MAX_ERRORS_SHOWN]]
    if len(errors) > MAX_ERRORS_SHOWN:
        shown.append(f"외 {len(errors) - MAX_ERRORS_SHOWN:,}건")
    _print_block("오류", shown, file=sys.stderr)
    print("[실패] 오류가 있어 이 파일은 사용할 수 없습니다", file=sys.stderr)


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


def _load_settings() -> Settings | None:
    try:
        return get_settings()
    except ValidationError as exc:
        print("설정 오류:", file=sys.stderr)
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"])
            print(f"  - {field}: {err['msg']}", file=sys.stderr)
        return None


def cmd_db_init(_args: argparse.Namespace) -> int:
    settings = _load_settings()
    if settings is None:
        return 1
    settings.ensure_dirs()
    setup_logging(settings)
    existed = settings.db_path.exists()
    try:
        conn = connect(settings.db_path)
        try:
            newly = migrate(conn)
            version = current_version(conn)
        finally:
            conn.close()
    except (DatabaseError, MigrationError) as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1

    print(f"DB          {settings.db_path} ({'기존 파일' if existed else '새로 만듦'})")
    if newly:
        for mg in newly:
            print(f"적용        {mg.version:04d}_{mg.name}.sql")
    else:
        print("적용        없음 (이미 최신)")
    print(f"스키마 버전 {version}")
    log.info("db init 완료 (새로 적용 %d개, 버전 %d)", len(newly), version)
    return 0


def cmd_db_status(_args: argparse.Namespace) -> int:
    settings = _load_settings()
    if settings is None:
        return 1
    if not settings.db_path.exists():
        print(f"DB 파일이 없습니다: {settings.db_path}", file=sys.stderr)
        print("먼저 'python -m hanjigap db init'을 실행하세요", file=sys.stderr)
        return 1
    try:
        conn = connect(settings.db_path)
    except DatabaseError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    try:
        version = current_version(conn)
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'schema_version' ORDER BY name"
            )
        ]
        counts = {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()

    print(f"DB          {settings.db_path}")
    print(f"스키마 버전 {version}")
    print(f"표          {len(tables)}개")
    width = max((len(t) for t in tables), default=0)
    for t in tables:
        print(f"  {t:<{width}}  {counts[t]:,}행")
    return 0


def _with_db(action) -> int:
    """설정을 읽고 최신 DB에 연결해서 action(conn)을 실행한다."""
    settings = _load_settings()
    if settings is None:
        return 1
    try:
        conn = connect_ready(settings.db_path)
    except DatabaseError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    try:
        return action(conn)
    except MemberError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def _print_member(m) -> None:
    print(f"{m.slug:<10} {_pad(m.display_name, 6, right=False)} {mask_email(m.email)}")


def cmd_member_add(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = add_member(conn, args.slug, args.name, args.email)
        print("등록했습니다")
        _print_member(member)
        return 0

    return _with_db(action)


def cmd_member_list(_args: argparse.Namespace) -> int:
    def action(conn) -> int:
        members = list_members(conn)
        if not members:
            print("등록된 구성원이 없습니다. 'member add'로 등록하세요")
            return 0
        print(f"{'slug':<10} {_pad('이름', 6, right=False)} 이메일")
        for m in members:
            _print_member(m)
        return 0

    return _with_db(action)


def cmd_member_update(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = update_member(
            conn,
            args.slug,
            display_name=args.name,
            email=args.email,
            clear_email=args.clear_email,
        )
        print("수정했습니다")
        _print_member(member)
        return 0

    return _with_db(action)


MAX_DIFF_SHOWN = 10


def cmd_settle_preview(args: argparse.Namespace) -> int:
    settings = _load_settings()
    if settings is None:
        return 1
    settings.ensure_dirs()
    setup_logging(settings)

    path: Path = args.file
    if not path.is_file():
        print(f"파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 1
    try:
        conn = connect_ready(settings.db_path)
    except DatabaseError as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1

    try:
        member = get_member(conn, args.member)
        year_month = args.month or previous_month(dt.datetime.now(settings.tz).date())
        data = path.read_bytes()
        parsed = parse_opened(_open_with_prompt(data), data, path.name)
        preview = preview_month(conn, member, parsed, year_month)
    except ExportInvalid as exc:
        _print_row_errors(exc.result.errors)
        return 1
    except (MemberError, ImportFailure, PreviewError) as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    _print_preview(preview, parsed.summary, settings)
    log.info(
        "settle preview %s %s: 신규 %d, 변경 %d, 삭제 %d, 동일 %d",
        member.slug,
        preview.year_month,
        len(preview.added),
        len(preview.changed),
        len(preview.removed),
        preview.unchanged,
    )
    return 0


def _local_date(utc_iso: str, settings: Settings) -> str:
    stamp = dt.datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    return stamp.astimezone(settings.tz).date().isoformat()


def _print_preview(preview: MonthPreview, summary: FileSummary, settings: Settings) -> None:
    kind = {
        "new": "새 정산",
        "baseline_to_settled": "기초 데이터 → 정산으로 전환",
        "resettle": "재정산",
    }[preview.kind]
    if preview.previous and preview.kind == "resettle":
        kind += f" (기존: 정산 완료 {_local_date(preview.previous.settled_at, settings)})"
    m = preview.month
    p = summary.period
    print(f"정산 대상   {preview.year_month} ({preview.member.display_name}) · {kind}")
    if p:
        print(
            f"파일        {p.start} ~ {p.end} ({p.source} 기준), 이 달 거래 {len(preview.rows):,}건"
        )
    print(
        f"합계        수입 {m.income:,} · 지출 {m.expense:,} · "
        f"이체 들어옴 {m.transfer_in:,} · 나감 {m.transfer_out:,}"
    )
    print()
    print(
        f"비교        신규 {len(preview.added):,} · 변경 {len(preview.changed):,} · "
        f"삭제 {len(preview.removed):,} · 동일 {preview.unchanged:,}"
    )
    _print_block("경고", preview.warnings, empty="없음")

    if preview.changed:
        print(f"\n변경 {len(preview.changed):,}건")
        for c in preview.changed[:MAX_DIFF_SHOWN]:
            detail = ", ".join(
                f"{f.label}: {f.before or '(빈 값)'} → {f.after or '(빈 값)'}" for f in c.changes
            )
            r = c.new.row
            print(_tx_line(r.date.isoformat(), r.time.isoformat(), r.amount, r.content, detail))
        _print_more(len(preview.changed))
    if preview.removed:
        print(f"\n삭제 {len(preview.removed):,}건")
        for s in preview.removed[:MAX_DIFF_SHOWN]:
            note = "(웹 수정 내용 있음)" if s.tx_key in preview.removed_with_override else ""
            print(_removed_line(s, note))
        _print_more(len(preview.removed))

    print()
    print("※ 미리보기입니다. DB는 바뀌지 않았습니다.")


def _tx_line(date: str, time: str, amount: int, content: str, detail: str) -> str:
    return f"  {date} {time}  {amount:>+12,}  {_pad(content, 18, right=False)}  {detail}".rstrip()


def _removed_line(s: StoredTx, note: str) -> str:
    return _tx_line(s.tx_date, s.tx_time, s.amount, s.content, note)


def _print_more(total: int) -> None:
    if total > MAX_DIFF_SHOWN:
        print(f"  … 외 {total - MAX_DIFF_SHOWN:,}건")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hanjigap", description="한지갑 관리 명령어")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="버전 출력").set_defaults(func=cmd_version)
    sub.add_parser("check", help="설정 확인 및 폴더 생성").set_defaults(func=cmd_check)

    inspect = sub.add_parser("inspect", help="뱅샐 파일 열어보기 (형식, 잠금, 시트 목록)")
    inspect.add_argument("file", type=Path, help="뱅샐 내보내기 파일 (zip 또는 xlsx)")
    inspect.set_defaults(func=cmd_inspect)

    db = sub.add_parser("db", help="DB 관리")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("init", help="DB 만들기 / 스키마 최신화").set_defaults(func=cmd_db_init)
    db_sub.add_parser("status", help="스키마 버전과 표별 행 수").set_defaults(func=cmd_db_status)

    member = sub.add_parser("member", help="구성원 관리")
    member_sub = member.add_subparsers(dest="member_command", required=True)

    m_add = member_sub.add_parser("add", help="구성원 등록")
    m_add.add_argument("slug", help="영문 식별자 (예: sangbeom)")
    m_add.add_argument("--name", required=True, help="화면에 보일 이름")
    m_add.add_argument("--email", help="로그인 이메일 (나중에 넣어도 됨)")
    m_add.set_defaults(func=cmd_member_add)

    member_sub.add_parser("list", help="구성원 목록").set_defaults(func=cmd_member_list)

    m_upd = member_sub.add_parser("update", help="구성원 정보 수정")
    m_upd.add_argument("slug")
    m_upd.add_argument("--name", help="새 이름")
    email_group = m_upd.add_mutually_exclusive_group()
    email_group.add_argument("--email", help="새 이메일")
    email_group.add_argument("--clear-email", action="store_true", help="이메일 지우기")
    m_upd.set_defaults(func=cmd_member_update)

    settle = sub.add_parser("settle", help="월 정산")
    settle_sub = settle.add_subparsers(dest="settle_command", required=True)
    s_prev = settle_sub.add_parser("preview", help="정산 미리보기 (DB는 바꾸지 않음)")
    s_prev.add_argument("file", type=Path, help="뱅샐 내보내기 파일 (zip 또는 xlsx)")
    s_prev.add_argument("--member", required=True, help="파일 주인의 slug (예: sangbeom)")
    s_prev.add_argument("--month", help="정산할 월 YYYY-MM (기본: 지난달)")
    s_prev.set_defaults(func=cmd_settle_preview)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
