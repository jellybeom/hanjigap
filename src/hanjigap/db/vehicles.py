"""자동차: 차량 · 직접 입력한 시세 · 추정 시세 계산 (J5a).

실제 입력은 웹 화면에서 한다. 이 모듈은 화면과 테스트가 함께 쓰는 함수다.

추정 시세 (저장하지 않고 볼 때마다 계산)
    값 = 기준값 × (1 − 감가율)^(지난 개월 / 12)
    기준값 = 그 달 이전(그 달 포함)의 기준 중 가장 최근 것
        구입가 (구입한 달)
        직접 입력한 시세 (vehicle_valuations)
        뱅샐 재무현황 '동산' 항목 (잔액 기록 bs_holdings, 소유자 파일에서)
    같은 달에 여러 기준이 있으면 직접 입력 > 뱅샐 > 구입가 순으로 쓴다.
    결과에는 화면의 '시세는 이렇게 계산해요' 칸에 쓸 설명이 함께 들어 있다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from hanjigap.db import validate
from hanjigap.db.fixed_items import FixedItem, list_fixed_items
from hanjigap.db.members import Member
from hanjigap.db.validate import InputError

VehicleError = InputError

BS_GROUP = "동산"  # 뱅샐 재무현황에서 자동차가 들어가는 묶음
DEFAULT_DEPRECIATION = 15.0
SOURCE_LABELS = {"purchase": "구입가", "manual": "직접 입력", "banksalad": "뱅샐"}
_SOURCE_PRIORITY = {"purchase": 0, "banksalad": 1, "manual": 2}


@dataclass(frozen=True)
class Vehicle:
    id: int
    owner_member_id: int
    name: str
    model_year: int | None
    purchased_on: str
    purchase_price: int
    depreciation_pct: float
    bs_asset_name: str | None
    inspection_due_on: str | None
    sold_on: str | None
    color: str | None
    memo: str | None

    @property
    def purchase_month(self) -> str:
        return self.purchased_on[:7]

    @property
    def active(self) -> bool:
        return self.sold_on is None


@dataclass(frozen=True)
class Anchor:
    year_month: str
    value: int
    source: str  # purchase | manual | banksalad

    @property
    def label(self) -> str:
        y, m = self.year_month.split("-")
        return f"{y}년 {int(m)}월 {SOURCE_LABELS[self.source]} {self.value:,}원"


@dataclass(frozen=True)
class ValueEstimate:
    year_month: str
    value: int
    anchor: Anchor
    months: int  # 기준 달부터 지난 개월
    rate_pct: float

    @property
    def factor_text(self) -> str:
        keep = 1 - self.rate_pct / 100
        return f"{keep:g}^({self.months}/12)"

    @property
    def formula(self) -> str:
        return f"{self.anchor.value:,}원 × {self.factor_text} = {self.value:,}원"

    @property
    def monthly_pct(self) -> float:
        """매달 직전 값에서 빠지는 비율 (%)"""
        return (1 - (1 - self.rate_pct / 100) ** (1 / 12)) * 100


_COLS = (
    "id, owner_member_id, name, model_year, purchased_on, purchase_price, depreciation_pct, "
    "bs_asset_name, inspection_due_on, sold_on, color, memo"
)


def _row(r: sqlite3.Row) -> Vehicle:
    return Vehicle(**dict(r))


# --- 차량 ----------------------------------------------------------------------


def add_vehicle(
    conn: sqlite3.Connection,
    owner: Member,
    *,
    name: str,
    purchased_on: str,
    purchase_price: int,
    model_year: int | None = None,
    depreciation_pct: float = DEFAULT_DEPRECIATION,
    bs_asset_name: str | None = None,
    inspection_due_on: str | None = None,
    color: str | None = None,
    memo: str | None = None,
) -> Vehicle:
    fields = _clean(
        name=name,
        purchased_on=purchased_on,
        purchase_price=purchase_price,
        model_year=model_year,
        depreciation_pct=depreciation_pct,
        bs_asset_name=bs_asset_name,
        inspection_due_on=inspection_due_on,
        color=color,
        memo=memo,
    )
    fields["owner_member_id"] = owner.id
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    try:
        cur = conn.execute(
            f"INSERT INTO vehicles ({cols}) VALUES ({marks})", tuple(fields.values())
        )
    except sqlite3.IntegrityError as exc:
        raise _integrity(exc) from None
    return get_vehicle(conn, cur.lastrowid)


def get_vehicle(conn: sqlite3.Connection, vehicle_id: int) -> Vehicle:
    row = conn.execute(f"SELECT {_COLS} FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if row is None:
        raise VehicleError(f"차량을 찾을 수 없어요: {vehicle_id}")
    return _row(row)


def list_vehicles(
    conn: sqlite3.Connection, owner: Member | None = None, *, include_sold: bool = False
) -> list[Vehicle]:
    sql = f"SELECT {_COLS} FROM vehicles"
    where, params = [], []
    if owner is not None:
        where.append("owner_member_id = ?")
        params.append(owner.id)
    if not include_sold:
        where.append("sold_on IS NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [_row(r) for r in conn.execute(sql + " ORDER BY id", params)]


def update_vehicle(conn: sqlite3.Connection, vehicle_id: int, **changes: Any) -> Vehicle:
    """바꿀 칸만. 선택 칸(연식 제외)은 ''를 넘기면 지운다. 연식은 0이면 지운다."""
    current = get_vehicle(conn, vehicle_id)
    if not changes:
        raise VehicleError("바꿀 내용이 없어요")
    allowed = set(_COLS.replace(" ", "").split(",")) - {"id", "owner_member_id", "sold_on"}
    unknown = set(changes) - allowed
    if unknown:
        raise VehicleError(f"바꿀 수 없는 칸이에요: {', '.join(sorted(unknown))}")
    merged = {k: v for k, v in current.__dict__.items() if k in allowed}
    merged.update(changes)
    fields = _clean(**merged)
    if current.sold_on and current.sold_on < fields["purchased_on"]:
        raise VehicleError("구입일이 매각일보다 늦어요")
    fields["updated_at"] = validate.now_utc()
    sets = ", ".join(f"{k} = ?" for k in fields)
    try:
        conn.execute(f"UPDATE vehicles SET {sets} WHERE id = ?", (*fields.values(), vehicle_id))
    except sqlite3.IntegrityError as exc:
        raise _integrity(exc) from None
    return get_vehicle(conn, vehicle_id)


def mark_sold(conn: sqlite3.Connection, vehicle_id: int, sold_on: str | None) -> Vehicle:
    """매각일 표시 (None이면 취소). 지우지 않는다."""
    v = get_vehicle(conn, vehicle_id)
    day = validate.parse_date(sold_on, "매각일").isoformat() if sold_on else None
    if day is not None and day < v.purchased_on:
        raise VehicleError("매각일이 구입일보다 빨라요")
    conn.execute(
        "UPDATE vehicles SET sold_on = ?, updated_at = ? WHERE id = ?",
        (day, validate.now_utc(), vehicle_id),
    )
    return get_vehicle(conn, vehicle_id)


# --- 직접 입력한 시세 -------------------------------------------------------------


def set_valuation(
    conn: sqlite3.Connection,
    vehicle_id: int,
    year_month: str,
    value: int,
    memo: str | None = None,
) -> Anchor:
    """그 달 시세를 직접 넣는다. 이미 있으면 바꾼다. 그 달부터 새 기준이 된다."""
    v = get_vehicle(conn, vehicle_id)
    ym = validate.year_month(year_month, "시세 월")
    if ym is None:
        raise VehicleError("시세 월을 입력하세요 (예: 2026-09)")
    if ym < v.purchase_month:
        raise VehicleError("구입한 달보다 앞선 시세는 넣을 수 없어요")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise VehicleError("시세는 1원 이상의 정수로 입력하세요")
    conn.execute(
        "INSERT INTO vehicle_valuations (vehicle_id, year_month, value, memo) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (vehicle_id, year_month) DO UPDATE SET value = excluded.value, "
        "memo = excluded.memo",
        (vehicle_id, ym, value, validate.optional_text(memo, "메모")),
    )
    return Anchor(ym, value, "manual")


def delete_valuation(conn: sqlite3.Connection, vehicle_id: int, year_month: str) -> bool:
    cur = conn.execute(
        "DELETE FROM vehicle_valuations WHERE vehicle_id = ? AND year_month = ?",
        (vehicle_id, year_month),
    )
    return cur.rowcount > 0


# --- 추정 시세 -------------------------------------------------------------------


def anchors(conn: sqlite3.Connection, vehicle: Vehicle) -> list[Anchor]:
    """기준값 목록 (달 순서). 같은 달이면 우선순위 낮은 것부터."""
    found = [Anchor(vehicle.purchase_month, vehicle.purchase_price, "purchase")]
    found += [
        Anchor(r["year_month"], r["value"], "manual")
        for r in conn.execute(
            "SELECT year_month, value FROM vehicle_valuations WHERE vehicle_id = ?", (vehicle.id,)
        )
    ]
    found += _banksalad_anchors(conn, vehicle)
    return sorted(found, key=lambda a: (a.year_month, _SOURCE_PRIORITY[a.source]))


def estimate(conn: sqlite3.Connection, vehicle: Vehicle, year_month: str) -> ValueEstimate | None:
    """그 달 말 추정 시세. 구입 전이거나 판 뒤의 달이면 None."""
    ym = validate.year_month(year_month, "월")
    if ym is None or ym < vehicle.purchase_month:
        return None
    if vehicle.sold_on and ym > vehicle.sold_on[:7]:
        return None
    usable = [a for a in anchors(conn, vehicle) if a.year_month <= ym]
    anchor = usable[-1]
    months = _months_between(anchor.year_month, ym)
    keep = 1 - vehicle.depreciation_pct / 100
    value = round(anchor.value * keep ** (months / 12))
    return ValueEstimate(ym, value, anchor, months, vehicle.depreciation_pct)


def value_series(
    conn: sqlite3.Connection, vehicle: Vehicle, until: str
) -> list[tuple[str, int, bool]]:
    """구입 달부터 until까지 (월, 추정 시세, 그 달이 기준값인가). 화면의 추이 그래프용."""
    marks = {a.year_month for a in anchors(conn, vehicle)}
    out = []
    ym = vehicle.purchase_month
    while ym <= until:
        e = estimate(conn, vehicle, ym)
        if e is None:
            break
        out.append((ym, e.value, ym in marks))
        ym = _add_month(ym)
    return out


def vehicle_fixed_items(conn: sqlite3.Connection, vehicle: Vehicle) -> list[FixedItem]:
    """이 차량에 연결된 고정비 (할부·보험·자동차세 등). 마감된 것 포함."""
    return [i for i in list_fixed_items(conn, include_ended=True) if i.vehicle_id == vehicle.id]


# --- 내부 ----------------------------------------------------------------------


def _banksalad_anchors(conn: sqlite3.Connection, vehicle: Vehicle) -> list[Anchor]:
    """소유자의 잔액 기록에서 '동산' 항목. 이름이 정해져 있으면 그 이름, 아니면 하나뿐일 때만."""
    rows = conn.execute(
        """
        SELECT s.id AS sid, s.year_month, h.name, h.amount
        FROM bs_snapshots s JOIN bs_holdings h ON h.snapshot_id = s.id
        WHERE s.member_id = ? AND h.section = 'asset' AND h.group_name = ?
              AND h.amount IS NOT NULL AND h.amount > 0
        ORDER BY s.year_month, s.id
        """,
        (vehicle.owner_member_id, BS_GROUP),
    ).fetchall()
    by_snapshot: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        by_snapshot.setdefault(r["sid"], []).append(r)
    latest_per_month: dict[str, Anchor] = {}
    for items in by_snapshot.values():
        if vehicle.bs_asset_name:
            items = [r for r in items if r["name"] == vehicle.bs_asset_name]
        elif len(items) != 1:
            continue  # 동산이 여러 개면 어느 것인지 몰라 쓰지 않는다
        for r in items[:1]:
            latest_per_month[r["year_month"]] = Anchor(r["year_month"], r["amount"], "banksalad")
    return list(latest_per_month.values())


def _months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def _add_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) + 1
    if m == 13:
        y, m = y + 1, 1
    return f"{y:04d}-{m:02d}"


def _clean(**v: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    name = validate.required(v["name"], "차량 이름")
    if len(name) > 40:
        raise VehicleError("차량 이름은 40자 이하로 입력하세요")
    out["name"] = name
    purchased = validate.date_or_month(v["purchased_on"], "구입일")
    if purchased is None:
        raise VehicleError("구입일을 입력하세요 (예: 2023-03)")
    out["purchased_on"] = purchased
    price = v["purchase_price"]
    if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
        raise VehicleError("구입가는 1원 이상의 정수로 입력하세요")
    out["purchase_price"] = price
    year = v.get("model_year")
    out["model_year"] = validate.in_range(year or None, "연식", 1980, 2100)
    rate = v.get("depreciation_pct")
    rate = DEFAULT_DEPRECIATION if rate is None else rate
    if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 <= rate <= 50:
        raise VehicleError("감가율은 0~50(%) 사이로 입력하세요")
    out["depreciation_pct"] = float(rate)
    out["bs_asset_name"] = validate.optional_text(v.get("bs_asset_name"), "뱅샐 항목 이름")
    due = v.get("inspection_due_on")
    out["inspection_due_on"] = (
        validate.parse_date(due.strip(), "정기검사 예정일").isoformat()
        if isinstance(due, str) and due.strip()
        else None
    )
    out["color"] = validate.color(v.get("color"))
    out["memo"] = validate.optional_text(v.get("memo"), "메모")
    return out


def _integrity(exc: sqlite3.IntegrityError) -> VehicleError:
    if "UNIQUE" in str(exc):
        return VehicleError("같은 사람에게 같은 이름의 차량이 이미 있어요")
    return VehicleError(f"차량 정보가 올바르지 않아요 ({exc})")
