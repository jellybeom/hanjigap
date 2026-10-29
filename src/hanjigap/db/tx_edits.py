"""웹에서 거래 고치기 (tx_overrides).

고칠 수 있는 칸: 뱅샐 분류(category) · 소분류(subcategory) · 내용(content) · 한지갑 메모(note)
    뱅샐 타입(수입·지출·이체)은 고치지 않는다. 대신 한지갑 분류(직접 지정·규칙)를 쓴다.
    분류를 고치면 지출 분류별 합계와 규칙 판단에 그 값이 쓰인다.
''를 넘기면 그 칸은 뱅샐 값으로 돌아간다. 고친 칸이 하나도 없으면 기록을 지운다.
거래 지문(tx_key)에 붙어서 다시 정산해도 남는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from hanjigap.db import validate
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError

EDITABLE = {"category": "분류", "subcategory": "소분류", "content": "내용", "note": "메모"}
CATEGORY_MAX = 30


@dataclass(frozen=True)
class TxEdit:
    category: str | None
    subcategory: str | None
    content: str | None
    note: str | None
    edited_by: int | None
    edited_at: str


def get_edit(conn: sqlite3.Connection, member: Member, tx_key: str) -> TxEdit | None:
    row = conn.execute(
        "SELECT category, subcategory, content, note, edited_by, edited_at FROM tx_overrides "
        "WHERE member_id = ? AND tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    return None if row is None else TxEdit(**dict(row))


def edit_tx(
    conn: sqlite3.Connection,
    member: Member,
    tx_key: str,
    *,
    edited_by: Member | None = None,
    **changes: Any,
) -> TxEdit | None:
    """바꿀 칸만 넘긴다. 결과(남은 고친 값)를 돌려준다. 모두 되돌아가면 None."""
    _check_tx(conn, member, tx_key)
    if not changes:
        raise InputError("바꿀 내용이 없어요")
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise InputError(f"고칠 수 없는 칸이에요: {', '.join(sorted(unknown))}")
    current = get_edit(conn, member, tx_key)
    values = {k: getattr(current, k) if current else None for k in EDITABLE}
    for key, value in changes.items():
        text = validate.optional_text(value, EDITABLE[key])
        if key in ("category", "subcategory") and text and len(text) > CATEGORY_MAX:
            raise InputError(f"{EDITABLE[key]}는 {CATEGORY_MAX}자 이하로 입력하세요")
        values[key] = text
    other = conn.execute(
        "SELECT tx_type, memo FROM tx_overrides WHERE member_id = ? AND tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    keep_other = other is not None and (other["tx_type"] is not None or other["memo"] is not None)
    if all(v is None for v in values.values()) and not keep_other:
        clear_edit(conn, member, tx_key)
        return None
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, category, subcategory, content, note, "
        "edited_by, edited_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (member_id, tx_key) DO UPDATE SET category = excluded.category, "
        "subcategory = excluded.subcategory, content = excluded.content, note = excluded.note, "
        "edited_by = excluded.edited_by, edited_at = excluded.edited_at",
        (
            member.id,
            tx_key,
            values["category"],
            values["subcategory"],
            values["content"],
            values["note"],
            edited_by.id if edited_by else None,
            validate.now_utc(),
        ),
    )
    return get_edit(conn, member, tx_key)


def clear_edit(conn: sqlite3.Connection, member: Member, tx_key: str) -> bool:
    """웹에서 고친 것을 모두 되돌린다."""
    cur = conn.execute(
        "DELETE FROM tx_overrides WHERE member_id = ? AND tx_key = ?", (member.id, tx_key)
    )
    return cur.rowcount > 0


def _check_tx(conn: sqlite3.Connection, member: Member, tx_key: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM transactions WHERE member_id = ? AND tx_key = ?", (member.id, tx_key)
    ).fetchone()
    if row is None:
        raise InputError("거래를 찾을 수 없어요")
