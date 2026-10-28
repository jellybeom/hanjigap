"""'뱅샐현황' 시트의 재무·보험·투자·대출 표 읽기 (J4b-1).

표 모양 (2026년 10월 실제 파일 기준)
    각 표: 'N.제목' 줄 → 설명 한 줄 → 머리글 → 항목 줄들 → '총계' 묶음
    재무현황: 왼쪽 자산(항목 · 상품명 · 금액), 오른쪽 부채(항목 · 상품명 · 금액)가 나란히.
              '항목'은 묶음의 첫 줄에만 있고 아래 줄은 비어 있다 (위 값을 이어받는다).
              끝: '총자산 … 금액 | 총부채 … 금액' 줄 → '순자산' 줄 → 순자산 금액 줄
    보험·투자·대출현황: 열 이름으로 찾는다 (순서가 바뀌어도 괜찮게).
              끝: '총계' 줄(이름: 보유 대출 상품, 총 대출 원금 …) → 바로 아래 줄(값)
    수익률·금리는 % 숫자 그대로다 (5.55 = 5.55%).

원칙
    '고객정보' 표는 읽지 않는다.
    이 표들은 참고 자료다. 못 읽어도 정산은 막지 않고 경고만 남긴다.
    항목을 더한 값이 '총계'와 맞는지 검증한다.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass, field
from typing import Any

from openpyxl import load_workbook

STATUS_SHEET = "뱅샐현황"
SECTIONS = ("asset", "liability", "insurance", "investment", "loan")
SECTION_LABELS = {
    "asset": "자산",
    "liability": "부채",
    "insurance": "보험",
    "investment": "투자",
    "loan": "대출",
}
_TITLE_RE = re.compile(r"^\s*\d+\s*\.\s*(\S.*)$")

# 표 이름 → (구분, {열 이름: 담을 칸})
_TABLES = {
    "보험현황": (
        "insurance",
        {
            "금융사": "institution",
            "보험명": "name",
            "계약상태": "status",
            "총납입금": "amount",
            "계약일자": "start_date",
            "만기일자": "end_date",
        },
    ),
    "투자현황": (
        "investment",
        {
            "투자상품종류": "group",
            "금융사": "institution",
            "상품명": "name",
            "투자원금": "principal",
            "평가금액": "amount",
            "수익률": "rate",
            "가입일자": "start_date",
            "만기일자": "end_date",
        },
    ),
    "대출현황": (
        "loan",
        {
            "대출종류": "group",
            "금융사": "institution",
            "상품명": "name",
            "대출원금": "principal",
            "대출잔액": "amount",
            "대출금리": "rate",
            "대출신규일": "start_date",
            "대출만기일": "end_date",
        },
    ),
}
_REQUIRED = {"name", "amount"}
TOTAL_WORDS = ("총계",)


@dataclass(frozen=True)
class Holding:
    section: str  # asset | liability | insurance | investment | loan
    group: str | None  # 자산·부채 항목, 투자상품종류, 대출종류
    institution: str | None
    name: str
    amount: int | None  # 자산·부채 금액, 투자 평가금액, 대출 잔액, 보험 총납입금
    principal: int | None = None  # 투자원금, 대출원금(마이너스 통장은 한도)
    rate: float | None = None  # 수익률·금리 (%)
    status: str | None = None  # 보험 계약상태
    start_date: str | None = None
    end_date: str | None = None
    row_no: int = 0


@dataclass(frozen=True)
class Check:
    name: str
    expected: int  # 뱅샐 '총계'에 적힌 값
    actual: int  # 한지갑이 항목을 더한 값

    @property
    def ok(self) -> bool:
        return self.expected == self.actual

    def __str__(self) -> str:
        return f"{self.name}: 뱅샐 {self.expected:,} / 한지갑 {self.actual:,}"


@dataclass
class StatusTables:
    holdings: list[Holding] = field(default_factory=list)
    totals: dict[str, dict[str, int]] = field(default_factory=dict)  # 구분 → {이름: 값}
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    found: set[str] = field(default_factory=set)  # 읽은 표 (구분)

    def of(self, section: str) -> list[Holding]:
        return [h for h in self.holdings if h.section == section]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def total_assets(self) -> int | None:
        return self.totals.get("asset", {}).get("총자산")

    @property
    def total_liabilities(self) -> int | None:
        return self.totals.get("liability", {}).get("총부채")

    @property
    def net_worth(self) -> int | None:
        return self.totals.get("asset", {}).get("순자산")


def read_status_tables(xlsx: bytes) -> StatusTables:
    result = StatusTables()
    try:
        wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    except Exception:
        result.warnings.append("엑셀을 읽을 수 없어 뱅샐현황 표를 건너뜁니다")
        return result
    try:
        if STATUS_SHEET not in wb.sheetnames:
            result.warnings.append(
                f"'{STATUS_SHEET}' 시트가 없어 재무·투자·대출·보험 표를 건너뜁니다"
            )
            return result
        rows = [list(r) for r in wb[STATUS_SHEET].iter_rows(values_only=True)]
    finally:
        wb.close()

    for title, body, first_row in _sections(rows):
        if "고객" in title:
            continue  # 개인정보는 읽지 않는다
        if title.endswith("재무현황"):
            _read_finance(body, first_row, result)
            continue
        for table_name, (section, columns) in _TABLES.items():
            if title.endswith(table_name):
                _read_table(section, columns, body, first_row, result)
    _cross_checks(result)
    for section in ("asset", "insurance", "investment", "loan"):
        if section not in result.found:
            result.warnings.append(f"뱅샐현황에서 {SECTION_LABELS[section]} 표를 찾지 못했어요")
    return result


# --- 표 나누기 -------------------------------------------------------------------


def _sections(rows: list[list[Any]]):
    """(제목, 본문 줄들, 본문 첫 줄 번호)"""
    starts = []
    for i, r in enumerate(rows):
        cells = [c for c in r if c is not None and str(c).strip()]
        if len(cells) == 1 and isinstance(cells[0], str):
            m = _TITLE_RE.match(cells[0].strip())
            if m:
                starts.append((i, m[1].strip()))
    for n, (i, title) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(rows)
        yield title, rows[i + 1 : end], i + 2


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)) if value is not None else ""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    text = str(value).replace(",", "").strip()
    try:
        return round(float(text)) if text and text != "-" else None
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    try:
        return float(text) if text and text != "-" else None
    except ValueError:
        return None


def _date(value: Any) -> str | None:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = _text(value)
    if text and re.fullmatch(r"\d{4}[-.]\d{2}[-.]\d{2}", text):
        return text.replace(".", "-")
    return None


def _cell(row: list[Any], j: int | None) -> Any:
    return row[j] if j is not None and j < len(row) else None


# --- 재무현황 --------------------------------------------------------------------


def _read_finance(body: list[list[Any]], first_row: int, result: StatusTables) -> None:
    header = None
    for k, r in enumerate(body):
        names = [_norm(c) for c in r]
        if names.count("항목") >= 2 and names.count("상품명") >= 2 and names.count("금액") >= 2:
            header = (k, names)
            break
    if header is None:
        result.warnings.append("재무현황 표의 머리글(항목 · 상품명 · 금액)을 찾지 못했어요")
        return
    k, names = header
    sides = []
    for nth in range(2):
        g = _nth_index(names, "항목", nth)
        sides.append(
            (
                ("asset", "liability")[nth],
                g,
                _nth_index(names, "상품명", nth),
                _nth_index(names, "금액", nth),
            )
        )
    result.found.update({"asset", "liability"})
    groups: dict[str, str | None] = {"asset": None, "liability": None}
    totals: dict[str, int] = {}
    rest = body[k + 1 :]
    i = 0
    while i < len(rest):
        r = rest[i]
        row_no = first_row + k + 1 + i
        joined = [_norm(c) for c in r]
        if "총자산" in joined or "총부채" in joined:
            for section, g, _, a in sides:
                label = _norm(_cell(r, g))
                value = _int(_cell(r, a))
                if label in ("총자산", "총부채") and value is not None:
                    result.totals.setdefault(section, {})[label] = value
            # 그다음: '순자산' 이름 줄 → 금액 줄
            for nxt in rest[i + 1 : i + 4]:
                if "순자산" in [_norm(c) for c in nxt]:
                    continue
                value = next((_int(c) for c in nxt if _int(c) is not None), None)
                if value is not None:
                    totals["순자산"] = value
                    break
            break
        for section, g, n, a in sides:
            group = _text(_cell(r, g))
            if group is not None and _int(group) is None:
                groups[section] = group
            name = _text(_cell(r, n))
            amount = _int(_cell(r, a))
            if name is None and amount is None:
                continue
            if name is None:
                result.warnings.append(f"재무현황 {row_no}행: 상품명이 비어 있어 건너뜁니다")
                continue
            result.holdings.append(
                Holding(section, groups[section], None, name, amount, row_no=row_no)
            )
        i += 1
    result.totals.setdefault("asset", {}).update(totals)
    if result.total_assets is None or result.total_liabilities is None:
        result.warnings.append("재무현황의 총자산·총부채 줄을 찾지 못했어요")


def _nth_index(names: list[str], word: str, nth: int) -> int | None:
    found = [j for j, n in enumerate(names) if n == word]
    return found[nth] if nth < len(found) else None


# --- 보험·투자·대출현황 ------------------------------------------------------------


def _read_table(
    section: str,
    columns: dict[str, str],
    body: list[list[Any]],
    first_row: int,
    result: StatusTables,
) -> None:
    label = SECTION_LABELS[section]
    header = None
    for k, r in enumerate(body):
        names = [_norm(c) for c in r]
        found = {columns[h]: names.index(h) for h in columns if h in names}
        if set(found) >= _REQUIRED:
            header = (k, found)
            break
    if header is None:
        result.warnings.append(f"{label}현황 표의 머리글을 찾지 못했어요")
        return
    k, cols = header
    missing = [h for h, f in columns.items() if f not in cols]
    if missing:
        result.warnings.append(f"{label}현황 표에 없는 열: {', '.join(missing)}")
    result.found.add(section)

    rest = body[k + 1 :]
    group = None
    for i, r in enumerate(rest):
        row_no = first_row + k + 1 + i
        if all(c is None or not str(c).strip() for c in r):
            continue
        if any(_norm(c) in TOTAL_WORDS for c in r):
            values = rest[i + 1] if i + 1 < len(rest) else []
            totals = {}
            for j, c in enumerate(r):
                name = _norm(c)
                if name and name not in TOTAL_WORDS:
                    value = _int(_cell(values, j))
                    if value is not None:
                        totals[name] = value
            result.totals[section] = totals
            break
        g = _text(_cell(r, cols.get("group")))
        if g is not None:
            group = g
        name = _text(_cell(r, cols["name"]))
        if name is None:
            result.warnings.append(f"{label}현황 {row_no}행: 이름이 비어 있어 건너뜁니다")
            continue
        result.holdings.append(
            Holding(
                section=section,
                group=group if "group" in cols else None,
                institution=_text(_cell(r, cols.get("institution"))),
                name=name,
                amount=_int(_cell(r, cols["amount"])),
                principal=_int(_cell(r, cols.get("principal"))),
                rate=_float(_cell(r, cols.get("rate"))),
                status=_text(_cell(r, cols.get("status"))),
                start_date=_date(_cell(r, cols.get("start_date"))),
                end_date=_date(_cell(r, cols.get("end_date"))),
                row_no=row_no,
            )
        )
    if section not in result.totals:
        result.warnings.append(f"{label}현황 표의 총계 줄을 찾지 못했어요")


# --- 검증 ----------------------------------------------------------------------


def _sum(items: list[Holding], attr: str) -> int:
    return sum(getattr(h, attr) or 0 for h in items)


def _cross_checks(t: StatusTables) -> None:
    checks = t.checks
    if t.total_assets is not None:
        checks.append(Check("총자산", t.total_assets, _sum(t.of("asset"), "amount")))
    if t.total_liabilities is not None:
        checks.append(Check("총부채", t.total_liabilities, _sum(t.of("liability"), "amount")))
    if None not in (t.total_assets, t.total_liabilities, t.net_worth):
        checks.append(Check("순자산", t.net_worth, t.total_assets - t.total_liabilities))

    for section, words in (
        ("insurance", (("납입", "amount"),)),
        ("investment", (("원금", "principal"), ("평가", "amount"))),
        ("loan", (("원금", "principal"), ("잔액", "amount"))),
    ):
        totals = t.totals.get(section)
        if not totals:
            continue
        items = t.of(section)
        label = SECTION_LABELS[section]
        for word, attr in words:
            key = next((k for k in totals if word in k), None)
            if key is not None:
                checks.append(Check(f"{label} {key}", totals[key], _sum(items, attr)))
        count_key = next((k for k in totals if "상품" in k), None)
        if count_key is not None:
            checks.append(Check(f"{label} {count_key}", totals[count_key], len(items)))
