"""J2a: 결제수단 이름별 사용 현황."""

from collections import defaultdict

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.holdings import instrument_usage
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
    c = connect(tmp_path / "h.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def loaded(conn, parsed):
    sb = add_member(conn, "sangbeom", "상범")
    jh = add_member(conn, "jaehee", "재희")
    first_load(conn, sb, parsed, "2026-09")
    return sb, jh


def expected_from_db(conn, member_id):
    stats = defaultdict(lambda: {"n": 0, "dates": [], "지출": 0, "수입": 0, "이체": 0})
    for r in conn.execute(
        "SELECT instrument, tx_type, tx_date FROM transactions WHERE member_id = ?", (member_id,)
    ):
        s = stats[r["instrument"]]
        s["n"] += 1
        s["dates"].append(r["tx_date"])
        s[r["tx_type"]] += 1
    return stats


def test_usage_matches_transactions(conn, loaded):
    sb, _ = loaded
    usage = instrument_usage(conn, sb)
    expected = expected_from_db(conn, sb.id)
    assert {u.instrument for u in usage} == set(expected)
    assert (
        sum(u.count for u in usage)
        == conn.execute("SELECT count(*) FROM transactions").fetchone()[0]
    )
    for u in usage:
        e = expected[u.instrument]
        assert u.count == e["n"] == u.expense + u.income + u.transfer
        assert (u.first_date, u.last_date) == (min(e["dates"]), max(e["dates"]))
        assert (u.expense, u.income, u.transfer) == (e["지출"], e["수입"], e["이체"])
        assert not u.linked and u.link_label == "-"


def test_sorted_by_count(conn, loaded):
    counts = [u.count for u in instrument_usage(conn, loaded[0])]
    assert counts == sorted(counts, reverse=True)
    assert len(counts) == 14  # 익명화 샘플의 결제수단 수


def test_other_member_not_mixed(conn, loaded):
    _, jh = loaded
    assert instrument_usage(conn, jh) == []


def test_link_status_shown(conn, loaded):
    sb, _ = loaded
    acc = conn.execute(
        "INSERT INTO accounts (member_id, institution, name, kind) "
        "VALUES (?, 'A은행', '주거래 통장', 'checking')",
        (sb.id,),
    ).lastrowid
    card = conn.execute(
        "INSERT INTO cards (member_id, issuer, name, kind) VALUES (?, '가 카드', '생활', 'credit')",
        (sb.id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO instrument_links (member_id, instrument, target, account_id) "
        "VALUES (?, '입출금통장', 'account', ?)",
        (sb.id, acc),
    )
    conn.execute(
        "INSERT INTO instrument_links (member_id, instrument, target, card_id) "
        "VALUES (?, '카드 A', 'card', ?)",
        (sb.id, card),
    )
    conn.execute(
        "INSERT INTO instrument_links (member_id, instrument, target) "
        "VALUES (?, '카카오페이 간편결제', 'easy_pay')",
        (sb.id,),
    )
    labels = {u.instrument: u.link_label for u in instrument_usage(conn, sb)}
    assert labels["입출금통장"] == "계좌 · 주거래 통장"
    assert labels["카드 A"] == "카드 · 생활"
    assert labels["카카오페이 간편결제"] == "간편결제"
    assert sum(1 for v in labels.values() if v == "-") == 11


def test_link_of_other_member_not_applied(conn, loaded):
    # 재희가 같은 이름을 연결해도 상범의 거래에는 영향 없음
    _, jh = loaded
    conn.execute(
        "INSERT INTO instrument_links (member_id, instrument, target) "
        "VALUES (?, '카드 A', 'easy_pay')",
        (jh.id,),
    )
    usage = {u.instrument: u for u in instrument_usage(conn, loaded[0])}
    assert not usage["카드 A"].linked


# --- 명령어 ---------------------------------------------------------------------


@pytest.fixture
def cli_db(isolated_env, parsed, capsys):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    cli.main(["member", "add", "jaehee", "--name", "재희"])
    c = connect(isolated_env / "data" / "hanjigap.db")
    sb = get_member(c, "sangbeom")
    first_load(c, sb, parsed, "2026-09")
    c.execute(
        "INSERT INTO instrument_links (member_id, instrument, target) "
        "VALUES (?, '카카오페이 간편결제', 'easy_pay')",
        (sb.id,),
    )
    c.close()
    capsys.readouterr()
    return isolated_env


def test_cli_list(cli_db, capsys):
    assert cli.main(["instrument", "list", "--member", "sangbeom"]) == 0
    out = capsys.readouterr().out
    assert "상범 · 결제수단 14개 · 연결 안 됨 13개" in out
    assert "결제수단" in out and "처음 ~ 마지막" in out
    first_row = out.splitlines()[2]
    assert first_row.startswith("카드 A")
    assert "간편결제" in out


def test_cli_unlinked_only(cli_db, capsys):
    assert cli.main(["instrument", "list", "--member", "sangbeom", "--unlinked"]) == 0
    out = capsys.readouterr().out
    body = out.split("※")[0]
    assert "카카오페이 간편결제" not in body
    assert body.count("   -\n") == 13


def test_cli_no_transactions(cli_db, capsys):
    assert cli.main(["instrument", "list", "--member", "jaehee"]) == 0
    assert "아직 없습니다" in capsys.readouterr().out


def test_cli_unknown_member(cli_db, capsys):
    assert cli.main(["instrument", "list", "--member", "nobody"]) == 1
    assert "구성원을 찾을 수 없습니다" in capsys.readouterr().err


def test_cli_logs_counts_only(cli_db, capsys):
    cli.main(["instrument", "list", "--member", "sangbeom"])
    log_text = "".join(p.read_text(encoding="utf-8") for p in (cli_db / "logs").glob("*.log*"))
    assert "결제수단 14개" in log_text
    assert "카드 A" not in log_text and "입출금통장" not in log_text
