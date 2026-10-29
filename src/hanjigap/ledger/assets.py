"""자산 현황: 뱅샐 재무현황 + 한지갑의 차·집 (F4b).

사람마다
    뱅샐 재무현황(잔액 기록 bs_snapshots): 총자산 · 총부채 · 순자산 · 항목별
    + 한지갑 차 추정 시세 (주인 기준)
    + 한지갑 집 자산 가치 (진행 중 계약의 주인 기준. 전세·월세는 보증금, 자가는 시세)
같은 자산을 두 번 세지 않는다
    그 사람의 뱅샐 재무현황에 '동산' 항목(금액 있음)이 있으면 한지갑 차 값은 더하지 않는다.
    '부동산' 항목이 있으면 한지갑 집 값은 더하지 않는다. (화면에 '뱅샐 값 사용'으로 표시)
    뱅샐 기록이 없는 사람은 한지갑 값을 더한다.
달
    month를 주면 그 달의 뱅샐 기록과 그 달 말 차·집 값. 없으면 사람마다 가장 최근 기록과 이번 달 값.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from hanjigap.db.estates import Property, PropertyValue, list_properties, value_at
from hanjigap.db.members import Member, list_members
from hanjigap.db.snapshots import Snapshot, latest_snapshot
from hanjigap.db.vehicles import ValueEstimate, Vehicle, estimate, list_vehicles

VEHICLE_GROUP = "동산"
ESTATE_GROUP = "부동산"


@dataclass(frozen=True)
class VehicleAsset:
    vehicle: Vehicle
    value: ValueEstimate
    included: bool  # False = 뱅샐 '동산'에 이미 있어서 더하지 않음


@dataclass(frozen=True)
class EstateAsset:
    prop: Property
    value: PropertyValue
    included: bool  # False = 뱅샐 '부동산'에 이미 있어서 더하지 않음


@dataclass
class MemberAssets:
    member: Member
    snapshot: Snapshot | None
    vehicles: list[VehicleAsset] = field(default_factory=list)
    estates: list[EstateAsset] = field(default_factory=list)

    @property
    def banksalad_net(self) -> int:
        return (self.snapshot.net_worth or 0) if self.snapshot else 0

    @property
    def added(self) -> int:
        """한지갑에서 더한 차·집 값"""
        return sum(v.value.value for v in self.vehicles if v.included) + sum(
            e.value.value for e in self.estates if e.included
        )

    @property
    def net_worth(self) -> int:
        return self.banksalad_net + self.added


@dataclass
class AssetOverview:
    month: str  # 차·집 값을 계산한 달
    members: list[MemberAssets]

    @property
    def net_worth(self) -> int:
        return sum(m.net_worth for m in self.members)

    @property
    def banksalad_net(self) -> int:
        return sum(m.banksalad_net for m in self.members)

    @property
    def added(self) -> int:
        return sum(m.added for m in self.members)


def has_group(snapshot: Snapshot | None, group: str) -> bool:
    if snapshot is None:
        return False
    return any(h.group == group and (h.amount or 0) > 0 for h in snapshot.of("asset"))


def asset_overview(conn: sqlite3.Connection, month: str | None, this_month: str) -> AssetOverview:
    value_month = month or this_month
    people = {m.id: MemberAssets(m, latest_snapshot(conn, m, month)) for m in list_members(conn)}
    for v in list_vehicles(conn, include_sold=True):
        e = estimate(conn, v, value_month)
        owner = people.get(v.owner_member_id)
        if e is not None and owner is not None:
            owner.vehicles.append(VehicleAsset(v, e, not has_group(owner.snapshot, VEHICLE_GROUP)))
    for p in list_properties(conn, include_moved_out=True):
        pv = value_at(conn, p, value_month)
        owner = people.get(pv.owner_member_id) if pv else None
        if pv is not None and owner is not None:
            owner.estates.append(EstateAsset(p, pv, not has_group(owner.snapshot, ESTATE_GROUP)))
    return AssetOverview(value_month, list(people.values()))
