"""익명화 규칙 테스트. 실제 데이터 없이 코드로 만든 가짜 엑셀만 사용한다."""

import datetime as dt

import pytest
from openpyxl import Workbook, load_workbook

from hanjigap.tools.anonymize import (
    EXPECTED_HEADER,
    LEDGER_SHEET,
    STATUS_SHEET,
    AnonymizeError,
    anonymize_file,
)

D = dt.datetime
T = dt.time
SELF = ["홍길동", "길동"]
SPOUSE = ["성춘향", "춘향", "향이"]

ROWS = [
    # 날짜, 시간, 타입, 대분류, 소분류, 내용, 금액, 화폐, 결제수단, 메모
    [
        D(2026, 9, 1),
        T(10, 0),
        "수입",
        "급여",
        "미분류",
        "회사급여",
        3_500_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 2),
        T(9, 0),
        "이체",
        "투자",
        "미분류",
        "9월 당직비",
        100_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 3),
        T(12, 0),
        "이체",
        "저축",
        "미분류",
        "점심값 정산",
        12_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 4),
        T(0, 0),
        "지출",
        "자동차",
        "통행료",
        "한국도로공사",
        -900,
        "KRW",
        "후불 하이패스",
        None,
    ],
    [
        D(2026, 9, 4),
        T(0, 0),
        "지출",
        "자동차",
        "통행료",
        "한국도로공사",
        -900,
        "KRW",
        "후불 하이패스",
        None,
    ],
    [
        D(2026, 9, 5),
        T(0, 29),
        "지출",
        "온라인쇼핑",
        "인터넷쇼핑",
        "네이버페이",
        -92_700,
        "KRW",
        "회사복지카드",
        None,
    ],
    [
        D(2026, 9, 5),
        T(0, 30),
        "지출",
        "온라인쇼핑",
        "인터넷쇼핑",
        "네이버페이",
        92_700,
        "KRW",
        "회사복지카드",
        None,
    ],
    [
        D(2026, 9, 6),
        T(14, 0),
        "이체",
        "내계좌이체",
        "미분류",
        "홍길동",
        -50_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 7),
        T(14, 0),
        "이체",
        "내계좌이체",
        "미분류",
        "길동",
        -60_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 8),
        T(8, 0),
        "지출",
        "경조/선물",
        "용돈(개인)",
        "향이 용돈",
        -300_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 9),
        T(8, 0),
        "이체",
        "이체",
        "미분류",
        "성춘향",
        30_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 10),
        T(18, 0),
        "이체",
        "카드대금",
        "미분류",
        "현대카드",
        -1_797_890,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 9, 11),
        T(18, 0),
        "이체",
        "이체",
        "미분류",
        "김철수",
        -50_000,
        "KRW",
        "입출금통장",
        None,
    ],
    [
        D(2026, 10, 1),
        T(13, 0),
        "지출",
        "식비",
        "한식",
        "맛집식당",
        -15_000,
        "KRW",
        "네이버페이 간편결제",
        "친구랑",
    ],
]


def _make_workbook(path, rows=ROWS, customer="홍길동"):
    wb = Workbook()
    status = wb.active
    status.title = STATUS_SHEET
    status["B5"], status["B6"] = "이름", customer
    status["E5"], status["E6"] = "신용점수", 950
    ws = wb.create_sheet(LEDGER_SHEET)
    ws.append(EXPECTED_HEADER)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _run(tmp_path, rows=ROWS, **kw):
    src = _make_workbook(tmp_path / "real.xlsx", rows)
    dst = tmp_path / "out.anon.xlsx"
    params = {
        "self_names": SELF,
        "spouse_names": SPOUSE,
        "secret": b"fixed-test-secret",
    }
    params.update(kw)
    result = anonymize_file(src, dst, **params)
    ws = load_workbook(dst)[LEDGER_SHEET]
    out = [list(r) for r in ws.iter_rows(min_row=2, values_only=True)]
    return result, out, load_workbook(dst)


def col(name):
    return EXPECTED_HEADER.index(name)


def test_stats_preserved(tmp_path):
    result, out, _ = _run(tmp_path)
    assert result.stats_match
    assert result.after.duplicate_rows == 1
    assert result.after.refund_pairs == 1
    assert result.after.zero_time == 2
    assert len(out) == len(ROWS)


def test_dates_types_categories_unchanged(tmp_path):
    _, out, _ = _run(tmp_path)
    for before, after in zip(ROWS, out, strict=True):
        for name in ("날짜", "타입", "대분류", "소분류", "화폐"):
            assert before[col(name)] == after[col(name)]
        assert after[col("시간")] == before[col("시간")]


def test_no_names_or_originals_left(tmp_path):
    _, out, wb = _run(tmp_path)
    text = " ".join(str(v) for row in out for v in row if v is not None)
    for word in [*SELF, *SPOUSE, "회사복지카드", "맛집식당", "김철수", "친구랑"]:
        assert word not in text
    status_text = " ".join(
        str(c.value) for row in wb[STATUS_SHEET].iter_rows() for c in row if c.value
    )
    assert "950" not in status_text and "홍길동" not in status_text


def test_roles_and_keywords(tmp_path):
    _, out, _ = _run(tmp_path)
    contents = [r[col("내용")] for r in out]
    assert contents[1].startswith("당직비 #")
    assert contents[2].startswith("점심 #")
    assert contents[7] == "본인"
    assert contents[8].startswith("본인") and contents[8] != contents[7]  # 일대일 유지
    assert contents[9].startswith("배우자 용돈 #")
    assert contents[10] == "배우자"
    assert contents[11].startswith("현대카드 #")
    assert contents[12].startswith("상대방 ")
    assert contents[13].startswith("가맹점 ")


def test_same_value_same_pseudonym(tmp_path):
    _, out, _ = _run(tmp_path)
    assert out[3] == out[4]  # 같은 행 유지
    assert out[5][col("내용")] == out[6][col("내용")]
    assert out[5][col("금액")] == -out[6][col("금액")]  # 취소 짝 유지


def test_amounts_sign_kept_and_masked(tmp_path):
    _, out, _ = _run(tmp_path)
    for before, after in zip(ROWS, out, strict=True):
        b, a = before[col("금액")], after[col("금액")]
        assert (b > 0) == (a > 0)
        assert a % 10 == 0
        assert 0.6 * abs(b) <= abs(a) <= 1.4 * abs(b) + 10
    assert sum(1 for b, a in zip(ROWS, out, strict=True) if b[col("금액")] != a[col("금액")]) >= 10


def test_instruments(tmp_path):
    result, out, _ = _run(tmp_path)
    m = result.instrument_map
    assert m["입출금통장"] == "입출금통장"
    assert m["네이버페이 간편결제"] == "네이버페이 간편결제"
    assert m["후불 하이패스"] == "하이패스카드 A"
    assert m["회사복지카드"] == "카드 A"


def test_memo_masked(tmp_path):
    _, out, _ = _run(tmp_path)
    assert out[13][col("메모")] == "메모"
    assert out[0][col("메모")] is None


def test_secret_changes_output(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, out1, _ = _run(tmp_path / "a", secret=b"one")
    _, out2, _ = _run(tmp_path / "b", secret=b"two")
    assert [r[col("금액")] for r in out1] != [r[col("금액")] for r in out2]


def test_forbidden_word_stops(tmp_path):
    rows = [list(r) for r in ROWS]
    rows[0][col("소분류")] = "비밀단어"  # 그대로 두는 컬럼에 금지어가 있는 경우
    with pytest.raises(AnonymizeError, match="안전 검사 실패"):
        _run(tmp_path, rows=rows, forbid=["비밀단어"])
    assert not (tmp_path / "out.anon.xlsx").exists()


def test_refuses_overwrite(tmp_path):
    _run(tmp_path)
    with pytest.raises(AnonymizeError, match="이미 있습니다"):
        _run(tmp_path)


def test_wrong_header(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = LEDGER_SHEET
    ws.append(["날짜", "금액"])
    wb.save(tmp_path / "bad.xlsx")
    with pytest.raises(AnonymizeError, match="헤더"):
        anonymize_file(
            tmp_path / "bad.xlsx",
            tmp_path / "o.xlsx",
            self_names=SELF,
            spouse_names=SPOUSE,
        )
