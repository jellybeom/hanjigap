"""'뱅샐현황' 시트의 구조만 살펴보기 (J4a).

재무·투자·대출·보험 표를 읽는 코드를 만들기 전에, 실제 파일에 어떤 표가 어떤 모양으로
있는지 확인하는 용도다. 금액·상품명 같은 값은 보여 주지 않는다.

보여 주는 것
    표 제목 ('1.고객정보'처럼 숫자와 점으로 시작하는 줄), 위치, 행 수
    설명 줄: 제목 아래 한 칸짜리 안내 문구 (숫자는 #으로 가림)
    머리글 줄: 글자만 있는 줄(칸 2개 이상, 숫자 없음, 칸마다 20자 이하. YYYY-MM은 예외).
        첫 줄을 머리글로 보고, 바로 이어지는 글자 줄(왼쪽·오른쪽 소제목 등)도 보여 준다.
    열마다: 숫자 칸 수 · 글자 칸 수 · 날짜 칸 수 · 빈칸 수
    분류처럼 보이는 열(글자만, 서로 다른 값 20개 이하, 숫자 없음, 15자 이하)은 그 값 목록
    (머리글이 상품명·보험명처럼 이름 열이면 보여 주지 않는다)
보여 주지 않는 것
    '고객' 이 들어간 표(고객정보)는 행 수만 보여 주고 내용은 건너뛴다.
    숫자 값, 날짜 값, 분류처럼 보이지 않는 글자 값
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
_TITLE_RE = re.compile(r"^\s*\d+\s*\.\s*\S")
_YM_RE = re.compile(r"^\d{4}-\d{2}$")
_DIGIT_RE = re.compile(r"\d")
LABEL_MAX_DISTINCT = 20
TEXT_ROWS_MAX = 6
LABEL_MAX_LEN = 15
HEADER_MAX_LEN = 20
PRIVATE_TITLE_WORDS = ("고객",)
# 머리글에 이 글자가 있으면 이름 열로 보고 값 목록을 보여 주지 않는다 (상품명, 보험명, 종목명 등)
NAME_HEADER_WORDS = ("명", "이름", "상품", "종목")


@dataclass
class ColumnInfo:
    letter: str
    header: str
    numbers: int = 0
    texts: int = 0
    dates: int = 0
    blanks: int = 0
    labels: list[str] | None = None  # 분류처럼 보이는 열의 값 목록


@dataclass
class Section:
    title: str
    title_row: int
    last_row: int
    private: bool = False
    note: str | None = None  # 제목 아래 설명 줄 (숫자는 #)
    header_row: int | None = None
    header_cells: list[tuple[str, str]] = field(default_factory=list)  # (열, 글자)
    text_rows: list[tuple[int, list[tuple[str, str]]]] = field(default_factory=list)
    data_rows: int = 0
    columns: list[ColumnInfo] = field(default_factory=list)

    @property
    def span(self) -> str:
        if not self.columns:
            return f"{self.title_row}~{self.last_row}행"
        return f"{self.columns[0].letter}{self.title_row}:{self.columns[-1].letter}{self.last_row}"


@dataclass
class StatusStructure:
    found: bool
    sheet_names: list[str]
    sections: list[Section] = field(default_factory=list)


def describe_status_sheet(xlsx: bytes) -> StatusStructure:
    wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    try:
        names = list(wb.sheetnames)
        if STATUS_SHEET not in names:
            return StatusStructure(False, names)
        rows = [list(r) for r in wb[STATUS_SHEET].iter_rows(values_only=True)]
    finally:
        wb.close()

    titles = [i for i, r in enumerate(rows) if _title_of(r) is not None]
    sections: list[Section] = []
    for n, start in enumerate(titles):
        end = (titles[n + 1] if n + 1 < len(titles) else len(rows)) - 1
        while end > start and _is_blank(rows[end]):
            end -= 1
        title = _title_of(rows[start]) or ""
        sec = Section(title=title, title_row=start + 1, last_row=end + 1)
        if any(w in title for w in PRIVATE_TITLE_WORDS):
            sec.private = True
        else:
            _describe_body(sec, rows[start + 1 : end + 1], start + 1)
        sections.append(sec)
    return StatusStructure(True, names, sections)


def safe_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if _YM_RE.match(text):
        return text
    if _DIGIT_RE.search(text):
        return "〈숫자 포함〉"
    return text[:HEADER_MAX_LEN]


# --- 내부 ----------------------------------------------------------------------


def _title_of(row: list[Any]) -> str | None:
    cells = [c for c in row if c is not None and str(c).strip()]
    if not cells or not isinstance(cells[0], str):
        return None
    first = cells[0].strip()
    return first if _TITLE_RE.match(first) and len(cells) == 1 else None


def _is_blank(row: list[Any]) -> bool:
    return all(c is None or (isinstance(c, str) and not c.strip()) for c in row)


def _is_note(row: list[Any]) -> bool:
    cells = [c for c in row if c is not None and str(c).strip()]
    return len(cells) == 1 and isinstance(cells[0], str)


def _is_text_row(row: list[Any]) -> bool:
    """머리글처럼 보이는 줄: 글자 칸 2개 이상, 모두 짧고 숫자 없음 (YYYY-MM은 허용)"""
    cells = [c for c in row if c is not None and str(c).strip()]
    if len(cells) < 2:
        return False
    for c in cells:
        if not isinstance(c, str):
            return False
        text = c.strip()
        if len(text) > HEADER_MAX_LEN:
            return False
        if _DIGIT_RE.search(text) and not _YM_RE.match(text):
            return False
    return True


def _cells(row: list[Any]) -> list[tuple[str, str]]:
    return [
        (get_column_letter(j + 1), safe_header(v))
        for j, v in enumerate(row)
        if v is not None and str(v).strip()
    ]


def _describe_body(sec: Section, body: list[list[Any]], first_index: int) -> None:
    filled = [(i, r) for i, r in enumerate(body) if not _is_blank(r)]
    if not filled:
        return
    if _is_note(filled[0][1]) and len(filled) > 1:
        note_row = filled.pop(0)[1]
        text = next(str(c).strip() for c in note_row if c is not None and str(c).strip())
        sec.note = _DIGIT_RE.sub("#", text)

    header_pos = next((k for k, (_, r) in enumerate(filled) if _is_text_row(r)), 0)
    head_i, head = filled[header_pos]
    sec.header_row = first_index + head_i + 1
    sec.header_cells = _cells(head)
    rest = filled[header_pos + 1 :]
    # 머리글 바로 아래 이어지는 글자 줄(소제목)만 따로 보여 준다. 데이터가 시작된 뒤의 줄은
    # 글자뿐이어도 데이터로 본다 (상품명만 있는 줄이 화면에 나오지 않게).
    data = []
    for i, r in rest:
        if not data and _is_text_row(r) and len(sec.text_rows) < TEXT_ROWS_MAX:
            sec.text_rows.append((first_index + i + 1, _cells(r)))
        else:
            data.append(r)
    sec.data_rows = len(data)

    width = max(len(r) for _, r in filled)
    used = [j for j in range(width) if any(j < len(r) and r[j] is not None for _, r in filled)]
    for j in used:
        info = ColumnInfo(get_column_letter(j + 1), safe_header(head[j] if j < len(head) else None))
        texts: list[str] = []
        for r in data:
            v = r[j] if j < len(r) else None
            if v is None or (isinstance(v, str) and not v.strip()):
                info.blanks += 1
            elif isinstance(v, bool | int | float):
                info.numbers += 1
            elif isinstance(v, dt.date | dt.datetime | dt.time):
                info.dates += 1
            else:
                info.texts += 1
                texts.append(str(v).strip())
        distinct = sorted(set(texts), key=texts.index)
        if (
            texts
            and not any(w in info.header for w in NAME_HEADER_WORDS)
            and info.numbers == 0
            and len(distinct) <= LABEL_MAX_DISTINCT
            and all(len(t) <= LABEL_MAX_LEN and not _DIGIT_RE.search(t) for t in distinct)
        ):
            info.labels = distinct
        sec.columns.append(info)
