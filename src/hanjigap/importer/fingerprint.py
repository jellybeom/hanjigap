"""거래 지문.

매월 1년치 파일을 다시 올려도 같은 거래를 같은 거래로 알아보기 위한 키를 만든다.

    ① 기본 조건: 날짜 | 시간 | 금액 | 결제수단   (내용은 넣지 않는다)
    ② 기본 조건이 같은 거래끼리 묶는다
    ③ 묶음 안에서는 내용 → 타입 → 대분류 → 소분류 → 메모 → 엑셀 행 순서로 정렬해 순번을 붙인다
    ④ 지문 = SHA-256(기본 조건 | 순번)의 앞 32자리

- 대부분의 거래는 묶음이 없어서 내용이 지문에 영향을 주지 않는다.
  뱅샐에서 내용·카테고리를 고쳐도 지문이 유지된다.
- 같은 초에 같은 금액으로 나가는 자동이체처럼 묶음이 생기는 경우, 내용 기준으로 순번을 정하므로
  뱅샐이 내보내는 순서가 바뀌어도 지문이 서로 뒤바뀌지 않는다.
- 엑셀 행 순서는 완전히 똑같은 행(하이패스 등)을 구분할 때만 쓰인다.
- 소유자는 지문에 넣지 않는다. DB에서 (소유자, 지문)을 고유하게 관리한다.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from hanjigap.importer.normalize import LedgerRow

KEY_LENGTH = 32


@dataclass(frozen=True, slots=True)
class KeyedRow:
    row: LedgerRow
    seq: int  # 기본 조건이 같은 거래 안에서의 순번 (1부터)
    base_key: str  # 예: "2026-09-20|14:10:17|-100000|입출금통장"
    tx_key: str  # 32자리 지문


@dataclass(frozen=True)
class KeyStats:
    total: int
    unique: int
    groups: int  # 기본 조건이 같은 거래가 2건 이상인 묶음 수
    largest_group: int

    @property
    def all_unique(self) -> bool:
        return self.total == self.unique


def base_key(row: LedgerRow) -> str:
    return "|".join(
        (
            row.date.isoformat(),
            row.time.isoformat(timespec="seconds"),
            str(row.amount),
            row.instrument,
        )
    )


def make_tx_key(base: str, seq: int) -> str:
    return hashlib.sha256(f"{base}|{seq}".encode()).hexdigest()[:KEY_LENGTH]


def _tie_order(row: LedgerRow) -> tuple:
    return (
        row.content,
        row.tx_type,
        row.category,
        row.subcategory,
        row.memo or "",
        row.row_no,
    )


def assign_keys(rows: Sequence[LedgerRow]) -> list[KeyedRow]:
    """지문을 붙인다. 결과는 입력과 같은 순서."""
    groups: dict[str, list[LedgerRow]] = defaultdict(list)
    for row in rows:
        groups[base_key(row)].append(row)

    seq_of: dict[int, tuple[str, int]] = {}  # id(row) → (base, seq)
    for base, members in groups.items():
        for seq, row in enumerate(sorted(members, key=_tie_order), start=1):
            seq_of[id(row)] = (base, seq)

    keyed = []
    for row in rows:
        base, seq = seq_of[id(row)]
        keyed.append(KeyedRow(row=row, seq=seq, base_key=base, tx_key=make_tx_key(base, seq)))

    if len({k.tx_key for k in keyed}) != len(keyed):  # 사실상 일어나지 않음
        raise RuntimeError("지문이 겹칩니다")
    return keyed


def key_stats(keyed: Sequence[KeyedRow]) -> KeyStats:
    sizes: dict[str, int] = defaultdict(int)
    for k in keyed:
        sizes[k.base_key] += 1
    multi = [n for n in sizes.values() if n > 1]
    return KeyStats(
        total=len(keyed),
        unique=len({k.tx_key for k in keyed}),
        groups=len(multi),
        largest_group=max(multi, default=1),
    )
