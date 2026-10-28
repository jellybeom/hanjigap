"""명령어.

python -m hanjigap version
python -m hanjigap check
python -m hanjigap inspect <뱅샐파일.zip 또는 .xlsx>
python -m hanjigap inspect-status <뱅샐파일>   ('뱅샐현황' 시트 구조만, 값은 숨김)
python -m hanjigap db init | status
python -m hanjigap member add | list | update
python -m hanjigap settle preview | apply <뱅샐파일> --member <slug> [--month YYYY-MM]
python -m hanjigap settle check <뱅샐파일> --member <slug>
python -m hanjigap settle first-load <뱅샐파일> --member <slug> [--month YYYY-MM]
python -m hanjigap instrument list --member <slug> [--unlinked]
python -m hanjigap instrument link <결제수단> --member <slug> (--account ID | --card ID
                                                                | --easy-pay)
python -m hanjigap instrument unlink <결제수단> --member <slug>
python -m hanjigap account add | list | update | close | reopen
python -m hanjigap card add | list | update | close | reopen
python -m hanjigap fixed check [--month YYYY-MM] [--member <slug>]
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
from hanjigap.db.holdings import (
    Account,
    Card,
    HoldingsError,
    InstrumentUsage,
    add_account,
    add_card,
    close_account,
    close_card,
    get_account,
    get_card,
    instrument_usage,
    link_instrument,
    links_by_target,
    list_accounts,
    list_cards,
    reopen_account,
    reopen_card,
    unlink_instrument,
    update_account,
    update_card,
)
from hanjigap.db.members import (
    MemberError,
    add_member,
    get_member,
    list_members,
    mask_email,
    update_member,
)
from hanjigap.db.migrate import MigrationError, current_version, migrate
from hanjigap.fixed.status import STATUS_LABELS, FixedStatus, month_status
from hanjigap.importer.cashflow import CashflowComparison, compare_cashflow, read_cashflow
from hanjigap.importer.errors import ErrorCode, ImportFailure
from hanjigap.importer.fingerprint import assign_keys, key_stats
from hanjigap.importer.normalize import group_issues, normalize
from hanjigap.importer.sheet import REQUIRED_COLUMNS, find_ledger
from hanjigap.importer.status_structure import StatusStructure, describe_status_sheet
from hanjigap.importer.summary import FileSummary, MonthSummary, parse_period, summarize
from hanjigap.importer.unpack import OpenedExport, open_export, peek_inner_name
from hanjigap.logging_setup import setup_logging
from hanjigap.settle.apply import apply_month
from hanjigap.settle.check import MonthCheck, check_all, check_recent
from hanjigap.settle.first_load import (
    FirstLoadError,
    FirstLoadPlan,
    ensure_first_load_allowed,
    first_load,
    plan_first_load,
)
from hanjigap.settle.pipeline import ExportInvalid, ParsedExport, parse_opened
from hanjigap.settle.preview import (
    MonthPreview,
    PreviewError,
    StoredTx,
    local_date,
    precheck_month,
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
        cashflow, cashflow_warnings = read_cashflow(opened.xlsx)
        comparison = compare_cashflow(cashflow, result.rows) if cashflow else None
        if summary.period:
            p = summary.period
            print(f"기간        {p.start} ~ {p.end} ({p.source} 기준, {len(summary.months)}개월)")

    warnings = [*ledger.warnings, *group_issues(result.warnings)]
    if summary:
        warnings += summary.warnings
        _print_cashflow(comparison, cashflow_warnings)
    _print_block("경고", warnings, empty="없음")

    if not result.ok:
        _print_row_errors(result.errors)
        return 1

    if summary and summary.months:
        print()
        _print_month_table(summary)
    return 0


def cmd_inspect_status(args: argparse.Namespace) -> int:
    path: Path = args.file
    if not path.is_file():
        print(f"파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 1
    try:
        opened = _open_with_prompt(path.read_bytes())
        structure = describe_status_sheet(opened.xlsx)
    except ImportFailure as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    except Exception:  # 엑셀 구조가 예상과 다를 때도 원인 확인용으로 메시지만
        print("[실패] 엑셀을 읽을 수 없습니다", file=sys.stderr)
        return 1
    _print_status_structure(structure)
    return 0


def _print_status_structure(st: StatusStructure) -> None:
    print(f"시트        {', '.join(st.sheet_names)}")
    if not st.found:
        print("'뱅샐현황' 시트가 없습니다")
        return
    print(f"뱅샐현황    표 {len(st.sections)}개 (숫자·날짜 값은 보여 주지 않아요)")
    for n, sec in enumerate(st.sections, start=1):
        print()
        if sec.private:
            print(
                f"[{n}] {sec.title}  ({sec.title_row}~{sec.last_row}행) → 개인정보라 내용은 건너뜀"
            )
            continue
        if sec.header_row is None:
            print(f"[{n}] {sec.title}  ({sec.title_row}행) → 내용 없음")
            continue
        print(
            f"[{n}] {sec.title}  {sec.span} · 머리글 {sec.header_row}행 · "
            f"데이터 {sec.data_rows:,}행"
        )
        head_w = max(_display_width(c.header or "-") for c in sec.columns) + 2
        for c in sec.columns:
            counts = []
            for label, value in (
                ("숫자", c.numbers),
                ("글자", c.texts),
                ("날짜", c.dates),
                ("빈칸", c.blanks),
            ):
                if value:
                    counts.append(f"{label} {value}")
            line = f"    {c.letter:<3}{_pad(c.header or '-', head_w, right=False)}"
            line += " · ".join(counts) if counts else "(데이터 없음)"
            if c.labels:
                line += f"   값: {', '.join(c.labels)}"
            print(line)
    print()
    print("※ 붙여 넣기 전에, '값:' 목록에 상품명 같은 개인적인 이름이 있으면 지워 주세요.")


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


MAX_MISMATCH_SHOWN = 10


def _print_cashflow(comparison: CashflowComparison | None, warnings) -> None:
    """뱅샐이 계산한 현금흐름현황과 한지갑 계산의 대조 결과."""
    if comparison is None:
        reason = warnings[0] if warnings else "현금흐름현황 표 없음"
        print(f"뱅샐 대조   건너뜀 ({reason})")
        return
    c = comparison
    print(
        f"뱅샐 대조   {c.months}개월 × {c.items}항목: 일치 {c.matched:,} · "
        f"부호 표시 차이 {c.sign_only:,} · 불일치 {len(c.mismatches):,}"
    )
    if c.mismatches:
        lines = [str(m) for m in c.mismatches[:MAX_MISMATCH_SHOWN]]
        if len(c.mismatches) > MAX_MISMATCH_SHOWN:
            lines.append(f"외 {len(c.mismatches) - MAX_MISMATCH_SHOWN:,}건")
        _print_block("대조 차이", lines)


def _print_row_errors(errors) -> None:
    shown = [str(e) for e in errors[:MAX_ERRORS_SHOWN]]
    if len(errors) > MAX_ERRORS_SHOWN:
        shown.append(f"외 {len(errors) - MAX_ERRORS_SHOWN:,}건")
    _print_block("오류", shown, file=sys.stderr)
    print("[실패] 오류가 있어 이 파일은 사용할 수 없습니다", file=sys.stderr)


def _print_block(title: str, lines: list[str], empty: str | None = None, file=None) -> None:
    """'경고        첫 줄' 다음 줄부터는 들여쓰기로 맞춘다. (제목은 화면 폭 12칸)"""
    file = file or sys.stdout
    head = _pad(title, 12, right=False)
    if not lines:
        if empty is not None:
            print(f"{head}{empty}", file=file)
        return
    for i, line in enumerate(lines):
        label = head if i == 0 else " " * 12
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
    except (MemberError, HoldingsError) as exc:
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


EMPTY_INSTRUMENT = "(비어 있음)"


def cmd_instrument_list(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = get_member(conn, args.member)
        usage = instrument_usage(conn, member)
        unlinked = [u for u in usage if not u.linked]
        print(f"{member.display_name} · 결제수단 {len(usage):,}개 · 연결 안 됨 {len(unlinked):,}개")
        # 실제 카드·통장 이름은 화면에만. 로그에는 건수만 남긴다.
        log.info(
            "instrument list (%s): 결제수단 %d개, 연결 안 됨 %d개",
            member.slug,
            len(usage),
            len(unlinked),
        )
        shown = unlinked if args.unlinked else usage
        if not usage:
            print("이 구성원의 거래가 아직 없습니다")
            return 0
        if not shown:
            print("연결 안 된 결제수단이 없습니다")
            return 0
        _print_instrument_table(shown)
        print()
        print("※ 연결은 이 이름 단위예요. 실제 통장·카드 여러 개가 한 이름으로 묶여 있으면")
        print("  거래를 서로 구별할 수 없어요. (수입·지출·이체 건수가 단서)")
        return 0

    return _with_db(action)


def _print_instrument_table(rows: list[InstrumentUsage]) -> None:
    names = [u.instrument or EMPTY_INSTRUMENT for u in rows]
    name_w = max(_display_width("결제수단"), *(_display_width(n) for n in names)) + 2

    def line(name: str, count: str, period: str, nums: tuple[str, str, str], link: str) -> str:
        return (
            _pad(name, name_w, right=False)
            + _pad(count, 6, right=True)
            + "   "
            + _pad(period, 23, right=False)
            + "".join(_pad(n, 7, right=True) for n in nums)
            + "   "
            + link
        )

    print(line("결제수단", "건수", "처음 ~ 마지막", ("지출", "수입", "이체"), "연결"))
    for name, u in zip(names, rows, strict=True):
        period = f"{u.first_date} ~ {u.last_date}"
        nums = (f"{u.expense:,}", f"{u.income:,}", f"{u.transfer:,}")
        print(line(name, f"{u.count:,}", period, nums, u.link_label))


def cmd_instrument_link(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = get_member(conn, args.member)
        link = link_instrument(
            conn,
            member,
            args.instrument,
            account_id=args.account,
            card_id=args.card,
            easy_pay=args.easy_pay,
            allow_unknown=args.force,
        )
        if link.account_id is not None:
            target = f"계좌 #{link.account_id} {get_account(conn, link.account_id).name}"
        elif link.card_id is not None:
            target = f"카드 #{link.card_id} {get_card(conn, link.card_id).name}"
        else:
            target = "간편결제"
        print(f"연결했습니다: 「{args.instrument}」 → {target}")
        log.info("instrument link (%s): %s", member.slug, link.target)
        return 0

    return _with_db(action)


def cmd_instrument_unlink(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = get_member(conn, args.member)
        if not unlink_instrument(conn, member, args.instrument):
            print(f"[실패] 연결되어 있지 않아요: 「{args.instrument}」", file=sys.stderr)
            return 1
        print(f"연결을 풀었습니다: 「{args.instrument}」")
        log.info("instrument unlink (%s)", member.slug)
        return 0

    return _with_db(action)


def _today() -> str:
    return dt.datetime.now(get_settings().tz).date().isoformat()


def _member_names(conn) -> dict[int, str]:
    return {m.id: m.display_name for m in list_members(conn)}


def _print_accounts(conn, accounts: list[Account]) -> None:
    names = _member_names(conn)
    links = links_by_target(conn, "accounts")
    name_w = max(_display_width(a.name) for a in accounts) + 2
    for a in accounts:
        parts = [f"{a.institution} · {a.kind_label}"]
        if a.number_last4:
            parts.append(f"끝자리 {a.number_last4}")
        if a.opened_on:
            parts.append(f"개설 {a.opened_on}")
        if links.get(a.id):
            parts.append(f"결제수단 {links[a.id]}개 연결")
        if a.closed_on:
            parts.append(f"해지 {a.closed_on}")
        print(
            f"{a.id:>4}  {_pad(names.get(a.member_id, '?'), 6, right=False)}"
            f"{_pad(a.name, name_w, right=False)}{' · '.join(parts)}"
        )


def _print_cards(conn, cards: list[Card]) -> None:
    names = _member_names(conn)
    links = links_by_target(conn, "cards")
    name_w = max(_display_width(c.name) for c in cards) + 2
    for c in cards:
        parts = [f"{c.issuer} · {c.kind_label}"]
        if c.number_last4:
            parts.append(f"끝자리 {c.number_last4}")
        if c.pay_day:
            parts.append(f"결제일 {c.pay_day}일 ({c.period_label})")
        if c.pay_account_id:
            parts.append(
                f"결제 계좌 #{c.pay_account_id} {get_account(conn, c.pay_account_id).name}"
            )
        if c.annual_fee:
            parts.append(f"연회비 {c.annual_fee:,}원")
        if links.get(c.id):
            parts.append(f"결제수단 {links[c.id]}개 연결")
        if c.closed_on:
            parts.append(f"해지 {c.closed_on}")
        print(
            f"{c.id:>4}  {_pad(names.get(c.member_id, '?'), 6, right=False)}"
            f"{_pad(c.name, name_w, right=False)}{' · '.join(parts)}"
        )


def _account_args(args: argparse.Namespace) -> dict:
    return dict(
        institution=args.institution,
        name=args.name,
        kind=args.kind,
        last4=args.last4,
        opened_on=args.opened,
        color=args.color,
        memo=args.memo,
        number_hint=args.hint,
    )


def cmd_account_add(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = get_member(conn, args.member)
        acc = add_account(conn, member, **_account_args(args))
        print("등록했습니다")
        _print_accounts(conn, [acc])
        log.info("account add (%s): id=%d", member.slug, acc.id)
        return 0

    return _with_db(action)


def cmd_account_list(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = get_member(conn, args.member) if args.member else None
        accounts = list_accounts(conn, member, include_closed=args.all)
        if not accounts:
            print("등록된 계좌가 없습니다. 'account add'로 등록하세요")
            return 0
        _print_accounts(conn, accounts)
        return 0

    return _with_db(action)


def cmd_account_update(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        acc = update_account(conn, args.id, **_account_args(args))
        print("수정했습니다")
        _print_accounts(conn, [acc])
        log.info("account update: id=%d", acc.id)
        return 0

    return _with_db(action)


def cmd_account_close(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        acc = close_account(conn, args.id, args.date or _today())
        print("해지로 표시했습니다 (지우지 않고 남겨 둡니다)")
        _print_accounts(conn, [acc])
        log.info("account close: id=%d", acc.id)
        return 0

    return _with_db(action)


def cmd_account_reopen(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        acc = reopen_account(conn, args.id)
        print("다시 사용 중으로 바꿨습니다")
        _print_accounts(conn, [acc])
        return 0

    return _with_db(action)


def _card_args(args: argparse.Namespace) -> dict:
    start, offset = args.period if args.period else (None, None)
    return dict(
        issuer=args.issuer,
        name=args.name,
        kind=args.kind,
        last4=args.last4,
        pay_day=args.pay_day,
        period_start_day=start,
        period_month_offset=offset,
        pay_account_id=args.pay_account,
        annual_fee=args.fee,
        color=args.color,
        memo=args.memo,
        number_hint=args.hint,
    )


def cmd_card_add(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = get_member(conn, args.member)
        card = add_card(conn, member, **_card_args(args))
        print("등록했습니다")
        _print_cards(conn, [card])
        log.info("card add (%s): id=%d", member.slug, card.id)
        return 0

    return _with_db(action)


def cmd_card_list(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        member = get_member(conn, args.member) if args.member else None
        cards = list_cards(conn, member, include_closed=args.all)
        if not cards:
            print("등록된 카드가 없습니다. 'card add'로 등록하세요")
            return 0
        _print_cards(conn, cards)
        return 0

    return _with_db(action)


def cmd_card_update(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        card = update_card(conn, args.id, **_card_args(args))
        print("수정했습니다")
        _print_cards(conn, [card])
        log.info("card update: id=%d", card.id)
        return 0

    return _with_db(action)


def cmd_card_close(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        card = close_card(conn, args.id, args.date or _today())
        print("해지로 표시했습니다 (지우지 않고 남겨 둡니다)")
        _print_cards(conn, [card])
        log.info("card close: id=%d", card.id)
        return 0

    return _with_db(action)


def cmd_card_reopen(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        card = reopen_card(conn, args.id)
        print("다시 사용 중으로 바꿨습니다")
        _print_cards(conn, [card])
        return 0

    return _with_db(action)


def cmd_fixed_check(args: argparse.Namespace) -> int:
    def action(conn) -> int:
        tz = get_settings().tz
        today = dt.datetime.now(tz).date()
        member = get_member(conn, args.member) if args.member else None
        year_month = args.month or previous_month(today)
        report = month_status(conn, year_month, member=member, today=today)
        counts = report.counts
        parts = [f"{label} {counts[key]:,}" for key, label in STATUS_LABELS.items() if counts[key]]
        who = f" · {member.display_name}" if member else ""
        print(
            f"{report.year_month} 고정비{who} · {len(report.statuses):,}건 · " + " · ".join(parts)
        )
        log.info(
            "fixed check %s: %d건 %s",
            report.year_month,
            len(report.statuses),
            dict(counts),
        )
        if not report.statuses:
            print("이 달에 해당하는 고정비가 없습니다")
            return 0
        print(
            f"생활 고정비 {report.total('living'):,}원 · "
            f"저축·상환 {report.total('saving'):,}원 (기준 금액)"
        )
        _print_fixed_table(conn, report.statuses)
        return 0

    return _with_db(action)


def _print_fixed_table(conn, statuses: list[FixedStatus]) -> None:
    names = _member_names(conn)
    name_w = max(_display_width("이름"), *(_display_width(s.item.name) for s in statuses)) + 2

    def line(day: str, name: str, who: str, amount: str, state: str) -> str:
        return (
            "  "
            + _pad(day, 7, right=False)
            + _pad(name, name_w, right=False)
            + _pad(who, 6, right=False)
            + _pad(amount, 12, right=True)
            + "   "
            + state
        )

    print(line("예정일", "이름", "담당", "기준 금액", "상태"))
    for s in statuses:
        print(
            line(
                s.expected_date[5:],
                s.item.name,
                names.get(s.item.member_id, "?"),
                f"{s.item.amount:,}원",
                _fixed_state(s),
            )
        )


def _fixed_state(s: FixedStatus) -> str:
    if s.tx is None:
        return s.label
    paid = f"{s.tx.date[5:]} {s.tx.paid:,}원"
    if s.status == "paid":
        return f"✓ {paid}"
    gap = s.amount_gap or 0
    return f"{s.label} · {paid} (기준보다 {gap:+,}원)"


MAX_DIFF_SHOWN = 10


def _settle_session(
    args: argparse.Namespace, run, *, with_month: bool = True, before_file=None
) -> int:
    """정산 명령 공통: 설정·DB·구성원 확인 → 비밀번호 전 기간 확인 → 파일 열기 → run(...)

    with_month=False면 정산 대상 월 없이 파일만 열고 run(..., preview=None)을 부른다.
    before_file(conn, member)는 파일을 열기(비밀번호 묻기) 전에 실행할 확인이다.
    """
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
        if before_file is not None:
            before_file(conn, member)
        data = path.read_bytes()
        if not with_month:
            parsed = parse_opened(_open_with_prompt(data), data, path.name)
            return run(conn, settings, member, parsed, None)
        year_month = args.month or previous_month(dt.datetime.now(settings.tz).date())
        # 비밀번호를 묻기 전에, 파일 이름(zip이면 안의 엑셀 이름)의 기간으로 먼저 확인
        precheck_month(peek_inner_name(data) or path.name, year_month)
        parsed = parse_opened(_open_with_prompt(data), data, path.name)
        preview = preview_month(conn, member, parsed, year_month, tz=settings.tz)
        return run(conn, settings, member, parsed, preview)
    except ExportInvalid as exc:
        _print_row_errors(exc.result.errors)
        return 1
    except (MemberError, ImportFailure, PreviewError, FirstLoadError) as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def _log_counts(action: str, member, preview: MonthPreview) -> None:
    log.info(
        "settle %s %s %s: 신규 %d, 변경 %d, 삭제 %d, 동일 %d",
        action,
        member.slug,
        preview.year_month,
        len(preview.added),
        len(preview.changed),
        len(preview.removed),
        preview.unchanged,
    )


def cmd_settle_preview(args: argparse.Namespace) -> int:
    def run(conn, settings, member, parsed, preview) -> int:
        _print_preview(preview, parsed, settings)
        recent = check_recent(conn, member, parsed, preview.year_month, tz=settings.tz)
        _print_checks(recent, args.file, member, empty="점검할 이전 정산 달 없음")
        print()
        print("※ 미리보기입니다. DB는 바뀌지 않았습니다.")
        _log_counts("preview", member, preview)
        return 0

    return _settle_session(args, run)


def ask_confirm(prompt: str) -> bool:
    """y를 입력해야만 True. (테스트에서 바꿔 끼울 수 있게 분리)"""
    try:
        return input(prompt).strip().lower() == "y"
    except EOFError:
        return False


def cmd_settle_apply(args: argparse.Namespace) -> int:
    def run(conn, settings, member, parsed, preview) -> int:
        _print_preview(preview, parsed, settings)
        recent = check_recent(conn, member, parsed, preview.year_month, tz=settings.tz)
        _print_checks(recent, args.file, member, empty="점검할 이전 정산 달 없음")
        print()
        if preview.kind == "resettle" and not preview.has_changes:
            print("변경 사항이 없어 저장하지 않았습니다")
            return 0
        if not args.yes and not ask_confirm("이대로 정산할까요? [y/N]: "):
            print("취소했습니다. DB는 바뀌지 않았습니다.")
            return 1

        result = apply_month(conn, member, parsed, preview.year_month, tz=settings.tz)
        if not result.saved:  # 확인하는 사이 같은 내용이 먼저 저장된 경우
            print("변경 사항이 없어 저장하지 않았습니다")
            return 0
        print(
            f"저장        신규 {result.added:,}건 추가 · 변경 {result.changed:,}건 · "
            f"삭제 {result.removed:,}건"
        )
        print(f"월 상태     {preview.year_month} {member.display_name} · 정산 완료")
        print(f"업로드 기록 #{result.batch_id}")
        _log_counts("apply", member, result.preview)
        return 0

    return _settle_session(args, run)


def cmd_settle_first_load(args: argparse.Namespace) -> int:
    def run(conn, settings, member, parsed, preview) -> int:
        plan = plan_first_load(conn, member, parsed, preview.year_month, tz=settings.tz)
        _print_first_load_plan(plan, parsed)
        print()
        if not args.yes and not ask_confirm("이대로 적재할까요? [y/N]: "):
            print("취소했습니다. DB는 바뀌지 않았습니다.")
            return 1
        result = first_load(conn, member, parsed, plan.target_month, tz=settings.tz)
        p = result.plan
        print(
            f"저장        {len(p.months)}개월, 거래 {p.total_rows:,}건 · "
            f"업로드 기록 #{result.batch_id}"
        )
        log.info(
            "settle first-load %s: %d개월(기초 %d), 거래 %d건",
            member.slug,
            len(p.months),
            p.baseline_count,
            p.total_rows,
        )
        return 0

    def before_file(conn, member) -> None:
        ensure_first_load_allowed(conn, member)  # 비밀번호를 묻기 전에 확인

    return _settle_session(args, run, before_file=before_file)


def _print_first_load_plan(plan: FirstLoadPlan, parsed: ParsedExport) -> None:
    p = parsed.summary.period
    period = f" · 파일 {p.start} ~ {p.end} ({p.source} 기준)" if p else ""
    print(f"첫 적재     {plan.member.display_name}{period}")
    print()
    print(f"{_pad('월', 10, right=False)}{_pad('건수', 8, right=True)}   상태")
    for m in plan.months:
        label = "정산 완료   ← 정산 대상" if m.status == "settled" else "기초 데이터"
        print(f"{m.year_month:<10}{m.count:>8,}   {label}")
    if plan.excluded:
        print("제외        " + ", ".join(f"{ym} ({why})" for ym, why in plan.excluded))
    print()
    settled = len(plan.months) - plan.baseline_count
    print(
        f"합계        {len(plan.months)}개월, 거래 {plan.total_rows:,}건 "
        f"(기초 {plan.baseline_count}개월 · 정산 {settled}개월)"
    )
    _print_cashflow(parsed.comparison, parsed.cashflow_warnings)
    warnings = list(dict.fromkeys(w for m in plan.months for w in m.preview.warnings))
    _print_block("경고", warnings, empty="없음")


def cmd_settle_check(args: argparse.Namespace) -> int:
    def run(conn, settings, member, parsed, _preview) -> int:
        p = parsed.summary.period
        if p:
            print(f"파일        {p.start} ~ {p.end} ({p.source} 기준)")
        checks = check_all(conn, member, parsed, tz=settings.tz)
        _print_checks(checks, args.file, member, empty="파일에 완전히 들어 있는 정산 달이 없습니다")
        print()
        print("※ 점검만 했습니다. DB는 바뀌지 않았습니다.")
        dirty = sum(1 for c in checks if not c.clean)
        log.info("settle check %s: 점검 %d개월, 변동 %d개월", member.slug, len(checks), dirty)
        return 0

    return _settle_session(args, run, with_month=False)


def _check_summary(c: MonthCheck) -> str:
    parts = [
        (len(c.added), "새로 생긴 거래"),
        (len(c.removed), "사라진 거래"),
        (len(c.changed), "내용이 바뀐 거래"),
        (len(c.shifted), "시각이 바뀐 거래"),
    ]
    text = ", ".join(f"{label} {n:,}건" for n, label in parts if n)
    return f"{c.year_month}: {text} → 재정산 권장" if text else f"{c.year_month}: 변동 없음"


def _print_checks(checks: list[MonthCheck], path: Path, member, *, empty: str) -> None:
    print()
    if not checks:
        _print_block("누락 점검", [], empty=empty)
        return
    lines: list[str] = []
    for c in checks:
        lines.append(_check_summary(c))
        detail = [
            *(
                ("+", k.row.date.isoformat(), k.row.time.isoformat(), k.row.amount, k.row.content)
                for k in c.added
            ),
            *(("−", s.tx_date, s.tx_time, s.amount, s.content) for s in c.removed),
            *(
                (
                    "*",
                    ch.new.row.date.isoformat(),
                    ch.new.row.time.isoformat(),
                    ch.new.row.amount,
                    ch.new.row.content,
                )
                for ch in c.changed
            ),
            *(
                (
                    "~",
                    t.new.row.date.isoformat(),
                    f"{t.old.tx_time} → {t.new.row.time.isoformat()}",
                    t.new.row.amount,
                    t.new.row.content,
                )
                for t in c.shifted
            ),
        ]
        for mark, date, time, amount, content in detail[:MAX_DIFF_SHOWN]:
            lines.append(f"  {mark} {date} {time}  {amount:>+12,}  {content}")
        if len(detail) > MAX_DIFF_SHOWN:
            lines.append(f"  … 외 {len(detail) - MAX_DIFF_SHOWN:,}건")
    for c in checks:
        lines += [f"⚠ {w}" for w in c.warnings]
    for c in checks:
        if not c.clean:
            lines.append(
                f'재정산: settle apply "{path.name}" --member {member.slug} --month {c.year_month}'
            )
    _print_block("누락 점검", lines)


def _print_preview(preview: MonthPreview, parsed: ParsedExport, settings: Settings) -> None:
    kind = {
        "new": "새 정산",
        "baseline_to_settled": "기초 데이터 → 정산으로 전환",
        "resettle": "재정산",
    }[preview.kind]
    if preview.previous and preview.kind == "resettle":
        kind += f" (기존: 정산 완료 {local_date(preview.previous.settled_at, settings.tz)})"
    m = preview.month
    p = parsed.summary.period
    print(f"정산 대상   {preview.year_month} ({preview.member.display_name}) · {kind}")
    if p:
        print(
            f"파일        {p.start} ~ {p.end} ({p.source} 기준), 이 달 거래 {len(preview.rows):,}건"
        )
    print(
        f"합계        수입 {m.income:,} · 지출 {m.expense:,} · "
        f"이체 들어옴 {m.transfer_in:,} · 나감 {m.transfer_out:,}"
    )
    _print_cashflow(parsed.comparison, parsed.cashflow_warnings)
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

    status = sub.add_parser(
        "inspect-status", help="'뱅샐현황' 시트 구조 보기 (값은 숨김, DB는 바꾸지 않음)"
    )
    status.add_argument("file", type=Path, help="뱅샐 내보내기 파일 (zip 또는 xlsx)")
    status.set_defaults(func=cmd_inspect_status)

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

    s_apply = settle_sub.add_parser("apply", help="정산 확정 저장")
    s_apply.add_argument("file", type=Path, help="뱅샐 내보내기 파일 (zip 또는 xlsx)")
    s_apply.add_argument("--member", required=True, help="파일 주인의 slug (예: sangbeom)")
    s_apply.add_argument("--month", help="정산할 월 YYYY-MM (기본: 지난달)")
    s_apply.add_argument("--yes", action="store_true", help="확인 없이 바로 저장")
    s_apply.set_defaults(func=cmd_settle_apply)

    s_first = settle_sub.add_parser(
        "first-load", help="첫 적재 (과거 달은 기초 데이터, 지난달은 정산)"
    )
    s_first.add_argument("file", type=Path, help="뱅샐 내보내기 파일 (zip 또는 xlsx)")
    s_first.add_argument("--member", required=True, help="파일 주인의 slug (예: sangbeom)")
    s_first.add_argument("--month", help="정산 완료로 넣을 월 YYYY-MM (기본: 지난달)")
    s_first.add_argument("--yes", action="store_true", help="확인 없이 바로 저장")
    s_first.set_defaults(func=cmd_settle_first_load)

    s_check = settle_sub.add_parser("check", help="이미 정산한 달 누락 점검 (DB는 바꾸지 않음)")
    s_check.add_argument("file", type=Path, help="뱅샐 내보내기 파일 (zip 또는 xlsx)")
    s_check.add_argument("--member", required=True, help="파일 주인의 slug (예: sangbeom)")
    s_check.set_defaults(func=cmd_settle_check)

    instrument = sub.add_parser("instrument", help="뱅샐 결제수단 (계좌·카드 연결)")
    inst_sub = instrument.add_subparsers(dest="instrument_command", required=True)
    i_list = inst_sub.add_parser("list", help="결제수단 이름별 건수·기간·연결 상태")
    i_list.add_argument("--member", required=True, help="구성원 slug (예: sangbeom)")
    i_list.add_argument("--unlinked", action="store_true", help="연결 안 된 것만")
    i_list.set_defaults(func=cmd_instrument_list)

    i_link = inst_sub.add_parser("link", help="결제수단 이름을 계좌·카드·간편결제에 연결")
    i_link.add_argument("instrument", help="결제수단 이름 ('instrument list'에서 그대로 복사)")
    i_link.add_argument("--member", required=True, help="구성원 slug")
    target = i_link.add_mutually_exclusive_group(required=True)
    target.add_argument("--account", type=int, metavar="ID", help="계좌 번호(#)")
    target.add_argument("--card", type=int, metavar="ID", help="카드 번호(#)")
    target.add_argument("--easy-pay", action="store_true", help="간편결제로 표시")
    i_link.add_argument("--force", action="store_true", help="거래에 없는 이름도 연결")
    i_link.set_defaults(func=cmd_instrument_link)

    i_unlink = inst_sub.add_parser("unlink", help="연결 풀기")
    i_unlink.add_argument("instrument", help="결제수단 이름")
    i_unlink.add_argument("--member", required=True, help="구성원 slug")
    i_unlink.set_defaults(func=cmd_instrument_unlink)

    _add_account_parsers(sub)
    _add_card_parsers(sub)

    fixed = sub.add_parser("fixed", help="고정비")
    fixed_sub = fixed.add_subparsers(dest="fixed_command", required=True)
    f_check = fixed_sub.add_parser(
        "check", help="그달 고정비가 빠져나갔는지 확인 (DB는 바꾸지 않음)"
    )
    f_check.add_argument("--month", help="YYYY-MM (기본: 지난달)")
    f_check.add_argument("--member", help="이 구성원만")
    f_check.set_defaults(func=cmd_fixed_check)
    return parser


_CLEAR_HELP = "('' 를 넣으면 지움)"


def _add_account_parsers(sub) -> None:
    account = sub.add_parser("account", help="계좌 관리 (번호는 끝 4자리만)")
    acc_sub = account.add_subparsers(dest="account_command", required=True)

    def fields(p, *, required: bool) -> None:
        p.add_argument("--institution", required=required, help="은행·증권사")
        p.add_argument("--name", required=required, help="계좌 이름 (예: 주거래 통장)")
        p.add_argument("--kind", required=required, help="종류: 입출금, 적금, 예금, 증권 …")
        p.add_argument("--last4", help=f"계좌번호 끝 4자리 {_CLEAR_HELP}")
        p.add_argument("--opened", help=f"개설일 YYYY-MM 또는 YYYY-MM-DD {_CLEAR_HELP}")
        p.add_argument("--color", help=f"대표색 #rrggbb {_CLEAR_HELP}")
        p.add_argument("--memo", help=f"용도 메모 {_CLEAR_HELP}")
        p.add_argument("--hint", help=f"전체 번호 보는 곳 {_CLEAR_HELP}")

    a_add = acc_sub.add_parser("add", help="계좌 등록")
    a_add.add_argument("--member", required=True, help="명의자 slug")
    fields(a_add, required=True)
    a_add.set_defaults(func=cmd_account_add)

    a_list = acc_sub.add_parser("list", help="계좌 목록")
    a_list.add_argument("--member", help="이 구성원만")
    a_list.add_argument("--all", action="store_true", help="해지한 계좌도")
    a_list.set_defaults(func=cmd_account_list)

    a_upd = acc_sub.add_parser("update", help="계좌 정보 수정")
    a_upd.add_argument("id", type=int, help="계좌 번호(#)")
    fields(a_upd, required=False)
    a_upd.set_defaults(func=cmd_account_update)

    a_close = acc_sub.add_parser("close", help="해지로 표시 (지우지 않음)")
    a_close.add_argument("id", type=int)
    a_close.add_argument("--date", help="해지일 YYYY-MM-DD (기본: 오늘)")
    a_close.set_defaults(func=cmd_account_close)

    a_reopen = acc_sub.add_parser("reopen", help="해지 표시 취소")
    a_reopen.add_argument("id", type=int)
    a_reopen.set_defaults(func=cmd_account_reopen)


def _add_card_parsers(sub) -> None:
    card = sub.add_parser("card", help="카드 관리 (번호는 끝 4자리만, CVC·유효기간은 받지 않음)")
    card_sub = card.add_subparsers(dest="card_command", required=True)

    def fields(p, *, required: bool) -> None:
        p.add_argument("--issuer", required=required, help="카드사")
        p.add_argument("--name", required=required, help="카드 이름")
        p.add_argument("--kind", required=required, help="종류: 신용, 체크, 하이패스, 기타")
        p.add_argument("--last4", help=f"카드번호 끝 4자리 {_CLEAR_HELP}")
        p.add_argument("--pay-day", type=int, help="결제일 1~31 (0을 넣으면 지움)")
        p.add_argument(
            "--period",
            type=int,
            nargs=2,
            metavar=("시작일", "몇달전"),
            help="이용기간. 예) 전월 1일~말일 = 1 -1, 전월 15일~당월 14일 = 15 -1 (0 0 = 지움)",
        )
        p.add_argument("--pay-account", type=int, metavar="ID", help="결제 계좌 번호(#) (0 = 지움)")
        p.add_argument("--fee", type=int, help="연회비 (원)")
        p.add_argument("--color", help=f"대표색 #rrggbb {_CLEAR_HELP}")
        p.add_argument("--memo", help=f"메모 {_CLEAR_HELP}")
        p.add_argument("--hint", help=f"전체 번호·CVC 보는 곳 {_CLEAR_HELP}")

    c_add = card_sub.add_parser("add", help="카드 등록")
    c_add.add_argument("--member", required=True, help="명의자 slug")
    fields(c_add, required=True)
    c_add.set_defaults(func=cmd_card_add)

    c_list = card_sub.add_parser("list", help="카드 목록")
    c_list.add_argument("--member", help="이 구성원만")
    c_list.add_argument("--all", action="store_true", help="해지한 카드도")
    c_list.set_defaults(func=cmd_card_list)

    c_upd = card_sub.add_parser("update", help="카드 정보 수정")
    c_upd.add_argument("id", type=int, help="카드 번호(#)")
    fields(c_upd, required=False)
    c_upd.set_defaults(func=cmd_card_update)

    c_close = card_sub.add_parser("close", help="해지로 표시 (지우지 않음)")
    c_close.add_argument("id", type=int)
    c_close.add_argument("--date", help="해지일 YYYY-MM-DD (기본: 오늘)")
    c_close.set_defaults(func=cmd_card_close)

    c_reopen = card_sub.add_parser("reopen", help="해지 표시 취소")
    c_reopen.add_argument("id", type=int)
    c_reopen.set_defaults(func=cmd_card_reopen)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
