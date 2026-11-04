"""F1: 웹 서버 뼈대 (상태 확인 · 로그인 · 오류 형식 · 트랜잭션 · 보안 헤더 · 로그 · serve 명령)."""

import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from hanjigap import __version__, cli
from hanjigap.config import Settings
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.db.validate import InputError
from hanjigap.web import SetupError, create_app
from hanjigap.web.deps import DB_NOT_READY, CurrentMember, Db


def make_settings(tmp_path, **over) -> Settings:
    values = {
        "env": "dev",
        "dev_member": "sangbeom",
        "db_path": tmp_path / "data" / "hanjigap.db",
        "data_dir": tmp_path / "data",
        "log_dir": tmp_path / "logs",
    }
    values.update(over)
    return Settings(**values)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "data" / "hanjigap.db"
    path.parent.mkdir()
    c = connect(path)
    migrate(c)
    add_member(c, "sangbeom", "상범", email="sb@example.com")
    add_member(c, "jaehee", "재희")
    c.close()
    return path


class Item(BaseModel):
    name: str
    amount: int


def with_test_routes(app):
    """트랜잭션·오류 확인용 경로 (테스트에서만)."""

    @app.post("/api/_test/members")
    def add(item: Item, conn: Db, member: CurrentMember) -> dict:
        add_member(conn, item.name, f"이름{item.amount}")
        if item.amount == 400:
            raise InputError("금액이 이상해요")
        if item.amount == 500:
            raise RuntimeError("비밀 내용 1234")
        if item.amount == 409:  # 저장(COMMIT) 단계에서 실패하게 만든다
            conn.execute("PRAGMA defer_foreign_keys = ON")
            conn.execute(
                "INSERT INTO accounts (member_id, institution, name, kind) "
                "VALUES (999, 'A은행', '통장', 'checking')"
            )
        return {"by": member.slug}

    @app.get("/api/_test/items/{item_id}")
    def item(item_id: int, member: CurrentMember) -> dict:
        return {"id": item_id}

    return app


@pytest.fixture
def client(tmp_path, db_path):
    app = with_test_routes(create_app(make_settings(tmp_path)))
    with TestClient(app) as c:
        yield c


def slugs(path) -> list[str]:
    c = connect(path)
    try:
        return [r[0] for r in c.execute("SELECT slug FROM members ORDER BY id")]
    finally:
        c.close()


# --- 상태 · 로그인 ---------------------------------------------------------------


def test_health(client):
    r = client.get("/api/health")
    assert (r.status_code, r.json()) == (200, {"status": "ok", "version": __version__})


def test_health_without_db(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        r = c.get("/api/health")
    assert (r.status_code, r.json()) == (503, {"error": DB_NOT_READY})
    assert str(tmp_path) not in r.text  # 경로를 바깥에 보여 주지 않는다


def test_me_dev_member(client):
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {
        "member": {"slug": "sangbeom", "name": "상범"},
        "members": [{"slug": "sangbeom", "name": "상범"}, {"slug": "jaehee", "name": "재희"}],
        "logout_url": None,  # 개발 모드 로그인
    }
    assert "example.com" not in r.text  # 이메일은 담지 않는다


def test_me_without_login(tmp_path, db_path):
    with TestClient(create_app(make_settings(tmp_path, dev_member=None))) as c:
        r = c.get("/api/me")
    assert (r.status_code, r.json()) == (401, {"error": "로그인 정보가 없어요"})


def test_me_unknown_dev_member(tmp_path, db_path):
    with TestClient(create_app(make_settings(tmp_path, dev_member="nobody"))) as c:
        r = c.get("/api/me")
    assert r.status_code == 401
    assert "HANJIGAP_DEV_MEMBER" in r.json()["error"]


def test_db_without_latest_schema(tmp_path):
    path = tmp_path / "data" / "hanjigap.db"
    path.parent.mkdir()
    c = connect(path)
    from hanjigap.db.migrate import load_migrations

    migrate(c, load_migrations()[:1])
    c.close()
    with TestClient(create_app(make_settings(tmp_path))) as c:
        assert c.get("/api/me").json() == {"error": DB_NOT_READY}


# --- 운영 모드 설정 ---------------------------------------------------------------


def test_prod_requires_cloudflare(tmp_path):
    with pytest.raises(SetupError, match="HANJIGAP_CF_TEAM_DOMAIN"):
        create_app(make_settings(tmp_path, env="prod", dev_member=None))


def test_prod_rejects_dev_member(tmp_path):
    with pytest.raises(SetupError, match="DEV_MEMBER"):
        create_app(make_settings(tmp_path, env="prod", cf_team_domain="x", cf_access_aud="y"))


def test_prod_has_no_docs_and_no_dev_login(tmp_path, db_path, monkeypatch):
    s = make_settings(tmp_path, env="prod", dev_member=None, cf_team_domain="x", cf_access_aud="y")
    with TestClient(create_app(s)) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/api/me").status_code == 401


def test_dev_has_docs(client):
    assert client.get("/docs").status_code == 200


# --- 오류 형식 -------------------------------------------------------------------


def test_not_found_and_method(client):
    assert client.get("/api/nothing").json() == {"error": "없는 주소예요"}
    r = client.delete("/api/me")
    assert (r.status_code, r.json()) == (405, {"error": "이 주소에서 할 수 없는 요청이에요"})


def test_validation_error(client):
    r = client.post("/api/_test/members", json={"name": "x1"})
    assert (r.status_code, r.json()) == (
        422,
        {"error": "입력 형식이 올바르지 않아요", "fields": ["amount"]},
    )
    r = client.get("/api/_test/items/abc")
    assert r.json()["fields"] == ["item_id"]


# --- 트랜잭션 --------------------------------------------------------------------


def test_write_commits_before_response(client, db_path):
    r = client.post("/api/_test/members", json={"name": "minsu", "amount": 1})
    assert (r.status_code, r.json()) == (200, {"by": "sangbeom"})
    assert slugs(db_path)[-1] == "minsu"


def test_input_error_rolls_back(client, db_path):
    r = client.post("/api/_test/members", json={"name": "minsu", "amount": 400})
    assert (r.status_code, r.json()) == (400, {"error": "금액이 이상해요"})
    assert "minsu" not in slugs(db_path)


def test_unexpected_error_rolls_back(client, db_path, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap.web")
    r = client.post("/api/_test/members", json={"name": "minsu", "amount": 500})
    assert (r.status_code, r.json()["error"]) == (
        500,
        "서버에서 문제가 생겼어요. 잠시 뒤 다시 해보세요",
    )
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "minsu" not in slugs(db_path)
    assert "처리 실패 POST /api/_test/members (RuntimeError)" in caplog.text
    assert "비밀 내용" not in caplog.text


def test_commit_failure_is_reported(client, db_path):
    r = client.post("/api/_test/members", json={"name": "minsu", "amount": 409})
    assert r.status_code == 500  # 저장이 실패하면 성공으로 답하지 않는다
    assert "minsu" not in slugs(db_path)


def test_read_has_no_open_transaction(client, db_path):
    # 읽는 요청은 DB를 잠그지 않는다: 동시에 다른 연결이 쓸 수 있어야 한다
    assert client.get("/api/me").status_code == 200
    c = sqlite3.connect(db_path, timeout=0)
    c.execute("INSERT INTO members (slug, display_name) VALUES ('minsu', '민수')")
    c.commit()
    c.close()


# --- 보안 -----------------------------------------------------------------------


def test_security_headers(client):
    r = client.get("/api/me")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}],
)
def test_cross_site_write_rejected(client, db_path, headers):
    r = client.post("/api/_test/members", json={"name": "minsu", "amount": 1}, headers=headers)
    assert (r.status_code, r.json()) == (403, {"error": "다른 사이트에서 온 요청은 받지 않아요"})
    assert "minsu" not in slugs(db_path)


def test_same_site_write_allowed(client, db_path):
    headers = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    r = client.post("/api/_test/members", json={"name": "minsu", "amount": 1}, headers=headers)
    assert r.status_code == 200


def test_cross_site_read_not_blocked_here(client):
    # 읽기는 브라우저가 다른 사이트에 응답을 넘기지 않으므로 여기서 막지 않는다
    assert client.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 200


# --- 로그 ----------------------------------------------------------------------


def test_log_has_route_template_only(client, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap.web")
    client.get("/api/_test/items/987654?q=비밀검색어")
    client.get("/api/nothing/123")
    assert "GET /api/_test/items/{item_id} 200" in caplog.text
    assert "GET (없는 경로) 404" in caplog.text
    assert "987654" not in caplog.text and "비밀검색어" not in caplog.text


# --- serve 명령 ----------------------------------------------------------------


@pytest.fixture
def fake_uvicorn(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    return calls


def test_cli_serve(isolated_env, fake_uvicorn, capsys, monkeypatch):
    monkeypatch.setenv("HANJIGAP_DEV_MEMBER", "sangbeom")
    cli.main(["db", "init"])
    capsys.readouterr()
    assert cli.main(["serve", "--port", "8123"]) == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8123" in out and "로그인 구성원: sangbeom" in out
    _app, kw = fake_uvicorn[0]
    assert kw["host"] == "127.0.0.1" and kw["port"] == 8123
    assert kw["access_log"] is False and kw["server_header"] is False


def test_cli_serve_only_local(isolated_env, fake_uvicorn, capsys):
    cli.main(["db", "init"])
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 1
    assert "127.0.0.1" in capsys.readouterr().err
    assert fake_uvicorn == []


def test_cli_serve_without_db(isolated_env, fake_uvicorn, capsys):
    assert cli.main(["serve"]) == 1
    assert "db init" in capsys.readouterr().err
    assert fake_uvicorn == []


def test_cli_serve_prod_needs_cloudflare(isolated_env, fake_uvicorn, capsys, monkeypatch):
    cli.main(["db", "init"])
    monkeypatch.setenv("HANJIGAP_ENV", "prod")
    from hanjigap.config import get_settings

    get_settings.cache_clear()
    assert cli.main(["serve"]) == 1
    assert "HANJIGAP_CF_TEAM_DOMAIN" in capsys.readouterr().err
    assert fake_uvicorn == []
