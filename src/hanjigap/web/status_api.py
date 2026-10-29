"""현황 API (F4b): 자산 현황 · 고정비 월 상태 · 메인 화면 묶음.

자산: 사람별 뱅샐 재무현황 + 한지갑 차·집 값 (같은 자산을 두 번 세지 않음, ledger/assets.py)
고정비 상태: 그달 고정비를 거래와 맞춰 본 결과 (fixed/status.py)
메인 화면: 월 요약 · 확인 필요 · 고정비 상태 개수 · 합산 순자산 · 알림을 한 번에
달을 비우면 지난달. 자산은 달을 비우면 사람마다 가장 최근 기록.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from fastapi import APIRouter, Request

from hanjigap.db import estates, vehicles
from hanjigap.db.members import get_member
from hanjigap.db.validate import year_month
from hanjigap.fixed.status import STATUS_LABELS, month_status
from hanjigap.ledger.assets import AssetOverview, MemberAssets, asset_overview
from hanjigap.ledger.classify import classify
from hanjigap.web.common import member_slugs, today
from hanjigap.web.deps import CurrentMember, Db
from hanjigap.web.fixed_api import fixed_out
from hanjigap.web.ledger_api import SETTLEMENT_LABELS, edit_notes, month_or_last, tx_out

router = APIRouter(prefix="/api", tags=["status"])

INSPECTION_ALERT_DAYS = 30
RENEWAL_ALERT_STAGES = frozenset({"notice", "closing", "overdue"})


# --- 자산 ----------------------------------------------------------------------


def _member_assets_out(m: MemberAssets) -> dict:
    s = m.snapshot
    snapshot = None
    if s is not None:
        groups: dict[tuple[str, str], dict] = {}
        for h in s.holdings:
            key = (h.section, h.group or "")
            g = groups.setdefault(
                key, {"section": h.section, "group": h.group, "amount": 0, "count": 0}
            )
            g["amount"] += h.amount or 0
            g["count"] += 1
        snapshot = {
            "as_of": s.as_of,
            "year_month": s.year_month,
            "total_assets": s.total_assets,
            "total_liabilities": s.total_liabilities,
            "net_worth": s.net_worth,
            "checks_ok": s.checks_ok,
            "groups": list(groups.values()),
            "items": [
                {
                    "section": h.section,
                    "group": h.group,
                    "institution": h.institution,
                    "name": h.name,
                    "amount": h.amount,
                    "principal": h.principal,
                    "rate": h.rate,
                    "status": h.status,
                    "start_date": h.start_date,
                    "end_date": h.end_date,
                }
                for h in s.holdings
            ],
        }
    return {
        "owner": m.member.slug,
        "name": m.member.display_name,
        "banksalad": snapshot,
        "vehicles": [
            {
                "id": v.vehicle.id,
                "name": v.vehicle.name,
                "value": v.value.value,
                "formula": v.value.formula,
                "included": v.included,
                "note": None if v.included else "뱅샐 값 사용",
            }
            for v in m.vehicles
        ],
        "estates": [
            {
                "id": e.prop.id,
                "name": e.prop.name,
                "value": e.value.value,
                "label": e.value.label,
                "included": e.included,
                "note": None if e.included else "뱅샐 값 사용",
            }
            for e in m.estates
        ],
        "banksalad_net": m.banksalad_net,
        "added": m.added,
        "net_worth": m.net_worth,
        "value_month": m.value_month,
    }


def _assets_out(o: AssetOverview, picked: list[MemberAssets]) -> dict:
    return {
        "value_month": o.month,
        "net_worth": sum(m.net_worth for m in picked),
        "banksalad_net": sum(m.banksalad_net for m in picked),
        "added": sum(m.added for m in picked),
        "members": [_member_assets_out(m) for m in picked],
    }


def _snapshot_months(conn: sqlite3.Connection, member) -> list[str]:
    sql = "SELECT DISTINCT year_month FROM bs_snapshots"
    params: tuple = ()
    if member is not None:
        sql += " WHERE member_id = ?"
        params = (member.id,)
    return [r[0] for r in conn.execute(sql + " ORDER BY year_month", params)]


@router.get("/assets")
def assets(
    request: Request,
    conn: Db,
    _me: CurrentMember,
    month: str | None = None,
    owner: str | None = None,
) -> dict:
    """자산 현황. month를 비우면 사람마다 가장 최근 기록. owner를 주면 그 사람만.

    months: 뱅샐 재무현황이 있는 달 (달 고르기용)
    trend: 기준 달(month, 없으면 기록이 있는 가장 최근 달)까지 최근 12개월 순자산
    breakdown: 홈 화면과 같은 투자·대출·현금·보험 묶음
    """
    ym = year_month(month, "월") if month else None
    member = get_member(conn, owner) if owner else None
    this_month = today(request).isoformat()[:7]
    overview = asset_overview(conn, ym, this_month)
    picked = _pick(overview, member)
    months = _snapshot_months(conn, member)
    end = ym or (months[-1] if months else month_or_last(request, None))
    trend = _trend(conn, end, this_month, member)
    return {
        **_assets_out(overview, picked),
        "month": ym,
        "owner": member.slug if member else None,
        "months": months,
        "trend_end": end,
        "trend": trend,
        "previous_net_worth": trend[-2]["net_worth"],
        "breakdown": _breakdown(picked),
    }


# --- 고정비 월 상태 ---------------------------------------------------------------


def _fixed_status(conn: sqlite3.Connection, request: Request, ym: str, owner: str | None) -> dict:
    member = get_member(conn, owner) if owner else None
    report = month_status(conn, ym, member=member, today=today(request))
    slugs = member_slugs(conn)
    this_month = today(request).isoformat()[:7]
    counts = report.counts
    return {
        "month": report.year_month,
        "counts": {key: counts.get(key, 0) for key in STATUS_LABELS},
        "total": report.total(),
        "total_living": report.total("living"),
        "total_saving": report.total("saving"),
        "items": [
            {
                "item": fixed_out(conn, s.item, slugs, this_month),
                "expected_date": s.expected_date,
                "status": s.status,
                "status_label": s.label,
                "amount_gap": s.amount_gap,
                "tx": (
                    None
                    if s.tx is None
                    else {
                        "tx_key": s.tx.tx_key,
                        "date": s.tx.date,
                        "amount": s.tx.amount,
                        "content": s.tx.content,
                    }
                ),
            }
            for s in report.statuses
        ],
    }


@router.get("/fixed-status")
def fixed_status(
    request: Request,
    conn: Db,
    _me: CurrentMember,
    month: str | None = None,
    owner: str | None = None,
) -> dict:
    return _fixed_status(conn, request, month_or_last(request, month), owner)


# --- 메인 화면 -------------------------------------------------------------------


def _alerts(conn: sqlite3.Connection, request: Request, ym: str, report, fixed: dict) -> list[dict]:
    alerts = []
    day = today(request)
    slugs = member_slugs(conn)
    names = {m: n for m, n in conn.execute("SELECT id, display_name FROM members")}
    settled = {
        r[0]
        for r in conn.execute("SELECT member_id FROM month_settlements WHERE year_month = ?", (ym,))
    }
    for mid in slugs:
        if mid not in settled:
            alerts.append(
                {
                    "kind": "not_settled",
                    "owner": slugs[mid],
                    "message": f"{names[mid]} {int(ym[5:])}월 {SETTLEMENT_LABELS[None]}",
                }
            )
    unconfirmed = report.counts()["unconfirmed"]
    if unconfirmed:
        alerts.append(
            {
                "kind": "unconfirmed",
                "count": unconfirmed,
                "message": f"확인 필요 거래 {unconfirmed}건",
            }
        )
    for key in ("missed", "different"):
        n = fixed["counts"][key]
        if n:
            alerts.append(
                {
                    "kind": f"fixed_{key}",
                    "count": n,
                    "message": f"고정비 {STATUS_LABELS[key]} {n}건",
                }
            )
    for category, amount in report.by_category("expense").items():
        if amount < 0:
            alerts.append(
                {
                    "kind": "negative_category",
                    "category": category,
                    "message": f"{category} 지출이 마이너스예요 (환불·입금이 더 많음)",
                }
            )
    for p in estates.list_properties(conn):
        c = estates.open_contract(conn, p.id)
        info = estates.renewal_info(c, day) if c else None
        if info and info.stage in RENEWAL_ALERT_STAGES:
            alerts.append(
                {
                    "kind": "renewal",
                    "property_id": p.id,
                    "stage": info.stage,
                    "message": f"{p.name} 계약 만기 {info.days_left}일 남음 · {info.stage_label}",
                }
            )
    for v in vehicles.list_vehicles(conn):
        if v.inspection_due_on:
            left = (dt.date.fromisoformat(v.inspection_due_on) - day).days
            if left <= INSPECTION_ALERT_DAYS:
                alerts.append(
                    {
                        "kind": "inspection",
                        "vehicle_id": v.id,
                        "days_left": left,
                        "message": (
                            f"{v.name} 정기검사 {left}일 남음"
                            if left >= 0
                            else f"{v.name} 정기검사 {-left}일 지남"
                        ),
                    }
                )
    return alerts


CASH_WORDS = ("입출금", "현금", "예금", "적금", "저축", "파킹", "CMA")  # 뱅샐 자산 묶음 이름
TREND_MONTHS = 12


def _shift(ym: str, delta: int) -> str:
    total = int(ym[:4]) * 12 + int(ym[5:7]) - 1 + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _pick(overview: AssetOverview, member) -> list[MemberAssets]:
    return [m for m in overview.members if member is None or m.member.id == member.id]


def _holdings(members: list[MemberAssets], section: str) -> list:
    return [h for m in members if m.snapshot for h in m.snapshot.of(section)]


def _breakdown(members: list[MemberAssets]) -> dict:
    """홈 화면 칸: 투자 · 대출 · 현금·예금 · 보험 (뱅샐 재무현황 기준)"""
    inv = _holdings(members, "investment")
    value = sum(h.amount or 0 for h in inv)
    principal = sum(h.principal or 0 for h in inv)
    loans = _holdings(members, "loan")
    cash = [
        h
        for h in _holdings(members, "asset")
        if h.group and any(word in h.group for word in CASH_WORDS)
    ]
    insurance = _holdings(members, "insurance")
    has = any(m.snapshot for m in members)
    total_assets = sum((m.snapshot.total_assets or 0) for m in members if m.snapshot)
    total_debt = sum((m.snapshot.total_liabilities or 0) for m in members if m.snapshot)
    return {
        "has_banksalad": has,
        "total_assets": total_assets + sum(m.added for m in members),
        "total_liabilities": total_debt,
        "investment": {
            "value": value,
            "principal": principal,
            "rate": round((value / principal - 1) * 100, 1) if principal else None,
            "count": len(inv),
        },
        "loans": {"balance": sum(h.amount or 0 for h in loans), "count": len(loans)},
        "cash": {"amount": sum(h.amount or 0 for h in cash), "count": len(cash)},
        "insurance": {"paid_total": sum(h.amount or 0 for h in insurance), "count": len(insurance)},
    }


def _trend(conn: sqlite3.Connection, ym: str, this_month: str, member) -> list[dict]:
    """최근 12개월 순자산. 뱅샐 기록이 없는 달은 null (그래프에서 건너뜀)."""
    out = []
    for back in range(TREND_MONTHS - 1, -1, -1):
        month = _shift(ym, -back)
        picked = _pick(asset_overview(conn, month, this_month), member)
        has = any(m.snapshot for m in picked)
        out.append({"month": month, "net_worth": sum(m.net_worth for m in picked) if has else None})
    return out


@router.get("/home")
def home(
    request: Request,
    conn: Db,
    _me: CurrentMember,
    month: str | None = None,
    owner: str | None = None,
) -> dict:
    """메인 화면 한 번에. owner를 주면 그 사람만 (알림은 집 전체)."""
    ym = month_or_last(request, month)
    member = get_member(conn, owner) if owner else None
    this_month = today(request).isoformat()[:7]
    report = classify(conn, ym, member=member)
    prev = classify(conn, _shift(ym, -1), member=member)
    fixed = _fixed_status(conn, request, ym, owner)
    picked = _pick(asset_overview(conn, ym, this_month), member)
    trend = _trend(conn, ym, this_month, member)
    slugs = member_slugs(conn)
    settled = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT member_id, status FROM month_settlements WHERE year_month = ?", (ym,)
        )
    }
    people = [member.id] if member else list(slugs)
    edits = edit_notes(conn)
    recent = sorted(report.items, key=lambda i: (i.date, i.time), reverse=True)[:5]
    household = classify(conn, ym) if member else report
    household_fixed = _fixed_status(conn, request, ym, None) if member else fixed
    return {
        "month": ym,
        "owner": member.slug if member else None,
        "settlement": [
            {
                "owner": slugs[mid],
                "status": settled.get(mid),
                "label": SETTLEMENT_LABELS[settled.get(mid)],
            }
            for mid in people
        ],
        "summary": {
            "income": report.income,
            "expense": report.expense,
            "saving": report.saving,
            "net": report.net,
            "income_diff": report.income - prev.income,
            "expense_diff": report.expense - prev.expense,
            "unconfirmed": report.counts()["unconfirmed"],
        },
        "fixed": {
            "counts": fixed["counts"],
            "count": len(fixed["items"]),
            "total_living": fixed["total_living"],
            "total_saving": fixed["total_saving"],
        },
        "assets": {
            "net_worth": sum(m.net_worth for m in picked),
            "banksalad_net": sum(m.banksalad_net for m in picked),
            "added": sum(m.added for m in picked),
            "previous_net_worth": trend[-2]["net_worth"],
            "by_member": [{"owner": m.member.slug, "net_worth": m.net_worth} for m in picked],
            **_breakdown(picked),
        },
        "trend": trend,
        "expense_by_category": [
            {"category": k, "amount": v} for k, v in report.by_category("expense").items()
        ],
        "recent": [tx_out(i, slugs, edits) for i in recent],
        "alerts": _alerts(conn, request, ym, household, household_fixed),
    }
