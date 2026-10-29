"""가계부 API (F4a): 정산된 달 · 월 요약 · 거래 목록·상세·수정 · 확인 필요 처리.

달을 비우면 지난달 (매달 초에 지난달을 정산하므로).
분류 결과는 저장하지 않고 요청마다 계산한다 (ledger/classify.py).
거래는 (주인 slug, 거래키)로 가리킨다. 거래키는 다시 정산해도 바뀌지 않는다.
확인 필요 처리(PUT .../class)는 그 거래를 직접 지정한다.
    remember를 켜면 같은 사람 · 같은 방향 · 같은 내용의 거래에 쓸 규칙도 함께 만든다.
"""

from __future__ import annotations

import datetime as dt
import math
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from hanjigap.db import class_rules, tx_edits
from hanjigap.db.members import Member, MemberError, get_member
from hanjigap.db.validate import InputError, code, year_month
from hanjigap.ledger.classify import CLASSES, SOURCES, ClassifiedTx, ClassReport, classify
from hanjigap.web.class_rules_api import rule_out
from hanjigap.web.common import member_slugs, today
from hanjigap.web.deps import CurrentMember, Db

router = APIRouter(prefix="/api", tags=["ledger"])

PAGE_SIZE = 50
SETTLEMENT_LABELS = {"settled": "정산됨", "baseline": "기준 데이터", None: "정산 전"}


class TxEditIn(BaseModel):
    category: str | None = None
    subcategory: str | None = None
    content: str | None = None
    note: str | None = None


class ClassIn(BaseModel):
    result: str  # income | expense | saving | excluded (한글 이름도 받음)
    result_category: str | None = None  # 지출 차감이면 지출 분류 (예: 식비)
    remember: bool = False  # 앞으로도 이렇게


# --- 도우미 ---------------------------------------------------------------------


def month_or_last(request: Request, month: str | None) -> str:
    """달을 비우면 지난달 (매달 초에 지난달을 정산하므로)."""
    if month is None or not month.strip():
        first = today(request).replace(day=1)
        last = first - dt.timedelta(days=1)
        return f"{last.year:04d}-{last.month:02d}"
    ym = year_month(month, "월")
    assert ym is not None
    return ym


def _prev(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) - 1
    return f"{y - (m == 0):04d}-{(m - 1) % 12 + 1:02d}"


def _owner(conn: sqlite3.Connection, owner: str | None) -> Member | None:
    return get_member(conn, owner) if owner else None


def _path_member(conn: sqlite3.Connection, slug: str) -> Member:
    try:
        return get_member(conn, slug)
    except MemberError as exc:
        raise HTTPException(404, str(exc)) from None


def edit_notes(conn: sqlite3.Connection) -> dict[tuple[int, str], str | None]:
    """웹에서 고친 거래 → 한지갑 메모"""
    rows = conn.execute("SELECT member_id, tx_key, note FROM tx_overrides")
    return {(r["member_id"], r["tx_key"]): r["note"] for r in rows}


def tx_out(i: ClassifiedTx, slugs: dict[int, str], edits: dict) -> dict:
    key = (i.member_id, i.tx_key)
    return {
        "owner": slugs[i.member_id],
        "tx_key": i.tx_key,
        "date": i.date,
        "time": i.time,
        "content": i.content,
        "amount": i.amount,
        "instrument": i.instrument,
        "tx_type": i.tx_type,
        "category": i.category,
        "subcategory": i.subcategory,
        "cls": i.cls,
        "class_label": i.class_label,
        "display_category": i.display_category,
        "source": i.source,
        "source_label": SOURCES[i.source],
        "reason": i.reason,
        "rule_id": i.rule_id,
        "suggestion": i.suggestion,
        "suggestion_label": CLASSES[i.suggestion] if i.suggestion else None,
        "pair_hint": i.pair_hint,
        "edited": key in edits,
        "note": edits.get(key),
    }


def _categories(totals: dict[str, int]) -> list[dict]:
    return [{"category": k, "amount": v} for k, v in totals.items()]


def _subcategories(report: ClassReport, cls: str) -> dict[str, list[dict]]:
    """보여줄 분류 → 뱅샐 소분류별 합계 (큰 순). 분류를 눌러 펼칠 때 쓴다."""
    out: dict[str, dict[str, int]] = {}
    for i in report.of(cls):
        subs = out.setdefault(i.display_category or "미분류", {})
        key = i.subcategory or "미분류"
        subs[key] = subs.get(key, 0) + i.value
    return {
        cat: [
            {"subcategory": k, "amount": v}
            for k, v in sorted(subs.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        for cat, subs in out.items()
    }


def _find(conn: sqlite3.Connection, member: Member, tx_key: str) -> ClassifiedTx:
    row = conn.execute(
        "SELECT year_month FROM transactions WHERE member_id = ? AND tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "거래를 찾을 수 없어요")
    report = classify(conn, row["year_month"], member=member)
    return next(i for i in report.items if i.tx_key == tx_key)


def _detail(conn: sqlite3.Connection, member: Member, tx_key: str) -> dict:
    item = _find(conn, member, tx_key)
    slugs = member_slugs(conn)
    out = tx_out(item, slugs, edit_notes(conn))
    original = conn.execute(
        "SELECT tx_type, category, subcategory, content, memo FROM transactions "
        "WHERE member_id = ? AND tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    out["original"] = dict(original)
    edit = tx_edits.get_edit(conn, member, tx_key)
    out["edit"] = (
        None
        if edit is None
        else {
            "category": edit.category,
            "subcategory": edit.subcategory,
            "content": edit.content,
            "note": edit.note,
            "edited_by": slugs.get(edit.edited_by),
            "edited_at": edit.edited_at,
        }
    )
    manual = conn.execute(
        "SELECT result, result_category, classified_by, classified_at FROM tx_classes "
        "WHERE member_id = ? AND tx_key = ?",
        (member.id, tx_key),
    ).fetchone()
    out["manual_class"] = (
        None
        if manual is None
        else {
            "result": manual["result"],
            "result_category": manual["result_category"],
            "classified_by": slugs.get(manual["classified_by"]),
            "classified_at": manual["classified_at"],
        }
    )
    return out


# --- 달 · 요약 -------------------------------------------------------------------


@router.get("/months")
def months(conn: Db, _me: CurrentMember) -> dict:
    """정산된 달 (최근 달부터). 사람마다 정산 상태와 거래 수."""
    slugs = member_slugs(conn)
    by_month: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT year_month, member_id, status, tx_count FROM month_settlements "
        "ORDER BY year_month DESC, member_id"
    ):
        by_month.setdefault(r["year_month"], []).append(
            {
                "owner": slugs[r["member_id"]],
                "status": r["status"],
                "label": SETTLEMENT_LABELS[r["status"]],
                "tx_count": r["tx_count"],
            }
        )
    return {"items": [{"month": ym, "members": ms} for ym, ms in by_month.items()]}


def _summary(report: ClassReport) -> dict:
    return {
        "income": report.income,
        "expense": report.expense,
        "saving": report.saving,
        "net": report.net,
    }


@router.get("/summary")
def summary(
    request: Request,
    conn: Db,
    _me: CurrentMember,
    month: str | None = None,
    owner: str | None = None,
) -> dict:
    ym = month_or_last(request, month)
    member = _owner(conn, owner)
    report = classify(conn, ym, member=member)
    prev = classify(conn, _prev(ym), member=member)
    slugs = member_slugs(conn)
    saving_out, saving_back = report.saving_flow()
    in_n, in_amt, out_n, out_amt = report.unconfirmed_flow()
    settled = {
        r["member_id"]: r["status"]
        for r in conn.execute(
            "SELECT member_id, status FROM month_settlements WHERE year_month = ?", (ym,)
        )
    }
    people = [member.id] if member else list(slugs)
    by_member = report.by_member()
    return {
        "month": ym,
        "owner": member.slug if member else None,
        **_summary(report),
        "counts": report.counts(),
        "saving_flow": {"out": saving_out, "back": saving_back},
        "unconfirmed": {
            "count": in_n + out_n,
            "in_count": in_n,
            "in_amount": in_amt,
            "out_count": out_n,
            "out_amount": out_amt,
        },
        "expense_by_category": _categories(report.by_category("expense")),
        "income_by_category": _categories(report.by_category("income")),
        "expense_by_subcategory": _subcategories(report, "expense"),
        "income_by_subcategory": _subcategories(report, "income"),
        "by_member": [
            {"owner": slugs[mid], **by_member.get(mid, {"income": 0, "expense": 0, "saving": 0})}
            for mid in people
        ],
        "previous": {
            "month": prev.start_month,
            **_summary(prev),
            "expense_diff": report.expense - prev.expense,
        },
        "settlement": [
            {
                "owner": slugs[mid],
                "status": settled.get(mid),
                "label": SETTLEMENT_LABELS[settled.get(mid)],
            }
            for mid in people
        ],
    }


TREND_MAX = 24


@router.get("/trend")
def trend(
    request: Request,
    conn: Db,
    _me: CurrentMember,
    month: str | None = None,
    owner: str | None = None,
    months: int = 12,
) -> dict:
    """month(비우면 지난달)까지 최근 months개월의 수입·지출·저축·투자 (막대그래프용)."""
    if not 1 <= months <= TREND_MAX:
        raise InputError(f"개월 수는 1~{TREND_MAX} 사이예요")
    end = month_or_last(request, month)
    member = _owner(conn, owner)
    settled = {r[0] for r in conn.execute("SELECT DISTINCT year_month FROM month_settlements")}
    items = []
    ym = end
    for _ in range(months):
        report = classify(conn, ym, member=member)
        items.append(
            {
                "month": ym,
                "income": report.income,
                "expense": report.expense,
                "saving": report.saving,
                "settled": ym in settled,
            }
        )
        ym = _prev(ym)
    return {"items": list(reversed(items))}


# --- 거래 ----------------------------------------------------------------------


@router.get("/transactions")
def list_transactions(
    request: Request,
    conn: Db,
    _me: CurrentMember,
    month: str | None = None,
    owner: str | None = None,
    cls: str | None = None,
    category: str | None = None,
    q: str | None = None,
    page: int = 1,
) -> dict:
    """최신순 50건씩. cls(한지갑 분류), category(보여줄 분류), q(내용·메모 검색)로 거르기."""
    ym = month_or_last(request, month)
    report = classify(conn, ym, member=_owner(conn, owner))
    edits = edit_notes(conn)
    items = report.items
    if cls:
        wanted = code(cls, CLASSES, "분류를")
        items = [i for i in items if i.cls == wanted]
    if category:
        items = [i for i in items if i.display_category == category]
    if q and q.strip():
        needle = q.strip().lower()
        items = [
            i
            for i in items
            if needle in i.content.lower()
            or needle in (edits.get((i.member_id, i.tx_key)) or "").lower()
        ]
    items = sorted(items, key=lambda i: (i.date, i.time), reverse=True)
    pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    if page < 1:
        raise InputError("페이지는 1부터예요")
    start = (page - 1) * PAGE_SIZE
    slugs = member_slugs(conn)
    return {
        "month": ym,
        "total": len(items),
        "page": page,
        "pages": pages,
        "class_counts": report.counts(),
        "items": [tx_out(i, slugs, edits) for i in items[start : start + PAGE_SIZE]],
    }


@router.get("/transactions/{owner}/{tx_key}")
def get_transaction(owner: str, tx_key: str, conn: Db, _me: CurrentMember) -> dict:
    return _detail(conn, _path_member(conn, owner), tx_key)


@router.patch("/transactions/{owner}/{tx_key}")
def edit_transaction(owner: str, tx_key: str, body: TxEditIn, conn: Db, me: CurrentMember) -> dict:
    member = _path_member(conn, owner)
    _find(conn, member, tx_key)
    tx_edits.edit_tx(conn, member, tx_key, edited_by=me, **body.model_dump(exclude_unset=True))
    return _detail(conn, member, tx_key)


@router.delete("/transactions/{owner}/{tx_key}/edit")
def clear_transaction_edit(owner: str, tx_key: str, conn: Db, _me: CurrentMember) -> dict:
    member = _path_member(conn, owner)
    _find(conn, member, tx_key)
    tx_edits.clear_edit(conn, member, tx_key)
    return _detail(conn, member, tx_key)


@router.put("/transactions/{owner}/{tx_key}/class")
def set_class(owner: str, tx_key: str, body: ClassIn, conn: Db, me: CurrentMember) -> dict:
    member = _path_member(conn, owner)
    _find(conn, member, tx_key)
    class_rules.set_tx_class(
        conn,
        member,
        tx_key,
        body.result,
        result_category=body.result_category,
        classified_by=me,
    )
    rule = None
    if body.remember:
        rule = class_rules.rule_for_tx(
            conn,
            member,
            tx_key,
            result=body.result,
            result_category=body.result_category,
            created_by=me,
        )
    return {
        "transaction": _detail(conn, member, tx_key),
        "rule": None if rule is None else rule_out(rule, member_slugs(conn)),
    }


@router.delete("/transactions/{owner}/{tx_key}/class")
def clear_class(owner: str, tx_key: str, conn: Db, _me: CurrentMember) -> dict:
    member = _path_member(conn, owner)
    _find(conn, member, tx_key)
    class_rules.clear_tx_class(conn, member, tx_key)
    return _detail(conn, member, tx_key)
