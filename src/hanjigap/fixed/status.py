"""그달 고정비 상태: 거래와 맞춰 보기.

상태는 저장하지 않고 볼 때마다 계산한다. (다시 정산해도 어긋나지 않게)

상태
    paid       빠져나감        조건에 맞는 거래를 찾았고 금액도 맞음
    different  금액 다름       찾았는데 금액이 다름 (변동 항목은 기준 금액 ±30%를 넘을 때만)
    missed     안 빠져나감     정산된 달인데 날짜 범위가 지나도록 못 찾음
    pending    정산 전         날짜는 지났지만 그달(또는 범위가 걸친 달) 파일을 아직 안 올림
    upcoming   예정            아직 날짜 범위가 끝나지 않음

거래 찾는 조건 (모두 만족)
    1. 담당자의 거래이고 돈이 나간 거래 (금액 < 0)
    2. 날짜: 예정일 ± match_days. 예정일이 그달에 없으면 말일 (9월 31일 → 9월 30일)
    3. 출금 수단: 고정비에 계좌·카드가 있고 그 계좌·카드에 연결된 뱅샐 결제수단 이름이 있으면
       그 이름의 거래만. 연결이 아직 없으면 결제수단은 따지지 않는다.
    4. 내용: '거래 찾는 글자'가 있으면 내용(웹에서 고친 내용 우선)에 그 글자가 들어 있어야 한다.
       글자가 없으면 금액이 맞아야 한다 (고정: 같은 금액, 변동: ±30%).

짝짓기
    거래 하나는 고정비 하나에만 쓴다. 날짜가 예정일에 가까운 쌍부터, 같으면 금액 차이가 작은
    쌍부터 정한다.
"""

from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from hanjigap.db import validate
from hanjigap.db.fixed_items import FixedItem, list_fixed_items
from hanjigap.db.members import Member

VARIABLE_TOLERANCE = 0.30
STATUS_LABELS = {
    "paid": "빠져나감",
    "different": "금액 다름",
    "missed": "안 빠져나감",
    "pending": "정산 전",
    "upcoming": "예정",
}


@dataclass(frozen=True)
class MatchedTx:
    tx_key: str
    date: str
    time: str
    amount: int  # 나간 돈이라 음수
    content: str
    instrument: str

    @property
    def paid(self) -> int:
        return -self.amount


@dataclass(frozen=True)
class FixedStatus:
    item: FixedItem
    expected_date: str
    status: str
    tx: MatchedTx | None

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def amount_gap(self) -> int | None:
        """실제 − 기준. 거래가 없으면 None"""
        return None if self.tx is None else self.tx.paid - self.item.amount


@dataclass
class MonthFixedReport:
    year_month: str
    statuses: list[FixedStatus] = field(default_factory=list)

    @property
    def counts(self) -> Counter:
        return Counter(s.status for s in self.statuses)

    def total(self, group: str | None = None) -> int:
        """기준 금액 합계. group = 'living' | 'saving' | None(전체)"""
        return sum(s.item.amount for s in self.statuses if group in (None, s.item.group))


def month_status(
    conn: sqlite3.Connection,
    year_month: str,
    *,
    member: Member | None = None,
    today: dt.date,
) -> MonthFixedReport:
    ym = validate.year_month(year_month, "월")
    items = list_fixed_items(conn, member, year_month=ym)
    year, month = int(ym[:4]), int(ym[5:7])
    expected = {it.id: _expected_date(year, month, it.pay_day) for it in items}

    # 후보 쌍 (거리, 금액 차이, 고정비, 거래) 모으기
    pairs: list[tuple[int, int, int, FixedItem, MatchedTx]] = []
    for it in items:
        day = expected[it.id]
        lo = day - dt.timedelta(days=it.match_days)
        hi = day + dt.timedelta(days=it.match_days)
        instruments = _linked_instruments(conn, it)
        for tx in _outflows(conn, it.member_id, lo, hi):
            if instruments and tx.instrument not in instruments:
                continue
            if it.match_text:
                if it.match_text.lower() not in tx.content.lower():
                    continue
            elif not _amount_ok(it, tx.paid):
                continue
            distance = abs((dt.date.fromisoformat(tx.date) - day).days)
            pairs.append((distance, abs(tx.paid - it.amount), it.id, it, tx))

    pairs.sort(key=lambda p: (p[0], p[1], p[2], p[4].date, p[4].time))
    chosen: dict[int, MatchedTx] = {}
    used: set[str] = set()
    for _, _, item_id, _, tx in pairs:
        if item_id in chosen or tx.tx_key in used:
            continue
        chosen[item_id] = tx
        used.add(tx.tx_key)

    report = MonthFixedReport(ym)
    for it in items:
        day = expected[it.id]
        tx = chosen.get(it.id)
        if tx is not None:
            status = "paid" if _amount_ok(it, tx.paid) else "different"
        else:
            end = day + dt.timedelta(days=it.match_days)
            if today <= end:
                status = "upcoming"
            elif _covered(conn, it.member_id, day - dt.timedelta(days=it.match_days), end):
                status = "missed"
            else:
                status = "pending"
        report.statuses.append(FixedStatus(it, day.isoformat(), status, tx))
    return report


# --- 내부 ----------------------------------------------------------------------


def _expected_date(year: int, month: int, pay_day: int) -> dt.date:
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(pay_day, last))


def _amount_ok(item: FixedItem, paid: int) -> bool:
    if item.amount_mode == "fixed":
        return paid == item.amount
    return abs(paid - item.amount) <= item.amount * VARIABLE_TOLERANCE


def _linked_instruments(conn: sqlite3.Connection, item: FixedItem) -> set[str]:
    if item.pay_account_id is not None:
        rows = conn.execute(
            "SELECT instrument FROM instrument_links WHERE member_id = ? AND account_id = ?",
            (item.member_id, item.pay_account_id),
        )
    elif item.pay_card_id is not None:
        rows = conn.execute(
            "SELECT instrument FROM instrument_links WHERE member_id = ? AND card_id = ?",
            (item.member_id, item.pay_card_id),
        )
    else:
        return set()
    return {r[0] for r in rows}


def _outflows(
    conn: sqlite3.Connection, member_id: int, lo: dt.date, hi: dt.date
) -> list[MatchedTx]:
    rows = conn.execute(
        """
        SELECT t.tx_key, t.tx_date, t.tx_time, t.amount,
               coalesce(o.content, t.content) AS content, t.instrument
        FROM transactions t
        LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key
        WHERE t.member_id = ? AND t.tx_date BETWEEN ? AND ? AND t.amount < 0
        ORDER BY t.tx_date, t.tx_time
        """,
        (member_id, lo.isoformat(), hi.isoformat()),
    )
    return [MatchedTx(*r) for r in rows]


def _covered(conn: sqlite3.Connection, member_id: int, lo: dt.date, hi: dt.date) -> bool:
    """날짜 범위가 걸친 달이 모두 정산(또는 기준 데이터)되어 있는가"""
    months = {lo.strftime("%Y-%m"), hi.strftime("%Y-%m")}
    have = {
        r[0]
        for r in conn.execute(
            "SELECT year_month FROM month_settlements WHERE member_id = ?", (member_id,)
        )
    }
    return months <= have
