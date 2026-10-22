"""뱅크샐러드 내보내기 파일을 테스트용으로 익명화한다.

사용 예:
    uv run python scripts/anonymize_banksalad.py 원본.xlsx ^
        --out tests/fixtures/sample.anon.xlsx ^
        --self 윤상범 상범 --spouse 이재희 재희 잴리

입력은 비밀번호가 없는 엑셀만 받는다.
결과 파일은 커밋 전에 반드시 엑셀로 열어서 눈으로 확인한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hanjigap.tools.anonymize import AnonymizeError, anonymize_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="뱅크샐러드 내보내기 파일 익명화")
    parser.add_argument("src", type=Path, help="원본 엑셀 (비밀번호 없음)")
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/sample.anon.xlsx"))
    parser.add_argument("--self", dest="self_names", nargs="+", required=True, help="본인 이름들")
    parser.add_argument(
        "--spouse",
        dest="spouse_names",
        nargs="+",
        required=True,
        help="배우자 이름·애칭들",
    )
    parser.add_argument(
        "--forbid", nargs="*", default=[], help="결과에 절대 남으면 안 되는 추가 단어"
    )
    parser.add_argument("--force", action="store_true", help="출력 파일 덮어쓰기")
    args = parser.parse_args(argv)

    try:
        result = anonymize_file(
            args.src,
            args.out,
            self_names=args.self_names,
            spouse_names=args.spouse_names,
            forbid=args.forbid,
            overwrite=args.force,
        )
    except AnonymizeError as exc:
        print(f"[중단] {exc}", file=sys.stderr)
        return 1

    print(f"{'항목':<14}{'원본':<40}결과")
    for (label, before), (_, after) in zip(
        result.before.as_rows(), result.after.as_rows(), strict=True
    ):
        mark = "OK" if before == after else "다름"
        print(f"{label:<14}{str(before):<40}{after}  [{mark}]")
    print(f"\n내용 가명 {result.content_map_size}개, 결제수단 {len(result.instrument_map)}개 변환")
    print(f"저장: {args.out}")
    print("커밋 전에 엑셀로 열어서 이름·가맹점·카드명이 보이지 않는지 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
