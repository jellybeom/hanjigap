"""윈도우 트레이 상주 (H2).

미니PC에서 콘솔 창 없이 한지갑 서버를 켜 두고, 알림 영역 아이콘 메뉴로 다룬다.
    start.vbs → .venv의 pythonw로 'python -m hanjigap tray' → 서버(스레드) + 트레이 아이콘(메인)
    start.vbs 바로가기를 shell:startup에 넣으면 윈도우에 로그인할 때 자동으로 켜진다.

메뉴
    한지갑 열기 (아이콘 더블클릭) · 상태 줄 · 지금 백업 · 백업 폴더 열기
    로그 보기 (최근 200줄 / 전체) · 다시 시작 (코드 업데이트 뒤) · 끄기

구조
    TrayController: 서버 켜기·끄기·백업·로그 같은 동작. 트레이 라이브러리 없이 시험할 수 있다.
    run_tray(): pystray 아이콘과 메뉴를 붙인다. (윈도우 전용 라이브러리라 여기서만 불러온다)

로그에는 동작 종류와 결과만 남긴다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hanjigap.config import Settings
from hanjigap.db.backup import BackupError, list_backups, make_backup
from hanjigap.logging_setup import LOG_FILE_NAME

log = logging.getLogger("hanjigap.tray")

ICON_PATH = Path(__file__).parent / "web" / "static" / "icons" / "icon-192.png"
TAIL_LINES = 200
START_TIMEOUT = 20.0  # 서버가 켜지기를 기다리는 시간(초)
STOP_TIMEOUT = 10.0


@dataclass
class ServerState:
    running: bool = False
    error: str | None = None  # 켜지 못한 이유 (화면에 보여 줄 문장)


class TrayController:
    """트레이 메뉴 뒤의 동작. 서버는 같은 프로세스의 스레드에서 돈다."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = ServerState()
        self._server = None  # uvicorn.Server
        self._thread: threading.Thread | None = None
        self.on_change = None  # 서버가 스스로 멈췄을 때 부른다 (아이콘 바꾸기)

    # --- 서버 ------------------------------------------------------------------

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.port}/"

    @property
    def open_url(self) -> str:
        """'한지갑 열기' 주소. 운영에서는 Cloudflare 주소로 (127.0.0.1은 로그인이 막힘)."""
        return self.settings.public_url or self.local_url

    def start(self) -> bool:
        """서버를 켠다. 켜졌으면 True, 아니면 state.error에 이유를 남기고 False."""
        from hanjigap.cli import server_running
        from hanjigap.db.connection import DatabaseError, connect_ready

        self.state = ServerState()
        if server_running(self.settings.port):
            return self._fail(
                f"포트 {self.settings.port}을 이미 쓰고 있어요. 한지갑이 켜져 있는지 확인하세요"
            )
        try:
            connect_ready(self.settings.db_path).close()
        except DatabaseError as exc:
            return self._fail(str(exc))

        import uvicorn

        from hanjigap.web import SetupError, create_app

        try:
            app = create_app(self.settings, auto_backup=True)
        except SetupError as exc:
            return self._fail(str(exc))
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.settings.port,
            access_log=False,
            server_header=False,
            log_level="warning",
            log_config=None,  # 콘솔이 없는 실행(pythonw)에서도 uvicorn이 로그 설정을 건드리지 않게
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=self._serve, args=(server,), name="hanjigap-server")
        thread.daemon = True
        self._server, self._thread = server, thread
        thread.start()
        waited = 0.0
        while not server.started and thread.is_alive() and waited < START_TIMEOUT:
            thread.join(0.05)
            waited += 0.05
        if not server.started:
            self.stop()
            return self._fail("서버를 켜지 못했어요. 로그를 확인하세요")
        self.state = ServerState(running=True)
        log.info("트레이: 서버 켜짐")
        return True

    def _serve(self, server) -> None:
        try:
            server.run()
        except BaseException as exc:  # uvicorn은 켜기 실패 때 SystemExit을 던진다
            log.error("트레이: 서버 실행 중단 (%s)", type(exc).__name__)
        finally:
            if self.state.running:  # 끄지 않았는데 멈춤
                self.state = ServerState(error="서버가 멈췄어요. '다시 시작'을 눌러 주세요")
                if self.on_change:
                    self.on_change()

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self.state = ServerState(error=self.state.error)
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(STOP_TIMEOUT)
            if thread.is_alive():
                log.warning("트레이: 서버가 제때 꺼지지 않음")
        self._server = self._thread = None

    def _fail(self, message: str) -> bool:
        self.state = ServerState(error=message)
        log.warning("트레이: 서버 켜기 실패")
        return False

    # --- 메뉴 동작 ---------------------------------------------------------------

    def status_text(self) -> str:
        if self.state.running:
            head = f"켜짐 · 포트 {self.settings.port}"
        else:
            head = "꺼짐" + (f" · {self.state.error}" if self.state.error else "")
        last = self.last_backup()
        tail = f" · 마지막 백업 {last:%m/%d %H:%M}" if last else " · 백업 없음"
        return head + tail

    def last_backup(self) -> datetime | None:
        items = list_backups(self.settings.backup_dir)
        return items[0].at if items else None

    def backup_now(self) -> str:
        """직접 백업. 알림으로 보여 줄 문장을 돌려준다."""
        try:
            result = make_backup(
                self.settings.db_path,
                self.settings.backup_dir,
                "manual",
                now=self.settings.now(),
                copy_dir=self.settings.copy_dir,
            )
        except BackupError as exc:
            return f"백업하지 못했어요: {exc}"
        if result.copied is False:
            return "백업했어요. 두 번째 사본은 복사하지 못했어요 (드라이브 확인)"
        return "백업했어요" + (" (두 번째 사본 포함)" if result.copied else "")

    @property
    def log_file(self) -> Path:
        return self.settings.log_dir / LOG_FILE_NAME

    def log_tail_file(self, lines: int = TAIL_LINES) -> Path:
        """최근 로그 몇 줄을 임시 파일로 만든다. (메모장으로 열기용)"""
        tail: deque[str] = deque(maxlen=lines)
        if self.log_file.is_file():
            with self.log_file.open(encoding="utf-8", errors="replace") as f:
                tail.extend(f)
        out = Path(tempfile.gettempdir()) / "hanjigap-log-tail.txt"
        body = "".join(tail) or "아직 로그가 없어요.\n"
        out.write_text(f"한지갑 로그 최근 {lines}줄 ({self.log_file})\n\n{body}", encoding="utf-8")
        return out

    def restart_command(self) -> list[str]:
        return [sys.executable, "-m", "hanjigap", "tray"]


# --- 윈도우 연결 -------------------------------------------------------------------


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 (탐색기·연결 프로그램으로 열기)
    else:  # 개발용
        subprocess.Popen(["xdg-open", str(path)])


def open_in_notepad(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["notepad.exe", str(path)])
    else:
        open_path(path)


def message_box(message: str) -> None:
    """콘솔이 없을 때도 보이는 알림 창. (윈도우가 아니면 stderr로)"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "한지갑", 0x40)  # 정보 아이콘
    else:
        print(message, file=sys.stderr)


def quiet_std_streams() -> None:
    """pythonw로 실행하면 콘솔이 없어 sys.stdout/stderr가 None이다. 쓰기 오류를 막아 둔다."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def load_icon_images():
    """켜짐(원래 색) · 꺼짐(흑백) 아이콘."""
    from PIL import Image, ImageOps

    on = Image.open(ICON_PATH).convert("RGBA").resize((64, 64))
    gray = ImageOps.grayscale(on.convert("RGB")).convert("RGBA")
    gray.putalpha(on.getchannel("A"))
    return on, gray


def run_tray(settings: Settings) -> int:
    from hanjigap.cli import server_running

    quiet_std_streams()
    try:
        import pystray
    except ImportError:
        message_box(
            "트레이 기능은 윈도우에서만 써요 (pystray·Pillow 필요). "
            "윈도우에서 'uv sync' 뒤 다시 실행하세요"
        )
        return 1
    if server_running(settings.port):  # 두 번 켜지 않게
        message_box(
            f"한지갑이 이미 켜져 있어요 (포트 {settings.port}). 알림 영역 아이콘을 확인하세요"
        )
        return 1

    ctl = TrayController(settings)
    image_on, image_off = load_icon_images()
    icon = pystray.Icon("hanjigap", image_off, "한지갑")

    def refresh() -> None:
        icon.icon = image_on if ctl.state.running else image_off
        icon.title = f"한지갑 · {'켜짐' if ctl.state.running else '꺼짐'}"
        icon.update_menu()

    ctl.on_change = lambda: (refresh(), notify(ctl.state.error or "서버가 멈췄어요"))

    def notify(message: str) -> None:
        try:
            icon.notify(message, "한지갑")
        except Exception:  # 알림을 못 띄워도 동작은 계속
            log.warning("트레이: 알림 표시 실패")

    def on_open(_icon, _item) -> None:
        webbrowser.open(ctl.open_url)

    def on_backup(_icon, _item) -> None:
        notify(ctl.backup_now())
        refresh()

    def on_backup_dir(_icon, _item) -> None:
        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        open_path(settings.backup_dir)

    def on_log_tail(_icon, _item) -> None:
        open_in_notepad(ctl.log_tail_file())

    def on_log_all(_icon, _item) -> None:
        if ctl.log_file.is_file():
            open_in_notepad(ctl.log_file)
        else:
            notify("아직 로그가 없어요")

    def on_restart(_icon, _item) -> None:
        log.info("트레이: 다시 시작")
        ctl.stop()
        subprocess.Popen(ctl.restart_command(), cwd=os.getcwd())
        icon.stop()

    def on_quit(_icon, _item) -> None:
        log.info("트레이: 끄기")
        ctl.stop()
        icon.stop()

    item = pystray.MenuItem
    icon.menu = pystray.Menu(
        item("한지갑 열기", on_open, default=True),
        item(lambda _i: ctl.status_text(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        item("지금 백업", on_backup),
        item("백업 폴더 열기", on_backup_dir),
        item(
            "로그 보기",
            pystray.Menu(
                item(f"최근 {TAIL_LINES}줄", on_log_tail),
                item("전체", on_log_all),
            ),
        ),
        pystray.Menu.SEPARATOR,
        item("다시 시작", on_restart),
        item("끄기", on_quit),
    )

    def setup(_icon) -> None:
        _icon.visible = True
        if ctl.start():
            notify(f"한지갑이 켜졌어요 · {ctl.open_url}")
        else:
            notify(f"한지갑을 켜지 못했어요: {ctl.state.error}")
        refresh()

    log.info("트레이 시작")
    icon.run(setup=setup)
    ctl.stop()
    return 0
