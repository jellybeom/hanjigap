"""구성원(상범·재희) 등록과 조회.

이름·이메일은 DB에만 저장한다. 코드 저장소에는 넣지 않는다.
이메일은 Cloudflare 로그인 이메일과 대소문자 상관없이 비교하도록 소문자로 저장한다.
삭제 기능은 일부러 두지 않는다. (거래가 연결된 구성원을 실수로 지우지 않도록)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

SLUG_RE = re.compile(r"^[a-z0-9_-]{2,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MemberError(Exception):
    pass


@dataclass(frozen=True)
class Member:
    id: int
    slug: str
    display_name: str
    email: str | None


def _row_to_member(row: sqlite3.Row) -> Member:
    return Member(row["id"], row["slug"], row["display_name"], row["email"])


def _check_slug(slug: str) -> str:
    if not SLUG_RE.match(slug):
        raise MemberError("slug는 영문 소문자·숫자·-·_ 2~32자여야 합니다")
    return slug


def _check_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise MemberError("이름은 비워둘 수 없습니다")
    return name


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    email = email.strip().lower()
    if not email:
        return None
    if not EMAIL_RE.match(email):
        raise MemberError("이메일 형식이 아닙니다")
    return email


def mask_email(email: str | None) -> str:
    """화면 표시용: 'scds@naver.com' → 's***@naver.com'"""
    if not email:
        return "(미설정)"
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def _email_taken(conn: sqlite3.Connection, email: str, except_id: int | None = None) -> bool:
    row = conn.execute("SELECT id FROM members WHERE email = ?", (email,)).fetchone()
    return row is not None and row["id"] != except_id


def add_member(
    conn: sqlite3.Connection, slug: str, display_name: str, email: str | None = None
) -> Member:
    slug = _check_slug(slug)
    name = _check_name(display_name)
    email = normalize_email(email)

    if conn.execute("SELECT 1 FROM members WHERE slug = ?", (slug,)).fetchone():
        raise MemberError(f"이미 있는 slug입니다: {slug}")
    if email and _email_taken(conn, email):
        raise MemberError("다른 구성원이 이미 쓰는 이메일입니다")

    cur = conn.execute(
        "INSERT INTO members (slug, display_name, email) VALUES (?, ?, ?)", (slug, name, email)
    )
    return Member(cur.lastrowid, slug, name, email)


def list_members(conn: sqlite3.Connection) -> list[Member]:
    rows = conn.execute("SELECT id, slug, display_name, email FROM members ORDER BY id")
    return [_row_to_member(r) for r in rows]


def get_member(conn: sqlite3.Connection, slug: str) -> Member:
    row = conn.execute(
        "SELECT id, slug, display_name, email FROM members WHERE slug = ?", (slug,)
    ).fetchone()
    if row is None:
        raise MemberError(f"구성원을 찾을 수 없습니다: {slug}")
    return _row_to_member(row)


def find_by_email(conn: sqlite3.Connection, email: str | None) -> Member | None:
    """로그인 이메일로 구성원 찾기 (대소문자 무관). 형식이 이상하면 None."""
    try:
        email = normalize_email(email)
    except MemberError:
        return None
    if not email:
        return None
    row = conn.execute(
        "SELECT id, slug, display_name, email FROM members WHERE email = ?", (email,)
    ).fetchone()
    return _row_to_member(row) if row else None


def update_member(
    conn: sqlite3.Connection,
    slug: str,
    *,
    display_name: str | None = None,
    email: str | None = None,
    clear_email: bool = False,
) -> Member:
    if email is not None and clear_email:
        raise MemberError("이메일 변경과 삭제를 동시에 할 수 없습니다")
    if display_name is None and email is None and not clear_email:
        raise MemberError("바꿀 항목이 없습니다 (--name, --email, --clear-email)")

    member = get_member(conn, slug)
    name = _check_name(display_name) if display_name is not None else member.display_name
    new_email = member.email
    if clear_email:
        new_email = None
    elif email is not None:
        new_email = normalize_email(email)
        if new_email is None:
            raise MemberError("이메일을 지우려면 --clear-email을 쓰세요")
        if _email_taken(conn, new_email, except_id=member.id):
            raise MemberError("다른 구성원이 이미 쓰는 이메일입니다")

    conn.execute(
        "UPDATE members SET display_name = ?, email = ? WHERE id = ?", (name, new_email, member.id)
    )
    return Member(member.id, member.slug, name, new_email)


# --- 별칭 (부부 이체 자동 인식용) ---------------------------------------------------

ALIAS_MIN, ALIAS_MAX = 2, 20


def list_aliases(conn: sqlite3.Connection, slug: str) -> list[str]:
    member = get_member(conn, slug)
    rows = conn.execute(
        "SELECT alias FROM member_aliases WHERE member_id = ? ORDER BY created_at, alias",
        (member.id,),
    )
    return [r[0] for r in rows]


def add_aliases(conn: sqlite3.Connection, slug: str, aliases: list[str]) -> list[str]:
    """별칭 추가 (이미 있으면 건너뜀). 다른 구성원의 이름·별칭과 겹치면 거부.

    전체 목록을 돌려준다.
    """
    member = get_member(conn, slug)
    others = {
        name.lower()
        for member_id, names in member_names(conn).items()
        if member_id != member.id
        for name in names
    }
    cleaned = []
    for raw in aliases:
        alias = raw.strip()
        if not ALIAS_MIN <= len(alias) <= ALIAS_MAX:
            raise MemberError(f"별칭은 {ALIAS_MIN}~{ALIAS_MAX}자로 입력하세요: {alias}")
        if alias.lower() in others:
            raise MemberError(f"다른 구성원의 이름·별칭과 같아요: {alias}")
        cleaned.append(alias)
    for alias in cleaned:
        conn.execute(
            "INSERT OR IGNORE INTO member_aliases (member_id, alias) VALUES (?, ?)",
            (member.id, alias),
        )
    return list_aliases(conn, slug)


def remove_aliases(conn: sqlite3.Connection, slug: str, aliases: list[str]) -> list[str]:
    member = get_member(conn, slug)
    for alias in aliases:
        conn.execute(
            "DELETE FROM member_aliases WHERE member_id = ? AND alias = ?",
            (member.id, alias.strip()),
        )
    return list_aliases(conn, slug)


def member_names(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """구성원 id → 찾을 이름들 (구성원 이름 2자 이상 + 별칭)"""
    out: dict[int, list[str]] = {}
    for m in list_members(conn):
        out[m.id] = [m.display_name] if len(m.display_name) >= ALIAS_MIN else []
    for r in conn.execute("SELECT member_id, alias FROM member_aliases ORDER BY created_at"):
        if r["alias"] not in out.setdefault(r["member_id"], []):
            out[r["member_id"]].append(r["alias"])
    return out
