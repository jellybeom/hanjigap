import pytest
from pydantic import ValidationError

from hanjigap import __version__
from hanjigap.cli import main
from hanjigap.config import Settings


def test_defaults(isolated_env):
    s = Settings()
    assert s.env == "dev"
    assert s.timezone == "Asia/Seoul"
    assert s.data_dir == (isolated_env / "data").resolve()
    assert s.db_path == (isolated_env / "data" / "hanjigap.db").resolve()
    assert s.data_dir.is_absolute()


def test_timezone_loads():
    # Windows에서 tzdata 패키지가 없으면 여기서 실패한다
    assert Settings().tz.key == "Asia/Seoul"


def test_env_var_override(monkeypatch, isolated_env):
    monkeypatch.setenv("HANJIGAP_ENV", "prod")
    monkeypatch.setenv("HANJIGAP_LOG_LEVEL", "debug")
    s = Settings()
    assert s.env == "prod"
    assert s.log_level == "DEBUG"


def test_dotenv_file(isolated_env):
    (isolated_env / ".env").write_text("HANJIGAP_DATA_DIR=./mydata\n", encoding="utf-8")
    assert Settings().data_dir == (isolated_env / "mydata").resolve()


def test_invalid_timezone(monkeypatch):
    monkeypatch.setenv("HANJIGAP_TIMEZONE", "Mars/Olympus")
    with pytest.raises(ValidationError):
        Settings()


def test_cli_version(capsys):
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_cli_check_creates_dirs(isolated_env, capsys):
    assert main(["check"]) == 0
    assert (isolated_env / "data").is_dir()
    assert (isolated_env / "logs" / "hanjigap.log").is_file()
    assert "설정 확인 완료" in capsys.readouterr().out


def test_cli_check_reports_bad_config(monkeypatch, capsys):
    monkeypatch.setenv("HANJIGAP_ENV", "staging")
    assert main(["check"]) == 1
    assert "설정 오류" in capsys.readouterr().err
