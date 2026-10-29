"""J7b: 구성원 별칭 · 부부 이체 자동 인식 · 부부 이체 짝 찾기 · 고정비 용돈."""

import hashlib
import itertools
import logging

import pytest

from hanjigap import cli
from hanjigap.db.class_rules import add_rule, set_tx_class
from hanjigap.db.connection import connect
from hanjigap.db.fixed_items import add_fixed_item
from hanjigap.db.holdings import add_account, link_instrument
from hanjigap.db.members import (
    MemberError,
    add_aliases,
    add_member,
    list_aliases,
    member_names,
    remove_aliases,
)
from hanjigap.db.migrate import load_migrations, migrate
from hanjigap.ledger.classify import classify

_n = itertools.count(1)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "f.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def people(conn):
    sb = add_member(conn, "sangbeom", "상범")
    jh = add_member(conn, "jaehee", "재희")
    conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (1, ?, 'zip', 0, 'applied')",
        ("a" * 64,),
    )
    return sb, jh


def tx(conn, member, amount, content, tx_type="이체", category="이체", date="2026-09-13",instrument="주거래 통장"):  # fmt: skip
    n = next(_n)
    key = hashlib.sha256(f"f{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, ?, '', ?, ?, ?, 1, ?)",
        (member.id, key, f"b{n}", date[:7], date, tx_type, category, content, amount,
         instrument, n),
    )  # fmt: skip
    return key


def one(conn, key, month="2026-09"):
    return next(i for i in classify(conn, month).items if i.tx_key == key)


# --- 별칭 ----------------------------------------------------------------------


def test_aliases(conn, people):
    assert add_aliases(conn, "jaehee", ["이재희", " 잴리 ", "이재희"]) == ["이재희", "잴리"]
    add_aliases(conn, "sangbeom", ["윤상범", "버미"])
    assert member_names(conn) == {1: ["상범", "윤상범", "버미"], 2: ["재희", "이재희", "잴리"]}
    assert remove_aliases(conn, "jaehee", ["잴리", "없는별칭"]) == ["이재희"]
    assert list_aliases(conn, "jaehee") == ["이재희"]


@pytest.mark.parametrize(
    ("aliases", "message"),
    [
        (["희"], "2~20자"),
        (["가" * 21], "2~20자"),
        (["상범"], "다른 구성원의 이름"),  # 상범의 구성원 이름
        (["버미"], "다른 구성원의 이름"),  # 상범의 별칭
        (["BUMMY"], "다른 구성원의 이름"),  # 대소문자 무관
    ],
)
def test_alias_rejects(conn, people, aliases, message):
    add_aliases(conn, "sangbeom", ["bummy", "버미"])
    with pytest.raises(MemberError, match=message):
        add_aliases(conn, "jaehee", aliases)
    assert list_aliases(conn, "jaehee") == []  # 하나라도 틀리면 아무것도 안 넣음


def test_alias_unknown_member(conn, people):
    with pytest.raises(MemberError, match="구성원을 찾을 수 없습니다"):
        add_aliases(conn, "nobody", ["이름"])


def test_short_display_name_not_used(conn):
    add_member(conn, "kim", "김")
    assert member_names(conn) == {1: []}


# --- 부부 이체 자동 인식 ------------------------------------------------------------


def test_partner_name_excluded(conn, people):
    sb, jh = people
    add_aliases(conn, "jaehee", ["이재희", "잴리"])
    add_aliases(conn, "sangbeom", ["윤상범", "버미"])
    cases = {  # 구성원 이름을 먼저, 그다음 별칭을 등록한 순서대로 찾는다
        tx(conn, sb, 1_600_000, "이재희"): "재희",
        tx(conn, sb, -30_000, "잴리 생일"): "잴리",
        tx(conn, jh, -500_000, "버미"): "버미",
        tx(conn, jh, 20_000, "윤상범"): "상범",
    }
    for key, found in cases.items():
        item = one(conn, key)
        assert (item.cls, item.display_category, item.source) == ("excluded", "부부 이체", "spouse")
        assert item.reason == f"부부 이체: 「{found}」"


def test_own_name_is_not_partner(conn, people):
    sb, _ = people
    add_aliases(conn, "sangbeom", ["윤상범"])
    key = tx(conn, sb, -500_000, "윤상범")  # 내 다른 계좌로 보냄
    assert one(conn, key).cls == "unconfirmed"


def test_partner_name_only_for_transfers(conn, people):
    sb, _ = people
    key = tx(conn, sb, -12_000, "재희네 분식", tx_type="지출", category="식비")
    assert one(conn, key).cls == "expense"


def test_partner_name_case_insensitive(conn, people):
    sb, _ = people
    add_aliases(conn, "jaehee", ["Jelly"])
    assert one(conn, tx(conn, sb, 1, "JELLY")).source == "spouse"


def test_rule_and_manual_beat_partner_name(conn, people):
    sb, _ = people
    key = tx(conn, sb, 50_000, "이재희 모임비")
    add_rule(conn, result="income", direction="in", pattern="모임비")
    assert one(conn, key).cls == "income"
    set_tx_class(conn, sb, key, "expense", result_category="경조/선물")
    assert one(conn, key).cls == "expense"


# --- 부부 이체 짝 찾기 --------------------------------------------------------------


def test_pair_hint(conn, people):
    sb, jh = people
    out = tx(conn, sb, -300_000, "생활비", date="2026-09-10")
    back = tx(conn, jh, 300_000, "모르는 이름", date="2026-09-11")  # 하루 뒤
    a, b = one(conn, out), one(conn, back)
    assert (a.cls, a.pair_hint, a.suggestion) == ("unconfirmed", True, "excluded")
    assert (b.pair_hint, b.suggestion) == (True, "excluded")


@pytest.mark.parametrize(
    ("member_index", "amount", "date"),
    [
        (1, 300_001, "2026-09-10"),  # 금액 다름
        (1, 300_000, "2026-09-13"),  # 3일 차이
        (1, -300_000, "2026-09-10"),  # 같은 방향
        (0, 300_000, "2026-09-10"),  # 같은 사람
    ],
)
def test_no_pair_hint(conn, people, member_index, amount, date):
    out = tx(conn, people[0], -300_000, "생활비", date="2026-09-10")
    tx(conn, people[member_index], amount, "누군가", date=date)
    item = one(conn, out)
    assert (item.pair_hint, item.suggestion) == (False, "expense")


def test_pair_across_month_boundary(conn, people):
    sb, jh = people
    out = tx(conn, sb, -100_000, "생활비", date="2026-09-30")
    tx(conn, jh, 100_000, "누군가", date="2026-10-01")
    assert one(conn, out).pair_hint


def test_pair_counterpart_must_be_transfer(conn, people):
    sb, jh = people
    out = tx(conn, sb, -100_000, "생활비", date="2026-09-10")
    tx(conn, jh, 100_000, "환불", tx_type="지출", category="식비", date="2026-09-10")
    assert not one(conn, out).pair_hint


# --- 고정비 용돈 ---------------------------------------------------------------


@pytest.fixture
def allowance(conn, people):
    sb, _ = people
    acc = add_account(conn, sb, institution="A은행", name="주거래 통장", kind="입출금")
    return add_fixed_item(
        conn,
        None,
        name="상범 용돈",
        category="용돈",
        amount=500_000,
        pay_day=25,
        method="자동이체",
        pay_account_id=acc.id,
        start_month="2026-01",
    )


@pytest.mark.parametrize("category", ["내계좌이체", "이체"])
def test_allowance_is_expense(conn, people, allowance, category):
    sb, _ = people
    key = tx(conn, sb, -500_000, "윤상범", category=category, date="2026-09-25")
    item = one(conn, key)
    assert (item.cls, item.display_category, item.source) == ("expense", "용돈", "fixed")
    assert item.reason == "고정비 용돈: 상범 용돈"


def test_allowance_beats_rule_but_not_manual(conn, people, allowance):
    sb, _ = people
    key = tx(conn, sb, -500_000, "윤상범", category="내계좌이체", date="2026-09-24")
    add_rule(conn, result="excluded", pattern="윤상범")
    assert one(conn, key).source == "fixed"
    set_tx_class(conn, sb, key, "excluded")
    assert one(conn, key).source == "manual"


def test_allowance_needs_match(conn, people, allowance):
    sb, _ = people
    wrong_amount = tx(conn, sb, -400_000, "윤상범", category="내계좌이체", date="2026-09-25")
    far = tx(conn, sb, -500_000, "윤상범", category="내계좌이체", date="2026-09-10")
    assert one(conn, wrong_amount).cls == one(conn, far).cls == "excluded"


def test_allowance_uses_linked_instrument(conn, people, allowance):
    sb, _ = people
    tx(conn, sb, -1, "x", instrument="주거래 통장")
    link_instrument(conn, sb, "주거래 통장", account_id=allowance.pay_account_id)
    other = tx(conn, sb, -500_000, "윤상범", category="내계좌이체", date="2026-09-25",
               instrument="다른 통장")  # fmt: skip
    assert one(conn, other).cls == "excluded"  # 연결된 통장에서 나간 것만


def test_other_fixed_items_not_affected(conn, people):
    sb, _ = people
    add_fixed_item(conn, sb, name="적금", category="저축", amount=300_000, pay_day=10,
                   method="직접 이체", start_month="2026-01")  # fmt: skip
    key = tx(conn, sb, -300_000, "적금", category="저축", date="2026-09-10")
    assert one(conn, key).cls == "saving"


def test_allowance_over_several_months(conn, people, allowance):
    sb, _ = people
    keys = [
        tx(conn, sb, -500_000, "윤상범", category="내계좌이체", date=f"2026-{m:02d}-25")
        for m in (8, 9, 10)
    ]
    report = classify(conn, "2026-08", "2026-10")
    assert [i.source for i in report.items if i.tx_key in keys] == ["fixed"] * 3
    assert report.by_category("expense") == {"용돈": 1_500_000}


def test_report_sources(conn, people, allowance):
    sb, jh = people
    tx(conn, sb, -500_000, "윤상범", category="내계좌이체", date="2026-09-25")
    tx(conn, sb, 1_000_000, "이재희")
    tx(conn, sb, -1, "식당", tx_type="지출", category="식비")
    assert classify(conn, "2026-09").sources() == {
        "manual": 0,
        "fixed": 1,
        "rule": 0,
        "spouse": 1,
        "default": 1,
    }


def test_upgrade_from_version_9(tmp_path):
    c = connect(tmp_path / "v9.db")
    migrate(c, load_migrations()[:9])
    assert [m.name for m in migrate(c)][0] == "member_aliases"
    c.close()


# --- 명령어 ---------------------------------------------------------------------


def test_cli_member_alias(isolated_env, capsys, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap")
    cli.main(["db", "init"])
    cli.main(["member", "add", "jaehee", "--name", "재희"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    capsys.readouterr()
    assert cli.main(["member", "alias", "jaehee", "이재희", "잴리"]) == 0
    out = capsys.readouterr().out
    assert "재희 별칭 2개" in out and "부부 이체로 찾는 이름: 재희, 이재희, 잴리" in out
    assert cli.main(["member", "alias", "jaehee", "--remove", "잴리"]) == 0
    assert "재희, 이재희" in capsys.readouterr().out
    assert cli.main(["member", "alias", "jaehee"]) == 0  # 보기만
    assert cli.main(["member", "alias", "sangbeom", "재희"]) == 1
    assert "다른 구성원의 이름" in capsys.readouterr().err
    assert "이재희" not in caplog.text and "잴리" not in caplog.text  # 로그엔 건수만


def test_cli_preview_shows_sources_and_hint(isolated_env, capsys):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    cli.main(["member", "add", "jaehee", "--name", "재희"])
    c = connect(isolated_env / "data" / "hanjigap.db")
    c.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (1, ?, 'zip', 0, 'applied')",
        ("a" * 64,),
    )
    from hanjigap.db.members import get_member

    sb, jh = get_member(c, "sangbeom"), get_member(c, "jaehee")
    tx(c, sb, 1_000_000, "이재희")
    tx(c, sb, -300_000, "생활비", date="2026-09-10")
    tx(c, jh, 300_000, "모르는 이름", date="2026-09-10")
    c.close()
    capsys.readouterr()
    assert cli.main(["classify", "preview", "--month", "2026-09"]) == 0
    out = capsys.readouterr().out
    assert "정해진 방법  직접 지정 0 · 고정비 용돈 0 · 규칙 0 · 부부 이체 1 · 뱅샐 기본값 2" in out
    assert out.count("추천: 제외 · 부부 이체 같아요") == 2
