"""J2b: 계좌·카드 등록·수정·해지와 결제수단 연결."""

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.holdings import (
    HoldingsError,
    add_account,
    add_card,
    close_account,
    close_card,
    get_card,
    instrument_usage,
    link_instrument,
    links_by_target,
    list_accounts,
    list_cards,
    reopen_account,
    unlink_instrument,
    update_account,
    update_card,
)
from hanjigap.db.members import add_member, get_member
from hanjigap.db.migrate import migrate
from hanjigap.settle.first_load import first_load
from hanjigap.settle.pipeline import parse_export

FILE_NAME = "2025-10-20~2026-10-20.xlsx"


@pytest.fixture(scope="module")
def parsed(sample_bytes):
    return parse_export(sample_bytes, None, FILE_NAME)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "m.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def people(conn):
    return add_member(conn, "sangbeom", "상범"), add_member(conn, "jaehee", "재희")


def account(conn, member, **over):
    values = dict(institution="A은행", name="주거래 통장", kind="입출금")
    values.update(over)
    return add_account(conn, member, **values)


def card(conn, member, **over):
    values = dict(issuer="가 카드", name="생활 신용카드", kind="신용")
    values.update(over)
    return add_card(conn, member, **values)


# --- 계좌 ----------------------------------------------------------------------


def test_add_account_normalizes(conn, people):
    acc = account(conn, people[0], last4=" 1234 ", opened_on="2015.03", color="#143D30", memo=" ")
    assert (acc.kind, acc.kind_label) == ("checking", "입출금")
    assert (acc.number_last4, acc.opened_on, acc.color, acc.memo) == (
        "1234",
        "2015-03",
        "#143d30",
        None,
    )
    assert acc.active


@pytest.mark.parametrize("kind", ["checking", "Checking", "입출금", "ISA", "isa", "연금저축"])
def test_kind_accepts_code_or_label(conn, people, kind):
    account(conn, people[0], kind=kind)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"kind": "통장"}, "종류를 알 수 없어요"),
        ({"last4": "123"}, "끝 4자리"),
        ({"last4": "1234-5678"}, "끝 4자리"),
        ({"opened_on": "2015-02-30"}, "개설일 형식"),
        ({"color": "green"}, "#rrggbb"),
        ({"memo": "110-123-456789"}, "전체 계좌·카드번호"),
        ({"number_hint": "1234 5678 9012 3456"}, "전체 계좌·카드번호"),
        ({"name": "  "}, "입력하세요"),
        ({"name": "가" * 101}, "100자"),
    ],
)
def test_add_account_rejects(conn, people, over, message):
    with pytest.raises(HoldingsError, match=message):
        account(conn, people[0], **over)


def test_duplicate_name_message(conn, people):
    account(conn, people[0])
    with pytest.raises(HoldingsError, match="같은 이름의 계좌"):
        account(conn, people[0], institution="B은행")


def test_update_and_clear(conn, people):
    acc = account(conn, people[0], memo="생활비", last4="1234")
    acc = update_account(conn, acc.id, memo="", last4="", name="생활비 통장")
    assert (acc.memo, acc.number_last4, acc.name) == (None, None, "생활비 통장")
    with pytest.raises(HoldingsError, match="바꿀 내용이 없어요"):
        update_account(conn, acc.id)
    with pytest.raises(HoldingsError, match="찾을 수 없어요"):
        update_account(conn, 999, memo="x")


def test_close_and_reopen(conn, people):
    acc = account(conn, people[0], opened_on="2020-01-01")
    with pytest.raises(HoldingsError, match="개설일보다 빨라요"):
        close_account(conn, acc.id, "2019-12-31")
    acc = close_account(conn, acc.id, "2026-10-28")
    assert not acc.active
    assert list_accounts(conn) == []
    assert [a.id for a in list_accounts(conn, include_closed=True)] == [acc.id]
    assert reopen_account(conn, acc.id).active


def test_list_by_member(conn, people):
    sb, jh = people
    account(conn, sb)
    account(conn, jh, name="급여 통장")
    assert [a.name for a in list_accounts(conn, jh)] == ["급여 통장"]
    assert len(list_accounts(conn)) == 2


# --- 카드 ----------------------------------------------------------------------


def test_add_card_full(conn, people):
    sb, jh = people
    pay = account(conn, jh, name="급여 통장")
    c = card(
        conn,
        sb,
        last4="0937",
        pay_day=14,
        period_start_day=15,
        period_month_offset=-1,
        pay_account_id=pay.id,
        annual_fee=15000,
    )
    assert (c.kind, c.pay_day, c.pay_account_id) == ("credit", 14, pay.id)
    assert c.period_label == "전월 15일 ~ 당월 14일"


@pytest.mark.parametrize(
    ("start", "offset", "label"),
    [
        (None, None, "전월 1일 ~ 말일"),
        (1, 0, "당월 1일 ~ 말일"),
        (31, -2, "전전월 31일 ~ 전월 30일"),
    ],
)
def test_period_label(conn, people, start, offset, label):
    c = card(conn, people[0], period_start_day=start, period_month_offset=offset)
    assert c.period_label == label


def test_check_card_period_label(conn, people):
    assert card(conn, people[0], kind="체크").period_label == "바로 출금"


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"kind": "직불"}, "종류를 알 수 없어요"),
        ({"pay_day": 32}, "결제일"),
        ({"period_start_day": 1}, "함께 입력"),
        ({"period_start_day": 1, "period_month_offset": -3}, "몇 달 전"),
        ({"pay_account_id": 999}, "계좌를 찾을 수 없어요"),
        ({"annual_fee": -1}, "0 이상"),
        ({"number_hint": "9410-1234-5678-9012"}, "전체 계좌·카드번호"),
    ],
)
def test_add_card_rejects(conn, people, over, message):
    with pytest.raises(HoldingsError, match=message):
        card(conn, people[0], **over)


def test_card_update_clears(conn, people):
    sb, _ = people
    pay = account(conn, sb)
    c = card(conn, sb, pay_day=14, period_start_day=15, period_month_offset=-1)
    c = update_card(conn, c.id, pay_account_id=pay.id)
    assert c.pay_account_id == pay.id
    c = update_card(
        conn, c.id, pay_day=0, pay_account_id=0, period_start_day=0, period_month_offset=0
    )
    assert (c.pay_day, c.pay_account_id, c.period_start_day) == (None, None, None)


def test_card_close(conn, people):
    c = card(conn, people[0])
    close_card(conn, c.id, "2026-02-28")
    assert list_cards(conn) == [] and get_card(conn, c.id).closed_on == "2026-02-28"


# --- 결제수단 연결 ---------------------------------------------------------------


@pytest.fixture
def loaded(conn, people, parsed):
    first_load(conn, people[0], parsed, "2026-09")
    return people


def test_link_account_card_easy_pay(conn, loaded):
    sb, _ = loaded
    acc = account(conn, sb)
    c = card(conn, sb)
    link_instrument(conn, sb, "입출금통장", account_id=acc.id)
    link_instrument(conn, sb, "카드 A", card_id=c.id)
    link_instrument(conn, sb, "카카오페이 간편결제", easy_pay=True)
    labels = {u.instrument: u.link_label for u in instrument_usage(conn, sb)}
    assert labels["입출금통장"] == "계좌 · 주거래 통장"
    assert labels["카드 A"] == "카드 · 생활 신용카드"
    assert labels["카카오페이 간편결제"] == "간편결제"
    assert links_by_target(conn, "accounts") == {acc.id: 1}


def test_relink_replaces(conn, loaded):
    sb, _ = loaded
    acc = account(conn, sb)
    c = card(conn, sb)
    link_instrument(conn, sb, "카드 A", account_id=acc.id)
    link_instrument(conn, sb, "카드 A", card_id=c.id)
    row = conn.execute("SELECT target, account_id, card_id FROM instrument_links").fetchall()
    assert [tuple(r) for r in row] == [("card", None, c.id)]


def test_link_rejects(conn, loaded):
    sb, jh = loaded
    acc = account(conn, sb)
    other = account(conn, jh, name="급여 통장")
    with pytest.raises(HoldingsError, match="하나만"):
        link_instrument(conn, sb, "카드 A", account_id=acc.id, easy_pay=True)
    with pytest.raises(HoldingsError, match="하나만"):
        link_instrument(conn, sb, "카드 A")
    with pytest.raises(HoldingsError, match="결제수단 이름이 없어요"):
        link_instrument(conn, sb, "카드 Z", easy_pay=True)
    with pytest.raises(HoldingsError, match="다른 사람의 계좌"):
        link_instrument(conn, sb, "입출금통장", account_id=other.id)


def test_link_unknown_with_force(conn, loaded):
    sb, _ = loaded
    link_instrument(conn, sb, "새 카드", easy_pay=True, allow_unknown=True)
    assert unlink_instrument(conn, sb, "새 카드")
    assert not unlink_instrument(conn, sb, "새 카드")


def test_closed_account_keeps_link(conn, loaded):
    sb, _ = loaded
    acc = account(conn, sb)
    link_instrument(conn, sb, "입출금통장", account_id=acc.id)
    close_account(conn, acc.id, "2026-10-28")
    usage = {u.instrument: u for u in instrument_usage(conn, sb)}
    assert usage["입출금통장"].link_label == "계좌 · 주거래 통장"


# --- 명령어 ---------------------------------------------------------------------


@pytest.fixture
def cli_db(isolated_env, parsed, capsys):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    cli.main(["member", "add", "jaehee", "--name", "재희"])
    c = connect(isolated_env / "data" / "hanjigap.db")
    first_load(c, get_member(c, "sangbeom"), parsed, "2026-09")
    c.close()
    capsys.readouterr()
    return isolated_env


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_account_flow(cli_db, capsys):
    code, out, _ = run(
        capsys,
        "account", "add", "--member", "sangbeom", "--institution", "A은행",
        "--name", "주거래 통장", "--kind", "입출금", "--last4", "1234", "--opened", "2015-03",
    )  # fmt: skip
    assert code == 0 and "등록했습니다" in out
    assert "A은행 · 입출금 · 끝자리 1234 · 개설 2015-03" in out

    code, out, _ = run(capsys, "account", "update", "1", "--memo", "생활비")
    assert code == 0 and "수정했습니다" in out

    code, out, _ = run(capsys, "account", "list")
    assert "   1  상범" in out and "주거래 통장" in out

    code, out, _ = run(capsys, "account", "close", "1", "--date", "2026-10-28")
    assert code == 0 and "해지 2026-10-28" in out
    assert "등록된 계좌가 없습니다" in run(capsys, "account", "list")[1]
    assert "해지 2026-10-28" in run(capsys, "account", "list", "--all")[1]
    assert run(capsys, "account", "reopen", "1")[0] == 0


def test_cli_card_flow(cli_db, capsys):
    run(capsys, "account", "add", "--member", "sangbeom", "--institution", "A은행",
        "--name", "주거래 통장", "--kind", "checking")  # fmt: skip
    code, out, _ = run(
        capsys,
        "card", "add", "--member", "sangbeom", "--issuer", "가 카드", "--name", "생활 신용카드",
        "--kind", "신용", "--last4", "0937", "--pay-day", "14", "--period", "15", "-1",
        "--pay-account", "1", "--fee", "15000",
    )  # fmt: skip
    assert code == 0
    assert "결제일 14일 (전월 15일 ~ 당월 14일)" in out
    assert "결제 계좌 #1 주거래 통장" in out and "연회비 15,000원" in out


def test_cli_link_flow(cli_db, capsys):
    run(capsys, "card", "add", "--member", "sangbeom", "--issuer", "가 카드",
        "--name", "생활 신용카드", "--kind", "credit")  # fmt: skip
    code, out, _ = run(
        capsys, "instrument", "link", "카드 A", "--member", "sangbeom", "--card", "1"
    )
    assert code == 0 and "「카드 A」 → 카드 #1 생활 신용카드" in out
    code, out, _ = run(
        capsys, "instrument", "link", "카카오페이 간편결제", "--member", "sangbeom", "--easy-pay"
    )
    assert code == 0
    out = run(capsys, "instrument", "list", "--member", "sangbeom")[1]
    assert "연결 안 됨 12개" in out
    assert "결제수단 1개 연결" in run(capsys, "card", "list")[1]

    code, out, _ = run(capsys, "instrument", "unlink", "카드 A", "--member", "sangbeom")
    assert code == 0 and "풀었습니다" in out
    code, _, err = run(capsys, "instrument", "unlink", "카드 A", "--member", "sangbeom")
    assert code == 1 and "연결되어 있지 않아요" in err


def test_cli_errors(cli_db, capsys):
    code, _, err = run(
        capsys, "instrument", "link", "없는 이름", "--member", "sangbeom", "--easy-pay"
    )
    assert code == 1 and "결제수단 이름이 없어요" in err
    code, _, err = run(
        capsys, "account", "add", "--member", "sangbeom", "--institution", "A은행",
        "--name", "통장", "--kind", "입출금", "--hint", "110-123-4567890",
    )  # fmt: skip
    assert code == 1 and "끝 4자리만 저장" in err
    code, _, err = run(capsys, "card", "update", "9", "--memo", "x")
    assert code == 1 and "카드를 찾을 수 없어요" in err


def test_cli_logs_without_names(cli_db, capsys):
    run(capsys, "account", "add", "--member", "sangbeom", "--institution", "A은행",
        "--name", "비밀 통장", "--kind", "입출금")  # fmt: skip
    run(capsys, "instrument", "link", "입출금통장", "--member", "sangbeom", "--account", "1")
    logs = "".join(p.read_text(encoding="utf-8") for p in (cli_db / "logs").glob("*.log*"))
    assert "account add (sangbeom): id=1" in logs
    assert "비밀 통장" not in logs and "입출금통장" not in logs
