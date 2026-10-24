import dataclasses
import datetime as dt
import random
from collections import Counter

import pytest

from hanjigap.importer.fingerprint import assign_keys, base_key, key_stats, make_tx_key
from hanjigap.importer.normalize import LedgerRow, normalize
from hanjigap.importer.sheet import find_ledger


@pytest.fixture(scope="module")
def sample_rows(sample_bytes) -> list[LedgerRow]:
    return normalize(find_ledger(sample_bytes)).rows


def make(row_no=2, content="가맹점 0001", **over) -> LedgerRow:
    base = dict(
        row_no=row_no,
        date=dt.date(2026, 9, 20),
        time=dt.time(14, 10, 17),
        tx_type="이체",
        category="저축",
        subcategory="미분류",
        content=content,
        amount=-100_000,
        currency="KRW",
        instrument="입출금통장",
        memo=None,
    )
    base.update(over)
    return LedgerRow(**base)


def reexport(rows: list[LedgerRow], seed: int) -> list[LedgerRow]:
    """뱅샐이 순서를 바꿔 다시 내보낸 것처럼: 행 순서를 섞고 행 번호를 새로 매긴다."""
    shuffled = rows[:]
    random.Random(seed).shuffle(shuffled)
    return [dataclasses.replace(r, row_no=i) for i, r in enumerate(shuffled, start=2)]


def identity(r: LedgerRow) -> tuple:
    """행 번호를 뺀 거래 자체."""
    return dataclasses.astuple(dataclasses.replace(r, row_no=0))


# --- 기본 -------------------------------------------------------------------


def test_sample_all_unique(sample_rows):
    stats = key_stats(assign_keys(sample_rows))
    assert stats.total == stats.unique == 1579
    assert stats.groups == 19 and stats.largest_group == 2


def test_key_format():
    k = assign_keys([make()])[0]
    assert k.base_key == "2026-09-20|14:10:17|-100000|입출금통장"
    assert k.seq == 1
    assert len(k.tx_key) == 32 and int(k.tx_key, 16) >= 0
    assert k.tx_key == make_tx_key(k.base_key, 1)


def test_deterministic():
    assert assign_keys([make()])[0].tx_key == assign_keys([make()])[0].tx_key


@pytest.mark.parametrize(
    "change",
    [
        {"amount": -100_001},
        {"time": dt.time(14, 10, 18)},
        {"date": dt.date(2026, 9, 21)},
        {"instrument": "카드 A"},
    ],
)
def test_base_fields_change_key(change):
    assert assign_keys([make()])[0].tx_key != assign_keys([make(**change)])[0].tx_key


def test_output_keeps_input_order(sample_rows):
    keyed = assign_keys(sample_rows)
    assert [k.row for k in keyed] == sample_rows


# --- 내용을 고쳐도 지문 유지 ------------------------------------------------------


def test_editing_content_keeps_key_when_not_grouped():
    before = assign_keys([make(content="부모님 용돈", category="경조/선물")])[0]
    after = assign_keys([make(content="명절 용돈", category="이체", subcategory="기타")])[0]
    assert before.tx_key == after.tx_key


def test_editing_content_in_sample_keeps_keys(sample_rows):
    keyed = assign_keys(sample_rows)
    grouped = {k.base_key for k in keyed if k.seq > 1}
    edited = [
        r if base_key(r) in grouped else dataclasses.replace(r, content="수정됨", category="기타")
        for r in sample_rows
    ]
    assert [k.tx_key for k in assign_keys(edited)] == [k.tx_key for k in keyed]


# --- 순서가 바뀌어도 거래가 섞이지 않음 (이번 단계의 핵심) ------------------------------


def test_same_second_transfers_do_not_swap():
    a = make(row_no=10, content="상범 청약")
    b = make(row_no=11, content="잴리 청약")
    first = {k.row.content: k.tx_key for k in assign_keys([a, b])}
    # 다음 달 내보내기에서 순서가 뒤바뀐 경우
    b2, a2 = dataclasses.replace(b, row_no=10), dataclasses.replace(a, row_no=11)
    second = {k.row.content: k.tx_key for k in assign_keys([b2, a2])}
    assert first == second
    assert first["상범 청약"] != first["잴리 청약"]


@pytest.mark.parametrize("seed", range(5))
def test_sample_reexport_in_any_order(sample_rows, seed):
    original = {k.tx_key: identity(k.row) for k in assign_keys(sample_rows)}
    shuffled = {k.tx_key: identity(k.row) for k in assign_keys(reexport(sample_rows, seed))}
    assert shuffled == original  # 모든 지문이 같은 거래를 가리킴


def test_identical_rows_get_seq_1_and_2(sample_rows):
    keyed = assign_keys(sample_rows)
    by_identity = Counter(identity(k.row) for k in keyed)
    twins = [k for k in keyed if by_identity[identity(k.row)] == 2]
    assert len(twins) == 6
    assert sorted(k.seq for k in twins) == [1, 1, 1, 2, 2, 2]


def test_group_order_uses_content_then_other_fields():
    rows = [
        make(row_no=2, content="나", tx_type="이체"),
        make(row_no=3, content="가", tx_type="지출"),
        make(row_no=4, content="가", tx_type="이체"),
    ]
    seqs = {(k.row.content, k.row.tx_type): k.seq for k in assign_keys(rows)}
    assert seqs == {("가", "이체"): 1, ("가", "지출"): 2, ("나", "이체"): 3}


def test_owner_not_in_key():
    # 같은 조건이면 누구 파일이든 같은 지문. 소유자 구분은 DB의 (소유자, 지문)에서 한다.
    assert assign_keys([make()])[0].tx_key == assign_keys([make(row_no=99)])[0].tx_key


def test_inspect_shows_fingerprint_line(tmp_path, sample_bytes, capsys):
    from hanjigap import cli

    path = tmp_path / "s.xlsx"
    path.write_bytes(sample_bytes)
    assert cli.main(["inspect", str(path)]) == 0
    assert "지문        1,579건 모두 고유 (같은 조건 묶음 19개는 순번으로 구분)" in (
        capsys.readouterr().out
    )
