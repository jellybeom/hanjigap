"""뱅크샐러드 내보내기 파일 익명화.

실제 파일에서 구조와 데이터 특징(기간, 00:00 거래, 같은 행, 취소 짝, 카테고리 어휘)은 유지하고
사람을 알아볼 수 있는 정보(내용, 금액, 결제수단, 메모, 고객정보)만 바꾼 테스트용 파일을 만든다.

- 같은 원래 값은 항상 같은 가명으로, 다른 원래 값은 항상 다른 가명으로 바꾼다(일대일).
  그래서 같은 행 쌍과 취소 짝이 그대로 유지된다.
- 가명 변환용 비밀값은 실행할 때마다 새로 만들고 저장하지 않는다.
- 결과에 금지어나 원래 문자열이 남아 있으면 저장하지 않고 중단한다.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

LEDGER_SHEET = "가계부 내역"
STATUS_SHEET = "뱅샐현황"
EXPECTED_HEADER = [
    "날짜",
    "시간",
    "타입",
    "대분류",
    "소분류",
    "내용",
    "금액",
    "화폐",
    "결제수단",
    "메모",
]

SELF_TOKEN = "본인"
SPOUSE_TOKEN = "배우자"

# 분류 규칙 테스트에 필요해서 남겨둘 키워드 (목록은 추후 확정)
KEYWORDS: tuple[str, ...] = (
    "급여",
    "수당",
    "당직비",
    "야식비",
    "세탁비",
    "점심",
    "저녁",
    "이자",
    "환불",
    "용돈",
    "청약",
    "연금",
    "현대카드",
    "KB카드",
    "국민카드",
    "롯데카드",
    "신한카드",
    "삼성카드",
    "하나카드",
    "우리카드",
    "BC카드",
    "NH카드",
)

# 누구나 쓰는 일반 이름이라 그대로 두는 결제수단
SAFE_INSTRUMENTS: frozenset[str] = frozenset({"입출금통장"})
SAFE_INSTRUMENT_MARK = "간편결제"
HIPASS_MARK = "하이패스"

AMOUNT_MIN_FACTOR = 0.7
AMOUNT_MAX_FACTOR = 1.3
AMOUNT_UNIT = 10


class AnonymizeError(Exception):
    """익명화를 진행할 수 없거나 결과가 안전하지 않을 때."""


@dataclass
class Stats:
    rows: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    positive: int = 0
    negative: int = 0
    zero_time: int = 0
    duplicate_rows: int = 0
    refund_pairs: int = 0
    months: int = 0

    def as_rows(self) -> list[tuple[str, Any]]:
        return [
            ("행 수", self.rows),
            ("타입별 건수", dict(sorted(self.by_type.items()))),
            ("양수 / 음수", f"{self.positive} / {self.negative}"),
            ("시간 00:00", self.zero_time),
            ("같은 행(중복분)", self.duplicate_rows),
            ("취소 짝 후보", self.refund_pairs),
            ("월 수", self.months),
        ]


@dataclass
class Result:
    before: Stats
    after: Stats
    content_map_size: int
    instrument_map: dict[str, str]

    @property
    def stats_match(self) -> bool:
        return self.before == self.after


# ---------------------------------------------------------------------------
# 가명 생성기
# ---------------------------------------------------------------------------


class _Pseudonymizer:
    def __init__(
        self,
        secret: bytes,
        self_names: list[str],
        spouse_names: list[str],
        keywords: tuple[str, ...] = KEYWORDS,
    ) -> None:
        self._secret = secret
        # 긴 이름부터 치환해야 "윤상범"이 "상범"보다 먼저 처리된다
        names = [(n, SELF_TOKEN) for n in self_names] + [(n, SPOUSE_TOKEN) for n in spouse_names]
        self._names = sorted({(n, t) for n, t in names if n}, key=lambda x: -len(x[0]))
        self._keywords = sorted(keywords, key=len, reverse=True)

        self.content_map: dict[str, str] = {}
        self._used_contents: set[str] = set()
        self._keyword_seq: Counter[str] = Counter()
        self._generic_seq: Counter[str] = Counter()

        self.instrument_map: dict[str, str] = {}
        self._card_seq = 0
        self._hipass_seq = 0

        self.amount_map: dict[int, int] = {}
        self._used_amounts: set[int] = set()

    # --- 내용 -------------------------------------------------------------
    def content(self, value: str | None, tx_type: str | None) -> str | None:
        if value is None or str(value).strip() == "":
            return value
        original = str(value)
        if original in self.content_map:
            return self.content_map[original]

        text = original
        roles: list[str] = []
        for name, token in self._names:
            if name in text:
                text = text.replace(name, " ")
                if token not in roles:
                    roles.append(token)

        keyword = next((k for k in self._keywords if k in text), None)
        remainder = "".join(ch for ch in text if ch.isalnum())

        parts = list(roles)
        if keyword:
            self._keyword_seq[keyword] += 1
            parts.append(f"{keyword} #{self._keyword_seq[keyword]:02d}")
        elif remainder or not roles:
            label = "상대방" if tx_type == "이체" else "가맹점"
            self._generic_seq[label] += 1
            parts.append(f"{label} {self._generic_seq[label]:04d}")

        candidate = " ".join(parts)
        result, k = candidate, 2
        while result in self._used_contents:  # 일대일 보장
            result = f"{candidate} #{k}"
            k += 1
        self._used_contents.add(result)
        self.content_map[original] = result
        return result

    # --- 결제수단 ---------------------------------------------------------
    def instrument(self, value: str | None) -> str | None:
        if value is None or str(value).strip() == "":
            return value
        original = str(value)
        if original in self.instrument_map:
            return self.instrument_map[original]
        if original in SAFE_INSTRUMENTS or SAFE_INSTRUMENT_MARK in original:
            result = original
        elif HIPASS_MARK in original:
            result = f"하이패스카드 {_letters(self._hipass_seq)}"
            self._hipass_seq += 1
        else:
            result = f"카드 {_letters(self._card_seq)}"
            self._card_seq += 1
        self.instrument_map[original] = result
        return result

    # --- 금액 -------------------------------------------------------------
    def prepare_amounts(self, amounts: list[int]) -> None:
        """서로 다른 금액이 같은 값이 되지 않도록 작은 금액부터 순서대로 배정한다."""
        for value in sorted({abs(a) for a in amounts}):
            if value == 0:
                self.amount_map[0] = 0
                continue
            digest = hmac.new(self._secret, str(value).encode(), hashlib.sha256).digest()
            ratio = int.from_bytes(digest[:8], "big") / 2**64
            factor = AMOUNT_MIN_FACTOR + (AMOUNT_MAX_FACTOR - AMOUNT_MIN_FACTOR) * ratio
            new = max(AMOUNT_UNIT, round(value * factor / AMOUNT_UNIT) * AMOUNT_UNIT)
            while new in self._used_amounts:
                new += AMOUNT_UNIT
            self._used_amounts.add(new)
            self.amount_map[value] = new

    def amount(self, value: int) -> int:
        mapped = self.amount_map[abs(value)]
        return -mapped if value < 0 else mapped


def _letters(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA"""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


# ---------------------------------------------------------------------------
# 읽기 · 통계 · 검사
# ---------------------------------------------------------------------------


def _read_ledger(path: Path) -> tuple[list[list[Any]], list[str], str | None]:
    """가계부 행, 각 열의 표시 형식, 고객정보 이름을 돌려준다."""
    try:
        wb = load_workbook(path)
    except Exception as exc:  # 암호 걸린 파일이면 여기서 실패
        raise AnonymizeError(
            f"엑셀 파일을 열 수 없습니다 (비밀번호가 걸려 있으면 해제 후 사용): {exc}"
        ) from exc

    if LEDGER_SHEET not in wb.sheetnames:
        raise AnonymizeError(f"'{LEDGER_SHEET}' 시트가 없습니다. 시트 목록: {wb.sheetnames}")
    ws = wb[LEDGER_SHEET]

    header = [c.value for c in ws[1]][: len(EXPECTED_HEADER)]
    if header != EXPECTED_HEADER:
        raise AnonymizeError(f"헤더가 예상과 다릅니다: {header}")

    rows: list[list[Any]] = []
    formats = ["General"] * len(EXPECTED_HEADER)
    for i, row in enumerate(ws.iter_rows(min_row=2, max_col=len(EXPECTED_HEADER))):
        values = [c.value for c in row]
        if all(v is None for v in values):
            continue
        if i == 0:
            formats = [c.number_format for c in row]
        rows.append(values)

    return rows, formats, _customer_name(wb)


def _customer_name(wb: Any) -> str | None:
    if STATUS_SHEET not in wb.sheetnames:
        return None
    ws = wb[STATUS_SHEET]
    for row in ws.iter_rows(max_row=30):
        for cell in row:
            if cell.value == "이름":
                below = ws.cell(row=cell.row + 1, column=cell.column).value
                return str(below) if below else None
    return None


def compute_stats(rows: list[list[Any]]) -> Stats:
    idx = {name: i for i, name in enumerate(EXPECTED_HEADER)}
    s = Stats(rows=len(rows))
    s.by_type = dict(Counter(r[idx["타입"]] for r in rows))
    amounts = [r[idx["금액"]] or 0 for r in rows]
    s.positive = sum(1 for a in amounts if a > 0)
    s.negative = sum(1 for a in amounts if a < 0)
    s.zero_time = sum(1 for r in rows if str(r[idx["시간"]]) in ("00:00:00", "00:00"))

    keys = Counter(tuple(r[: idx["메모"]]) for r in rows)
    s.duplicate_rows = sum(n - 1 for n in keys.values() if n > 1)

    signs: dict[tuple, set[int]] = {}
    for r in rows:
        if r[idx["타입"]] != "지출" or not r[idx["금액"]]:
            continue
        key = (r[idx["날짜"]], r[idx["내용"]], abs(r[idx["금액"]]))
        signs.setdefault(key, set()).add(1 if r[idx["금액"]] > 0 else -1)
    s.refund_pairs = sum(1 for v in signs.values() if len(v) == 2)

    s.months = len({(r[idx["날짜"]].year, r[idx["날짜"]].month) for r in rows if r[idx["날짜"]]})
    return s


def _safety_check(
    out_rows: list[list[Any]],
    forbidden: list[str],
    original_contents: set[str],
) -> None:
    idx = {name: i for i, name in enumerate(EXPECTED_HEADER)}
    problems: list[str] = []
    for n, row in enumerate(out_rows, start=2):
        for col, value in zip(EXPECTED_HEADER, row, strict=True):
            if not isinstance(value, str):
                continue
            for word in forbidden:
                if word and word in value:
                    problems.append(f"{n}행 {col}: 금지어 포함")
        content = row[idx["내용"]]
        if isinstance(content, str) and content in original_contents:
            problems.append(f"{n}행 내용: 원래 문자열이 그대로 남음")
    if problems:
        preview = "\n  ".join(problems[:10])
        raise AnonymizeError(f"안전 검사 실패 ({len(problems)}건):\n  {preview}")


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


def anonymize_file(
    src: Path,
    dst: Path,
    *,
    self_names: list[str],
    spouse_names: list[str],
    forbid: list[str] | None = None,
    secret: bytes | None = None,
    overwrite: bool = False,
) -> Result:
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise AnonymizeError("입력 파일과 출력 파일이 같습니다.")
    if dst.exists() and not overwrite:
        raise AnonymizeError(f"출력 파일이 이미 있습니다: {dst} (--force로 덮어쓰기)")

    rows, formats, customer = _read_ledger(src)
    if not rows:
        raise AnonymizeError("가계부 내역에 거래가 없습니다.")

    idx = {name: i for i, name in enumerate(EXPECTED_HEADER)}
    pseudo = _Pseudonymizer(secret or secrets.token_bytes(32), self_names, spouse_names)
    pseudo.prepare_amounts([r[idx["금액"]] or 0 for r in rows])

    out_rows: list[list[Any]] = []
    for r in rows:
        new = list(r)
        new[idx["내용"]] = pseudo.content(r[idx["내용"]], r[idx["타입"]])
        new[idx["금액"]] = pseudo.amount(r[idx["금액"]] or 0)
        new[idx["결제수단"]] = pseudo.instrument(r[idx["결제수단"]])
        if r[idx["메모"]] not in (None, ""):
            new[idx["메모"]] = "메모"
        out_rows.append(new)

    forbidden = [*self_names, *spouse_names, *(forbid or [])]
    if customer:
        forbidden.append(customer)
    original_contents = {str(r[idx["내용"]]) for r in rows if r[idx["내용"]]}
    _safety_check(out_rows, forbidden, original_contents)

    result = Result(
        before=compute_stats(rows),
        after=compute_stats(out_rows),
        content_map_size=len(pseudo.content_map),
        instrument_map=dict(pseudo.instrument_map),
    )
    if not result.stats_match:
        raise AnonymizeError("원본과 결과의 데이터 특징이 다릅니다. 파일을 저장하지 않았습니다.")

    _write(dst, out_rows, formats)
    return result


def _write(dst: Path, rows: list[list[Any]], formats: list[str]) -> None:
    wb = Workbook()
    status = wb.active
    status.title = STATUS_SHEET
    status["B3"] = "익명화로 내용 제거됨"

    ws = wb.create_sheet(LEDGER_SHEET)
    ws.append(EXPECTED_HEADER)
    for row in rows:
        ws.append(row)
    for col, fmt in enumerate(formats, start=1):
        if fmt == "General":
            continue
        for (cell,) in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            cell.number_format = fmt

    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
