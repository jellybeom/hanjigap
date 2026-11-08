"""한지갑 집계에서 뺄 뱅샐현황 항목 (0011).

각자 용돈 통장·마이너스 통장처럼 뱅샐 가계부에서는 뺐지만 '뱅샐현황'에는 남는 개인 영역.
- 원본(bs_holdings)은 그대로 두고, 보여 줄 때 apply_excludes로 빼고 합계를 다시 낸다.
  (지난 달 기록에도 바로 적용 · 규칙을 지우면 다시 나타남)
- 규칙은 본인 것만 보고 바꾼다. 다른 사람의 규칙은 목록에도 나오지 않는다.
- 합계: 재무현황의 자산·부채 쪽 항목만 총계에 들어 있다. 투자·대출·보험 표는 상세 목록이라
  거기서 빼면 목록·묶음 합계에서만 빠진다. 그래서 같은 것을 함께 빼도록 짝(pairs)을 알려 준다.
- 상품명은 로그에 남기지 않는다. (부르는 쪽 책임)
"""

from __future__ import annotations

import dataclasses
import sqlite3
from dataclasses import dataclass

from hanjigap.db.members import Member
from hanjigap.db.snapshots import Snapshot
from hanjigap.db.validate import InputError
from hanjigap.importer.status_tables import SECTION_LABELS, Holding

SECTION_ORDER = ("asset", "liability", "investment", "loan", "insurance")
# 재무현황(총계에 들어가는 쪽) ↔ 상세 표
COUNTERPART = {
    "asset": "investment",
    "investment": "asset",
    "liability": "loan",
    "loan": "liability",
}

Key = tuple[str, str, str, str]  # (section, group, institution, name)


@dataclass(frozen=True)
class ExcludeRule:
    id: int
    member_id: int
    section: str
    group: str
    institution: str
    name: str

    @property
    def key(self) -> Key:
        return (self.section, self.group, self.institution, self.name)

    @property
    def section_label(self) -> str:
        return SECTION_LABELS[self.section]


def key_of(h: Holding) -> Key:
    return (h.section, h.group or "", h.institution or "", h.name or "")


def _rule(row) -> ExcludeRule:
    return ExcludeRule(row["id"], row["member_id"], row["section"], row["group_name"],
                       row["institution"], row["name"])  # fmt: skip


def list_rules(conn: sqlite3.Connection, member: Member) -> list[ExcludeRule]:
    rows = conn.execute(
        "SELECT * FROM holding_excludes WHERE member_id = ? ORDER BY id", (member.id,)
    )
    return [_rule(r) for r in rows]


def keys_by_member(conn: sqlite3.Connection) -> dict[int, set[Key]]:
    out: dict[int, set[Key]] = {}
    for r in conn.execute("SELECT * FROM holding_excludes"):
        out.setdefault(r["member_id"], set()).add(_rule(r).key)
    return out


def add_rule(conn: sqlite3.Connection, member: Member, key: Key) -> ExcludeRule:
    """같은 규칙이 있으면 그것을 돌려준다."""
    section, group, institution, name = (str(v or "").strip() for v in key)
    if section not in SECTION_ORDER:
        raise InputError("알 수 없는 표예요")
    if not name or len(name) > 200:
        raise InputError("이름이 없는 항목은 뺄 수 없어요")
    conn.execute(
        "INSERT OR IGNORE INTO holding_excludes "
        "(member_id, section, group_name, institution, name) VALUES (?, ?, ?, ?, ?)",
        (member.id, section, group, institution, name),
    )
    row = conn.execute(
        "SELECT * FROM holding_excludes WHERE member_id = ? AND section = ? AND group_name = ? "
        "AND institution = ? AND name = ?",
        (member.id, section, group, institution, name),
    ).fetchone()
    return _rule(row)


def remove_rule(conn: sqlite3.Connection, member: Member, rule_id: int) -> bool:
    """본인 규칙만 지운다."""
    cur = conn.execute(
        "DELETE FROM holding_excludes WHERE id = ? AND member_id = ?", (rule_id, member.id)
    )
    return cur.rowcount > 0


def apply_excludes(snapshot: Snapshot | None, keys: set[Key]) -> Snapshot | None:
    """규칙에 맞는 항목을 빼고 자산·부채·순자산 합계를 다시 낸 기록. 뺄 게 없으면 그대로."""
    if snapshot is None or not keys:
        return snapshot
    dropped = [h for h in snapshot.holdings if key_of(h) in keys]
    if not dropped:
        return snapshot
    minus_assets = sum(h.amount or 0 for h in dropped if h.section == "asset")
    minus_debt = sum(h.amount or 0 for h in dropped if h.section == "liability")
    return dataclasses.replace(
        snapshot,
        holdings=tuple(h for h in snapshot.holdings if key_of(h) not in keys),
        total_assets=(
            None if snapshot.total_assets is None else snapshot.total_assets - minus_assets
        ),
        total_liabilities=(
            None if snapshot.total_liabilities is None else snapshot.total_liabilities - minus_debt
        ),
        net_worth=(
            None if snapshot.net_worth is None else snapshot.net_worth - minus_assets + minus_debt
        ),
    )


def pairs(holdings: list[Holding], target: Holding) -> list[Holding]:
    """같은 것으로 보이는 상대 표의 항목 (재무현황 ↔ 대출·투자현황, 원 단위까지 같은 금액).

    예) 재무현황 부채 '○○은행 마이너스 통장' 240,410원 ↔ 대출현황 '○○은행 · 마이너스 통장' 240,410원
    함께 빼기를 권하는 데만 쓴다. (사람이 확인하고 고른다)
    """
    other = COUNTERPART.get(target.section)
    if other is None or not target.amount:
        return []
    return [h for h in holdings if h.section == other and h.amount == target.amount]
