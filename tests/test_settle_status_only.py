"""G4: 거래가 그대로여도 재무현황(순자산) 표는 저장 (그 달 기록이 없거나 다를 때만)."""

import dataclasses
import datetime as dt

import pytest

import hanjigap.web.ledger_api as ledger_mod
from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.db.snapshots import latest_snapshot
from hanjigap.settle.apply import apply_month, status_needs_saving
from hanjigap.settle.pipeline import parse_export

FILE_NAME = "2025-10-20~2026-10-20.xlsx"


def tables_file(sample_bytes, changes=None):
    # 표를 붙이는 도우미는 test_snapshots와 같은 것을 쓴다
    from test_snapshots import with_status_tables

    return with_status_tables(sample_bytes, changes)


@pytest.fixture(scope="module")
def parsed(sample_bytes):
    return parse_export(tables_file(sample_bytes), None, FILE_NAME)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "s.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def me(conn):
    return add_member(conn, "sangbeom", "상범")


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def settled_batch(conn):
    return conn.execute("SELECT batch_id FROM month_settlements").fetchone()[0]


# --- 정산 함수 -------------------------------------------------------------------


def test_missing_snapshot_is_saved_alone(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-09")
    conn.execute("DELETE FROM bs_snapshots")  # 표 저장 기능 전에 정산한 달
    batch = settled_batch(conn)
    tx_before = count(conn, "transactions")
    assert status_needs_saving(conn, me, parsed, "2026-09")

    r = apply_month(conn, me, parsed, "2026-09")
    assert (r.saved, r.status_only, r.added, r.changed, r.removed) == (True, True, 0, 0, 0)
    snap = latest_snapshot(conn, me, "2026-09")
    assert snap is not None and snap.batch_id == r.batch_id and snap.net_worth == 6_259_590
    assert count(conn, "transactions") == tx_before
    assert settled_batch(conn) == batch  # 정산 기록은 그대로

    again = apply_month(conn, me, parsed, "2026-09")
    assert (again.saved, again.status_only) == (False, False)


def test_status_only_does_not_add_cashflow(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-09")
    cashflow = count(conn, "bs_cashflow")
    conn.execute("DELETE FROM bs_snapshots")
    apply_month(conn, me, parsed, "2026-09")
    assert count(conn, "bs_cashflow") == cashflow


def test_changed_tables_are_saved(conn, me, sample_bytes, parsed):
    apply_month(conn, me, parsed, "2026-09")
    other = parse_export(
        tables_file(sample_bytes, {44: ["연금 자산", "IRP", None, 500_000]}), None, FILE_NAME
    )
    assert status_needs_saving(conn, me, other, "2026-09")
    r = apply_month(conn, me, other, "2026-09")
    assert r.status_only and count(conn, "bs_snapshots") == 2
    irp = [h for h in latest_snapshot(conn, me, "2026-09").of("asset") if h.name == "IRP"]
    assert irp[0].amount == 500_000


def test_same_tables_save_nothing(conn, me, parsed):
    apply_month(conn, me, parsed, "2026-09")
    assert not status_needs_saving(conn, me, parsed, "2026-09")
    assert apply_month(conn, me, parsed, "2026-09").saved is False
    assert count(conn, "bs_snapshots") == 1


def test_file_without_tables(conn, me, sample_bytes, parsed):
    apply_month(conn, me, parsed, "2026-09")
    conn.execute("DELETE FROM bs_snapshots")
    plain = parse_export(sample_bytes, None, FILE_NAME)
    assert not status_needs_saving(conn, me, plain, "2026-09")
    assert not status_needs_saving(conn, me, dataclasses.replace(parsed, status=None), "2026-09")
    assert apply_month(conn, me, plain, "2026-09").saved is False


def test_cli_status_only(isolated_env, sample_bytes, capsys):
    path = isolated_env / FILE_NAME
    path.write_bytes(tables_file(sample_bytes))
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    args = ["settle", "apply", str(path), "--member", "sangbeom", "--month", "2026-09", "--yes"]
    assert cli.main(args) == 0
    with connect(isolated_env / "data" / "hanjigap.db") as c:
        c.execute("DELETE FROM bs_snapshots")
    capsys.readouterr()
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "재무현황(순자산) 표만 새로 저장합니다" in out and "재무현황만 저장" in out
    assert cli.main(args) == 0
    assert "변경 사항이 없어 저장하지 않았습니다" in capsys.readouterr().out


# --- 웹 업로드 -------------------------------------------------------------------


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(ledger_mod, "today", lambda _r: dt.date(2026, 10, 29))


def upload(api, data):
    return api.post(
        "/api/uploads", files={"file": (FILE_NAME, data, "application/octet-stream")}
    ).json()


def test_web_status_only(api, api_conn, sample_bytes, fixed_today):
    data = tables_file(sample_bytes)
    first = upload(api, data)
    assert (first["will_save"], first["status_only"]) == (True, False)
    api.post(f"/api/uploads/{first['upload_id']}/apply")
    api_conn.execute("DELETE FROM bs_snapshots")

    p = upload(api, data)
    assert (p["kind"], p["will_save"], p["status_only"]) == ("resettle", True, True)
    assert not any("바뀐 거래가 없어요" in w for w in p["warnings"])
    done = api.post(f"/api/uploads/{p['upload_id']}/apply").json()
    assert (done["saved"], done["status_only"]) == (True, True)
    assert done["message"] == "거래는 그대로라서 재무현황(순자산)만 저장했어요"
    assert api.get("/api/home").json()["assets"]["has_banksalad"] is True

    p = upload(api, data)
    assert (p["will_save"], p["status_only"]) == (False, False)
    assert any("바뀐 거래가 없어요" in w for w in p["warnings"])
    api.delete(f"/api/uploads/{p['upload_id']}")
