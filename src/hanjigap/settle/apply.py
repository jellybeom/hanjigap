"""정산 확정 저장.

하나의 트랜잭션 안에서 미리보기를 다시 계산하고 그대로 저장한다. 중간에 실패하면 전부 취소된다.

- 거래는 지운 뒤 다시 넣지 않고 지문 기준으로 추가·수정(upsert)한다. 동일한 거래의 id가 유지된다.
- 웹 수정 내용(tx_overrides)은 건드리지 않는다. 지문으로 연결돼 있어 재정산 후에도 그대로다.
- 지우는 대상은 '그 사람의 그 달'에서 파일에 없는 거래뿐이다.
- 이미 정산 완료인 달에 바뀐 게 없으면 아무것도 저장하지 않는다(업로드 기록도 만들지 않는다).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo

from hanjigap.db.members import Member
from hanjigap.importer.fingerprint import KeyedRow
from hanjigap.settle.pipeline import ParsedExport
from hanjigap.settle.preview import DEFAULT_TZ, MonthPreview, StoredTx, preview_month

_PERIOD_SOURCE = {"파일 이름": "filename", "거래 날짜": "dates"}

_UPSERT = """
INSERT INTO transactions (
    member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, tx_type,
    category, subcategory, content, amount, currency, instrument, memo, batch_id, row_no
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (member_id, tx_key) DO UPDATE SET
    seq = excluded.seq,
    base_key = excluded.base_key,
    year_month = excluded.year_month,
    tx_date = excluded.tx_date,
    tx_time = excluded.tx_time,
    tx_type = excluded.tx_type,
    category = excluded.category,
    subcategory = excluded.subcategory,
    content = excluded.content,
    amount = excluded.amount,
    currency = excluded.currency,
    instrument = excluded.instrument,
    memo = excluded.memo,
    batch_id = excluded.batch_id,
    row_no = excluded.row_no
"""

_MONTH_STATE = """
INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (member_id, year_month) DO UPDATE SET
    status = excluded.status,
    batch_id = excluded.batch_id,
    tx_count = excluded.tx_count,
    settled_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
"""


@dataclass(frozen=True)
class ApplyResult:
    saved: bool
    batch_id: int | None
    preview: MonthPreview  # 트랜잭션 안에서 다시 계산한 미리보기

    @property
    def added(self) -> int:
        return len(self.preview.added)

    @property
    def changed(self) -> int:
        return len(self.preview.changed)

    @property
    def removed(self) -> int:
        return len(self.preview.removed)


def apply_month(
    conn: sqlite3.Connection,
    member: Member,
    parsed: ParsedExport,
    year_month: str,
    *,
    uploaded_by: int | None = None,
    tz: ZoneInfo = DEFAULT_TZ,
) -> ApplyResult:
    conn.execute("BEGIN IMMEDIATE")  # 쓰기 잠금: 동시에 다른 정산이 끼어들지 못하게
    try:
        preview = preview_month(conn, member, parsed, year_month, tz=tz)
        if preview.kind == "resettle" and not preview.has_changes:
            conn.execute("ROLLBACK")
            return ApplyResult(saved=False, batch_id=None, preview=preview)

        batch_id = insert_batch(conn, member, parsed, uploaded_by)
        insert_cashflow(conn, member, batch_id, parsed)
        write_month(conn, member, preview, batch_id, "settled")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return ApplyResult(saved=True, batch_id=batch_id, preview=preview)


def write_month(
    conn: sqlite3.Connection,
    member: Member,
    preview: MonthPreview,
    batch_id: int,
    status: Literal["baseline", "settled"],
) -> None:
    """한 달 쓰기 (호출하는 쪽이 연 트랜잭션 안에서). 일반 정산과 첫 적재가 함께 쓴다."""
    _upsert_rows(conn, member, preview.year_month, preview.rows, batch_id)
    _delete_rows(conn, member, preview.removed)
    _write_month_state(conn, member, preview.year_month, batch_id, len(preview.rows), status)


def insert_batch(
    conn: sqlite3.Connection, member: Member, parsed: ParsedExport, uploaded_by: int | None
) -> int:
    period = parsed.summary.period
    cur = conn.execute(
        "INSERT INTO import_batches (member_id, uploaded_by, file_sha256, source_name, container, "
        "period_start, period_end, period_source, row_count, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied')",
        (
            member.id,
            uploaded_by,
            parsed.file_sha256,
            parsed.file_name,
            parsed.opened.container,
            period.start.isoformat() if period else None,
            period.end.isoformat() if period else None,
            _PERIOD_SOURCE[period.source] if period else None,
            len(parsed.keyed),
        ),
    )
    return cur.lastrowid


def insert_cashflow(
    conn: sqlite3.Connection, member: Member, batch_id: int, parsed: ParsedExport
) -> None:
    """뱅샐 현금흐름현황을 이 업로드 기록에 붙여 저장한다. 표가 없으면 아무것도 하지 않는다."""
    if parsed.cashflow is None:
        return
    conn.executemany(
        "INSERT INTO bs_cashflow (batch_id, member_id, year_month, kind, item, amount) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(batch_id, member.id, *row) for row in parsed.cashflow.rows()],
    )


def _upsert_rows(
    conn: sqlite3.Connection, member: Member, ym: str, rows: list[KeyedRow], batch_id: int
) -> None:
    conn.executemany(
        _UPSERT,
        [
            (
                member.id,
                k.tx_key,
                k.seq,
                k.base_key,
                ym,
                k.row.date.isoformat(),
                k.row.time.isoformat(timespec="seconds"),
                k.row.tx_type,
                k.row.category,
                k.row.subcategory,
                k.row.content,
                k.row.amount,
                k.row.currency,
                k.row.instrument,
                k.row.memo,
                batch_id,
                k.row.row_no,
            )
            for k in rows
        ],
    )


def _delete_rows(conn: sqlite3.Connection, member: Member, removed: list[StoredTx]) -> None:
    conn.executemany(
        "DELETE FROM transactions WHERE member_id = ? AND tx_key = ?",
        [(member.id, s.tx_key) for s in removed],
    )


def _write_month_state(
    conn: sqlite3.Connection,
    member: Member,
    ym: str,
    batch_id: int,
    tx_count: int,
    status: Literal["baseline", "settled"],
) -> None:
    conn.execute(_MONTH_STATE, (member.id, ym, status, batch_id, tx_count))
