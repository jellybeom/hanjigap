"""H1: DB 백업 · 보관 정리 · 되돌리기 · 정산 직전 백업 · 백업 API · 명령어."""

import asyncio
import datetime as dt
import socket
import sqlite3
from pathlib import Path

import pytest

from hanjigap import cli
from hanjigap.config import Settings, get_settings
from hanjigap.db import backup as bk
from hanjigap.db.connection import connect
from hanjigap.db.members import add_member
from hanjigap.db.migrate import migrate
from test_api_uploads import fixed_today, upload  # noqa: F401 (날짜 고정 fixture)
from test_apply import cli_env, run  # noqa: F401 (fixture)

NOW = dt.datetime(2026, 10, 30, 11, 7, 0)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data" / "hanjigap.db"
    path.parent.mkdir()
    c = connect(path)  # WAL 모드
    migrate(c)
    add_member(c, "sangbeom", "상범")
    c.close()
    return path


def names(folder):
    return sorted(p.name for p in folder.iterdir())


def touch(folder, at, kind):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"hanjigap-{at:%Y%m%d-%H%M%S}-{kind}.db"
    path.write_bytes(b"x")
    return path


# --- 만들기 --------------------------------------------------------------------


def test_make_backup_is_complete_copy(db, tmp_path):
    folder = tmp_path / "backups"
    r = bk.make_backup(db, folder, "manual", now=NOW)
    assert r.copied is None
    assert r.backup.path.name == "hanjigap-20261030-110700-manual.db"
    assert (r.backup.kind, r.backup.label, r.backup.at) == ("manual", "직접", NOW)
    assert names(folder) == ["hanjigap-20261030-110700-manual.db"]  # .partial 없음
    c = sqlite3.connect(r.backup.path)
    # 파일 하나로 완결 (WAL 아님) · 내용 같음
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert c.execute("SELECT slug FROM members").fetchall() == [("sangbeom",)]
    c.close()


def test_same_second_does_not_overwrite(db, tmp_path):
    folder = tmp_path / "b"
    a = bk.make_backup(db, folder, "manual", now=NOW).backup
    b = bk.make_backup(db, folder, "manual", now=NOW).backup
    assert a.path != b.path and b.at == NOW + dt.timedelta(seconds=1)


def test_missing_db(tmp_path):
    with pytest.raises(bk.BackupError, match="DB 파일이 없어요"):
        bk.make_backup(tmp_path / "none.db", tmp_path / "b", "manual", now=NOW)


def test_list_ignores_other_files(tmp_path):
    folder = tmp_path / "b"
    touch(folder, NOW, "daily")
    (folder / "메모.txt").write_text("x")
    (folder / "hanjigap-20261030-110700-other.db").write_text("x")
    (folder / "hanjigap-20261399-110700-daily.db").write_text("x")  # 없는 날짜
    assert [b.kind for b in bk.list_backups(folder)] == ["daily"]
    assert bk.list_backups(tmp_path / "없음") == []


# --- 보관 정리 ------------------------------------------------------------------


def test_prune_rules(tmp_path):
    folder = tmp_path / "b"
    start = dt.datetime(2025, 1, 1, 4, 0)
    days = [start + dt.timedelta(days=i) for i in range(600)]  # 20개월 남짓 매일
    for at in days:
        touch(folder, at, "daily")
    for i in range(15):
        touch(folder, NOW - dt.timedelta(days=i, hours=1), "settle")
    for i in range(12):
        touch(folder, NOW - dt.timedelta(minutes=i), "manual")
    for i in range(7):
        touch(folder, NOW - dt.timedelta(hours=i + 2), "restore")
    (folder / "메모.txt").write_text("남겨야 함")

    bk.prune(folder)
    left = bk.list_backups(folder)
    count = {k: sum(1 for b in left if b.kind == k) for k in bk.KINDS}
    assert (count["settle"], count["manual"], count["restore"]) == (12, 10, 5)
    daily = sorted(b.at for b in left if b.kind == "daily")
    recent = days[-14:]
    assert daily[-14:] == recent
    # 최근 14일 말고는 매달 1일 백업만, 12개월치
    older = [d for d in daily if d not in recent]
    assert all(d.day == 1 for d in older)
    months = {f"{d:%Y-%m}" for d in daily}
    assert len(months) == 12
    assert (folder / "메모.txt").exists()
    # 가장 최근 것은 언제나 남는다
    assert max(b.at for b in left if b.kind == "settle") == NOW - dt.timedelta(hours=1)


def test_ensure_daily_once_per_day(db, tmp_path):
    folder = tmp_path / "b"
    assert bk.ensure_daily(db, folder, now=NOW) is not None
    assert bk.ensure_daily(db, folder, now=NOW + dt.timedelta(hours=5)) is None
    bk.make_backup(db, folder, "manual", now=NOW + dt.timedelta(days=1))  # 직접 백업은 별개
    assert bk.ensure_daily(db, folder, now=NOW + dt.timedelta(days=1, hours=1)) is not None
    assert [b.kind for b in bk.list_backups(folder)] == ["daily", "manual", "daily"]


# --- 두 번째 사본 ---------------------------------------------------------------


def test_copy_dir_mirrors_and_prunes(db, tmp_path):
    folder, copy = tmp_path / "b", tmp_path / "D" / "사본"
    copy.mkdir(parents=True)
    (copy / "가족사진.jpg").write_bytes(b"x")
    for i in range(12):
        r = bk.make_backup(db, folder, "manual", now=NOW + dt.timedelta(minutes=i), copy_dir=copy)
        assert r.copied is True
    assert names(folder) == [n for n in names(copy) if n != "가족사진.jpg"]
    assert len(bk.list_backups(copy)) == bk.KEEP["manual"]
    assert (copy / "가족사진.jpg").exists()


def test_copy_failure_keeps_backup(db, tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    r = bk.make_backup(db, tmp_path / "b", "manual", now=NOW, copy_dir=blocker)
    assert r.copied is False and r.backup.path.exists()


# --- 되돌리기 -------------------------------------------------------------------


def test_restore_round_trip(db, tmp_path):
    folder = tmp_path / "b"
    saved = bk.make_backup(db, folder, "manual", now=NOW).backup
    c = connect(db)
    add_member(c, "jaehee", "재희")  # 백업 뒤에 바뀐 내용
    c.close()

    before, version = bk.restore(
        db, saved.path, folder, now=NOW + dt.timedelta(minutes=1), latest_version=999
    )
    assert before.kind == "restore" and version >= 1
    c = connect(db)
    assert [r[0] for r in c.execute("SELECT slug FROM members")] == ["sangbeom"]
    c.close()
    # 되돌리기 직전 DB(재희 있음)는 백업으로 남아 있다
    c = sqlite3.connect(before.path)
    assert c.execute("SELECT count(*) FROM members").fetchone()[0] == 2
    c.close()


def test_restore_rejects(db, tmp_path):
    folder = tmp_path / "b"
    text = tmp_path / "x.db"
    text.write_text("not sqlite")
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
    kw = {"now": NOW, "latest_version": 999}
    with pytest.raises(bk.BackupError, match="한지갑 백업 파일이 아니에요"):
        bk.restore(db, text, folder, **kw)
    with pytest.raises(bk.BackupError, match="한지갑 백업 파일이 아니에요"):
        bk.restore(db, other, folder, **kw)
    with pytest.raises(bk.BackupError, match="파일이 없어요"):
        bk.restore(db, tmp_path / "없음.db", folder, **kw)
    with pytest.raises(bk.BackupError, match="지금 쓰는 DB"):
        bk.restore(db, db, folder, **kw)
    saved = bk.make_backup(db, folder, "manual", now=NOW).backup
    with pytest.raises(bk.BackupError, match="더 새 버전"):
        bk.restore(db, saved.path, folder, now=NOW, latest_version=0)
    assert [b.kind for b in bk.list_backups(folder)] == ["manual"]  # 거부하면 아무것도 안 함


# --- 설정 ----------------------------------------------------------------------


def test_settings_backup_dirs(tmp_path):
    s = Settings(backup_copy_dir="  ")
    assert s.backup_copy_dir is None and s.copy_dir is None
    assert s.backup_dir == (tmp_path / "data" / "backups").resolve()
    same = Settings(backup_dir=tmp_path / "b", backup_copy_dir=str(tmp_path / "b"))
    assert same.copy_dir is None
    assert Settings(backup_copy_dir=str(tmp_path / "D")).copy_dir == (tmp_path / "D").resolve()
    with pytest.raises(ValueError):
        Settings(port=80)


def test_daily_loop_backs_up_at_start(db, tmp_path):
    from hanjigap.web.app import daily_backups

    settings = Settings(db_path=db, data_dir=db.parent, backup_dir=tmp_path / "b")

    async def once():
        task = asyncio.create_task(daily_backups(settings, interval=0.01))
        await asyncio.sleep(0.3)
        task.cancel()

    asyncio.run(once())
    assert [b.kind for b in bk.list_backups(tmp_path / "b")] == ["daily"]  # 하루 한 번만


# --- 웹 ------------------------------------------------------------------------


def test_backup_api(api, tmp_path):
    r = api.get("/api/backups").json()
    assert (
        r["last"] is None
        and r["count"] == 0
        and r["copy"] == {"on": False, "count": 0, "last": None}
    )
    r = api.post("/api/backups")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["message"] == "백업했어요" and body["count"] == 1
    assert body["last"]["kind"] == "manual" and body["last"]["label"] == "직접"
    assert body["counts"] == {"daily": 0, "settle": 0, "manual": 1, "restore": 0}
    assert "path" not in str(body) and str(tmp_path) not in str(body)  # 경로는 보내지 않는다


def test_backup_api_copy_and_failure(api, tmp_path, monkeypatch):
    api.app.state.settings.backup_copy_dir = tmp_path / "D"
    body = api.post("/api/backups").json()
    assert body["copy"]["on"] and body["copy"]["count"] == 1

    def boom(*a, **k):
        raise bk.BackupError("x")

    import hanjigap.web.system as system

    monkeypatch.setattr(system, "make_backup", boom)
    r = api.post("/api/backups")
    assert r.status_code == 500 and "백업을 만들지 못했어요" in r.json()["error"]


def test_web_settle_backs_up_first(api, sample_bytes):
    p = upload(api, sample_bytes).json()
    assert api.post(f"/api/uploads/{p['upload_id']}/apply").status_code == 200
    assert api.get("/api/backups").json()["counts"]["settle"] == 1


def test_web_settle_stops_without_backup(api, api_conn, sample_bytes, monkeypatch):
    import hanjigap.web.uploads_api as uploads_mod

    def boom(*a, **k):
        raise bk.BackupError("x")

    monkeypatch.setattr(uploads_mod, "make_backup", boom)
    p = upload(api, sample_bytes).json()
    r = api.post(f"/api/uploads/{p['upload_id']}/apply")
    assert r.status_code == 503 and "백업을 만들지 못해서 멈췄어요" in r.json()["error"]
    assert api_conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0


# --- 명령어 ---------------------------------------------------------------------


def test_cli_settle_backs_up(cli_env, capsys, isolated_env):  # noqa: F811
    path, _ = cli_env
    assert run(path, "--yes") == 0
    assert "(정산 직전)" in capsys.readouterr().out
    assert [b.kind for b in bk.list_backups(isolated_env / "data" / "backups")] == ["settle"]


def test_cli_backup_list_restore(cli_env, capsys, isolated_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("HANJIGAP_PORT", str(free_port()))
    get_settings.cache_clear()
    assert cli.main(["db", "backup"]) == 0
    first = bk.list_backups(isolated_env / "data" / "backups")[0]
    assert cli.main(["db", "backups"]) == 0
    out = capsys.readouterr().out
    assert first.path.name in out and "보관 규칙" in out and "설정 안 함" in out

    cli.main(["member", "add", "jaehee", "--name", "재희"])
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: False)
    assert cli.main(["db", "restore", first.path.name]) == 1  # 취소
    # 백업 폴더 안의 이름만, '.db' 없이 적어도 찾는다
    assert cli.main(["db", "restore", first.path.stem, "--yes"]) == 0
    out = capsys.readouterr().out
    assert "되돌렸습니다" in out and "· 직접 · 스키마" in out
    c = connect(isolated_env / "data" / "hanjigap.db")
    assert c.execute("SELECT count(*) FROM members").fetchone()[0] == 1
    c.close()


def test_cli_restore_checks_file_before_asking(
    cli_env, capsys, isolated_env, monkeypatch  # noqa: F811
):
    monkeypatch.setenv("HANJIGAP_PORT", str(free_port()))
    get_settings.cache_clear()
    monkeypatch.setattr(cli, "ask_confirm", lambda prompt: pytest.fail("묻지 않아야 함"))
    assert cli.main(["db", "restore", "hanjigap-20261030-112825-daily"]) == 1
    assert "백업 파일을 찾지 못했습니다" in capsys.readouterr().err
    bad = isolated_env / "bad.db"
    bad.write_text("not sqlite")
    assert cli.main(["db", "restore", str(bad)]) == 1
    assert "한지갑 백업 파일이 아니에요" in capsys.readouterr().err
    assert bk.list_backups(isolated_env / "data" / "backups") == []  # 아무것도 만들지 않음


def test_find_backup_file(tmp_path, monkeypatch):
    folder = tmp_path / "b"
    path = touch(folder, NOW, "daily")
    here = tmp_path / "여기.db"
    here.write_text("x")
    monkeypatch.chdir(tmp_path)
    assert cli.find_backup_file(Path(path.stem), folder) == path
    assert cli.find_backup_file(Path(path.name), folder) == path
    assert cli.find_backup_file(path, folder) == path
    assert cli.find_backup_file(Path("여기"), folder) == Path("여기.db")
    assert cli.find_backup_file(Path("없음"), folder) is None


def test_cli_restore_refused_while_server_runs(cli_env, capsys, monkeypatch):  # noqa: F811
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        monkeypatch.setenv("HANJIGAP_PORT", str(server.getsockname()[1]))
        get_settings.cache_clear()
        assert cli.main(["db", "restore", "x.db", "--yes"]) == 1
    assert "서버가 켜져 있습니다" in capsys.readouterr().err


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- check (운영 점검) -----------------------------------------------------------


@pytest.fixture
def prod_env(cli_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("HANJIGAP_ENV", "prod")
    monkeypatch.setenv("HANJIGAP_CF_TEAM_DOMAIN", "myteam")
    monkeypatch.setenv("HANJIGAP_CF_ACCESS_AUD", "a" * 64)
    get_settings.cache_clear()
    seen = []
    monkeypatch.setattr(cli, "fetch_access_certs", lambda url: seen.append(url) or 2)
    return seen


def test_check_prod_ok(prod_env, capsys):
    cli.main(["member", "update", "sangbeom", "--email", "me@example.com"])
    capsys.readouterr()
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "공개키 2개" in out and "설정 확인 완료" in out
    assert prod_env == ["https://myteam.cloudflareaccess.com/cdn-cgi/access/certs"]


def test_check_prod_problems(prod_env, capsys, monkeypatch):
    def offline(url):
        raise OSError("offline")

    monkeypatch.setattr(cli, "fetch_access_certs", offline)
    monkeypatch.setenv("HANJIGAP_DEV_MEMBER", "sangbeom")
    get_settings.cache_clear()
    assert cli.main(["check"]) == 1
    out = capsys.readouterr().out
    assert "공개키를 받지 못했습니다 (OSError)" in out
    assert "DEV_MEMBER를 지워야" in out and "로그인 이메일이 없어" in out
    assert "살펴볼 것 3개" in out


def test_check_dev_shows_backup_settings(isolated_env, capsys):
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "backup_dir" in out and "port" in out and "(미설정)" in out
