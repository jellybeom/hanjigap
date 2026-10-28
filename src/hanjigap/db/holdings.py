"""계좌 · 카드 · 뱅샐 결제수단 연결.

J2a: 결제수단 이름별 사용 현황 (연결 단위가 실제 데이터에 맞는지 확인용)
J2b에서 계좌·카드 등록과 연결 함수가 이 모듈에 추가된다.

결제수단 이름은 실제 카드·통장 이름이라 화면(터미널)에만 보여 주고 로그에는 건수만 남긴다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from hanjigap.db.members import Member

TARGET_LABELS = {"account": "계좌", "card": "카드", "easy_pay": "간편결제"}


@dataclass(frozen=True)
class InstrumentUsage:
    instrument: str  # 뱅샐 '결제수단' 값 그대로 ('' 가능)
    count: int
    first_date: str
    last_date: str
    expense: int  # 타입별 건수
    income: int
    transfer: int
    target: str | None  # None = 연결 안 됨
    target_name: str | None  # 연결된 계좌·카드 이름

    @property
    def linked(self) -> bool:
        return self.target is not None

    @property
    def link_label(self) -> str:
        if self.target is None:
            return "-"
        label = TARGET_LABELS[self.target]
        return f"{label} · {self.target_name}" if self.target_name else label


def instrument_usage(conn: sqlite3.Connection, member: Member) -> list[InstrumentUsage]:
    """그 사람 거래에 나온 결제수단 이름별 건수·기간·타입별 건수·연결 상태. 건수 많은 순."""
    rows = conn.execute(
        """
        SELECT t.instrument                       AS instrument,
               count(*)                           AS n,
               min(t.tx_date)                     AS first_date,
               max(t.tx_date)                     AS last_date,
               sum(t.tx_type = '지출')            AS expense,
               sum(t.tx_type = '수입')            AS income,
               sum(t.tx_type = '이체')            AS transfer,
               l.target                           AS target,
               coalesce(a.name, c.name)           AS target_name
        FROM transactions t
        LEFT JOIN instrument_links l
               ON l.member_id = t.member_id AND l.instrument = t.instrument
        LEFT JOIN accounts a ON a.id = l.account_id
        LEFT JOIN cards c    ON c.id = l.card_id
        WHERE t.member_id = ?
        GROUP BY t.instrument
        ORDER BY n DESC, t.instrument
        """,
        (member.id,),
    ).fetchall()
    return [
        InstrumentUsage(
            instrument=r["instrument"],
            count=r["n"],
            first_date=r["first_date"],
            last_date=r["last_date"],
            expense=r["expense"],
            income=r["income"],
            transfer=r["transfer"],
            target=r["target"],
            target_name=r["target_name"],
        )
        for r in rows
    ]
