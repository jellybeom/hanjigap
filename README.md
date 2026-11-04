# 한지갑 (hanjigap)

두 사람의 지갑을 하나처럼. 부부 각자의 뱅크샐러드 월간 내보내기를 모아 합산 가계부·자산관리를 제공하는 개인용 웹 서비스입니다.

## 요구 사항

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## 설치

```powershell
uv sync
copy .env.example .env   # 필요할 때만. 없어도 기본값으로 실행됩니다.
```

## 명령어

```powershell
uv run python -m hanjigap version   # 버전 출력
uv run python -m hanjigap check     # 설정 확인, data·logs 폴더 생성
```

## 웹 서버

```powershell
uv run hanjigap serve               # http://127.0.0.1:8000 (이 컴퓨터에서만 접속)
```

개발 중에는 `.env`에 `HANJIGAP_DEV_MEMBER=sangbeom`을 적으면 로그인 없이 그 구성원으로 시험할 수 있고,
API 설명서는 http://127.0.0.1:8000/docs 에서 볼 수 있습니다. 운영 모드(`HANJIGAP_ENV=prod`)에서는
개발 모드 로그인과 API 설명서가 꺼지고, Cloudflare Access 설정이 없으면 서버가 켜지지 않습니다.

## 미니PC 상주 (윈도우)

처음 설치(Cloudflare Access 로그인·Worker·Tunnel·홈 화면 추가)는 [docs/deploy-windows.md](docs/deploy-windows.md)를 따라 합니다.
바깥 접속용 Worker는 [worker/](worker/README.md)에 있습니다.

```powershell
uv sync                             # 트레이 라이브러리(pystray·Pillow)는 윈도우에서만 설치됩니다
start.vbs                           # 콘솔 창 없이 트레이 아이콘으로 서버 켜기
```

- 부팅 때 자동 실행: `Win+R` → `shell:startup` → 열린 폴더에 `start.vbs` **바로가기**를 넣습니다.
- 트레이 메뉴: 한지갑 열기(아이콘 클릭) · 상태 · 지금 백업 · 백업 폴더 열기 · 로그 보기 · 다시 시작 · 끄기
- `.env`의 `HANJIGAP_PUBLIC_URL`(예: `https://hanjigap.하위이름.workers.dev`)을 적으면 '한지갑 열기'가 그 주소로 열립니다.
- 업데이트: 트레이 '끄기' → `git pull` → `uv sync` → `start.vbs`
  (코드만 바뀌었으면 `git pull` 뒤 트레이 '다시 시작'으로도 됩니다)

## 백업

서버가 켜질 때와 매일 한 번, 정산을 저장하기 직전에 `data/backups`로 자동 백업합니다.
`.env`의 `HANJIGAP_BACKUP_COPY_DIR`을 적으면 같은 백업을 그 폴더에도 하나 더 둡니다.

```powershell
uv run hanjigap db backup           # 지금 백업
uv run hanjigap db backups          # 목록과 보관 규칙
uv run hanjigap db restore 이름     # 되돌리기 (서버를 끈 상태에서만, 지금 DB는 먼저 백업)
```

## 개발

```powershell
uv run pytest         # 테스트
uv run ruff check         # 린트 (실수 점검)
uv run black --check .    # 포맷 확인 (실패하면 uv run black .)
```

포매터는 Black(줄 길이 100)을 사용합니다. 편집기에서 `hanjigap` 폴더를 루트로 열면 `pyproject.toml` 설정이 자동 적용됩니다.

## 데이터 보호 원칙

- 실제 뱅크샐러드 파일(`*.xlsx`)과 DB(`data/`), 로그(`logs/`)는 git에 올리지 않습니다.
- 뱅크샐러드 엑셀 비밀번호는 설정 파일에 저장하지 않습니다.
- 로그에는 건수·기간·처리 결과만 남기고 거래 내용·금액·이름은 기록하지 않습니다.