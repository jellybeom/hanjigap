"""G8: 설정 화면용 API (구성원 이름 바꾸기 · 분류 규칙 미리보기)."""

import pytest

from test_api_review import add_tx

# --- 이름 바꾸기 -----------------------------------------------------------------


def test_rename_member(api, api_conn):
    r = api.patch("/api/members/jaehee", json={"name": " 재희2 "})
    assert r.status_code == 200, r.json()
    assert (r.json()["name"], r.json()["slug"]) == ("재희2", "jaehee")
    assert api.get("/api/me").json()["members"][1]["name"] == "재희2"
    # 이메일은 그대로 (웹에서는 바꾸지 않는다)
    email = api_conn.execute("SELECT email FROM members WHERE slug = 'jaehee'").fetchone()[0]
    api.patch("/api/members/jaehee", json={"name": "재희"})
    assert (
        api_conn.execute("SELECT email FROM members WHERE slug = 'jaehee'").fetchone()[0] == email
    )


def test_rename_changes_spouse_detection(api, api_conn):
    key = add_tx(api_conn, 1, -50_000, "잴리공주")
    assert api.get(f"/api/transactions/sangbeom/{key}").json()["cls"] == "unconfirmed"
    api.patch("/api/members/jaehee", json={"name": "잴리공주"})
    assert api.get(f"/api/transactions/sangbeom/{key}").json()["source"] == "spouse"


@pytest.mark.parametrize(
    ("slug", "body", "status"),
    [("jaehee", {"name": "  "}, 400), ("nobody", {"name": "x"}, 404), ("jaehee", {}, 422),
     ("jaehee", {"name": "x", "email": "a@b.c"}, 200)],
)  # fmt: skip
def test_rename_rejects(api, api_conn, slug, body, status):
    before = api_conn.execute("SELECT email FROM members WHERE slug = 'jaehee'").fetchone()[0]
    assert api.patch(f"/api/members/{slug}", json=body).status_code == status
    # email 칸을 보내도 무시된다
    assert (
        api_conn.execute("SELECT email FROM members WHERE slug = 'jaehee'").fetchone()[0] == before
    )


# --- 규칙 미리보기 ----------------------------------------------------------------


@pytest.fixture
def txs(api_conn):
    keys = {
        "a": add_tx(api_conn, 1, -100_000, "OO학원 9월", date="2026-09-05"),
        "b": add_tx(api_conn, 1, -100_000, "OO학원 8월", date="2026-08-05"),
        "c": add_tx(api_conn, 2, -100_000, "OO학원", date="2026-09-06"),
        "d": add_tx(api_conn, 1, 100_000, "OO학원 환불", date="2026-09-07"),
        "e": add_tx(api_conn, 1, -5_000, "편의점", date="2026-09-08"),
    }
    return keys


def test_preview_counts_and_examples(api, txs):
    r = api.post("/api/class-rules/preview", json={"pattern": "oo학원", "direction": "out"})
    assert r.status_code == 200, r.json()
    p = r.json()
    assert (p["count"], p["manual"], p["months"]) == (3, 0, 2)
    assert [x["content"] for x in p["examples"]] == ["OO학원", "OO학원 9월", "OO학원 8월"]
    assert p["examples"][0]["owner"] == "jaehee"
    assert p["label"] == "나간 돈 · 내용 「oo학원」 → 지출"


def test_preview_owner_and_manual(api, txs):
    api.put(f"/api/transactions/sangbeom/{txs['a']}/class", json={"result": "excluded"})
    body = {"owner": "sangbeom", "pattern": "학원", "result": "지출", "result_category": "교육"}
    p = api.post("/api/class-rules/preview", json=body).json()
    # 상범의 학원 거래 3건 중 1건은 직접 지정이라 규칙이 바꾸지 않는다
    assert (p["count"], p["manual"]) == (2, 1)
    assert p["label"].endswith("→ 지출 › 교육")


def test_preview_saves_nothing_and_validates(api, txs):
    api.post("/api/class-rules/preview", json={"pattern": "학원"})
    assert api.get("/api/class-rules").json()["items"] == []
    r = api.post("/api/class-rules/preview", json={})
    assert r.status_code == 400 and "조건이 하나 이상" in r.json()["error"]
    assert (
        api.post("/api/class-rules/preview", json={"pattern": "x", "owner": "nobody"}).status_code
        == 400
    )
    assert (
        api.post("/api/class-rules/preview", json={"pattern": "x", "result": "모름"}).status_code
        == 400
    )
