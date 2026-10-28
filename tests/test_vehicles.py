"""J5a: 자동차 (차량 · 직접 입력 시세 · 추정 시세 · 고정비 연결)."""

import pytest

from hanjigap.db.connection import connect
from hanjigap.db.fixed_items import FixedItemError, add_fixed_item, update_fixed_item
from hanjigap.db.holdings import add_account
from hanjigap.db.members import add_member
from hanjigap.db.migrate import load_migrations, migrate
from hanjigap.db.vehicles import (
    VehicleError,
    add_vehicle,
    anchors,
    delete_valuation,
    estimate,
    get_vehicle,
    list_vehicles,
    mark_sold,
    set_valuation,
    update_vehicle,
    value_series,
    vehicle_fixed_items,
)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "v.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def people(conn):
    return add_member(conn, "sangbeom", "상범"), add_member(conn, "jaehee", "재희")


@pytest.fixture
def car(conn, people):
    return add_vehicle(
        conn, people[1], name="패밀리 SUV", purchased_on="2023-03", purchase_price=32_000_000
    )


# --- 차량 ----------------------------------------------------------------------


def test_add_defaults(car, people):
    assert (car.owner_member_id, car.depreciation_pct, car.purchase_month) == (
        people[1].id,
        15.0,
        "2023-03",
    )
    assert car.active and car.model_year is None


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"purchase_price": 0}, "구입가"),
        ({"purchased_on": "2023/03"}, "구입일 형식"),
        ({"depreciation_pct": 60}, "0~50"),
        ({"model_year": 1900}, "연식"),
        ({"color": "blue"}, "#rrggbb"),
        ({"memo": "번호 1234567890"}, "전체 계좌·카드번호"),
        ({"inspection_due_on": "2027-02-30"}, "정기검사 예정일 형식"),
    ],
)
def test_add_rejects(conn, people, over, message):
    values = dict(name="차", purchased_on="2023-03", purchase_price=1)
    values.update(over)
    with pytest.raises(VehicleError, match=message):
        add_vehicle(conn, people[0], **values)


def test_duplicate_name(conn, people, car):
    with pytest.raises(VehicleError, match="같은 이름의 차량"):
        add_vehicle(conn, people[1], name="패밀리 SUV", purchased_on="2024-01", purchase_price=1)


def test_update(conn, car):
    v = update_vehicle(conn, car.id, depreciation_pct=12, model_year=2023, memo="첫 차")
    assert (v.depreciation_pct, v.model_year, v.memo) == (12.0, 2023, "첫 차")
    v = update_vehicle(conn, car.id, memo="", model_year=0)
    assert (v.memo, v.model_year) == (None, None)
    with pytest.raises(VehicleError, match="바꿀 수 없는 칸"):
        update_vehicle(conn, car.id, owner_member_id=1)


def test_sold(conn, car):
    with pytest.raises(VehicleError, match="구입일보다 빨라요"):
        mark_sold(conn, car.id, "2022-12-31")
    v = mark_sold(conn, car.id, "2026-10-01")
    assert not v.active and list_vehicles(conn) == []
    assert len(list_vehicles(conn, include_sold=True)) == 1
    assert mark_sold(conn, car.id, None).active


# --- 추정 시세 -------------------------------------------------------------------


def test_estimate_from_purchase(conn, car):
    e = estimate(conn, car, "2024-03")
    assert (e.value, e.months, e.anchor.source) == (27_200_000, 12, "purchase")
    assert e.formula == "32,000,000원 × 0.85^(12/12) = 27,200,000원"
    assert e.anchor.label == "2023년 3월 구입가 32,000,000원"
    assert e.monthly_pct == pytest.approx(1.3452, abs=1e-4)


def test_estimate_like_mockup(conn, car):
    set_valuation(conn, car.id, "2024-09", 25_600_000)
    set_valuation(conn, car.id, "2026-03", 21_900_000)
    e = estimate(conn, car, "2026-09")
    assert e.formula == "21,900,000원 × 0.85^(6/12) = 20,190,802원"
    assert e.anchor.label == "2026년 3월 직접 입력 21,900,000원"
    prev = estimate(conn, car, "2026-08")
    assert prev.value - e.value == 275_309  # 지난달보다 −275,309원


def test_estimate_bounds(conn, car):
    assert estimate(conn, car, "2023-02") is None
    sold = mark_sold(conn, car.id, "2026-10-15")
    assert estimate(conn, sold, "2026-10") is not None
    assert estimate(conn, sold, "2026-11") is None


def test_valuation_replace_and_delete(conn, car):
    set_valuation(conn, car.id, "2026-03", 21_900_000)
    set_valuation(conn, car.id, "2026-03", 22_000_000, memo="중고차 시세 사이트")
    assert estimate(conn, car, "2026-03").value == 22_000_000
    assert delete_valuation(conn, car.id, "2026-03")
    assert estimate(conn, car, "2026-03").anchor.source == "purchase"


@pytest.mark.parametrize(
    ("ym", "value", "message"),
    [("2023-01", 1, "구입한 달보다"), ("2026-03", 0, "1원 이상"), ("2026/03", 1, "YYYY-MM")],
)
def test_valuation_rejects(conn, car, ym, value, message):
    with pytest.raises(VehicleError, match=message):
        set_valuation(conn, car.id, ym, value)


def test_series(conn, car):
    set_valuation(conn, car.id, "2023-06", 30_000_000)
    series = value_series(conn, car, "2023-08")
    assert [ym for ym, _, _ in series] == [
        "2023-03",
        "2023-04",
        "2023-05",
        "2023-06",
        "2023-07",
        "2023-08",
    ]
    assert [mark for _, _, mark in series] == [True, False, False, True, False, False]
    assert series[3][1] == 30_000_000


# --- 뱅샐 '동산' ------------------------------------------------------------------


def snapshot(conn, member, ym, items):
    batch = conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (?, ?, 'zip', 0, 'applied')",
        (member.id, f"{ym}{member.id}".ljust(64, "0")),
    ).lastrowid
    sid = conn.execute(
        "INSERT INTO bs_snapshots (batch_id, member_id, as_of, year_month, checks_ok) "
        "VALUES (?, ?, ?, ?, 1)",
        (batch, member.id, f"{ym}-20", ym),
    ).lastrowid
    for seq, (group, name, amount) in enumerate(items, start=1):
        conn.execute(
            "INSERT INTO bs_holdings (snapshot_id, seq, section, group_name, name, amount) "
            "VALUES (?, ?, 'asset', ?, ?, ?)",
            (sid, seq, group, name, amount),
        )


def test_banksalad_value_used(conn, people, car):
    snapshot(
        conn, people[1], "2026-09", [("동산", "내 차", 20_500_000), ("자유입출금 자산", "통장", 1)]
    )
    e = estimate(conn, car, "2026-10")
    assert (e.anchor.source, e.anchor.value, e.months) == ("banksalad", 20_500_000, 1)
    assert e.anchor.label == "2026년 9월 뱅샐 20,500,000원"


def test_manual_beats_banksalad_same_month(conn, people, car):
    snapshot(conn, people[1], "2026-09", [("동산", "내 차", 20_500_000)])
    set_valuation(conn, car.id, "2026-09", 20_000_000)
    assert estimate(conn, car, "2026-09").anchor.source == "manual"


def test_banksalad_ambiguous_needs_name(conn, people, car):
    snapshot(
        conn, people[1], "2026-09", [("동산", "차 A", 20_500_000), ("동산", "차 B", 9_000_000)]
    )
    assert estimate(conn, car, "2026-09").anchor.source == "purchase"  # 어느 것인지 모름
    named = update_vehicle(conn, car.id, bs_asset_name="차 B")
    assert estimate(conn, named, "2026-09").anchor.value == 9_000_000


def test_banksalad_of_other_member_ignored(conn, people, car):
    snapshot(conn, people[0], "2026-09", [("동산", "내 차", 20_500_000)])  # 상범 파일
    assert estimate(conn, car, "2026-09").anchor.source == "purchase"


def test_banksalad_empty_group_ignored(conn, people, car):
    snapshot(conn, people[1], "2026-09", [("동산", "내 차", 0)])
    assert [a.source for a in anchors(conn, car)] == ["purchase"]


# --- 고정비 연결 ------------------------------------------------------------------


def test_fixed_items_linked(conn, people, car):
    acc = add_account(conn, people[0], institution="A은행", name="주거래 통장", kind="입출금")
    loan = add_fixed_item(
        conn,
        None,
        name="자동차 할부",
        category="대출",
        amount=240_000,
        pay_day=15,
        method="자동이체",
        pay_account_id=acc.id,
        start_month="2023-04",
        vehicle_id=car.id,
    )
    add_fixed_item(
        conn,
        None,
        name="자동차보험",
        category="보험",
        amount=980_000,
        pay_day=14,
        cycle="매년",
        pay_month=3,
        method="자동이체",
        pay_account_id=acc.id,
        start_month="2023-03",
        vehicle_id=car.id,
    )
    assert [i.name for i in vehicle_fixed_items(conn, car)] == ["자동차보험", "자동차 할부"]
    update_fixed_item(conn, loan.id, vehicle_id=0)  # 떼기
    assert [i.name for i in vehicle_fixed_items(conn, car)] == ["자동차보험"]
    with pytest.raises(FixedItemError, match="차량을 찾을 수 없어요"):
        update_fixed_item(conn, loan.id, vehicle_id=999)


def test_get_unknown(conn):
    with pytest.raises(VehicleError, match="찾을 수 없어요"):
        get_vehicle(conn, 1)


def test_upgrade_from_version_5(tmp_path):
    c = connect(tmp_path / "v5.db")
    migrate(c, load_migrations()[:5])
    c.execute("INSERT INTO members (slug, display_name) VALUES ('sangbeom', '상범')")
    c.execute(
        "INSERT INTO fixed_items (member_id, name, category, amount, pay_day, method, start_month) "
        "VALUES (1, '용돈', 'allowance', 1, 1, 'manual', '2026-01')"
    )
    assert [m.name for m in migrate(c)][0] == "vehicles"
    assert c.execute("SELECT vehicle_id FROM fixed_items").fetchone()[0] is None
    c.close()
