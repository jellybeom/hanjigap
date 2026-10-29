"""수입·지출·저축·제외 분류와 월 요약 (J7).

판단 순서 (위가 먼저). 결과는 저장하지 않는다.
    1. 거래별 직접 지정 (tx_classes)
    2. 고정비 '용돈' 항목과 맞춰진 거래 → 지출 › 용돈
       (뱅샐에서 내계좌이체로 찍혀도. 용돈 계좌는 뱅샐에 연결하지 않으므로)
    3. 규칙 (class_rules, position 순)
    4. 부부 이체: 뱅샐 '이체'이고 내용에 다른 구성원의 이름·별칭이 있으면 → 제외
    5. 뱅샐 기본값
        지출 → 지출 (환불·취소는 그 분류 지출에서 빠진다)
        수입 → 수입
        이체 › 카드대금 · 내계좌이체 → 제외
        이체 › 저축 · 투자 → 저축·투자 (나간 돈은 더하고 돌아온 돈은 뺀다)
        이체 › 현금 → 나간 돈은 지출(현금), 들어온 돈은 제외
        그 밖의 이체 → 확인 필요
웹에서 고친 타입·분류·내용(tx_overrides)이 있으면 그 값으로 판단한다.

부부 이체 같아요 (추천만, 자동으로 정하지 않음)
    확인 필요 이체 중에서 다른 구성원에게 하루 차이 안에 같은 금액의 반대 방향 이체가 있으면
    추천을 '제외'로 바꾸고 pair_hint를 켠다.

금액 부호 (요약)
    수입 = 들어온 돈 합계 (돌려준 돈은 뺀다)
    지출 = 나간 돈 합계 − 들어온 돈 (환불·정산 입금)
    저축·투자 = 나간 돈 − 돌아온 돈
"""

from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field, replace

from hanjigap.db import validate
from hanjigap.db.class_rules import RESULTS, ClassRule, list_rules, tx_classes
from hanjigap.db.members import Member, member_names
from hanjigap.db.validate import InputError
from hanjigap.fixed.status import month_status

CLASSES = {**RESULTS, "unconfirmed": "확인 필요"}
SOURCES = {
    "manual": "직접 지정",
    "fixed": "고정비 용돈",
    "rule": "규칙",
    "spouse": "부부 이체",
    "default": "뱅샐 기본값",
}
ALLOWANCE = "allowance"  # 고정비 분류 '용돈'
ALLOWANCE_LABEL = "용돈"
SPOUSE_LABEL = "부부 이체"
PAIR_DAYS = 1
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
    source: str  # manual | fixed | rule | spouse | default
    reason: str
    rule_id: int | None = None
    pair_hint: bool = False  # 부부 이체 같아요

    @property
    def class_label(self) -> str:
        return CLASSES[self.cls]

    @property
    def suggestion(self) -> str | None:
        """확인 필요 거래의 추천: 부부 이체 같으면 제외, 아니면 나간 돈은 지출, 들어온 돈은 수입"""
        if self.cls != "unconfirmed":
            return None
        if self.pair_hint:
            return "excluded"
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

    def sources(self) -> dict[str, int]:
        """어떻게 정해졌는지별 건수"""
        out = {k: 0 for k in SOURCES}
        for i in self.items:
            out[i.source] += 1
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
    ctx = _Context(
        manual=tx_classes(conn),
        allowance=_allowance_txs(conn, start, end, member),
        rules=[r for r in (list_rules(conn) if rules is None else rules) if r.active],
        partner_names=_partner_names(conn),
    )
    sql = _TX_SQL + " WHERE t.year_month BETWEEN ? AND ?"
    params: list = [start, end]
    if member is not None:
        sql += " AND t.member_id = ?"
        params.append(member.id)
    report = ClassReport(start, end)
    for r in conn.execute(sql + " ORDER BY t.tx_date, t.tx_time, t.id", params):
        report.items.append(_classify_one(dict(r), ctx))
    _mark_pairs(conn, report)
    return report


_TX_SQL = """
    SELECT t.member_id, t.tx_key, t.tx_date, t.tx_time, t.amount, t.instrument,
           coalesce(o.tx_type, t.tx_type)         AS tx_type,
           coalesce(o.category, t.category)       AS category,
           coalesce(o.subcategory, t.subcategory) AS subcategory,
           coalesce(o.content, t.content)         AS content
    FROM transactions t
    LEFT JOIN tx_overrides o ON o.member_id = t.member_id AND o.tx_key = t.tx_key
"""


@dataclass
class _Context:
    manual: dict[tuple[int, str], tuple[str, str | None]]
    allowance: dict[tuple[int, str], str]  # (구성원, 거래) → 고정비 이름
    rules: list[ClassRule]
    partner_names: dict[int, list[str]]  # 구성원 → 다른 구성원들의 이름·별칭 (소문자)


def _classify_one(tx: dict, ctx: _Context) -> ClassifiedTx:
    key = (tx["member_id"], tx["tx_key"])
    rule_id = None
    chosen = ctx.manual.get(key)
    if chosen is not None:
        cls, category, source, reason = chosen[0], chosen[1], "manual", "직접 지정"
    elif key in ctx.allowance:
        cls, category, source = "expense", ALLOWANCE_LABEL, "fixed"
        reason = f"고정비 용돈: {ctx.allowance[key]}"
    else:
        rule = next((r for r in ctx.rules if r.matches(tx)), None)
        partner = _partner_in(tx, ctx.partner_names)
        if rule is not None:
            cls, category, source = rule.result, rule.result_category, "rule"
            reason, rule_id = f"규칙: {rule.label}", rule.id
        elif partner is not None:
            cls, category, source = "excluded", SPOUSE_LABEL, "spouse"
            reason = f"부부 이체: 「{partner}」"
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


def _partner_in(tx: dict, partner_names: dict[int, list[str]]) -> str | None:
    """부부 이체면 찾은 이름. 뱅샐 '이체'만 본다."""
    if tx["tx_type"] != "이체":
        return None
    content = (tx["content"] or "").lower()
    return next((n for n in partner_names.get(tx["member_id"], []) if n in content), None)


def _partner_names(conn: sqlite3.Connection) -> dict[int, list[str]]:
    names = member_names(conn)
    return {
        member_id: [n.lower() for other, ns in names.items() if other != member_id for n in ns]
        for member_id in names
    }


def _allowance_txs(
    conn: sqlite3.Connection, start: str, end: str, member: Member | None
) -> dict[tuple[int, str], str]:
    """고정비 '용돈' 항목과 맞춰진 거래. 거래 맞추기는 고정비 상태 계산을 그대로 쓴다."""
    if not conn.execute(
        "SELECT 1 FROM fixed_items WHERE category = ? LIMIT 1", (ALLOWANCE,)
    ).fetchone():
        return {}
    out: dict[tuple[int, str], str] = {}
    today = dt.date.today()  # 상태(예정·안 빠져나감)는 쓰지 않으므로 아무 날이어도 된다
    ym = start
    while ym <= end:
        for st in month_status(conn, ym, member=member, today=today).statuses:
            if st.item.category == ALLOWANCE and st.tx is not None:
                out[(st.item.member_id, st.tx.tx_key)] = st.item.name
        y, m = int(ym[:4]), int(ym[5:7]) + 1
        ym = f"{y + (m > 12):04d}-{(m - 1) % 12 + 1:02d}"
    return out


def _mark_pairs(conn: sqlite3.Connection, report: ClassReport) -> None:
    """확인 필요 이체 중 부부 이체 같은 것에 표시. 상대 거래는 기간 밖(하루 차이)도 본다."""
    targets = [
        n for n, i in enumerate(report.items) if i.cls == "unconfirmed" and i.tx_type == "이체"
    ]
    if not targets:
        return
    lo = dt.date.fromisoformat(report.start_month + "-01") - dt.timedelta(days=PAIR_DAYS)
    y, m = int(report.end_month[:4]), int(report.end_month[5:7])
    month_end = dt.date(y, m, calendar.monthrange(y, m)[1])
    hi = month_end + dt.timedelta(days=PAIR_DAYS)
    by_amount: dict[int, list[tuple[int, dt.date]]] = defaultdict(list)
    for r in conn.execute(
        _TX_SQL + " WHERE t.tx_date BETWEEN ? AND ?", (lo.isoformat(), hi.isoformat())
    ):
        if r["tx_type"] == "이체":
            by_amount[r["amount"]].append((r["member_id"], dt.date.fromisoformat(r["tx_date"])))
    for n in targets:
        item = report.items[n]
        day = dt.date.fromisoformat(item.date)
        for other, other_day in by_amount.get(-item.amount, []):
            if other != item.member_id and abs((other_day - day).days) <= PAIR_DAYS:
                report.items[n] = replace(item, pair_hint=True)
                break
