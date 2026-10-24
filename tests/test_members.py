import pytest

from hanjigap import cli
from hanjigap.db.connection import DatabaseError, connect, connect_ready
from hanjigap.db.members import (
    MemberError,
    add_member,
    find_by_email,
    get_member,
    list_members,
    mask_email,
    normalize_email,
    update_member,
)
from hanjigap.db.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "m.db")
    migrate(c)
    yield c
    c.close()


# --- 함수 ---------------------------------------------------------------------


def test_add_and_list(conn):
    a = add_member(conn, "sangbeom", "상범", "Me@Example.COM")
    b = add_member(conn, "jaehee", "재희")
    assert a.email == "me@example.com"  # 소문자로 저장
    assert b.email is None
    assert [m.slug for m in list_members(conn)] == ["sangbeom", "jaehee"]
    assert get_member(conn, "jaehee").display_name == "재희"


def test_name_trimmed(conn):
    assert add_member(conn, "sangbeom", "  상범 ").display_name == "상범"


@pytest.mark.parametrize("slug", ["A", "상범", "with space", "x", "a" * 33, "Sangbeom"])
def test_bad_slug(conn, slug):
    with pytest.raises(MemberError, match="slug"):
        add_member(conn, slug, "이름")


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_name(conn, name):
    with pytest.raises(MemberError, match="이름"):
        add_member(conn, "sangbeom", name)


@pytest.mark.parametrize("email", ["no-at", "a@b", "a b@c.com", "@naver.com"])
def test_bad_email(conn, email):
    with pytest.raises(MemberError, match="이메일 형식"):
        add_member(conn, "sangbeom", "상범", email)


def test_blank_email_is_none():
    assert normalize_email("  ") is None and normalize_email(None) is None


def test_duplicate_slug(conn):
    add_member(conn, "sangbeom", "상범")
    with pytest.raises(MemberError, match="이미 있는 slug"):
        add_member(conn, "sangbeom", "다른이름")


def test_duplicate_email_case_insensitive(conn):
    add_member(conn, "sangbeom", "상범", "me@example.com")
    with pytest.raises(MemberError, match="이미 쓰는 이메일"):
        add_member(conn, "jaehee", "재희", "ME@example.com")


def test_update_name_and_email(conn):
    add_member(conn, "jaehee", "재희")
    m = update_member(conn, "jaehee", display_name="잴리", email="Her@Example.com")
    assert (m.display_name, m.email) == ("잴리", "her@example.com")
    assert get_member(conn, "jaehee") == m


def test_update_keeps_other_fields(conn):
    add_member(conn, "sangbeom", "상범", "me@example.com")
    assert update_member(conn, "sangbeom", display_name="상범2").email == "me@example.com"
    assert update_member(conn, "sangbeom", email="new@example.com").display_name == "상범2"


def test_clear_email(conn):
    add_member(conn, "sangbeom", "상범", "me@example.com")
    assert update_member(conn, "sangbeom", clear_email=True).email is None


def test_update_same_email_to_self_ok(conn):
    add_member(conn, "sangbeom", "상범", "me@example.com")
    assert update_member(conn, "sangbeom", email="ME@example.com").email == "me@example.com"


def test_update_email_taken(conn):
    add_member(conn, "sangbeom", "상범", "me@example.com")
    add_member(conn, "jaehee", "재희")
    with pytest.raises(MemberError, match="이미 쓰는 이메일"):
        update_member(conn, "jaehee", email="me@example.com")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "바꿀 항목이 없습니다"),
        ({"email": "a@b.com", "clear_email": True}, "동시에"),
        ({"email": "  "}, "--clear-email"),
    ],
)
def test_update_invalid_requests(conn, kwargs, message):
    add_member(conn, "sangbeom", "상범")
    with pytest.raises(MemberError, match=message):
        update_member(conn, "sangbeom", **kwargs)


def test_unknown_member(conn):
    with pytest.raises(MemberError, match="찾을 수 없습니다: jaehe"):
        get_member(conn, "jaehe")
    with pytest.raises(MemberError, match="찾을 수 없습니다"):
        update_member(conn, "jaehe", display_name="x")


def test_find_by_email(conn):
    add_member(conn, "sangbeom", "상범", "me@example.com")
    assert find_by_email(conn, " ME@Example.com ").slug == "sangbeom"
    assert find_by_email(conn, "other@example.com") is None
    assert find_by_email(conn, None) is None
    assert find_by_email(conn, "not-an-email") is None


@pytest.mark.parametrize(
    ("email", "masked"),
    [("scds@naver.com", "s***@naver.com"), ("a@b.co", "a***@b.co"), (None, "(미설정)")],
)
def test_mask_email(email, masked):
    assert mask_email(email) == masked


# --- connect_ready ------------------------------------------------------------


def test_connect_ready_requires_file(tmp_path):
    with pytest.raises(DatabaseError, match="db init"):
        connect_ready(tmp_path / "none.db")


def test_connect_ready_requires_latest_schema(tmp_path):
    connect(tmp_path / "empty.db").close()  # 파일만 있고 스키마 없음
    with pytest.raises(DatabaseError, match="최신이 아닙니다"):
        connect_ready(tmp_path / "empty.db")


# --- 명령어 -------------------------------------------------------------------


def test_cli_members_flow(isolated_env, capsys):
    assert cli.main(["member", "list"]) == 1
    assert "db init" in capsys.readouterr().err

    assert cli.main(["db", "init"]) == 0
    capsys.readouterr()
    assert cli.main(["member", "list"]) == 0
    assert "등록된 구성원이 없습니다" in capsys.readouterr().out

    assert cli.main(["member", "add", "sangbeom", "--name", "상범", "--email", "Me@Naver.com"]) == 0
    out = capsys.readouterr().out
    assert "등록했습니다" in out and "m***@naver.com" in out and "Me@Naver.com" not in out

    assert cli.main(["member", "add", "jaehee", "--name", "재희"]) == 0
    capsys.readouterr()

    assert cli.main(["member", "list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("slug")
    assert out[1].startswith("sangbeom") and "m***@naver.com" in out[1]
    assert out[2].startswith("jaehee") and "(미설정)" in out[2]

    assert cli.main(["member", "update", "jaehee", "--email", "her@gmail.com"]) == 0
    assert "h***@gmail.com" in capsys.readouterr().out

    assert cli.main(["db", "status"]) == 0
    assert "members            2행" in capsys.readouterr().out


def test_cli_member_errors(isolated_env, capsys):
    cli.main(["db", "init"])
    cli.main(["member", "add", "sangbeom", "--name", "상범"])
    capsys.readouterr()

    assert cli.main(["member", "add", "sangbeom", "--name", "상범"]) == 1
    assert "이미 있는 slug" in capsys.readouterr().err

    assert cli.main(["member", "update", "jaehe", "--name", "x"]) == 1
    assert "찾을 수 없습니다: jaehe" in capsys.readouterr().err

    assert cli.main(["member", "update", "sangbeom"]) == 1
    assert "바꿀 항목이 없습니다" in capsys.readouterr().err

    with pytest.raises(SystemExit):  # argparse: --email과 --clear-email 동시 사용 불가
        cli.main(["member", "update", "sangbeom", "--email", "a@b.com", "--clear-email"])
