import io
import zipfile

import pytest
from openpyxl import load_workbook

from hanjigap import cli
from hanjigap.importer import unpack as unpack_mod
from hanjigap.importer.errors import ErrorCode, ImportFailure
from hanjigap.importer.unpack import open_export

PW = "test1234"
INNER = "2025-10-20~2026-10-20.xlsx"
SHEETS = ["뱅샐현황", "가계부 내역"]


def _sheets(data: bytes) -> list[str]:
    return load_workbook(io.BytesIO(data), read_only=True).sheetnames


def _fail(data, password=None) -> ErrorCode:
    with pytest.raises(ImportFailure) as exc:
        open_export(data, password)
    return exc.value.code


@pytest.fixture(scope="module")
def export_zip(make_zip, sample_bytes):
    """뱅샐 원본과 같은 형태: ZipCrypto로 잠긴 zip 안에 잠기지 않은 엑셀 1개."""
    return make_zip([(INNER, sample_bytes)], PW)


# --- 정상 경로 ---------------------------------------------------------------


def test_banksalad_style_zip(export_zip, sample_bytes):
    opened = open_export(export_zip, PW)
    assert opened.container == "zip"
    assert opened.zip_encrypted and not opened.xlsx_encrypted
    assert opened.inner_name == INNER
    assert opened.lock_label == "zip 비밀번호"
    assert opened.xlsx == sample_bytes
    assert _sheets(opened.xlsx) == SHEETS


def test_zip_with_locked_excel_inside(make_zip, encrypted_sample_bytes, sample_bytes):
    data = make_zip([(INNER, encrypted_sample_bytes)], PW)
    opened = open_export(data, PW)
    assert opened.zip_encrypted and opened.xlsx_encrypted
    assert opened.lock_label == "zip 비밀번호 + 엑셀 비밀번호"
    assert _sheets(opened.xlsx) == SHEETS


def test_unlocked_zip(make_zip, sample_bytes):
    opened = open_export(make_zip([(INNER, sample_bytes)]))
    assert opened.container == "zip" and not opened.zip_encrypted
    assert opened.lock_label == "없음"


def test_plain_xlsx_still_works(sample_bytes):
    opened = open_export(sample_bytes)
    assert opened.container == "xlsx" and opened.inner_name is None
    assert opened.xlsx is sample_bytes


def test_locked_xlsx_still_works(encrypted_sample_bytes):
    opened = open_export(encrypted_sample_bytes, PW)
    assert opened.container == "xlsx" and opened.xlsx_encrypted
    assert opened.lock_label == "엑셀 비밀번호"


def test_extra_non_excel_files_ignored(make_zip, sample_bytes):
    data = make_zip(
        [("readme.txt", b"hi"), ("__MACOSX/._x.xlsx", b"junk"), (INNER, sample_bytes)], PW
    )
    assert open_export(data, PW).inner_name == INNER


def test_korean_password(make_zip, sample_bytes):
    data = make_zip([(INNER, sample_bytes)], "한지갑2026")
    assert open_export(data, "한지갑2026").xlsx == sample_bytes


def test_cp949_file_name():
    # 한국어 윈도우 압축 프로그램처럼 UTF-8 표시 없이 cp949로 저장된 이름
    info = zipfile.ZipInfo("가계부.xlsx".encode("cp949").decode("cp437"))
    assert unpack_mod._display_name(info) == "가계부.xlsx"


# --- 오류 경로 ---------------------------------------------------------------


def test_zip_requires_password(export_zip):
    assert _fail(export_zip) == ErrorCode.PASSWORD_REQUIRED
    assert _fail(export_zip, "") == ErrorCode.PASSWORD_REQUIRED


@pytest.mark.parametrize("wrong", ["wrong", "test12345", "TEST1234", "x" * 30])
def test_zip_wrong_password(export_zip, wrong):
    assert _fail(export_zip, wrong) == ErrorCode.WRONG_PASSWORD


def test_wrong_password_passing_first_check(make_zip, sample_bytes):
    # ZipCrypto 1차 검사(1바이트)를 우연히 통과하는 틀린 비밀번호도 결국 WRONG_PASSWORD여야 한다
    data = make_zip([(INNER, sample_bytes)], PW)
    info = zipfile.ZipFile(io.BytesIO(data)).infolist()[0]
    passed = 0
    for i in range(3000):
        pwd = f"guess{i}"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf, zf.open(info, pwd=pwd.encode()) as m:
                m.read(1)
        except RuntimeError:
            continue  # 1차 검사에서 걸림
        except Exception:
            pass
        passed += 1
        assert _fail(data, pwd) == ErrorCode.WRONG_PASSWORD
        if passed >= 3:
            break
    assert passed >= 1


def test_zip_without_excel(make_zip):
    data = make_zip([("readme.txt", b"hello")], PW)
    assert _fail(data, PW) == ErrorCode.ZIP_NO_EXCEL


def test_zip_with_two_excels(make_zip, sample_bytes):
    data = make_zip([("a.xlsx", sample_bytes), ("b.xlsx", sample_bytes)], PW)
    assert _fail(data, PW) == ErrorCode.ZIP_MULTIPLE_EXCEL


def test_zip_with_fake_excel_inside(make_zip):
    data = make_zip([(INNER, b"not really excel")], PW)
    assert _fail(data, PW) == ErrorCode.NOT_EXCEL


def test_zip_bomb_guard(monkeypatch, make_zip, sample_bytes):
    monkeypatch.setattr(unpack_mod, "MAX_UNCOMPRESSED_BYTES", len(sample_bytes) - 1)
    data = make_zip([(INNER, sample_bytes)], PW)
    assert _fail(data, PW) == ErrorCode.FILE_TOO_LARGE


def test_size_header_lies(monkeypatch, make_zip, sample_bytes):
    # 헤더 크기를 믿지 않고 실제로 읽은 양으로 제한하는지
    monkeypatch.setattr(unpack_mod, "MAX_UNCOMPRESSED_BYTES", len(sample_bytes) - 1)
    monkeypatch.setattr(unpack_mod, "_pick_excel", lambda archive: archive.infolist()[0])
    data = make_zip([(INNER, sample_bytes)], PW)
    assert _fail(data, PW) == ErrorCode.FILE_TOO_LARGE


def test_aes_zip_unsupported(make_zip, sample_bytes):
    data = bytearray(make_zip([(INNER, sample_bytes)], PW))
    # 로컬 헤더와 중앙 디렉터리의 압축 방식을 AES(99)로 바꿈
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        pos = data.find(sig)
        method_at = pos + (8 if sig == b"PK\x03\x04" else 10)
        data[method_at : method_at + 2] = (99).to_bytes(2, "little")
    assert _fail(bytes(data), PW) == ErrorCode.ZIP_UNSUPPORTED


def test_password_not_in_message(export_zip):
    with pytest.raises(ImportFailure) as exc:
        open_export(export_zip, "my-secret-pw")
    assert "my-secret-pw" not in str(exc.value)


# --- inspect 명령 ------------------------------------------------------------


def test_inspect_banksalad_zip(monkeypatch, tmp_path, export_zip, capsys):
    path = tmp_path / "뱅샐.zip"
    path.write_bytes(export_zip)
    monkeypatch.setattr(cli, "ask_password", lambda: PW)
    assert cli.main(["inspect", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"zip (안의 파일: {INNER})" in out
    assert "zip 비밀번호" in out
    assert "뱅샐현황, 가계부 내역" in out
