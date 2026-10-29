"""F5: 웹에서 뱅샐 파일 올리기 (미리보기 · 확정 · 취소 · 첫 적재 · 비밀번호 · 안전장치)."""

import datetime as dt
import logging

import pytest
from starlette.formparsers import MultiPartParser

import hanjigap.web.ledger_api as ledger_mod
import hanjigap.web.uploads_api as uploads_mod

NAME = "2025-10-20~2026-10-20.xlsx"
SAMPLE_PASSWORD = "test1234"  # conftest의 잠금 샘플 비밀번호


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(ledger_mod, "today", lambda _r: dt.date(2026, 10, 29))


def upload(api, data, name=NAME, **form):
    return api.post(
        "/api/uploads", files={"file": (name, data, "application/octet-stream")}, data=form
    )


def as_member(api, slug):
    api.app.state.settings.dev_member = slug


# --- 첫 적재 -------------------------------------------------------------------


def test_first_load_flow(api, sample_bytes):
    r = upload(api, sample_bytes)
    assert r.status_code == 201, r.json()
    p = r.json()
    assert (p["owner"], p["mode"], p["kind"], p["month"]) == (
        "sangbeom",
        "first_load",
        "first_load",
        "2026-09",
    )
    assert p["file"]["period_start"] == "2025-10-20" and p["file"]["rows"] == 1579
    months = p["first_load"]["months"]
    assert months[-1] == {"month": "2026-09", "status": "settled", "count": 180}
    assert {m["status"] for m in months[:-1]} == {"baseline"}
    assert {e["month"] for e in p["first_load"]["excluded"]} == {"2025-10", "2026-10"}
    assert p["month_summary"]["count"] == 180
    assert p["expires_in"] == 900

    r = api.post(f"/api/uploads/{p['upload_id']}/apply")
    assert r.status_code == 200, r.json()
    done = r.json()
    assert (done["saved"], done["mode"], done["message"]) == (True, "first_load", "정산했어요")
    assert done["summary"]["unconfirmed"] == 7
    assert api.get("/api/months").json()["items"][0]["members"][0]["status"] == "settled"
    # 확정하면 보관한 것은 지운다
    assert api.post(f"/api/uploads/{p['upload_id']}/apply").status_code == 404


def test_monthly_settle_after_first_load(api, sample_bytes):
    first = upload(api, sample_bytes).json()
    api.post(f"/api/uploads/{first['upload_id']}/apply")
    p = upload(api, sample_bytes).json()
    assert (p["mode"], p["kind"]) == ("settle", "resettle")
    assert p["changes"] == {"added": 0, "changed": 0, "removed": 0, "unchanged": 180}
    assert any("이미 올린 파일" in w for w in p["warnings"])
    assert any("바뀐 거래가 없어요" in w for w in p["warnings"])
    done = api.post(f"/api/uploads/{p['upload_id']}/apply").json()
    assert (done["saved"], done["message"]) == (False, "바뀐 거래가 없어 저장하지 않았어요")


def test_upload_for_other_member(api, sample_bytes):
    first = upload(api, sample_bytes).json()
    api.post(f"/api/uploads/{first['upload_id']}/apply")
    p = upload(api, sample_bytes, owner="jaehee").json()
    assert (p["owner"], p["mode"]) == ("jaehee", "first_load")
    assert any("상범의 파일로 이미 올라가 있습니다" in w for w in p["warnings"])
    assert any("상범의 파일일 수 있습니다" in w for w in p["warnings"])


# --- 비밀번호 · 파일 형식 ----------------------------------------------------------


def test_locked_excel(api, encrypted_sample_bytes):
    r = upload(api, encrypted_sample_bytes)
    assert (r.status_code, r.json()["code"]) == (400, "PASSWORD_REQUIRED")
    r = upload(api, encrypted_sample_bytes, password="wrong")
    assert (r.status_code, r.json()["code"]) == (400, "WRONG_PASSWORD")
    assert r.json()["error"] == "비밀번호가 맞지 않습니다"
    assert upload(api, encrypted_sample_bytes, password=SAMPLE_PASSWORD).status_code == 201


def test_locked_zip(api, sample_bytes, make_zip):
    data = make_zip([(NAME, sample_bytes)], password="zip-pass")
    r = upload(api, data, name="banksalad.zip", password="zip-pass")
    assert r.status_code == 201, r.json()
    assert r.json()["file"]["period_end"] == "2026-10-20"  # 안의 엑셀 이름으로 기간을 읽음


@pytest.mark.parametrize("name", ["report.pdf", "data.csv", ""])
def test_wrong_file_type(api, sample_bytes, name):
    r = upload(api, sample_bytes, name=name or "noext")
    assert (r.status_code, r.json()["code"]) == (400, "NOT_EXCEL")


def test_too_large(api, sample_bytes, monkeypatch):
    monkeypatch.setattr(uploads_mod, "MAX_BYTES", 1000)
    r = upload(api, sample_bytes)
    assert (r.status_code, r.json()["code"]) == (400, "FILE_TOO_LARGE")


def test_not_a_banksalad_file(api):
    r = upload(api, b"PK\x03\x04 not really a zip")
    assert r.status_code == 400 and r.json()["code"] in {"NOT_EXCEL", "DECRYPT_FAILED"}


def test_incomplete_month(api, sample_bytes):
    r = upload(api, sample_bytes, month="2026-10")
    assert (r.status_code, r.json()["code"]) == (400, "CANNOT_SETTLE")
    assert "일부" in r.json()["error"]


def test_unknown_owner(api, sample_bytes):
    assert upload(api, sample_bytes, owner="nobody").status_code == 400


def test_missing_file(api):
    assert api.post("/api/uploads", data={"password": "x"}).status_code == 422


# --- 보관 · 확정 · 취소 ------------------------------------------------------------


def test_only_uploader_can_apply(api, sample_bytes):
    p = upload(api, sample_bytes).json()
    as_member(api, "jaehee")
    r = api.post(f"/api/uploads/{p['upload_id']}/apply")
    assert r.status_code == 404 and "다시 올려 주세요" in r.json()["error"]
    assert api.delete(f"/api/uploads/{p['upload_id']}").status_code == 404
    as_member(api, "sangbeom")
    assert api.post(f"/api/uploads/{p['upload_id']}/apply").status_code == 200


def test_cancel(api, sample_bytes):
    p = upload(api, sample_bytes).json()
    assert api.delete(f"/api/uploads/{p['upload_id']}").json() == {"deleted": True}
    assert api.post(f"/api/uploads/{p['upload_id']}/apply").status_code == 404
    assert api.get("/api/months").json()["items"] == []


def test_expires_after_15_minutes(api, sample_bytes):
    now = [1000.0]
    api.app.state.uploads.clock = lambda: now[0]
    p = upload(api, sample_bytes).json()
    now[0] += 15 * 60 + 1
    assert api.post(f"/api/uploads/{p['upload_id']}/apply").status_code == 404


def test_keeps_at_most_four(api, sample_bytes):
    ids = [upload(api, sample_bytes).json()["upload_id"] for _ in range(5)]
    store = api.app.state.uploads
    assert ids[0] not in store.items and len(store.items) == 4


def test_db_changed_between_preview_and_apply(api, sample_bytes):
    a = upload(api, sample_bytes).json()
    b = upload(api, sample_bytes).json()
    assert api.post(f"/api/uploads/{b['upload_id']}/apply").status_code == 200
    r = api.post(f"/api/uploads/{a['upload_id']}/apply")  # 그 사이 첫 적재가 끝남
    assert (r.status_code, r.json()["code"]) == (400, "CANNOT_SETTLE")
    assert "이미 정산 기록이 있습니다" in r.json()["error"]


def test_cross_site_upload_rejected(api, sample_bytes):
    r = api.post(
        "/api/uploads",
        files={"file": (NAME, sample_bytes, "application/octet-stream")},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_upload_stays_in_memory():
    assert MultiPartParser.spool_max_size > uploads_mod.MAX_BYTES


def test_log_has_no_file_details(api, encrypted_sample_bytes, caplog):
    caplog.set_level(logging.INFO, logger="hanjigap")
    upload(api, encrypted_sample_bytes, password="wrong-secret")
    p = upload(api, encrypted_sample_bytes, password=SAMPLE_PASSWORD).json()
    api.post(f"/api/uploads/{p['upload_id']}/apply")
    text = caplog.text
    assert "웹 업로드 미리보기: first_load 2026-09 1579건" in text
    assert "웹 업로드 확정: first_load 2026-09 저장=True" in text
    assert "웹 업로드 거부: ImportFailure" in text
    assert SAMPLE_PASSWORD not in text and "wrong-secret" not in text
    assert NAME not in text and p["upload_id"] not in text
