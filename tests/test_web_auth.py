"""F2: Cloudflare Access 로그인 토큰 확인.

실제 Cloudflare 대신 테스트용 키로 토큰을 만들어 확인한다.
"""

import json
import logging
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from hanjigap.config import Settings
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from hanjigap.web import SetupError, create_app
from hanjigap.web.auth import AccessVerifier, team_url

TEAM = "myteam"
ISSUER = "https://myteam.cloudflareaccess.com"
AUD = "aud-1234"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeKeys:
    """Cloudflare 공개키 목록 대신."""

    def __init__(self, keys):
        self.keys = keys

    def get_signing_key_from_jwt(self, token):
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self.keys:
            raise jwt.PyJWKClientError("Unable to find a signing key")
        return SimpleNamespace(key=self.keys[kid])


def token(private_key, kid="k1", **over):
    now = int(time.time())
    claims = {
        "aud": [AUD],
        "email": "Jaehee@Example.com",
        "exp": now + 3600,
        "iat": now,
        "iss": ISSUER,
        "sub": "user-1",
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def settings(tmp_path, **over) -> Settings:
    values = {
        "env": "prod",
        "cf_team_domain": TEAM,
        "cf_access_aud": AUD,
        "db_path": tmp_path / "data" / "hanjigap.db",
        "data_dir": tmp_path / "data",
        "log_dir": tmp_path / "logs",
    }
    values.update(over)
    return Settings(**values)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data" / "hanjigap.db"
    path.parent.mkdir()
    c = connect(path)
    migrate(c)
    add_member(c, "sangbeom", "상범", email="sb@example.com")
    add_member(c, "jaehee", "재희", email="jaehee@example.com")
    c.close()


@pytest.fixture
def client(tmp_path, db, key):
    app = create_app(settings(tmp_path))
    app.state.verifier = AccessVerifier.create(TEAM, AUD, keys=FakeKeys({"k1": key.public_key()}))
    with TestClient(app) as c:
        yield c


def me(client, tok=None, cookie=None):
    headers = {"Cf-Access-Jwt-Assertion": tok} if tok else {}
    if cookie:
        client.cookies.set("CF_Authorization", cookie)
    return client.get("/api/me", headers=headers)


# --- 팀 주소 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["myteam", "MyTeam.cloudflareaccess.com", "https://myteam.cloudflareaccess.com/", " myteam "],
)
def test_team_url(value):
    assert team_url(value) == ISSUER


@pytest.mark.parametrize(
    "value", ["", "evil.com", "myteam.cloudflareaccess.com.evil.com", "my_team", "a/b"]
)
def test_team_url_rejects(value):
    with pytest.raises(ValueError):
        team_url(value)


def test_bad_team_domain_stops_server(tmp_path):
    with pytest.raises(SetupError, match="HANJIGAP_CF_TEAM_DOMAIN 형식"):
        create_app(settings(tmp_path, cf_team_domain="evil.com"))


def test_real_key_client_uses_team_url(tmp_path):
    app = create_app(settings(tmp_path))
    assert app.state.verifier.keys.uri == f"{ISSUER}/cdn-cgi/access/certs"


# --- 로그인 성공 ----------------------------------------------------------------


def test_login_with_header(client, key):
    r = me(client, token(key))
    assert r.status_code == 200
    assert r.json()["member"] == {"slug": "jaehee", "name": "재희"}  # 대소문자 무관


def test_login_with_cookie(client, key):
    r = me(client, cookie=token(key, email="sb@example.com"))
    assert r.json()["member"]["slug"] == "sangbeom"


def test_logout_url_only_with_access_login(client, key, tmp_path):
    # Access로 로그인하면 로그아웃 주소를 알려 준다 (설정 화면의 로그아웃 버튼)
    assert me(client, token(key)).json()["logout_url"] == "/cdn-cgi/access/logout"


def test_aud_as_plain_string(client, key):
    assert me(client, token(key, aud=AUD)).status_code == 200


def test_small_clock_difference_allowed(client, key):
    assert me(client, token(key, exp=int(time.time()) - 10)).status_code == 200


# --- 로그인 거부 ----------------------------------------------------------------


def test_no_token(client):
    assert (me(client).status_code, me(client).json()) == (401, {"error": "로그인 정보가 없어요"})


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda k, o: token(k, exp=int(time.time()) - 120), "만료"),
        (lambda k, o: token(k, aud=["other-app"]), "올바르지 않아요"),
        (lambda k, o: token(k, iss="https://evil.cloudflareaccess.com"), "올바르지 않아요"),
        (lambda k, o: token(o), "올바르지 않아요"),  # 다른 키로 서명
        (lambda k, o: token(k, kid="k9"), "올바르지 않아요"),  # 모르는 키
        (lambda k, o: token(k, exp=None), "올바르지 않아요"),  # 만료 시각 없음
        (lambda k, o: "not-a-token", "올바르지 않아요"),
    ],
)
def test_rejected_tokens(client, key, other_key, make, message):
    r = me(client, make(key, other_key))
    assert r.status_code == 401 and message in r.json()["error"]


def test_hs256_token_rejected(client):
    # 서명 방식을 바꿔 치기하는 공격 (공개키를 비밀번호처럼 쓰는 HS256)
    forged = jwt.encode(
        {"aud": AUD, "iss": ISSUER, "email": "sb@example.com", "exp": 2**31, "iat": 1},
        "secret-secret-secret-secret-secret!",
        algorithm="HS256",
        headers={"kid": "k1"},
    )
    assert me(client, forged).status_code == 401


def test_unsigned_token_rejected(client):
    header = jwt.utils.base64url_encode(json.dumps({"alg": "none", "kid": "k1"}).encode())
    body = jwt.utils.base64url_encode(
        json.dumps({"aud": AUD, "iss": ISSUER, "email": "sb@example.com", "exp": 2**31}).encode()
    )
    assert me(client, f"{header.decode()}.{body.decode()}.").status_code == 401


def test_no_email_claim(client, key):
    r = me(client, token(key, email=None))  # 서비스 토큰 등
    assert (r.status_code, r.json()) == (403, {"error": "이메일로 로그인한 사람만 쓸 수 있어요"})


def test_unregistered_email(client, key):
    r = me(client, token(key, email="stranger@example.com"))
    assert (r.status_code, r.json()) == (403, {"error": "한지갑에 등록되지 않은 이메일이에요"})


def test_key_server_unreachable(tmp_path, db, key, monkeypatch):
    app = create_app(settings(tmp_path))

    def fail(_self):
        raise jwt.PyJWKClientConnectionError("down")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fail)
    with TestClient(app) as c:
        r = me(c, token(key))
    assert r.status_code == 503 and "Cloudflare" in r.json()["error"]


def test_real_key_client_with_published_keys(tmp_path, db, key, monkeypatch):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="k1", alg="RS256", use="sig")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda _self: {"keys": [jwk]})
    with TestClient(create_app(settings(tmp_path))) as c:
        assert me(c, token(key)).json()["member"]["slug"] == "jaehee"


# --- 개발 모드와 함께 -------------------------------------------------------------


def test_dev_member_wins_in_dev(tmp_path, db, key):
    app = create_app(settings(tmp_path, env="dev", dev_member="sangbeom"))
    with TestClient(app) as c:
        body = me(c).json()
        assert body["member"]["slug"] == "sangbeom"
        assert body["logout_url"] is None  # 개발 모드 로그인에는 로그아웃이 없다


def test_dev_without_dev_member_uses_cloudflare(tmp_path, db, key):
    app = create_app(settings(tmp_path, env="dev"))
    app.state.verifier = AccessVerifier.create(TEAM, AUD, keys=FakeKeys({"k1": key.public_key()}))
    with TestClient(app) as c:
        assert me(c).status_code == 401
        assert me(c, token(key)).json()["member"]["slug"] == "jaehee"


# --- 로그 ----------------------------------------------------------------------


def test_log_has_reason_only(client, key, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap.web")
    expired = token(key, exp=int(time.time()) - 120)
    me(client, expired)
    me(client, token(key, email="stranger@example.com"))
    assert "로그인 거부: 만료" in caplog.text
    assert "로그인 거부: 등록되지 않은 이메일" in caplog.text
    assert "example.com" not in caplog.text and expired not in caplog.text
