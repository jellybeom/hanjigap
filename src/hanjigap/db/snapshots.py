"""뱅샐현황 잔액 기록 저장·조회 (0005).

저장은 정산(apply_month, first_load)이 연 트랜잭션 안에서 insert_snapshot을 부른다.
화면은 latest_snapshot으로 그 달의 가장 최근 기록을 가져온다.
상품명·보험명은 DB에만 저장하고 로그에는 개수만 남긴다. (부르는 쪽 책임)
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass

from hanjigap.db.members import Member
from hanjigap.importer.status_tables import Holding, StatusTables


@dataclass(frozen=True)
class Snapshot:
    id: int
    batch_id: int
    member_id: int
    as_of: str
    year_month: str
    total_assets: int | None
    total_liabilities: int | None
    net_worth: int | None
    checks_ok: bool
    holdings: tuple[Holding, ...]

    def of(self, section: str) -> list[Holding]:
        return [h for h in self.holdings if h.section == section]


def insert_snapshot(
    conn: sqlite3.Connection,
    member: Member,
    batch_id: int,
    tables: StatusTables | None,
    *,
    as_of: dt.date,
    year_month: str,
) -> int | None:
    """표를 하나도 못 읽었으면 저장하지 않고 None."""
    if tables is None or not tables.found:
        return None
    cur = conn.execute(
        "INSERT INTO bs_snapshots (batch_id, member_id, as_of, year_month, total_assets, "
        "total_liabilities, net_worth, checks_ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            batch_id,
            member.id,
            as_of.isoformat(),
            year_month,
            tables.total_assets,
            tables.total_liabilities,
            tables.net_worth,
            int(tables.ok),
        ),
    )
    snapshot_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO bs_holdings (snapshot_id, seq, section, group_name, institution, name, "
        "amount, principal, rate, status, start_date, end_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                snapshot_id,
                seq,
                h.section,
                h.group,
                h.institution,
                h.name,
                h.amount,
                h.principal,
                h.rate,
                h.status,
                h.start_date,
                h.end_date,
            )
            for seq, h in enumerate(tables.holdings, start=1)
        ],
    )
    return snapshot_id


def latest_snapshot(
    conn: sqlite3.Connection, member: Member, year_month: str | None = None
) -> Snapshot | None:
    """그 달(없으면 전체)의 가장 최근 기록. 같은 달을 다시 정산했으면 나중 것."""
    sql = (
        "SELECT id, batch_id, member_id, as_of, year_month, total_assets, total_liabilities, "
        "net_worth, checks_ok FROM bs_snapshots WHERE member_id = ?"
    )
    params: list = [member.id]
    if year_month is not None:
        sql += " AND year_month = ?"
        params.append(year_month)
    row = conn.execute(sql + " ORDER BY year_month DESC, id DESC LIMIT 1", params).fetchone()
    if row is None:
        return None
    holdings = tuple(
        Holding(
            section=h["section"],
            group=h["group_name"],
            institution=h["institution"],
            name=h["name"],
            amount=h["amount"],
            principal=h["principal"],
            rate=h["rate"],
            status=h["status"],
            start_date=h["start_date"],
            end_date=h["end_date"],
            row_no=0,
        )
        for h in conn.execute(
            "SELECT * FROM bs_holdings WHERE snapshot_id = ? ORDER BY seq", (row["id"],)
        )
    )
    return Snapshot(
        id=row["id"],
        batch_id=row["batch_id"],
        member_id=row["member_id"],
        as_of=row["as_of"],
        year_month=row["year_month"],
        total_assets=row["total_assets"],
        total_liabilities=row["total_liabilities"],
        net_worth=row["net_worth"],
        checks_ok=bool(row["checks_ok"]),
        holdings=holdings,
    )
