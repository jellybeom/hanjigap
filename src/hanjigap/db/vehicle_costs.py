"""자동차 유지비: 규칙 · 거래별 붙이기·빼기 · 기간별 합계 (J5b).

유지비는 저장하지 않고 볼 때마다 거래에서 모은다.

어떤 거래가 자동차 비용인가 (위에서부터 먼저 맞는 것)
    1. 거래별 '빼기' 표시 → 아님
    2. 거래별 '붙이기' 표시 → 맞음 (그때 고른 비용 종류, 없으면 기타)
    3. 지출 거래이고 규칙에 맞으면 → 맞음. 규칙은 분류 → 글자 → 결제수단 순으로 본다.
    분류·내용은 웹에서 고친 값이 있으면 그 값을 쓴다.
    구입한 달 전, 판 달 뒤의 거래는 넣지 않는다.

금액
    비용 = −거래 금액. 같은 분류의 환불(+)은 비용을 줄인다.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from hanjigap.db import validate
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError
from hanjigap.db.vehicles import Vehicle

CostError = InputError

COST_TYPES = {
    "fuel": "주유",
    "toll": "통행료",
    "parking": "주차",
    "wash": "세차",
    "repair": "정비",
    "insurance": "보험",
    "tax": "세금",
    "fine": "과태료",
    "other": "기타",
}
RULE_KINDS = {"category": "분류", "text": "글자", "instrument": "결제수단"}
_KIND_ORDER = {"category": 0, "text": 1, "instrument": 2}

# 뱅샐 분류 기준 기본 규칙. '자동차 > 할부/리스'는 유지비가 아니라서 넣지 않는다.
DEFAULT_RULES: tuple[tuple[str, str | None, str | None, str], ...] = (
    # (kind, 분류 또는 글자, 소분류, 비용 종류)
    ("category", "자동차", "주유", "fuel"),
    ("category", "자동차", "통행료", "toll"),
    ("category", "자동차", "주차", "parking"),
    ("category", "자동차", "세차", "wash"),
    ("category", "자동차", "정비/수리", "repair"),
    ("category", "자동차", "차량보험", "insurance"),
    ("text", "과태료", None, "fine"),
    ("text", "범칙금", None, "fine"),
    ("text", "자동차세", None, "tax"),
)


@dataclass(frozen=True)
class CostRule:
    id: int
    vehicle_id: int
    kind: str
    member_id: int | None
    category: str | None
    subcategory: str | None
    pattern: str | None
    cost_type: str
    active: bool

    @property
    def label(self) -> str:
        if self.kind == "category":
            what = f"{self.category} › {self.subcategory}" if self.subcategory else self.category
            return f"분류 {what}"
        return f"{RULE_KINDS[self.kind]} 「{self.pattern}」"

    def matches(self, tx: dict[str, Any]) -> bool:
        if self.member_id is not None and tx["member_id"] != self.member_id:
            return False
        if self.kind == "category":
            if tx["category"] != self.category:
                return False
            return self.subcategory is None or tx["subcategory"] == self.subcategory
        if self.kind == "text":
            return self.pattern.lower() in (tx["content"] or "").lower()
        return tx["instrument"] == self.pattern


@dataclass(frozen=True)
class CostTx:
    member_id: int
    tx_key: str
    date: str
    content: str
    instrument: str
    category: str
    subcategory: str
    cost: int  # 양수 = 쓴 돈
    cost_type: str
    reason: str  # '분류 자동차 › 주유', '직접 붙임' 등
    manual: bool

    @property
    def cost_label(self) -> str:
        return COST_TYPES[self.cost_type]


@dataclass
class CostReport:
    start_month: str
    end_month: str
    items: list[CostTx] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(i.cost for i in self.items)

    @property
    def months(self) -> int:
        return _months_between(self.start_month, self.end_month) + 1

    @property
    def monthly_average(self) -> int:
        return round(self.total / self.months)

    def by_type(self) -> dict[str, int]:
        """비용 종류별 합계, 큰 순"""
        out: dict[str, int] = defaultdict(int)
        for i in self.items:
            out[i.cost_type] += i.cost
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def by_month(self) -> dict[str, int]:
        """시작 달부터 끝 달까지 모든 달 (없으면 0)"""
        out = {}
        ym = self.start_month
        while ym <= self.end_month:
            out[ym] = 0
            ym = _add_month(ym)
        for i in self.items:
            out[i.date[:7]] = out.get(i.date[:7], 0) + i.cost
        return out


# --- 규칙 ----------------------------------------------------------------------


_RULE_COLS = "id, vehicle_id, kind, member_id, category, subcategory, pattern, cost_type, active"


def _rule(r: sqlite3.Row) -> CostRule:
    d = dict(r)
    d["active"] = bool(d["active"])
    return CostRule(**d)


def create_default_rules(conn: sqlite3.Connection, vehicle: Vehicle) -> list[CostRule]:
    """차량을 등록할 때 한 번. 이미 규칙이 있으면 아무것도 하지 않는다."""
    if list_rules(conn, vehicle, include_inactive=True):
        return []
    made = []
    for kind, what, sub, cost_type in DEFAULT_RULES:
        if kind == "category":
            made.append(
                add_rule(conn, vehicle, "category", cost_type, category=what, subcategory=sub)
            )
        else:
            made.append(add_rule(conn, vehicle, kind, cost_type, pattern=what))
    return made


def add_rule(
    conn: sqlite3.Connection,
    vehicle: Vehicle,
    kind: str,
    cost_type: str,
    *,
    category: str | None = None,
    subcategory: str | None = None,
    pattern: str | None = None,
    member: Member | None = None,
) -> CostRule:
    kind = validate.code(kind, RULE_KINDS, "규칙 종류를")
    cost_type = validate.code(cost_type, COST_TYPES, "비용 종류를")
    if kind == "category":
        category = validate.required(category, "분류")
        subcategory = validate.optional_text(subcategory, "소분류")
        pattern = None
    else:
        pattern = validate.required(pattern, "찾을 글자" if kind == "text" else "결제수단 이름")
        if len(pattern) > 50:
            raise CostError("찾을 글자는 50자 이하로 입력하세요")
        category = subcategory = None
        if kind == "instrument" and member is None:
            raise CostError("결제수단 규칙은 누구의 결제수단인지 정해 주세요")
    cur = conn.execute(
        "INSERT INTO vehicle_cost_rules (vehicle_id, kind, member_id, category, subcategory, "
        "pattern, cost_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            vehicle.id,
            kind,
            member.id if member else None,
            category,
            subcategory,
            pattern,
            cost_type,
        ),
    )
    return _get_rule(conn, cur.lastrowid)


def list_rules(
    conn: sqlite3.Connection, vehicle: Vehicle, *, include_inactive: bool = False
) -> list[CostRule]:
    rules = [
        _rule(r)
        for r in conn.execute(
            f"SELECT {_RULE_COLS} FROM vehicle_cost_rules WHERE vehicle_id = ? ORDER BY id",
            (vehicle.id,),
        )
    ]
    if not include_inactive:
        rules = [r for r in rules if r.active]
    return sorted(rules, key=lambda r: (_KIND_ORDER[r.kind], r.id))


def set_rule_active(conn: sqlite3.Connection, rule_id: int, active: bool) -> CostRule:
    _get_rule(conn, rule_id)
    conn.execute("UPDATE vehicle_cost_rules SET active = ? WHERE id = ?", (int(active), rule_id))
    return _get_rule(conn, rule_id)


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    _get_rule(conn, rule_id)
    conn.execute("DELETE FROM vehicle_cost_rules WHERE id = ?", (rule_id,))


def get_rule(conn: sqlite3.Connection, rule_id: int) -> CostRule:
    return _get_rule(conn, rule_id)


def _get_rule(conn: sqlite3.Connection, rule_id: int) -> CostRule:
    row = conn.execute(
        f"SELECT {_RULE_COLS} FROM vehicle_cost_rules WHERE id = ?", (rule_id,)
    ).fetchone()
    if row is None:
        raise CostError(f"규칙을 찾을 수 없어요: {rule_id}")
    return _rule(row)


# --- 거래별 붙이기·빼기 ------------------------------------------------------------


def tag_tx(
    conn: sqlite3.Connection,
    vehicle: Vehicle,
    member: Member,
    tx_key: str,
    *,
    include: bool,
    cost_type: str | None = None,
    tagged_by: Member | None = None,
) -> None:
    """include=True면 붙이기(비용 종류, 없으면 기타), False면 빼기. 이미 있으면 바꾼다."""
    exists = conn.execute(
        "SELECT 1 FROM transactions WHERE member_id = ? AND tx_key = ?", (member.id, tx_key)
    ).fetchone()
    if exists is None:
        raise CostError("거래를 찾을 수 없어요")
    ctype = validate.code(cost_type, COST_TYPES, "비용 종류를") if include and cost_type else None
    conn.execute(
        "INSERT INTO vehicle_tx_tags (vehicle_id, member_id, tx_key, action, cost_type, tagged_by, "
        "tagged_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (vehicle_id, member_id, tx_key) DO UPDATE SET action = excluded.action, "
        "cost_type = excluded.cost_type, tagged_by = excluded.tagged_by, "
        "tagged_at = excluded.tagged_at",
        (
            vehicle.id,
            member.id,
            tx_key,
            "include" if include else "exclude",
            ctype,
            tagged_by.id if tagged_by else None,
            validate.now_utc(),
        ),
    )


def untag_tx(conn: sqlite3.Connection, vehicle: Vehicle, member: Member, tx_key: str) -> bool:
    """붙이기·빼기를 지워 규칙대로 돌아간다."""
    cur = conn.execute(
        "DELETE FROM vehicle_tx_tags WHERE vehicle_id = ? AND member_id = ? AND tx_key = ?",
        (vehicle.id, member.id, tx_key),
    )
    return cur.rowcount > 0


# --- 유지비 계산 ------------------------------------------------------------------


def vehicle_costs(
    conn: sqlite3.Connection, vehicle: Vehicle, start_month: str, end_month: str
) -> CostReport:
    start = validate.year_month(start_month, "시작 월")
    end = validate.year_month(end_month, "끝 월")
    if start is None or end is None or start > end:
        raise CostError("기간을 YYYY-MM ~ YYYY-MM 으로 입력하세요")
    start = max(start, vehicle.purchase_month)
    if vehicle.sold_on:
        end = min(end, vehicle.sold_on[:7])
    tags = {
        (r["member_id"], r["tx_key"]): (r["action"], r["cost_type"])
        for r in conn.execute(
            "SELECT member_id, tx_key, action, cost_type FROM vehicle_tx_tags WHERE vehicle_id = ?",
            (vehicle.id,),
        )
    }
    return collect_costs(conn, list_rules(conn, vehicle), tags, start, end)


def collect_costs(
    conn: sqlite3.Connection,
    rules: list[CostRule],
    tags: dict[tuple[int, str], tuple[str, str | None]],
    start: str,
    end: str,
) -> CostReport:
    """규칙과 표시로 그 기간의 자동차 비용 거래를 모은다.

    차량 없이 기본 규칙만 시험할 때도 쓴다.
    """
    report = CostReport(start, end)
    if start > end:
        return report
    rows = conn.execute(
        """
        SELECT t.member_id, t.tx_key, t.tx_date, t.amount, t.instrument,
               coalesce(o.tx_type, t.tx_type)         AS tx_type,
               coalesce(o.category, t.category)       AS category,
               coalesce(o.subcategory, t.subcategory) AS subcategory,
               coalesce(o.content, t.content)         AS content
        FROM transactions t
        LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key
        WHERE t.year_month BETWEEN ? AND ?
        ORDER BY t.tx_date, t.tx_time, t.id
        """,
        (start, end),
    )
    ordered = sorted(rules, key=lambda r: (_KIND_ORDER[r.kind], r.id))
    for r in rows:
        tx = dict(r)
        tag = tags.get((tx["member_id"], tx["tx_key"]))
        if tag is not None and tag[0] == "exclude":
            continue
        if tag is not None:
            cost_type, reason, manual = tag[1] or "other", "직접 붙임", True
        else:
            if tx["tx_type"] != "지출":
                continue
            rule = next((rule for rule in ordered if rule.matches(tx)), None)
            if rule is None:
                continue
            cost_type, reason, manual = rule.cost_type, f"규칙: {rule.label}", False
        report.items.append(
            CostTx(
                member_id=tx["member_id"],
                tx_key=tx["tx_key"],
                date=tx["tx_date"],
                content=tx["content"] or "",
                instrument=tx["instrument"],
                category=tx["category"],
                subcategory=tx["subcategory"],
                cost=-tx["amount"],
                cost_type=cost_type,
                reason=reason,
                manual=manual,
            )
        )
    return report


def default_rule_objects() -> list[CostRule]:
    """DB에 넣지 않은 기본 규칙 (차량 등록 전에 실제 거래로 시험해 볼 때)"""
    out = []
    for n, (kind, what, sub, cost_type) in enumerate(DEFAULT_RULES, start=1):
        out.append(
            CostRule(
                id=n,
                vehicle_id=0,
                kind=kind,
                member_id=None,
                category=what if kind == "category" else None,
                subcategory=sub,
                pattern=None if kind == "category" else what,
                cost_type=cost_type,
                active=True,
            )
        )
    return out


def _months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def _add_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) + 1
    if m == 13:
        y, m = y + 1, 1
    return f"{y:04d}-{m:02d}"
