"""H2: 트레이 뒤의 동작 (서버 켜기·끄기 · 상태 · 백업 · 로그). 아이콘 자체는 윈도우에서 확인."""

import socket
import urllib.request

import pytest

from hanjigap import cli
from hanjigap.config import Settings, get_settings
from hanjigap.db import backup as bk
from hanjigap.tray import TrayController


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def settings(tmp_path, api_db):
    return Settings(
        env="dev",
        dev_member="sangbeom",
        db_path=api_db,
        data_dir=api_db.parent,
        log_dir=tmp_path / "logs",
        backup_dir=tmp_path / "backups",
        port=free_port(),
    )


def test_start_serves_and_stops(settings):
    ctl = TrayController(settings)
    assert ctl.start(), ctl.state.error
    try:
        assert ctl.state.running
        with urllib.request.urlopen(ctl.local_url + "api/health", timeout=5) as res:
            assert res.status == 200
        assert ctl.status_text().startswith(f"켜짐 · 포트 {settings.port}")
    finally:
        ctl.stop()
    assert not ctl.state.running and not cli.server_running(settings.port)
    # 켜질 때 매일 백업이 만들어진다 (serve와 같음)
    assert [b.kind for b in bk.list_backups(settings.backup_dir)] == ["daily"]


def test_start_refuses_busy_port(settings):
    with socket.socket() as other:
        other.bind(("127.0.0.1", settings.port))
        other.listen()
        ctl = TrayController(settings)
        assert not ctl.start()
    assert "이미 쓰고 있어요" in ctl.state.error
    assert ctl.status_text().startswith("꺼짐 · 포트")


def test_start_reports_db_problem(settings, tmp_path):
    settings.db_path = tmp_path / "없음.db"
    ctl = TrayController(settings)
    assert not ctl.start()
    assert "DB 파일이 없습니다" in ctl.state.error


def test_start_reports_setup_problem(settings):
    settings.env = "prod"  # Access 설정 없이 운영 모드
    ctl = TrayController(settings)
    assert not ctl.start()
    assert "HANJIGAP_DEV_MEMBER" in ctl.state.error


def test_backup_now_and_status(settings, tmp_path):
    ctl = TrayController(settings)
    assert ctl.status_text().endswith("백업 없음")
    assert ctl.backup_now() == "백업했어요"
    assert "마지막 백업" in ctl.status_text()
    settings.backup_copy_dir = tmp_path / "D"
    assert ctl.backup_now() == "백업했어요 (두 번째 사본 포함)"
    blocker = tmp_path / "file"
    blocker.write_text("x")
    settings.backup_copy_dir = blocker
    assert "두 번째 사본은 복사하지 못했어요" in ctl.backup_now()
    settings.db_path = tmp_path / "없음.db"
    assert ctl.backup_now().startswith("백업하지 못했어요")


def test_log_tail(settings):
    ctl = TrayController(settings)
    assert "아직 로그가 없어요" in ctl.log_tail_file().read_text(encoding="utf-8")
    settings.log_dir.mkdir(parents=True)
    ctl.log_file.write_text("".join(f"줄 {i}\n" for i in range(500)), encoding="utf-8")
    text = ctl.log_tail_file(lines=200).read_text(encoding="utf-8")
    assert "줄 300\n" in text and "줄 299\n" not in text and text.endswith("줄 499\n")


def test_open_url_and_restart(settings):
    ctl = TrayController(settings)
    assert ctl.open_url == f"http://127.0.0.1:{settings.port}/"
    settings.public_url = "https://hanjigap.example.com/"
    assert ctl.open_url == "https://hanjigap.example.com/"
    assert ctl.restart_command()[1:] == ["-m", "hanjigap", "tray"]


def test_public_url_setting():
    assert Settings(public_url="https://a.example.com").public_url == "https://a.example.com/"
    assert Settings(public_url=" ").public_url is None
    with pytest.raises(ValueError):
        Settings(public_url="a.example.com")


def test_cli_tray_without_pystray(isolated_env, capsys, monkeypatch):
    """윈도우가 아니면(pystray 없음) 알림 문장을 보이고 끝낸다."""
    import builtins

    real_import = builtins.__import__

    def no_pystray(name, *a, **k):
        if name == "pystray":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pystray)
    assert cli.main(["tray"]) == 1
    assert "윈도우에서만" in capsys.readouterr().err


def test_cli_tray_bad_settings(isolated_env, capsys, monkeypatch):
    monkeypatch.setenv("HANJIGAP_PORT", "80")
    get_settings.cache_clear()
    assert cli.main(["tray"]) == 1
    err = capsys.readouterr().err
    assert "설정(.env) 오류" in err and "port" in err


# --- 트레이 연결 (가짜 pystray로 메뉴 구성과 동작 순서만 확인) -------------------------


class FakeIcon:
    last = None

    def __init__(self, name, image, title):
        self.name, self.icon, self.title = name, image, title
        self.notes, self.visible, self.stopped = [], False, False
        FakeIcon.last = self

    def notify(self, message, title):
        self.notes.append(message)

    def update_menu(self):
        pass

    def stop(self):
        self.stopped = True

    def run(self, setup):
        setup(self)
        self.items = {i.text if isinstance(i.text, str) else "상태": i for i in self.menu.items}
        self.items["상태"].text(None)  # 상태 줄 글자를 만들 수 있다
        self.items["지금 백업"].action(self, None)
        self.items["끄기"].action(self, None)


class FakeItem:
    def __init__(self, text, action, default=False, enabled=True):
        self.text, self.action, self.default, self.enabled = text, action, default, enabled


class FakeMenu:
    SEPARATOR = FakeItem("-", None)

    def __init__(self, *items):
        self.items = [i for i in items if i is not FakeMenu.SEPARATOR]


def test_run_tray_flow(settings, monkeypatch):
    import sys
    import types

    import hanjigap.tray as tray

    fake = types.SimpleNamespace(Icon=FakeIcon, MenuItem=FakeItem, Menu=FakeMenu)
    monkeypatch.setitem(sys.modules, "pystray", fake)
    monkeypatch.setattr(tray, "load_icon_images", lambda: ("on", "off"))
    assert tray.run_tray(settings) == 0
    icon = FakeIcon.last
    assert icon.visible and icon.stopped
    assert icon.notes[0].startswith("한지갑이 켜졌어요") and icon.notes[1] == "백업했어요"
    assert icon.items["한지갑 열기"].default
    assert set(icon.items) >= {"백업 폴더 열기", "로그 보기", "다시 시작"}
    assert not cli.server_running(settings.port)  # 끄기 뒤 서버도 꺼짐


def test_run_tray_refuses_second_instance(settings, monkeypatch, capsys):
    import sys
    import types

    fake = types.SimpleNamespace(Icon=FakeIcon, MenuItem=FakeItem, Menu=FakeMenu)
    monkeypatch.setitem(sys.modules, "pystray", fake)
    from hanjigap.tray import run_tray

    with socket.socket() as other:
        other.bind(("127.0.0.1", settings.port))
        other.listen()
        assert run_tray(settings) == 1
    assert "이미 켜져 있어요" in capsys.readouterr().err
