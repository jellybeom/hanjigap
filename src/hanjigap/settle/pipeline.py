"""뱅샐 파일 → 정리된 거래까지 한 번에 (B1~B5, 뱅샐 현금흐름 대조, 뱅샐현황 표 포함).

명령어, 정산 확정, 웹 업로드가 모두 이 함수를 쓴다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from hanjigap.importer.cashflow import (
    Cashflow,
    CashflowComparison,
    compare_cashflow,
    read_cashflow,
)
from hanjigap.importer.fingerprint import KeyedRow, assign_keys
from hanjigap.importer.normalize import NormalizeResult, normalize
from hanjigap.importer.sheet import LedgerSheet, find_ledger
from hanjigap.importer.status_tables import StatusTables, read_status_tables
from hanjigap.importer.summary import FileSummary, parse_period, summarize
from hanjigap.importer.unpack import OpenedExport, open_export


class ExportInvalid(Exception):
    """행 정규화 오류가 있어 이 파일을 쓸 수 없을 때."""

    def __init__(self, result: NormalizeResult) -> None:
        self.result = result
        super().__init__(f"오류가 {len(result.errors):,}건 있어 이 파일은 사용할 수 없습니다")


@dataclass(frozen=True)
class ParsedExport:
    opened: OpenedExport
    ledger: LedgerSheet
    normalized: NormalizeResult
    keyed: list[KeyedRow]
    summary: FileSummary
    file_sha256: str  # 업로드한 원본 파일(zip 또는 xlsx)의 해시
    file_name: str | None  # 기간을 읽은 이름 (zip이면 안의 엑셀 이름)
    cashflow: Cashflow | None = None  # 뱅샐현황 시트의 현금흐름현황 (참고·대조용)
    cashflow_warnings: tuple[str, ...] = ()
    comparison: CashflowComparison | None = None
    status: StatusTables | None = None  # 뱅샐현황의 재무·보험·투자·대출 표 (참고)


def parse_opened(opened: OpenedExport, data: bytes, file_name: str | None) -> ParsedExport:
    """이미 연 파일(비밀번호 처리 완료)을 정리한다. data는 업로드한 원본 바이트."""
    ledger = find_ledger(opened.xlsx)
    result = normalize(ledger)
    if not result.ok:
        raise ExportInvalid(result)
    name = opened.inner_name if opened.container == "zip" else file_name
    cashflow, cf_warnings = read_cashflow(opened.xlsx)
    return ParsedExport(
        opened=opened,
        ledger=ledger,
        normalized=result,
        keyed=assign_keys(result.rows),
        summary=summarize(result.rows, parse_period(name)),
        file_sha256=hashlib.sha256(data).hexdigest(),
        file_name=name,
        cashflow=cashflow,
        cashflow_warnings=tuple(cf_warnings),
        comparison=compare_cashflow(cashflow, result.rows) if cashflow else None,
        status=read_status_tables(opened.xlsx),
    )


def parse_export(data: bytes, password: str | None, file_name: str | None = None) -> ParsedExport:
    return parse_opened(open_export(data, password), data, file_name)
