"""Cloudflare Access 로그인 확인 (F2).

흐름
    Cloudflare Access가 이메일 OTP로 로그인을 받고, 요청마다 로그인 토큰(JWT)을 붙여 보낸다.
        헤더 Cf-Access-Jwt-Assertion (없으면 쿠키 CF_Authorization)
    서버는 그 토큰을 그대로 믿지 않고 직접 확인한다.
        서명: 우리 팀의 Cloudflare 공개키 (https://<팀>.cloudflareaccess.com/cdn-cgi/access/certs)
        발급자(iss) = 우리 팀 주소, 대상(aud) = 이 앱의 AUD 값, 만료(exp)
    확인된 이메일을 구성원 이메일(members.email)과 맞춰 상범·재희를 구분한다.

공개키는 한 시간 동안 기억해 두고, 처음 보는 키가 오면 다시 받아 온다. (Cloudflare 키 교체 대비)
로그에는 거부 이유의 종류만 남긴다. 이메일·토큰은 남기지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

TOKEN_HEADER = "cf-access-jwt-assertion"
TOKEN_COOKIE = "CF_Authorization"
LEEWAY_SECONDS = 30  # 미니PC와 Cloudflare 시계가 조금 어긋나도 받는다
KEY_CACHE_SECONDS = 3600
_TEAM_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com$")

INVALID = "로그인 정보가 올바르지 않아요. 새로고침해서 다시 로그인하세요"
EXPIRED = "로그인이 만료됐어요. 새로고침해서 다시 로그인하세요"
NO_EMAIL = "이메일로 로그인한 사람만 쓸 수 있어요"
UNREACHABLE = "로그인 확인 서버(Cloudflare)에 연결하지 못했어요. 잠시 뒤 다시 해보세요"


class AccessDenied(Exception):
    def __init__(self, status: int, message: str, reason: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.reason = reason  # 로그용 (예: 만료, 서명)


class KeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


def team_url(team_domain: str) -> str:
    """팀 주소. 'myteam', 'myteam.cloudflareaccess.com', 'https://…/' 모두 받는다."""
    d = team_domain.strip().lower().removeprefix("https://").rstrip("/")
    if "." not in d:
        d += ".cloudflareaccess.com"
    if not _TEAM_RE.match(d):
        raise ValueError(team_domain)
    return f"https://{d}"


@dataclass
class AccessVerifier:
    issuer: str
    audience: str
    keys: KeySource

    @classmethod
    def create(
        cls, team_domain: str, audience: str, keys: KeySource | None = None
    ) -> AccessVerifier:
        issuer = team_url(team_domain)
        if keys is None:
            keys = jwt.PyJWKClient(
                f"{issuer}/cdn-cgi/access/certs",
                cache_keys=True,
                lifespan=KEY_CACHE_SECONDS,
                timeout=10,
            )
        return cls(issuer, audience.strip(), keys)

    def email(self, token: str) -> str:
        """토큰을 확인하고 로그인한 이메일을 돌려준다. 안 되면 AccessDenied."""
        try:
            key = self.keys.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError:
            raise AccessDenied(503, UNREACHABLE, "공개키 받기 실패") from None
        except (jwt.PyJWKClientError, jwt.InvalidTokenError):
            raise AccessDenied(401, INVALID, "알 수 없는 키·형식") from None
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError:
            raise AccessDenied(401, EXPIRED, "만료") from None
        except jwt.InvalidAudienceError:
            raise AccessDenied(401, INVALID, "대상(aud) 다름") from None
        except jwt.InvalidIssuerError:
            raise AccessDenied(401, INVALID, "발급자(iss) 다름") from None
        except jwt.InvalidTokenError:
            raise AccessDenied(401, INVALID, "서명·내용 오류") from None
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise AccessDenied(403, NO_EMAIL, "이메일 없음")
        return email
