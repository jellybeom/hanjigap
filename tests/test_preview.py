import dataclasses
import datetime as dt

import pytest

from hanjigap import cli
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.importer.fingerprint import assign_keys
from hanjigap.settle.pipeline import ExportInvalid, ParsedExport, parse_export
from hanjigap.settle.preview import (
    PreviewError,
    parse_year_month,
    preview_month,
    previous_month,
)

FILE_NAME = "2025-10-20~2026-10-20.xlsx"
YM = "2026-09"


@pytest.fixture(scope="module")
def parsed(sample_bytes) -> ParsedExport:
    return parse_export(sample_bytes, None, FILE_NAME)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "p.db")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def members(conn):
    return add_member(conn, "sangbeom", "상범"), add_member(conn, "jaehee", "재희")


def month_rows(parsed, ym=YM):
    y, m = map(int, ym.split("-"))
    return [k for k in parsed.keyed if (k.row.date.year, k.row.date.month) == (y, m)]


def store(conn, member, keyed, ym=YM, status="settled", sha="a" * 64):
    """D2 대신 DB에 '이미 정산된 상태'를 만든다."""
    bid = conn.execute(
        "INSERT INTO import_batches (member_id, file_sha256, container, row_count, status) "
        "VALUES (?, ?, 'xlsx', ?, 'applied')",
        (member.id, sha, len(keyed)),
    ).lastrowid
    for k in keyed:
        r = k.row
        conn.execute(
            "INSERT INTO transactions (member_id, tx_key, seq, base_key, year_month, tx_date, "
            "tx_time, tx_type, category, subcategory, content, amount, currency, instrument, "
            "memo, batch_id, row_no) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                member.id,
                k.tx_key,
                k.seq,
                k.base_key,
                ym,
                r.date.isoformat(),
                r.time.isoformat(),
                r.tx_type,
                r.category,
                r.subcategory,
                r.content,
                r.amount,
                r.currency,
                r.instrument,
                r.memo,
                bid,
                r.row_no,
            ),
        )
    conn.execute(
        "INSERT INTO month_settlements (member_id, year_month, status, batch_id, tx_count, "
        "settled_at) VALUES (?, ?, ?, ?, ?, '2026-10-03T01:00:00Z')",
        (member.id, ym, status, bid, len(keyed)),
    )
    return bid


def with_rows(parsed: ParsedExport, rows) -> ParsedExport:
    """거래 일부를 바꾼 파일을 흉내 낸다."""
    keyed = assign_keys(rows)
    return dataclasses.replace(parsed, keyed=keyed)


# --- 월 계산 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (dt.date(2026, 11, 3), "2026-10"),
        (dt.date(2027, 1, 1), "2026-12"),
        (dt.date(2026, 3, 31), "2026-02"),
    ],
)
def test_previous_month(today, expected):
    assert previous_month(today) == expected


@pytest.mark.parametrize("value", ["2026-9", "2026/09", "2026-13", "202609", "abc"])
def test_bad_year_month(value):
    with pytest.raises(PreviewError, match="YYYY-MM"):
        parse_year_month(value)


def test_month_outside_file(conn, members, parsed):
    with pytest.raises(PreviewError, match="파일 기간"):
        preview_month(conn, members[0], parsed, "2026-11")


@pytest.mark.parametrize(
    ("ym", "span"), [("2026-10", "10-01 ~ 10-20"), ("2025-10", "10-20 ~ 10-31")]
)
def test_partial_month_rejected(conn, members, parsed, ym, span):
    with pytest.raises(PreviewError, match=span):
        preview_month(conn, members[0], parsed, ym)


# --- 비교 ----------------------------------------------------------------------


def test_empty_db_all_new(conn, members, parsed):
    p = preview_month(conn, members[0], parsed, YM)
    assert p.kind == "new" and p.previous is None
    assert len(p.added) == len(p.rows) == 180
    assert (len(p.changed), len(p.removed), p.unchanged) == (0, 0, 0)
    assert p.month.label == YM and p.month.count == 180
    assert p.warnings == []


def test_same_data_all_unchanged(conn, members, parsed):
    store(conn, members[0], month_rows(parsed))
    p = preview_month(conn, members[0], parsed, YM)
    assert p.kind == "resettle" and p.previous.status == "settled"
    assert p.unchanged == 180 and not p.has_changes


def test_baseline_becomes_settled(conn, members, parsed):
    store(conn, members[0], month_rows(parsed), status="baseline")
    assert preview_month(conn, members[0], parsed, YM).kind == "baseline_to_settled"


def test_changed_fields_detected(conn, members, parsed):
    store(conn, members[0], month_rows(parsed))
    target = month_rows(parsed)[0].row
    edited = [
        (
            dataclasses.replace(r, category="급여", content="고친 내용", memo="메모")
            if r is target
            else r
        )
        for r in parsed.normalized.rows
    ]
    p = preview_month(conn, members[0], with_rows(parsed, edited), YM)
    assert len(p.changed) == 1 and p.unchanged == 179
    labels = {c.label: (c.before, c.after) for c in p.changed[0].changes}
    assert labels["대분류"][1] == "급여"
    assert labels["내용"] == (target.content, "고친 내용")
    assert labels["메모"] == (None, "메모")


def test_removed_and_added(conn, members, parsed):
    rows = month_rows(parsed)
    store(conn, members[0], rows)
    gone = rows[0].row
    extra = dataclasses.replace(gone, amount=gone.amount - 10, row_no=9999)
    new_rows = [r for r in parsed.normalized.rows if r is not gone] + [extra]
    p = preview_month(conn, members[0], with_rows(parsed, new_rows), YM)
    assert len(p.removed) == 1 and p.removed[0].tx_key == rows[0].tx_key
    assert len(p.added) == 1 and p.added[0].row.amount == gone.amount - 10
    assert p.unchanged == 179


def test_removed_with_override_warns(conn, members, parsed):
    rows = month_rows(parsed)
    store(conn, members[0], rows)
    conn.execute(
        "INSERT INTO tx_overrides (member_id, tx_key, category) VALUES (?, ?, '저축')",
        (members[0].id, rows[0].tx_key),
    )
    remaining = [r for r in parsed.normalized.rows if r is not rows[0].row]
    p = preview_month(conn, members[0], with_rows(parsed, remaining), YM)
    assert p.removed_with_override == {rows[0].tx_key}
    assert any("웹에서 고친 거래 1건" in w for w in p.warnings)


def test_other_months_and_members_ignored(conn, members, parsed):
    store(conn, members[0], month_rows(parsed, "2026-08"), ym="2026-08")
    p = preview_month(conn, members[0], parsed, YM)
    assert len(p.added) == 180 and not p.removed


# --- 경고 ----------------------------------------------------------------------


def test_owner_mismatch_warning(conn, members, parsed):
    sangbeom, jaehee = members
    store(conn, jaehee, month_rows(parsed))  # 같은 파일이 재희 것으로 들어가 있음
    p = preview_month(conn, sangbeom, parsed, YM)
    assert any("재희의 파일일 수 있습니다" in w for w in p.warnings)


def test_owner_check_skips_tiny_months(conn, members, parsed):
    rows = month_rows(parsed)[:3]
    store(conn, members[1], rows)
    small = with_rows(parsed, [k.row for k in rows])
    small = dataclasses.replace(small, summary=parsed.summary)
    p = preview_month(conn, members[0], small, YM)
    assert not any("파일일 수 있습니다" in w for w in p.warnings)


def test_duplicate_upload_warnings(conn, members, parsed):
    sangbeom, jaehee = members
    store(conn, sangbeom, [], ym="2026-08", sha=parsed.file_sha256)
    store(conn, jaehee, [], ym="2026-07", sha=parsed.file_sha256)
    warnings = preview_month(conn, sangbeom, parsed, YM).warnings
    assert any("이미 올린 파일입니다" in w for w in warnings)
    assert any("재희의 파일로 이미 올라가 있습니다" in w for w in warnings)


def test_preview_does_not_write(conn, members, parsed):
    store(conn, members[0], month_rows(parsed)[:10])
    before = conn.total_changes
    preview_month(conn, members[0], parsed, YM)
    assert conn.total_changes == before


# --- 파이프라인 ------------------------------------------------------------------


def test_pipeline_fields(parsed, sample_bytes):
    assert len(parsed.keyed) == 1579
    assert parsed.summary.period.source == "파일 이름"
    assert len(parsed.file_sha256) == 64 and parsed.file_name == FILE_NAME


def test_pipeline_rejects_invalid_rows(sample_bytes):
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(sample_bytes))
    wb["가계부 내역"]["G2"] = "abc"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ExportInvalid) as exc:
        parse_export(buf.getvalue(), None, FILE_NAME)
    assert exc.value.result.errors[0].column == "금액"


# --- 명령어 ---------------------------------------------------------------------


@pytest.fixture
def cli_env(isolated_env, sample_bytes, capsys):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    path = isolated_env / FILE_NAME
    path.write_bytes(sample_bytes)
    capsys.readouterr()
    return path


def test_cli_preview(cli_env, capsys):
    assert cli.main(["settle", "preview", str(cli_env), "--member", "sangbeom", "--month", YM]) == 0
    out = capsys.readouterr().out
    assert "정산 대상   2026-09 (상범) · 새 정산" in out
    assert "이 달 거래 180건" in out
    assert "신규 180 · 변경 0 · 삭제 0 · 동일 0" in out
    assert "DB는 바뀌지 않았습니다" in out


def test_cli_preview_default_month(cli_env, capsys, monkeypatch):
    monkeypatch.setattr(cli, "previous_month", lambda today: "2026-09")
    assert cli.main(["settle", "preview", str(cli_env), "--member", "sangbeom"]) == 0
    assert "2026-09" in capsys.readouterr().out


def test_cli_preview_errors(cli_env, capsys):
    base = ["settle", "preview", str(cli_env)]
    assert cli.main([*base, "--member", "nobody"]) == 1
    assert "구성원을 찾을 수 없습니다" in capsys.readouterr().err
    assert cli.main([*base, "--member", "sangbeom", "--month", "2026-10"]) == 1
    assert "일부(10-01 ~ 10-20)만" in capsys.readouterr().err


def test_cli_preview_needs_db(isolated_env, sample_bytes, capsys):
    path = isolated_env / FILE_NAME
    path.write_bytes(sample_bytes)
    assert cli.main(["settle", "preview", str(path), "--member", "sangbeom"]) == 1
    assert "db init" in capsys.readouterr().err
