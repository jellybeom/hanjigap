"""'뱅샐현황' 시트의 재무·보험·투자·대출 표 읽기 (J4b-1).

표 모양 (2026년 10월 실제 파일 기준)
    각 표: 'N.제목' 줄 → 설명 한 줄 → 머리글 → 항목 줄들 → '총계' 묶음
    재무현황: 왼쪽 자산(항목 · 상품명 · 금액), 오른쪽 부채(항목 · 상품명 · 금액)가 나란히.
              '항목'은 묶음의 첫 줄에만 있고 아래 줄은 비어 있다 (위 값을 이어받는다).
              끝: '총자산 … 금액 | 총부채 … 금액' 줄 → '순자산' 줄 → 순자산 금액 줄
    보험·투자·대출현황: 열 이름으로 찾는다 (순서가 바뀌어도 괜찮게).
              끝: '총계' 줄(이름: 보유 대출 상품, 총 대출 원금 …) → 바로 아래 줄(값)
    수익률·금리는 % 숫자 그대로다 (5.55 = 5.55%).
    '총계' 칸은 계산식(=SUM(…) 등)이고 파일에 저장된 계산 결과는 0이다. 엑셀은 열 때 다시
    계산하지만 openpyxl은 하지 않으므로, 계산식이 있으면 한지갑이 직접 계산한다.
    (SUM · COUNT · COUNTA, 칸·범위, + − × ÷, 괄호만. 모르는 계산식이면 그 검증만 건너뛴다)

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
from openpyxl.utils import get_column_letter

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
    formulas = _read_formulas(xlsx)
    sheet = _Sheet(rows, formulas, result.warnings)

    for title, body, first_row in _sections(rows):
        if "고객" in title:
            continue  # 개인정보는 읽지 않는다
        if title.endswith("재무현황"):
            _read_finance(body, first_row, result, sheet)
            continue
        for table_name, (section, columns) in _TABLES.items():
            if title.endswith(table_name):
                _read_table(section, columns, body, first_row, result, sheet)
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


# --- 계산식 --------------------------------------------------------------------


def _read_formulas(xlsx: bytes) -> list[list[Any]]:
    wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=False)
    try:
        return [list(r) for r in wb[STATUS_SHEET].iter_rows(values_only=True)]
    finally:
        wb.close()


class _Unsupported(Exception):
    pass


_TOKEN_RE = re.compile(
    r"\s*(?:(?P<range>\$?[A-Z]{1,3}\$?\d+:\$?[A-Z]{1,3}\$?\d+)"
    r"|(?P<ref>\$?[A-Z]{1,3}\$?\d+)"
    r"|(?P<func>[A-Z]+)\("
    r"|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<op>[-+*/(),]))"
)
_REF_RE = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")
MAX_DEPTH = 20


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


class _Sheet:
    """값과 계산식을 함께 들고 있다가, 계산식 칸은 직접 계산해서 돌려준다."""

    def __init__(self, values: list[list[Any]], formulas: list[list[Any]], warnings: list[str]):
        self.values = values
        self.formulas = formulas
        self.warnings = warnings

    def _at(self, grid: list[list[Any]], row_no: int, col: int) -> Any:
        if row_no < 1 or row_no > len(grid):
            return None
        row = grid[row_no - 1]
        return row[col] if 0 <= col < len(row) else None

    def num(self, row_no: int, col: int | None, depth: int = 0) -> int | None:
        if col is None:
            return None
        value = self._number(row_no, col, depth)
        return None if value is None else round(value)

    def _number(self, row_no: int, col: int, depth: int) -> float | None:
        f = self._at(self.formulas, row_no, col)
        if isinstance(f, str) and f.startswith("="):
            if depth > MAX_DEPTH:
                raise _Unsupported("계산식이 너무 깊어요")
            try:
                return _Eval(self, f[1:], depth + 1).run()
            except _Unsupported:
                if depth == 0:
                    ref = f"{get_column_letter(col + 1)}{row_no}"
                    self.warnings.append(
                        f"합계 칸 {ref}의 계산식({f})을 읽지 못해 그 검증은 건너뜁니다"
                    )
                    return None
                raise
        v = self._at(self.values, row_no, col)
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, int | float):
            return float(v)
        parsed = _int(v)
        return None if parsed is None else float(parsed)

    def filled(self, row_no: int, col: int) -> bool:
        f = self._at(self.formulas, row_no, col)
        if isinstance(f, str) and f.startswith("="):
            return True
        v = self._at(self.values, row_no, col)
        return v is not None and str(v).strip() != ""


class _Eval:
    """=SUM(E41:E91) · =COUNTA(D110:D117) · =E92-I92 같은 간단한 계산식만 계산한다."""

    def __init__(self, sheet: _Sheet, expr: str, depth: int):
        self.sheet = sheet
        self.depth = depth
        self.tokens = self._tokenize(expr.upper())
        self.pos = 0

    def _tokenize(self, expr: str) -> list[tuple[str, str]]:
        tokens, i = [], 0
        while i < len(expr):
            if expr[i].isspace():
                i += 1
                continue
            m = _TOKEN_RE.match(expr, i)
            if not m:
                raise _Unsupported(expr)
            kind = m.lastgroup
            tokens.append((kind, m.group(kind)))
            i = m.end()
        return tokens

    def run(self) -> float:
        value = self._expr()
        if self.pos != len(self.tokens):
            raise _Unsupported("남은 글자")
        return value

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise _Unsupported("끝")
        self.pos += 1
        return tok

    def _expect(self, op: str) -> None:
        if self._take() != ("op", op):
            raise _Unsupported(op)

    def _expr(self) -> float:
        value = self._term()
        while self._peek() in (("op", "+"), ("op", "-")):
            op = self._take()[1]
            rhs = self._term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def _term(self) -> float:
        value = self._factor()
        while self._peek() in (("op", "*"), ("op", "/")):
            op = self._take()[1]
            rhs = self._factor()
            if op == "/" and rhs == 0:
                raise _Unsupported("0으로 나눔")
            value = value * rhs if op == "*" else value / rhs
        return value

    def _factor(self) -> float:
        kind, text = self._take()
        if kind == "num":
            return float(text)
        if kind == "ref":
            return self._ref_value(text) or 0.0
        if kind == "op" and text == "-":
            return -self._factor()
        if kind == "op" and text == "(":
            value = self._expr()
            self._expect(")")
            return value
        if kind == "func":
            return self._func(text)
        raise _Unsupported(text)

    def _ref_value(self, text: str) -> float | None:
        m = _REF_RE.fullmatch(text)
        return self.sheet._number(int(m[2]), _col_index(m[1]), self.depth)

    def _cells(self, text: str):
        a, b = text.split(":")
        ma, mb = _REF_RE.fullmatch(a), _REF_RE.fullmatch(b)
        c1, c2 = sorted((_col_index(ma[1]), _col_index(mb[1])))
        r1, r2 = sorted((int(ma[2]), int(mb[2])))
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                yield r, c

    def _func(self, name: str) -> float:
        if name not in ("SUM", "COUNT", "COUNTA"):
            raise _Unsupported(name)
        total = 0.0
        while True:
            tok = self._peek()
            if tok is not None and tok[0] == "range":
                self._take()
                for r, c in self._cells(tok[1]):
                    v = self.sheet._number(r, c, self.depth)
                    if name == "SUM":
                        total += v or 0.0
                    elif name == "COUNT":
                        total += v is not None
                    else:
                        total += self.sheet.filled(r, c)
            else:
                v = self._expr()
                total += v if name == "SUM" else 1
            sep = self._take()
            if sep == ("op", ")"):
                return total
            if sep != ("op", ","):
                raise _Unsupported(sep[1])


# --- 재무현황 --------------------------------------------------------------------


def _read_finance(
    body: list[list[Any]], first_row: int, result: StatusTables, sheet: _Sheet
) -> None:
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
                value = sheet.num(row_no, a)
                if label in ("총자산", "총부채") and value is not None:
                    result.totals.setdefault(section, {})[label] = value
            # 그다음: '순자산' 이름 줄 → 금액 줄
            for offset, nxt in enumerate(rest[i + 1 : i + 4], start=1):
                if "순자산" in [_norm(c) for c in nxt]:
                    continue
                rn = row_no + offset
                width = max(len(nxt), 30)
                nums = (sheet.num(rn, c) for c in range(width) if sheet.filled(rn, c))
                value = next((v for v in nums if v is not None), None)
                if value is not None:
                    totals["순자산"] = value
                    break
            break
        for section, g, n, a in sides:
            group = _text(_cell(r, g))
            if group is not None and _int(group) is None:
                groups[section] = group
            name = _text(_cell(r, n))
            amount = sheet.num(row_no, a)
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
    sheet: _Sheet,
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
            totals = {}
            for j, c in enumerate(r):
                name = _norm(c)
                if name and name not in TOTAL_WORDS:
                    value = sheet.num(row_no + 1, j)  # 이름 바로 아래 줄이 값
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
                amount=sheet.num(row_no, cols["amount"]),
                principal=sheet.num(row_no, cols.get("principal")),
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
