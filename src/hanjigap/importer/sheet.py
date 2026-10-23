"""가계부 시트 찾기와 헤더 검증.

열은 위치가 아니라 이름으로 찾는다. 뱅샐이 열 순서를 바꾸거나 새 열을 추가해도 동작하게 하고,
형식이 바뀐 흔적(시트 이름 변경, 새 열, 선택 열 없음)은 경고로 남긴다.
각 행의 값을 정리하는 일은 다음 단계(B3)에서 한다.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from openpyxl import load_workbook

from hanjigap.importer.errors import ErrorCode, ImportFailure

LEDGER_SHEET_NAME = "가계부 내역"
REQUIRED_COLUMNS: tuple[str, ...] = (
    "날짜",
    "시간",
    "타입",
    "대분류",
    "소분류",
    "내용",
    "금액",
    "결제수단",
)
OPTIONAL_COLUMNS: dict[str, str] = {
    "화폐": "KRW로 처리",
    "메모": "빈 값으로 처리",
}
KNOWN_COLUMNS: tuple[str, ...] = REQUIRED_COLUMNS + tuple(OPTIONAL_COLUMNS)
HEADER_SEARCH_ROWS = 5

_WS = re.compile(r"\s+")


@dataclass
class LedgerSheet:
    sheet_name: str
    sheet_names: list[str]
    header_row: int  # 엑셀 기준 행 번호 (1부터)
    columns: dict[str, int]  # 열 이름 → 0부터 시작하는 열 위치
    warnings: list[str] = field(default_factory=list)
    _rows: list[tuple[int, tuple[Any, ...]]] = field(default_factory=list, repr=False)

    @property
    def data_rows(self) -> int:
        return len(self._rows)

    @property
    def optional_found(self) -> list[str]:
        return [c for c in OPTIONAL_COLUMNS if c in self.columns]

    def iter_rows(self) -> Iterator[tuple[int, dict[str, Any]]]:
        """(엑셀 행 번호, {열 이름: 원래 값})을 돌려준다. 없는 선택 열은 None."""
        for row_no, values in self._rows:
            record = {
                name: values[idx] if idx < len(values) else None
                for name, idx in self.columns.items()
            }
            for name in KNOWN_COLUMNS:
                record.setdefault(name, None)
            yield row_no, record


def find_ledger(xlsx: bytes) -> LedgerSheet:
    try:
        wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportFailure(ErrorCode.NOT_EXCEL, "엑셀을 읽을 수 없음") from exc

    try:
        names = list(wb.sheetnames)
        warnings: list[str] = []

        if LEDGER_SHEET_NAME in names:
            ws = wb[LEDGER_SHEET_NAME]
            header = _find_header(ws)
            if header is None:
                raise ImportFailure(ErrorCode.HEADER_MISSING_COLUMNS, ", ".join(_best_missing(ws)))
        else:
            ws, header = _search_other_sheets(wb)
            if ws is None or header is None:
                raise ImportFailure(ErrorCode.LEDGER_SHEET_NOT_FOUND, f"시트 목록: {names}")
            warnings.append(
                f"시트 이름이 예상과 다릅니다: '{ws.title}' (기대: '{LEDGER_SHEET_NAME}')"
            )

        header_row, cells = header
        columns = _map_columns(cells, warnings)
        rows = _collect_rows(ws, header_row)
        return LedgerSheet(
            sheet_name=ws.title,
            sheet_names=names,
            header_row=header_row,
            columns=columns,
            warnings=warnings,
            _rows=rows,
        )
    finally:
        wb.close()


# ---------------------------------------------------------------------------


def _normalize(value: Any) -> str:
    return _WS.sub("", str(value)) if value is not None else ""


def _head_rows(ws) -> list[tuple[int, list[str]]]:
    out = []
    for i, row in enumerate(ws.iter_rows(max_row=HEADER_SEARCH_ROWS, values_only=True), start=1):
        out.append((i, [_normalize(v) for v in row]))
    return out


def _find_header(ws) -> tuple[int, list[str]] | None:
    """처음 몇 행 중 필수 열이 모두 있는 첫 행."""
    for row_no, cells in _head_rows(ws):
        if all(col in cells for col in REQUIRED_COLUMNS):
            return row_no, cells
    return None


def _best_missing(ws) -> list[str]:
    """헤더를 못 찾았을 때, 가장 많이 맞은 행 기준으로 빠진 필수 열."""
    best: list[str] = list(REQUIRED_COLUMNS)
    for _, cells in _head_rows(ws):
        missing = [c for c in REQUIRED_COLUMNS if c not in cells]
        if len(missing) < len(best):
            best = missing
    return best


def _search_other_sheets(wb):
    for name in wb.sheetnames:
        ws = wb[name]
        header = _find_header(ws)
        if header is not None:
            return ws, header
    return None, None


def _map_columns(cells: list[str], warnings: list[str]) -> dict[str, int]:
    counts = Counter(c for c in cells if c)
    duplicates = [c for c in KNOWN_COLUMNS if counts[c] > 1]
    if duplicates:
        raise ImportFailure(ErrorCode.HEADER_DUPLICATE_COLUMNS, ", ".join(duplicates))

    columns = {c: cells.index(c) for c in KNOWN_COLUMNS if c in cells}
    for col, handling in OPTIONAL_COLUMNS.items():
        if col not in columns:
            warnings.append(f"선택 열이 없습니다: {col} ({handling})")
    unknown = [c for c in cells if c and c not in KNOWN_COLUMNS]
    if unknown:
        warnings.append(f"처음 보는 열은 무시합니다: {', '.join(unknown)}")
    return columns


def _collect_rows(ws, header_row: int) -> list[tuple[int, tuple[Any, ...]]]:
    rows = []
    for row_no, values in enumerate(
        ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1
    ):
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue  # 빈 행
        rows.append((row_no, tuple(values)))
    return rows
