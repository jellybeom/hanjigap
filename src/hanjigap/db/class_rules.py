"""분류 규칙 · 거래별 직접 지정 (J7a).

실제 입력은 웹 화면에서 한다. 이 모듈은 화면과 테스트가 함께 쓰는 함수다.
분류 계산은 ledger/classify.py.

규칙
    조건이 하나 이상 있어야 한다. 비운 조건은 '상관없음'.
    순서: position이 작은 것부터 먼저 본다. 새 규칙은 맨 위에 들어간다.
    규칙은 설정이라 지울 수 있다. (거래 기록이 아님)
거래별 직접 지정
    거래 지문(tx_key)에 붙는다. 다시 정산해도 남는다. 지우면 규칙·기본값으로 돌아간다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from hanjigap.db import validate
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError

ClassError = InputError

RESULTS = {"income": "수입", "expense": "지출", "saving": "저축·투자", "excluded": "제외"}
DIRECTIONS = {"in": "들어온 돈", "out": "나간 돈", "any": "모두"}
TX_TYPES = ("수입", "지출", "이체")
PATTERN_MAX = 50
CATEGORY_MAX = 30


@dataclass(frozen=True)
class ClassRule:
    id: int
    position: int
    member_id: int | None
    direction: str
    tx_type: str | None
    category: str | None
    subcategory: str | None
    pattern: str | None
    instrument: str | None
    amount: int | None
    result: str
    result_category: str | None
    memo: str | None
    active: bool

    @property
    def result_label(self) -> str:
        return RESULTS[self.result]

    @property
    def label(self) -> str:
        """화면 표시용 조건 설명. 예) '들어온 돈 · 내용 「당직비」 → 수입 › 수당'"""
        parts = []
        if self.direction != "any":
            parts.append(DIRECTIONS[self.direction])
        if self.tx_type:
            parts.append(f"뱅샐 {self.tx_type}")
        if self.category:
            cat = f"{self.category} › {self.subcategory}" if self.subcategory else self.category
            parts.append(f"분류 {cat}")
        if self.pattern:
            parts.append(f"내용 「{self.pattern}」")
        if self.instrument:
            parts.append(f"결제수단 「{self.instrument}」")
        if self.amount:
            parts.append(f"{self.amount:,}원")
        result = self.result_label
        if self.result_category:
            result += f" › {self.result_category}"
        return f"{' · '.join(parts)} → {result}"

    def matches(self, tx: dict[str, Any]) -> bool:
        if self.member_id is not None and tx["member_id"] != self.member_id:
            return False
        if self.direction == "in" and tx["amount"] <= 0:
            return False
        if self.direction == "out" and tx["amount"] >= 0:
            return False
        if self.tx_type is not None and tx["tx_type"] != self.tx_type:
            return False
        if self.category is not None and tx["category"] != self.category:
            return False
        if self.subcategory is not None and tx["subcategory"] != self.subcategory:
            return False
        if self.pattern is not None and self.pattern.lower() not in (tx["content"] or "").lower():
            return False
        if self.instrument is not None and tx["instrument"] != self.instrument:
            return False
        return self.amount is None or abs(tx["amount"]) == self.amount


_COLS = (
    "id, position, member_id, direction, tx_type, category, subcategory, pattern, instrument, "
    "amount, result, result_category, memo, active"
)
_EDITABLE = (
    "member_id",
    "direction",
    "tx_type",
    "category",
    "subcategory",
    "pattern",
    "instrument",
    "amount",
    "result",
    "result_category",
    "memo",
)


def _row(r: sqlite3.Row) -> ClassRule:
    d = dict(r)
    d["active"] = bool(d["active"])
    return ClassRule(**d)


# --- 규칙 ----------------------------------------------------------------------


def add_rule(
    conn: sqlite3.Connection,
    *,
    result: str,
    member: Member | None = None,
    direction: str = "any",
    tx_type: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    pattern: str | None = None,
    instrument: str | None = None,
    amount: int | None = None,
    result_category: str | None = None,
    memo: str | None = None,
    created_by: Member | None = None,
) -> ClassRule:
    """새 규칙. 맨 위(가장 먼저 보는 자리)에 들어간다."""
    fields = _clean(
        {
            "member_id": member.id if member else None,
            "direction": direction,
            "tx_type": tx_type,
            "category": category,
            "subcategory": subcategory,
            "pattern": pattern,
            "instrument": instrument,
            "amount": amount,
            "result": result,
            "result_category": result_category,
            "memo": memo,
        }
    )
    top = conn.execute("SELECT min(position) FROM class_rules").fetchone()[0]
    fields["position"] = 1 if top is None else top - 1
    fields["created_by"] = created_by.id if created_by else None
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    cur = conn.execute(f"INSERT INTO class_rules ({cols}) VALUES ({marks})", tuple(fields.values()))
    return get_rule(conn, cur.lastrowid)


def get_rule(conn: sqlite3.Connection, rule_id: int) -> ClassRule:
    row = conn.execute(f"SELECT {_COLS} FROM class_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise ClassError(f"분류 규칙을 찾을 수 없어요: {rule_id}")
    return _row(row)


def list_rules(conn: sqlite3.Connection, *, include_inactive: bool = True) -> list[ClassRule]:
    """먼저 보는 순서대로."""
    sql = f"SELECT {_COLS} FROM class_rules"
    if not include_inactive:
        sql += " WHERE active = 1"
    return [_row(r) for r in conn.execute(sql + " ORDER BY position, id")]


def update_rule(conn: sqlite3.Connection, rule_id: int, **changes: Any) -> ClassRule:
    """바꿀 칸만. 조건 칸은 ''(금액은 0)를 넘기면 비운다. member는 Member 또는 None(모두)."""
    current = get_rule(conn, rule_id)
    if not changes:
        raise ClassError("바꿀 내용이 없어요")
    if "member" in changes:
        member = changes.pop("member")
        changes["member_id"] = member.id if member else None
    unknown = set(changes) - set(_EDITABLE)
    if unknown:
        raise ClassError(f"바꿀 수 없는 칸이에요: {', '.join(sorted(unknown))}")
    merged = {k: getattr(current, k) for k in _EDITABLE}
    merged.update(changes)
    fields = _clean(merged)
    fields["updated_at"] = validate.now_utc()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE class_rules SET {sets} WHERE id = ?", (*fields.values(), rule_id))
    return get_rule(conn, rule_id)


def set_rule_active(conn: sqlite3.Connection, rule_id: int, active: bool) -> ClassRule:
    get_rule(conn, rule_id)
    conn.execute(
        "UPDATE class_rules SET active = ?, updated_at = ? WHERE id = ?",
        (int(active), validate.now_utc(), rule_id),
    )
    return get_rule(conn, rule_id)


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    get_rule(conn, rule_id)
    conn.execute("DELETE FROM class_rules WHERE id = ?", (rule_id,))


def reorder_rules(conn: sqlite3.Connection, rule_ids: list[int]) -> list[ClassRule]:
    """화면에서 바꾼 순서 그대로. 모든 규칙 번호를 빠짐없이 한 번씩 넘겨야 한다."""
    existing = {r.id for r in list_rules(conn)}
    if len(rule_ids) != len(set(rule_ids)) or set(rule_ids) != existing:
        raise ClassError("규칙 순서에는 모든 규칙을 한 번씩 넣어 주세요")
    for position, rule_id in enumerate(rule_ids, start=1):
        conn.execute("UPDATE class_rules SET position = ? WHERE id = ?", (position, rule_id))
    return list_rules(conn)


def rule_for_tx(
    conn: sqlite3.Connection,
    member: Member,
    tx_key: str,
    *,
    result: str,
    result_category: str | None = None,
    created_by: Member | None = None,
) -> ClassRule:
    """'앞으로도 이렇게': 그 거래의 사람 · 방향 · 내용으로 규칙을 만든다."""
    tx = _tx(conn, member, tx_key)
    content = (tx["content"] or "").strip()
    if not content:
        raise ClassError("내용이 비어 있는 거래는 규칙으로 만들 수 없어요")
    if len(content) > PATTERN_MAX:
        raise ClassError(f"내용이 {PATTERN_MAX}자를 넘어서 규칙으로 만들 수 없어요")
    return add_rule(
        conn,
        result=result,
        member=member,
        direction="in" if tx["amount"] > 0 else "out",
        pattern=content,
        result_category=result_category,
        created_by=created_by,
    )


def preview_rule(
    conn: sqlite3.Connection, *, member: Member | None = None, limit: int = 3, **values: Any
) -> dict[str, Any]:
    """저장하기 전 미리보기: 이 조건에 맞는 거래 수 (모든 달)와 최근 예시.

    직접 지정한 거래는 규칙보다 먼저라 바뀌지 않으므로 따로 센다.
    조건 검사는 저장할 때와 같다 (_clean). 결과는 비워 두면 '지출'로 보고 검사한다.
    """
    fields = _clean({**values, "member_id": member.id if member else None,
                     "result": values.get("result") or "expense"})  # fmt: skip
    rule = ClassRule(id=0, position=0, active=True, **fields)
    manual = {
        (r["member_id"], r["tx_key"])
        for r in conn.execute("SELECT member_id, tx_key FROM tx_classes")
    }
    rows = conn.execute(
        "SELECT t.member_id, t.tx_key, t.tx_date, t.amount, t.instrument, "
        "coalesce(o.tx_type, t.tx_type) AS tx_type, coalesce(o.category, t.category) AS category, "
        "coalesce(o.subcategory, t.subcategory) AS subcategory, "
        "coalesce(o.content, t.content) AS content FROM transactions t "
        "LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key "
        "ORDER BY t.tx_date DESC, t.tx_time DESC, t.id DESC"
    )
    hits = [dict(r) for r in rows if rule.matches(dict(r))]
    applies = [h for h in hits if (h["member_id"], h["tx_key"]) not in manual]
    return {
        "label": rule.label,
        "count": len(applies),
        "manual": len(hits) - len(applies),
        "months": len({h["tx_date"][:7] for h in applies}),
        "examples": applies[:limit],
    }


def rule_preview(conn: sqlite3.Connection, member: Member, tx_key: str) -> dict[str, Any]:
    """'앞으로도 이렇게'를 켜면 만들 규칙의 조건과, 함께 바뀔 수 있는 다른 거래 수.

    규칙은 지난 달 거래에도 적용된다 (분류는 볼 때마다 계산하므로).
    직접 지정한 거래는 규칙보다 먼저라서 세지 않는다.
    """
    tx = _tx(conn, member, tx_key)
    content = (tx["content"] or "").strip()
    direction = "in" if tx["amount"] > 0 else "out"
    out: dict[str, Any] = {
        "possible": True,
        "reason": None,
        "pattern": content,
        "direction": direction,
        "direction_label": DIRECTIONS[direction],
        "others": 0,
        "other_months": 0,
    }
    if not content:
        return {**out, "possible": False, "reason": "내용이 비어 있어서 규칙으로 만들 수 없어요"}
    if len(content) > PATTERN_MAX:
        reason = f"내용이 {PATTERN_MAX}자를 넘어서 규칙으로 만들 수 없어요"
        return {**out, "possible": False, "reason": reason}
    needle = content.lower()
    rows = conn.execute(
        "SELECT t.year_month, t.amount, coalesce(o.content, t.content) AS content "
        "FROM transactions t "
        "LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key "
        "LEFT JOIN tx_classes c ON c.member_id = t.member_id AND c.tx_key = t.tx_key "
        "WHERE t.member_id = ? AND t.tx_key != ? AND c.tx_key IS NULL",
        (member.id, tx_key),
    )
    hits = [
        r
        for r in rows
        if (r["amount"] > 0 if direction == "in" else r["amount"] < 0)
        and needle in (r["content"] or "").lower()
    ]
    out["others"] = len(hits)
    out["other_months"] = len({r["year_month"] for r in hits})
    return out


# --- 거래별 직접 지정 --------------------------------------------------------------


def set_tx_class(
    conn: sqlite3.Connection,
    member: Member,
    tx_key: str,
    result: str,
    *,
    result_category: str | None = None,
    classified_by: Member | None = None,
) -> None:
    """이미 있으면 바꾼다."""
    _tx(conn, member, tx_key)
    res = validate.code(result, RESULTS, "분류를")
    cat = _category(result_category)
    conn.execute(
        "INSERT INTO tx_classes (member_id, tx_key, result, result_category, classified_by, "
        "classified_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (member_id, tx_key) DO UPDATE SET "
        "result = excluded.result, result_category = excluded.result_category, "
        "classified_by = excluded.classified_by, classified_at = excluded.classified_at",
        (
            member.id,
            tx_key,
            res,
            cat,
            classified_by.id if classified_by else None,
            validate.now_utc(),
        ),
    )


def clear_tx_class(conn: sqlite3.Connection, member: Member, tx_key: str) -> bool:
    cur = conn.execute(
        "DELETE FROM tx_classes WHERE member_id = ? AND tx_key = ?", (member.id, tx_key)
    )
    return cur.rowcount > 0


def tx_classes(conn: sqlite3.Connection) -> dict[tuple[int, str], tuple[str, str | None]]:
    """(member_id, tx_key) → (분류, 보여줄 분류)"""
    return {
        (r["member_id"], r["tx_key"]): (r["result"], r["result_category"])
        for r in conn.execute("SELECT member_id, tx_key, result, result_category FROM tx_classes")
    }


# --- 내부 ----------------------------------------------------------------------


def _tx(conn: sqlite3.Connection, member: Member, tx_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT t.amount, coalesce(o.content, t.content) AS content FROM transactions t "
        "LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key "
        "WHERE t.member_id = ? AND t.tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    if row is None:
        raise ClassError("거래를 찾을 수 없어요")
    return row


def _category(value: str | None) -> str | None:
    cat = validate.optional_text(value, "보여줄 분류")
    if cat is not None and len(cat) > CATEGORY_MAX:
        raise ClassError(f"보여줄 분류는 {CATEGORY_MAX}자 이하로 입력하세요")
    return cat


def _text(value: str | None, label: str, limit: int = PATTERN_MAX) -> str | None:
    text = validate.optional_text(value, label)
    if text is not None and len(text) > limit:
        raise ClassError(f"{label}은(는) {limit}자 이하로 입력하세요")
    return text


def _clean(v: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"member_id": v.get("member_id")}
    out["direction"] = validate.code(v.get("direction") or "any", DIRECTIONS, "방향을")
    tx_type = (v.get("tx_type") or "").strip() or None
    if tx_type is not None and tx_type not in TX_TYPES:
        raise ClassError(f"뱅샐 타입은 {', '.join(TX_TYPES)} 중 하나예요")
    out["tx_type"] = tx_type
    out["category"] = _text(v.get("category"), "뱅샐 분류")
    out["subcategory"] = _text(v.get("subcategory"), "뱅샐 소분류")
    if out["subcategory"] is not None and out["category"] is None:
        raise ClassError("소분류를 쓰려면 분류도 입력하세요")
    out["pattern"] = _text(v.get("pattern"), "내용 글자")
    out["instrument"] = _text(v.get("instrument"), "결제수단")
    amount = v.get("amount") or None
    if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool)):
        raise ClassError("금액은 1원 이상의 정수로 입력하세요")
    if amount is not None and amount < 0:
        amount = -amount  # 부호 없이 비교한다
    out["amount"] = amount
    out["result"] = validate.code(v.get("result"), RESULTS, "분류를")
    out["result_category"] = _category(v.get("result_category"))
    out["memo"] = validate.optional_text(v.get("memo"), "메모")
    conditions = ("tx_type", "category", "pattern", "instrument", "amount")
    if out["direction"] == "any" and all(out[k] is None for k in conditions):
        raise ClassError("규칙에는 조건이 하나 이상 있어야 해요 (방향, 분류, 내용 글자 등)")
    return out
