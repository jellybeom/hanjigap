import io
import zipfile

import pytest
from openpyxl import load_workbook

from hanjigap import cli
from hanjigap.importer import decrypt as decrypt_mod
from hanjigap.importer.decrypt import decrypt_xlsx
from hanjigap.importer.errors import ErrorCode, ImportFailure

SHEETS = ["뱅샐현황", "가계부 내역"]
SAMPLE_PASSWORD = "test1234"  # conftest.encrypted_sample_bytes와 같은 값


def _sheets(data: bytes) -> list[str]:
    return load_workbook(io.BytesIO(data), read_only=True).sheetnames


def _code(exc_info) -> ErrorCode:
    return exc_info.value.code


# --- decrypt_xlsx -----------------------------------------------------------


def test_encrypted_with_correct_password(encrypted_sample_bytes, sample_bytes):
    result = decrypt_xlsx(encrypted_sample_bytes, SAMPLE_PASSWORD)
    assert result.was_encrypted is True
    assert _sheets(result.data) == SHEETS
    assert result.data != encrypted_sample_bytes


def test_encrypted_without_password(encrypted_sample_bytes):
    with pytest.raises(ImportFailure) as exc:
        decrypt_xlsx(encrypted_sample_bytes, None)
    assert _code(exc) == ErrorCode.PASSWORD_REQUIRED


def test_encrypted_with_empty_password(encrypted_sample_bytes):
    with pytest.raises(ImportFailure) as exc:
        decrypt_xlsx(encrypted_sample_bytes, "")
    assert _code(exc) == ErrorCode.PASSWORD_REQUIRED


def test_encrypted_with_wrong_password(encrypted_sample_bytes):
    with pytest.raises(ImportFailure) as exc:
        decrypt_xlsx(encrypted_sample_bytes, "wrong-pass")
    assert _code(exc) == ErrorCode.WRONG_PASSWORD
    assert "wrong-pass" not in str(exc.value)  # 비밀번호가 메시지에 남지 않음


def test_plain_file_passes_through(sample_bytes):
    result = decrypt_xlsx(sample_bytes)
    assert result.was_encrypted is False
    assert result.data is sample_bytes


def test_plain_file_ignores_password(sample_bytes):
    result = decrypt_xlsx(sample_bytes, "anything")
    assert result.was_encrypted is False
    assert _sheets(result.data) == SHEETS


@pytest.mark.parametrize("data", [b"", b"hello, not excel", b"\x00" * 1000])
def test_not_excel(data):
    with pytest.raises(ImportFailure) as exc:
        decrypt_xlsx(data)
    assert _code(exc) == ErrorCode.NOT_EXCEL


def test_zip_but_not_xlsx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<doc/>")
    with pytest.raises(ImportFailure) as exc:
        decrypt_xlsx(buf.getvalue())
    assert _code(exc) == ErrorCode.NOT_EXCEL


def test_too_large(monkeypatch, sample_bytes):
    monkeypatch.setattr(decrypt_mod, "MAX_FILE_BYTES", len(sample_bytes) - 1)
    with pytest.raises(ImportFailure) as exc:
        decrypt_xlsx(sample_bytes)
    assert _code(exc) == ErrorCode.FILE_TOO_LARGE


def test_corrupted_encrypted_file(encrypted_sample_bytes):
    # 파일 뒷부분(암호화된 본문)을 훼손: 비밀번호는 맞지만 무결성 검사에서 걸려야 한다
    damaged = bytearray(encrypted_sample_bytes)
    for i in range(len(damaged) - 4000, len(damaged) - 3000):
        damaged[i] ^= 0xFF
    with pytest.raises(ImportFailure) as exc:
        decrypt_xlsx(bytes(damaged), SAMPLE_PASSWORD)
    assert _code(exc) == ErrorCode.DECRYPT_FAILED


# --- inspect 명령 ------------------------------------------------------------


@pytest.fixture
def files(tmp_path, sample_bytes, encrypted_sample_bytes):
    plain = tmp_path / "plain.xlsx"
    locked = tmp_path / "locked.xlsx"
    plain.write_bytes(sample_bytes)
    locked.write_bytes(encrypted_sample_bytes)
    return plain, locked


def _feed_passwords(monkeypatch, *passwords):
    it = iter(passwords)
    calls = []

    def fake():
        calls.append(1)
        return next(it)

    monkeypatch.setattr(cli, "ask_password", fake)
    return calls


def test_inspect_plain_does_not_ask(monkeypatch, files, capsys):
    calls = _feed_passwords(monkeypatch)
    assert cli.main(["inspect", str(files[0])]) == 0
    out = capsys.readouterr().out
    assert "형식        xlsx" in out and "잠금        없음" in out and "가계부 내역" in out
    assert calls == []


def test_inspect_encrypted(monkeypatch, files, capsys):
    _feed_passwords(monkeypatch, SAMPLE_PASSWORD)
    assert cli.main(["inspect", str(files[1])]) == 0
    out = capsys.readouterr().out
    assert "엑셀 비밀번호" in out and "뱅샐현황, 가계부 내역" in out


def test_inspect_retries_then_succeeds(monkeypatch, files, capsys):
    calls = _feed_passwords(monkeypatch, "x", "y", SAMPLE_PASSWORD)
    assert cli.main(["inspect", str(files[1])]) == 0
    assert len(calls) == 3
    assert "(1/3)" in capsys.readouterr().err


def test_inspect_gives_up_after_three(monkeypatch, files, capsys):
    calls = _feed_passwords(monkeypatch, "x", "y", "z")
    assert cli.main(["inspect", str(files[1])]) == 1
    assert len(calls) == 3
    assert "비밀번호가 맞지 않습니다" in capsys.readouterr().err


def test_inspect_missing_file(tmp_path, capsys):
    assert cli.main(["inspect", str(tmp_path / "nope.xlsx")]) == 1
    assert "찾을 수 없습니다" in capsys.readouterr().err
