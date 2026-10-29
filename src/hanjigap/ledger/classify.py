"""수입·지출·저축·제외 분류와 월 요약 (J7a).

판단 순서 (위가 먼저). 결과는 저장하지 않는다.
    1. 거래별 직접 지정 (tx_classes)
    2. 고정비 '용돈' 항목과 맞춰진 거래 → 지출 › 용돈 (J7b에서 추가)
    3. 규칙 (class_rules, position 순)
    4. 뱅샐 기본값
        지출 → 지출 (환불·취소는 그 분류 지출에서 빠진다)
        수입 → 수입
        이체 › 카드대금 · 내계좌이체 → 제외
        이체 › 저축 · 투자 → 저축·투자 (나간 돈은 더하고 돌아온 돈은 뺀다)
        이체 › 현금 → 나간 돈은 지출(현금), 들어온 돈은 제외
        그 밖의 이체 → 확인 필요
웹에서 고친 타입·분류·내용(tx_overrides)이 있으면 그 값으로 판단한다.

금액 부호 (요약)
    수입 = 들어온 돈 합계 (돌려준 돈은 뺀다)
    지출 = 나간 돈 합계 − 들어온 돈 (환불·정산 입금)
    저축·투자 = 나간 돈 − 돌아온 돈
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from hanjigap.db import validate
from hanjigap.db.class_rules import RESULTS, ClassRule, list_rules, tx_classes
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError

CLASSES = {**RESULTS, "unconfirmed": "확인 필요"}
SOURCES = {"manual": "직접 지정", "rule": "규칙", "default": "뱅샐 기본값"}
EXCLUDED_TRANSFERS = frozenset({"카드대금", "내계좌이체"})
SAVING_TRANSFERS = frozenset({"저축", "투자"})
CASH = "현금"


@dataclass(frozen=True)
class ClassifiedTx:
    member_id: int
    tx_key: str
    date: str
    time: str
    tx_type: str
    category: str
    subcategory: str
    content: str
    instrument: str
    amount: int  # 뱅샐 금액 그대로 (들어온 돈 +, 나간 돈 −)
    cls: str  # income | expense | saving | excluded | unconfirmed
    display_category: str  # 화면에 보여줄 분류
    source: str  # manual | rule | default
    reason: str
    rule_id: int | None = None

    @property
    def class_label(self) -> str:
        return CLASSES[self.cls]

    @property
    def suggestion(self) -> str | None:
        """확인 필요 거래의 추천: 나간 돈은 지출, 들어온 돈은 수입"""
        if self.cls != "unconfirmed":
            return None
        return "expense" if self.amount < 0 else "income"

    @property
    def value(self) -> int:
        """요약에 더하는 값 (수입은 +가 늘어남, 지출·저축은 나간 돈이 +)"""
        return self.amount if self.cls == "income" else -self.amount


@dataclass
class ClassReport:
    start_month: str
    end_month: str
    items: list[ClassifiedTx] = field(default_factory=list)

    def of(self, cls: str) -> list[ClassifiedTx]:
        return [i for i in self.items if i.cls == cls]

    def total(self, cls: str) -> int:
        """수입·지출·저축·투자 합계. 제외·확인 필요는 합계를 내지 않는다(0)."""
        if cls in ("excluded", "unconfirmed"):
            return 0
        return sum(i.value for i in self.of(cls))

    @property
    def income(self) -> int:
        return self.total("income")

    @property
    def expense(self) -> int:
        return self.total("expense")

    @property
    def saving(self) -> int:
        return self.total("saving")

    @property
    def net(self) -> int:
        """수입 − 지출 − 저축·투자 (이번 달 남은 돈)"""
        return self.income - self.expense - self.saving

    def counts(self) -> dict[str, int]:
        out = {c: 0 for c in CLASSES}
        for i in self.items:
            out[i.cls] += 1
        return out

    def by_category(self, cls: str) -> dict[str, int]:
        """보여줄 분류별 합계, 큰 순"""
        out: dict[str, int] = defaultdict(int)
        for i in self.of(cls):
            out[i.display_category or "미분류"] += i.value
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    def by_member(self) -> dict[int, dict[str, int]]:
        """구성원별 수입·지출·저축·투자"""
        out: dict[int, dict[str, int]] = defaultdict(
            lambda: {"income": 0, "expense": 0, "saving": 0}
        )
        for i in self.items:
            if i.cls in ("income", "expense", "saving"):
                out[i.member_id][i.cls] += i.value
        return dict(out)

    def saving_flow(self) -> tuple[int, int]:
        """(저축·투자로 나간 돈, 돌아온 돈)"""
        items = self.of("saving")
        return (
            -sum(i.amount for i in items if i.amount < 0),
            sum(i.amount for i in items if i.amount > 0),
        )

    def unconfirmed_flow(self) -> tuple[int, int, int, int]:
        """확인 필요: (들어온 건수, 들어온 돈, 나간 건수, 나간 돈)"""
        items = self.of("unconfirmed")
        ins = [i.amount for i in items if i.amount > 0]
        outs = [-i.amount for i in items if i.amount < 0]
        return len(ins), sum(ins), len(outs), sum(outs)


def default_class(tx: dict) -> tuple[str, str, str]:
    """뱅샐 기본값: (분류, 보여줄 분류, 이유)"""
    kind, category, amount = tx["tx_type"], tx["category"] or "", tx["amount"]
    if kind == "지출":
        return "expense", category, "뱅샐 지출"
    if kind == "수입":
        return "income", category, "뱅샐 수입"
    if category in EXCLUDED_TRANSFERS:
        return "excluded", category, f"뱅샐 이체 › {category}"
    if category in SAVING_TRANSFERS:
        return "saving", category, f"뱅샐 이체 › {category}"
    if category == CASH:
        if amount < 0:
            return "expense", CASH, "현금 출금"
        return "excluded", CASH, "현금 입금"
    return "unconfirmed", category, f"뱅샐 이체 › {category or '미분류'}"


def classify(
    conn: sqlite3.Connection,
    start_month: str,
    end_month: str | None = None,
    *,
    member: Member | None = None,
    rules: list[ClassRule] | None = None,
) -> ClassReport:
    """그 기간 거래를 분류한다. member를 주면 그 사람 거래만. rules를 주면 DB 규칙 대신."""
    start = validate.year_month(start_month, "시작 월")
    end = validate.year_month(end_month or start_month, "끝 월")
    if start is None or end is None or start > end:
        raise InputError("기간을 YYYY-MM ~ YYYY-MM 으로 입력하세요")
    active = [r for r in (list_rules(conn) if rules is None else rules) if r.active]
    manual = tx_classes(conn)
    sql = """
        SELECT t.member_id, t.tx_key, t.tx_date, t.tx_time, t.amount, t.instrument,
               coalesce(o.tx_type, t.tx_type)         AS tx_type,
               coalesce(o.category, t.category)       AS category,
               coalesce(o.subcategory, t.subcategory) AS subcategory,
               coalesce(o.content, t.content)         AS content
        FROM transactions t
        LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key
        WHERE t.year_month BETWEEN ? AND ?
    """
    params: list = [start, end]
    if member is not None:
        sql += " AND t.member_id = ?"
        params.append(member.id)
    report = ClassReport(start, end)
    for r in conn.execute(sql + " ORDER BY t.tx_date, t.tx_time, t.id", params):
        report.items.append(_classify_one(dict(r), manual, active))
    return report


def _classify_one(
    tx: dict,
    manual: dict[tuple[int, str], tuple[str, str | None]],
    rules: list[ClassRule],
) -> ClassifiedTx:
    rule_id = None
    chosen = manual.get((tx["member_id"], tx["tx_key"]))
    if chosen is not None:
        cls, category, source, reason = chosen[0], chosen[1], "manual", "직접 지정"
    else:
        rule = next((r for r in rules if r.matches(tx)), None)
        if rule is not None:
            cls, category, source = rule.result, rule.result_category, "rule"
            reason, rule_id = f"규칙: {rule.label}", rule.id
        else:
            cls, category, reason = default_class(tx)
            source = "default"
    if not category:
        category = tx["category"] or ""
    return ClassifiedTx(
        member_id=tx["member_id"],
        tx_key=tx["tx_key"],
        date=tx["tx_date"],
        time=tx["tx_time"],
        tx_type=tx["tx_type"],
        category=tx["category"] or "",
        subcategory=tx["subcategory"] or "",
        content=tx["content"] or "",
        instrument=tx["instrument"] or "",
        amount=tx["amount"],
        cls=cls,
        display_category=category,
        source=source,
        reason=reason,
        rule_id=rule_id,
    )
