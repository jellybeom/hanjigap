"""화면의 선택 칸을 채우는 선택지 목록. 코드와 한글 이름을 함께 준다."""

from __future__ import annotations

from fastapi import APIRouter

from hanjigap.db import estates, fixed_items, holdings, vehicle_costs
from hanjigap.web.common import choices
from hanjigap.web.deps import CurrentMember

router = APIRouter(prefix="/api", tags=["options"])

PERIOD_MONTH_OFFSETS = {"0": "당월", "-1": "전월", "-2": "전전월"}


@router.get("/options")
def options(_member: CurrentMember) -> dict:
    return {
        "account_kinds": choices(holdings.ACCOUNT_KINDS),
        "card_kinds": choices(holdings.CARD_KINDS),
        "card_period_month_offsets": choices(PERIOD_MONTH_OFFSETS),
        "link_targets": choices(holdings.TARGET_LABELS),
        "fixed_categories": choices(fixed_items.CATEGORIES),
        "fixed_methods": choices(fixed_items.METHODS),
        "fixed_amount_modes": choices(fixed_items.AMOUNT_MODES),
        "fixed_cycles": choices(fixed_items.CYCLES),
        "vehicle_cost_types": choices(vehicle_costs.COST_TYPES),
        "vehicle_rule_kinds": choices(vehicle_costs.RULE_KINDS),
        "property_kinds": choices(estates.PROPERTY_KINDS),
        "contract_types": choices(estates.CONTRACT_TYPES),
        "valuation_sources": choices(estates.VALUATION_SOURCES),
    }
