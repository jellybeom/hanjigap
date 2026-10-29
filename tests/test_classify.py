"""J7a: 수입·지출·저축·제외 분류 (규칙 · 거래별 직접 지정 · 뱅샐 기본값 · 월 요약 · 미리보기)."""

import hashlib
import itertools
import logging
import sqlite3

import pytest

from hanjigap import cli
from hanjigap.db.class_rules import (
    ClassError,
    add_rule,
    clear_tx_class,
    delete_rule,
    get_rule,
    list_rules,
    reorder_rules,
    rule_for_tx,
    set_rule_active,
    set_tx_class,
    update_rule,
)
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member, get_member
from hanjigap.db.migrate import load_migrations, migrate
from hanjigap.ledger.classify import classify, default_class
from hanjigap.settle.first_load import first_load
from hanjigap.settle.pipeline import parse_export

_n = itertools.count(1)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "c.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def sb(conn):
    m = add_member(conn, "sangbeom", "상범")
    add_member(conn, "jaehee", "재희")
    conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (1, ?, 'zip', 0, 'applied')",
        ("a" * 64,),
    )
    return m


@pytest.fixture
def jh(conn, sb):
    return get_member(conn, "jaehee")


def tx(conn, member, amount, tx_type="지출", category="", content="", sub="", date="2026-09-10",instrument="카드"):  # fmt: skip
    n = next(_n)
    key = hashlib.sha256(f"c{n}".encode()).hexdigest()[:32]
    conn.execute(
        "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, tx_time, "
        "tx_type, category, subcategory, content, amount, instrument, batch_id, row_no) "
        "VALUES (?, ?, 1, ?, ?, ?, '10:00:00', ?, ?, ?, ?, ?, ?, 1, ?)",
        (member.id, key, f"b{n}", date[:7], date, tx_type, category, sub, content, amount,
         instrument, n),
    )  # fmt: skip
    return key


def one(conn, key):
    return next(i for i in classify(conn, "2026-09").items if i.tx_key == key)


# --- 뱅샐 기본값 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tx_type", "category", "amount", "expected"),
    [
        ("지출", "식비", -10_000, "expense"),
        ("지출", "식비", 3_000, "expense"),  # 환불도 지출 칸 (지출에서 빠짐)
        ("수입", "급여", 3_000_000, "income"),
        ("수입", "금융수입", 210, "income"),
        ("이체", "카드대금", -500_000, "excluded"),
        ("이체", "내계좌이체", -100_000, "excluded"),
        ("이체", "내계좌이체", 100_000, "excluded"),
        ("이체", "저축", -300_000, "saving"),
        ("이체", "투자", 200_000, "saving"),
        ("이체", "현금", -50_000, "expense"),
        ("이체", "현금", 50_000, "excluded"),
        ("이체", "이체", -30_000, "unconfirmed"),
        ("이체", "이체", 30_000, "unconfirmed"),
        ("이체", "", -1, "unconfirmed"),
        ("이체", "새 분류", -1, "unconfirmed"),  # 모르는 이체는 확인 필요
    ],
)
def test_default_class(tx_type, category, amount, expected):
    cls, _, _ = default_class({"tx_type": tx_type, "category": category, "amount": amount})
    assert cls == expected


def test_cash_withdrawal_category(conn, sb):
    key = tx(conn, sb, -50_000, "이체", "현금", "ATM 출금")
    item = one(conn, key)
    assert (item.cls, item.display_category, item.reason) == ("expense", "현금", "현금 출금")


def test_unconfirmed_suggestion(conn, sb):
    out = one(conn, tx(conn, sb, -30_000, "이체", "이체", "부모님"))
    back = one(conn, tx(conn, sb, 30_000, "이체", "이체", "점심값"))
    assert (out.suggestion, back.suggestion) == ("expense", "income")
    assert one(conn, tx(conn, sb, -1, "지출", "식비")).suggestion is None


def test_web_override_used(conn, sb):
    key = tx(conn, sb, -30_000, "이체", "이체", "관리비")
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, tx_type, category) "
        "VALUES (?, ?, '지출', '주거/통신')",
        (sb.id, key),
    )
    item = one(conn, key)
    assert (item.cls, item.display_category) == ("expense", "주거/통신")


# --- 규칙 ----------------------------------------------------------------------


def test_income_rules(conn, sb, jh):
    add_rule(conn, result="수입", member=sb, direction="in", pattern="OO공사급여",
             result_category="급여")  # fmt: skip
    add_rule(conn, result="income", member=sb, direction="in", pattern="당직비",
             result_category="수당")  # fmt: skip
    pay = tx(conn, sb, 3_000_000, "이체", "이체", "OO공사급여")
    duty = tx(conn, sb, 120_000, "이체", "이체", "9월 당직비")
    refund = tx(conn, sb, -120_000, "이체", "이체", "9월 당직비 반납")  # 나간 돈이라 안 맞음
    other = tx(conn, jh, 120_000, "이체", "이체", "9월 당직비")  # 재희 거래라 안 맞음
    assert [one(conn, k).display_category for k in (pay, duty)] == ["급여", "수당"]
    assert one(conn, duty).reason == "규칙: 들어온 돈 · 내용 「당직비」 → 수입 › 수당"
    assert one(conn, refund).cls == "unconfirmed"
    assert one(conn, other).cls == "unconfirmed"


def test_rule_conditions(conn, sb):
    rule = add_rule(conn, result="excluded", tx_type="이체", category="이체", amount=-500_000,
                    instrument="주거래 통장")  # fmt: skip
    assert rule.amount == 500_000  # 부호 없이 저장
    hit = tx(conn, sb, -500_000, "이체", "이체", "x", instrument="주거래 통장")
    miss_amount = tx(conn, sb, -500_001, "이체", "이체", "x", instrument="주거래 통장")
    miss_inst = tx(conn, sb, -500_000, "이체", "이체", "x", instrument="다른 통장")
    assert one(conn, hit).cls == "excluded"
    assert one(conn, miss_amount).cls == one(conn, miss_inst).cls == "unconfirmed"


def test_pattern_case_insensitive(conn, sb):
    add_rule(conn, result="expense", pattern="netflix")
    assert one(conn, tx(conn, sb, -17_000, "이체", "이체", "NETFLIX.COM")).cls == "expense"


def test_category_from_bank_when_rule_has_none(conn, sb):
    add_rule(conn, result="expense", category="생활", subcategory="마트")
    item = one(conn, tx(conn, sb, -1, "지출", "생활", sub="마트"))
    assert (item.display_category, item.source) == ("생활", "rule")


def test_newest_rule_first_and_reorder(conn, sb):
    first = add_rule(conn, result="income", pattern="정산")
    second = add_rule(conn, result="expense", pattern="정산", result_category="식비")
    assert [r.id for r in list_rules(conn)] == [second.id, first.id]
    key = tx(conn, sb, 8_000, "이체", "이체", "점심 정산")
    assert one(conn, key).cls == "expense"  # 새 규칙이 먼저
    reorder_rules(conn, [first.id, second.id])
    assert one(conn, key).cls == "income"
    with pytest.raises(ClassError, match="모든 규칙을 한 번씩"):
        reorder_rules(conn, [first.id])
    with pytest.raises(ClassError, match="모든 규칙을 한 번씩"):
        reorder_rules(conn, [first.id, first.id])


def test_inactive_and_deleted(conn, sb):
    rule = add_rule(conn, result="excluded", pattern="배우자")
    key = tx(conn, sb, 100_000, "이체", "이체", "배우자")
    set_rule_active(conn, rule.id, False)
    assert one(conn, key).cls == "unconfirmed"
    set_rule_active(conn, rule.id, True)
    assert one(conn, key).cls == "excluded"
    delete_rule(conn, rule.id)
    assert one(conn, key).cls == "unconfirmed"
    with pytest.raises(ClassError, match="찾을 수 없어요"):
        get_rule(conn, rule.id)


def test_update_rule(conn, sb, jh):
    rule = add_rule(conn, result="income", direction="in", pattern="세탁비", member=sb)
    r = update_rule(conn, rule.id, result_category="수당", member=None, memo="분기마다")
    assert (r.result_category, r.member_id, r.memo) == ("수당", None, "분기마다")
    r = update_rule(conn, rule.id, pattern="", amount=30_000)  # 글자 대신 금액
    assert (r.pattern, r.amount) == (None, 30_000)
    with pytest.raises(ClassError, match="조건이 하나 이상"):
        update_rule(conn, rule.id, direction="any", amount=0)
    with pytest.raises(ClassError, match="바꿀 수 없는 칸"):
        update_rule(conn, rule.id, position=1)
    with pytest.raises(ClassError, match="바꿀 내용이 없어요"):
        update_rule(conn, rule.id)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"result": "income"}, "조건이 하나 이상"),
        ({"result": "용돈", "pattern": "x"}, "분류를 알 수 없어요"),
        ({"result": "income", "direction": "옆", "pattern": "x"}, "방향을 알 수 없어요"),
        ({"result": "income", "tx_type": "입금"}, "뱅샐 타입은"),
        ({"result": "income", "subcategory": "마트"}, "분류도 입력"),
        ({"result": "income", "pattern": "가" * 51}, "50자 이하"),
        ({"result": "income", "pattern": "x", "result_category": "가" * 31}, "30자 이하"),
        ({"result": "income", "pattern": "110-123-456789"}, "끝 4자리만"),
        ({"result": "income", "amount": "1000"}, "정수"),
    ],
)
def test_rule_rejects(conn, sb, kwargs, message):
    with pytest.raises(ClassError, match=message):
        add_rule(conn, **kwargs)


def test_rule_without_condition_rejected_in_db(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO class_rules (position, result) VALUES (1, 'income')")


# --- 앞으로도 이렇게 ---------------------------------------------------------------


def test_rule_for_tx(conn, sb):
    key = tx(conn, sb, 120_000, "이체", "이체", "3분기 세탁비")
    rule = rule_for_tx(conn, sb, key, result="수입", result_category="수당", created_by=sb)
    assert (rule.member_id, rule.direction, rule.pattern) == (sb.id, "in", "3분기 세탁비")
    assert one(conn, key).cls == "income"


def test_rule_for_tx_rejects(conn, sb):
    empty = tx(conn, sb, -1, "이체", "이체", "")
    long = tx(conn, sb, -1, "이체", "이체", "가" * 51)
    with pytest.raises(ClassError, match="내용이 비어"):
        rule_for_tx(conn, sb, empty, result="expense")
    with pytest.raises(ClassError, match="50자를 넘어서"):
        rule_for_tx(conn, sb, long, result="expense")
    with pytest.raises(ClassError, match="거래를 찾을 수 없어요"):
        rule_for_tx(conn, sb, "0" * 32, result="expense")


# --- 거래별 직접 지정 --------------------------------------------------------------


def test_manual_beats_rule_and_default(conn, sb):
    add_rule(conn, result="income", pattern="정산")
    key = tx(conn, sb, 8_000, "이체", "이체", "점심 정산")
    set_tx_class(conn, sb, key, "지출", result_category="식비", classified_by=sb)
    item = one(conn, key)
    assert (item.cls, item.display_category, item.source) == ("expense", "식비", "manual")
    set_tx_class(conn, sb, key, "excluded")  # 다시 정하면 바뀐다
    assert one(conn, key).cls == "excluded"
    assert clear_tx_class(conn, sb, key)
    assert one(conn, key).cls == "income"  # 규칙으로 돌아감
    assert not clear_tx_class(conn, sb, key)


def test_manual_rejects(conn, sb, jh):
    key = tx(conn, sb, -1, "이체", "이체", "x")
    with pytest.raises(ClassError, match="거래를 찾을 수 없어요"):
        set_tx_class(conn, jh, key, "expense")  # 다른 사람 거래
    with pytest.raises(ClassError, match="분류를 알 수 없어요"):
        set_tx_class(conn, sb, key, "unconfirmed")


def test_manual_survives_resettle(conn, sb):
    key = tx(conn, sb, -1, "이체", "이체", "x")
    set_tx_class(conn, sb, key, "expense")
    conn.execute("DELETE FROM transactions")
    assert conn.execute("SELECT count(*) FROM tx_classes").fetchone()[0] == 1


# --- 월 요약 -------------------------------------------------------------------


def test_month_report(conn, sb, jh):
    tx(conn, sb, 3_000_000, "수입", "급여", "급여")
    tx(conn, sb, -50_000, "지출", "식비", "식당")
    tx(conn, sb, 8_000, "지출", "식비", "환불")
    tx(conn, jh, -200_000, "지출", "생활", "마트")
    tx(conn, jh, -40_000, "이체", "현금", "ATM")
    tx(conn, sb, -300_000, "이체", "저축", "적금")
    tx(conn, sb, 100_000, "이체", "투자", "매도")
    tx(conn, sb, -900_000, "이체", "카드대금", "카드")
    tx(conn, sb, -30_000, "이체", "이체", "부모님")
    tx(conn, sb, 15_000, "이체", "이체", "정산")
    tx(conn, sb, -1, "지출", "식비", date="2026-10-01")  # 다른 달
    r = classify(conn, "2026-09")
    assert (r.income, r.expense, r.saving) == (3_000_000, 282_000, 200_000)
    assert r.net == 3_000_000 - 282_000 - 200_000
    assert r.counts() == {
        "income": 1,
        "expense": 4,
        "saving": 2,
        "excluded": 1,
        "unconfirmed": 2,
    }
    assert r.by_category("expense") == {"생활": 200_000, "식비": 42_000, "현금": 40_000}
    assert r.saving_flow() == (300_000, 100_000)
    assert r.unconfirmed_flow() == (1, 15_000, 1, 30_000)
    assert r.by_member() == {
        sb.id: {"income": 3_000_000, "expense": 42_000, "saving": 200_000},
        jh.id: {"income": 0, "expense": 240_000, "saving": 0},
    }
    assert classify(conn, "2026-09", member=jh).expense == 240_000
    assert len(classify(conn, "2026-09", "2026-10").items) == 11


def test_bad_period(conn, sb):
    with pytest.raises(ClassError, match="기간"):
        classify(conn, "2026-09", "2026-08")
    with pytest.raises(ClassError, match="YYYY-MM"):
        classify(conn, "2026-9")


def test_upgrade_from_version_8(tmp_path):
    c = connect(tmp_path / "v8.db")
    migrate(c, load_migrations()[:8])
    assert [m.name for m in migrate(c)][0] == "classify"
    c.close()


# --- 미리보기 명령 ---------------------------------------------------------------


@pytest.fixture
def loaded(isolated_env, sample_bytes, capsys):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    c = connect(isolated_env / "data" / "hanjigap.db")
    parsed = parse_export(sample_bytes, None, "2025-10-20~2026-10-20.xlsx")
    first_load(c, get_member(c, "sangbeom"), parsed, "2026-09")
    c.close()
    capsys.readouterr()
    return isolated_env


def test_cli_preview(loaded, capsys, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap")
    assert cli.main(["classify", "preview", "--month", "2026-09"]) == 0
    out = capsys.readouterr().out
    assert "분류 미리보기 2026-09 (모든 구성원) · 거래 180건 · 규칙 0개 적용" in out
    assert "확인 필요 거래 7건" in out and "추천: 지출" in out
    assert "남은 돈" in out and "지출 분류" in out
    assert "상대방" not in caplog.text and "배우자" not in caplog.text  # 로그엔 건수만


def test_cli_preview_with_rule_and_member(loaded, capsys):
    c = connect(loaded / "data" / "hanjigap.db")
    add_rule(c, result="excluded", direction="in", pattern="배우자")
    c.close()
    assert cli.main(["classify", "preview", "--month", "2026-09", "--member", "sangbeom"]) == 0
    out = capsys.readouterr().out
    assert "(상범)" in out and "규칙 1개 적용" in out and "확인 필요 거래 4건" in out


def test_cli_preview_errors(loaded, capsys):
    assert cli.main(["classify", "preview", "--member", "nobody"]) == 1
    assert cli.main(["classify", "preview", "--month", "2026-13"]) == 1
    assert "[실패]" in capsys.readouterr().err
