# 미니PC 설치 안내 (H3)

집 윈도우 미니PC에서 한지갑을 켜 두고, 휴대폰·다른 PC에서 `https://주소`로 접속하게 만드는 순서입니다.
위에서부터 차례대로 하면 됩니다. (Cloudflare 화면의 메뉴 이름은 2026년 11월 기준이며, 가끔 바뀝니다)

```
휴대폰·PC ──https──▶ Cloudflare (Access: 이메일 코드 로그인) ──Tunnel──▶ 미니PC cloudflared ──▶ 127.0.0.1:8000 한지갑
```

- 미니PC는 공유기 포트를 열지 않습니다. cloudflared가 Cloudflare 쪽으로 **나가는** 연결만 만듭니다.
- 로그인은 Cloudflare Access가 받고, 한지갑 서버가 그 로그인 표(토큰)를 한 번 더 확인합니다.

**중요한 순서:** Access 앱(로그인 문) → `.env` 운영 모드 → 서버 켜기 → **마지막에** Tunnel 주소 연결.
주소를 먼저 열면 로그인 문 없이 바깥에 드러나는 순간이 생깁니다.

---

## 1. Cloudflare 준비 (한 번만)

1. **계정**: dash.cloudflare.com 가입 → 메일로 이메일 인증 → 프로필의 보안에서 **2단계 인증** 켜기
2. **도메인**: Domain Registration → Register domains → 이름 검색 → Purchase
   - 연락처는 영문(ASCII)만, 기간 1년, 자동 갱신은 기본으로 켜짐, 해외 결제 카드
   - 이름에 실명·'가계부' 같은 말은 넣지 않기 (주소만 보고 무엇인지 모르게)
3. **Zero Trust**: 왼쪽 메뉴 **Zero Trust** → 팀 이름 정하기 → **Free** 플랜 (결제 수단 입력은 필요, 청구 없음)
   - 팀 이름은 `팀이름.cloudflareaccess.com` 이 되고, 로그인 화면 주소에 보입니다. 실명은 피하기

아래에서 쓰는 값 (예시):

| 이름        | 예시                | 쓰는 곳                                           |
| ----------- | ------------------- | ------------------------------------------------- |
| 도메인      | `example.com`       | Access 앱, Tunnel                                 |
| 한지갑 주소 | `money.example.com` | Access 앱, Tunnel, `.env`의 `HANJIGAP_PUBLIC_URL` |
| 팀 이름     | `myteam`            | `.env`의 `HANJIGAP_CF_TEAM_DOMAIN`                |
| AUD 값      | 64자리 영문·숫자    | `.env`의 `HANJIGAP_CF_ACCESS_AUD`                 |

---

## 2. 미니PC에 한지갑 설치

PowerShell에서:

```powershell
cd C:\Users\이름\code                 # 원하는 위치
git clone <한지갑 저장소 주소> hanjigap
cd hanjigap
uv sync
copy .env.example .env
```

### DB 준비 — 둘 중 하나

**A. 새로 시작**

```powershell
uv run hanjigap db init
uv run hanjigap member add sangbeom --name 상범 --email 상범로그인메일
uv run hanjigap member add jaehee --name 재희 --email 재희로그인메일
```

**B. 테스트 PC에서 쓰던 DB 옮기기** (계좌·카드·차·집·규칙 등록을 그대로)

```powershell
# 테스트 PC
uv run hanjigap db backup               # 출력된 백업 파일을 USB로 미니PC에 복사

# 미니PC
uv run hanjigap db restore D:\hanjigap-20261102-150000-manual.db --yes
uv run hanjigap db init                 # 스키마 최신화 (이미 최신이면 '적용 없음')
uv run hanjigap member update sangbeom --email 상범로그인메일
uv run hanjigap member update jaehee --email 재희로그인메일
```

- 로그인 이메일은 **Access 정책에 넣을 메일과 똑같아야** 합니다. (대소문자 무관)
- 이메일은 웹에서 바꾸지 않습니다. 바꿀 때도 미니PC에서 `member update`로.

---

## 3. Access 앱 (로그인 문) 만들기

### 3-1. 로그인 방법: 이메일 코드(One-time PIN)

Zero Trust → **Integrations** → **Identity providers** → Add new identity provider → **One-time PIN**

- 코드는 10분 동안 한 번만 쓸 수 있습니다.
- 정책에 없는 메일로 요청하면 화면에는 "코드를 보냈다"고 나오지만 실제로는 보내지 않습니다.

### 3-2. 정책: 두 사람만

Zero Trust → **Access controls** → **Policies** → Add a policy

| 항목             | 값                                      |
| ---------------- | --------------------------------------- |
| Policy name      | `부부`                                  |
| Action           | **Allow**                               |
| Session duration | **1 week**                              |
| Include          | **Emails** → 상범 메일, 재희 메일 (2개) |

### 3-3. 앱

Zero Trust → **Access controls** → **Applications** → Create new application → **Self-hosted and private**

| 항목             | 값                                                               |
| ---------------- | ---------------------------------------------------------------- |
| Application name | `한지갑`                                                         |
| Public hostname  | Subdomain `money` · Domain `example.com` · Path 비움             |
| Access policies  | `부부` 선택                                                      |
| Login methods    | **One-time PIN** 만 (켜 두면 편함: Apply instant authentication) |
| Session Duration | **1 week**                                                       |

Create 후 앱을 다시 열어 **Application Audience (AUD) Tag** 를 복사해 둡니다. (앱 설정의 기본 정보 쪽)
팀 이름은 Zero Trust → **Settings** 에서 다시 볼 수 있습니다.

---

## 4. `.env`를 운영 모드로

미니PC의 `hanjigap\.env`:

```ini
HANJIGAP_ENV=prod
HANJIGAP_CF_TEAM_DOMAIN=myteam
HANJIGAP_CF_ACCESS_AUD=복사한AUD값
HANJIGAP_PUBLIC_URL=https://money.example.com
HANJIGAP_PORT=8000
HANJIGAP_BACKUP_COPY_DIR=D:/hanjigap-backup      # C:만 있으면 C:/hanjigap-backup
```

- `HANJIGAP_DEV_MEMBER` 줄은 **지웁니다**. (남아 있으면 운영 모드 서버가 켜지지 않음)
- 두 번째 사본 폴더는 OneDrive·바탕 화면·문서 아래를 피합니다. (클라우드 동기화)

확인:

```powershell
uv run hanjigap check
```

`[확인] Cloudflare Access 공개키 N개를 받았습니다` 와 `설정 확인 완료` 가 나오면 됩니다.
`[주의]`·`[실패]` 줄이 있으면 그 줄대로 고칩니다.

---

## 5. 상주 실행

1. 프로젝트 폴더의 `start.vbs` 더블클릭 → 알림 영역에 한지갑 아이콘, "한지갑이 켜졌어요" 알림
2. 부팅 때 자동 실행: `Win+R` → `shell:startup` → 열린 폴더에 `start.vbs`의 **바로가기**를 넣기
3. 다른 봇들과 같은 조건:
   - 윈도우 **자동 로그인**이 되어 있어야 재부팅(윈도우 업데이트) 뒤에도 켜집니다.
   - 전원 옵션에서 **절전 안 함**. (화면 끄기는 괜찮음)

---

## 6. Tunnel 연결 (바깥 주소 열기)

### 6-1. cloudflared 설치

관리자 PowerShell:

```powershell
winget install --id Cloudflare.cloudflared
```

(winget이 없으면 Cloudflare 문서의 Windows용 설치 파일(.msi)을 받아 설치)

### 6-2. 터널 만들기

Zero Trust → **Networks** → **Connectors** → **Cloudflare Tunnels** → Create a tunnel

1. 종류 **Cloudflared** → 이름 `minipc`
2. 환경 **Windows** 를 고르면 설치 명령이 나옵니다. 그중 `cloudflared.exe service install 긴토큰` 을 **관리자 PowerShell**에서 실행
   - 윈도우 **서비스**로 설치되어, 윈도우 로그인 전에도 켜지고 재부팅 뒤에도 자동으로 켜집니다.
   - 토큰은 비밀번호와 같습니다. 저장소·메신저에 붙여 넣지 않기
3. 화면 아래 Connectors에 미니PC가 **Connected** 로 보이면 Next

### 6-3. 주소 연결 (Published application route)

| 항목         | 값                                                                                                   |
| ------------ | ---------------------------------------------------------------------------------------------------- |
| Subdomain    | `money` (3-3과 같게)                                                                                 |
| Domain       | `example.com`                                                                                        |
| Path         | 비움                                                                                                 |
| Service Type | **HTTP**                                                                                             |
| URL          | **`127.0.0.1:8000`** (`localhost` 말고 숫자로. 윈도우는 localhost를 IPv6로 먼저 찾아 실패할 수 있음) |

저장하면 바로 `https://money.example.com` 이 열립니다.

---

## 7. 접속 확인

1. 휴대폰(와이파이 끄고 LTE로) → `https://money.example.com`
2. Cloudflare 로그인 화면 → 이메일 입력 → 메일로 온 6자리 코드 입력 → 한지갑 홈
3. 재희 메일로도 한 번
4. 등록하지 않은 메일로 시도 → 코드 메일이 오지 않아야 정상
5. 트레이 '한지갑 열기'가 같은 주소로 열리는지

## 8. 휴대폰 홈 화면에 추가

- **아이폰 (Safari)**: 한지갑을 연 상태에서 공유 버튼 → **홈 화면에 추가**
- **안드로이드 (Chrome)**: 오른쪽 위 ⋮ → **홈 화면에 추가** (또는 **앱 설치**)

홈 화면 아이콘으로 처음 열 때 로그인을 한 번 더 할 수 있습니다. (브라우저와 로그인 보관이 따로인 경우)

## 9. Cloudflare에서 꺼 둘 것

이 도메인의 대시보드(Zero Trust 말고 도메인 쪽)에서:

- **Rocket Loader**: 끔 (Speed → Optimization). 켜면 스크립트를 바꿔 끼워 화면이 깨집니다.
- **Web Analytics 자동 삽입**: 끔. 한지갑의 보안 정책(CSP)이 막아 오류만 남습니다.
- 캐시 규칙(Cache Rules)을 이 주소에 따로 만들지 않기. (한지갑이 알맞은 캐시 지시를 이미 보냄)

---

## 평소 운영

| 할 일         | 방법                                                                                                      |
| ------------- | --------------------------------------------------------------------------------------------------------- |
| 업데이트      | 트레이 **끄기** → `git pull` → `uv sync` → `start.vbs` (코드만 바뀌면 `git pull` 뒤 트레이 **다시 시작**) |
| 백업 확인     | 트레이 상태 줄 '마지막 백업', 또는 `uv run hanjigap db backups`                                           |
| 한 달에 한 번 | 정산 끝난 뒤 `db backups` 맨 위 파일 하나를 USB에 복사                                                    |
| 되돌리기      | 트레이 **끄기** → `uv run hanjigap db restore 이름` → `start.vbs`                                         |
| 로그          | 트레이 **로그 보기** (건수·결과만 남고 내용·금액·이름은 없음)                                             |

## 문제가 생기면

| 보이는 것                                 | 원인 · 할 일                                                          |
| ----------------------------------------- | --------------------------------------------------------------------- |
| 브라우저에 Cloudflare **502 Bad Gateway** | 한지갑 서버가 꺼짐 → 미니PC 트레이 아이콘 확인, `start.vbs`           |
| Cloudflare **1033** 등 터널 오류          | cloudflared 서비스가 꺼짐 → 서비스(services.msc)에서 Cloudflared 시작 |
| "로그인 정보가 올바르지 않아요"           | `.env`의 팀 이름·AUD 값 확인 → 트레이 다시 시작                       |
| "한지갑에 등록되지 않은 이메일이에요"     | Access 정책 메일과 `member` 이메일이 다름 → `member update --email`   |
| "로그인이 만료됐어요"                     | 1주가 지남 → 새로고침해서 코드로 다시 로그인                          |
| 코드 메일이 안 옴                         | 스팸함 확인, 정책 3-2의 메일 철자 확인                                |
| 트레이 아이콘이 흑백                      | 마우스를 올리거나 메뉴 상태 줄에서 이유 확인 → 로그 보기              |

## 보안 점검

- [ ] `.env`, `data/`, 뱅샐 파일은 저장소에 올라가지 않음 (`.gitignore`)
- [ ] `.env`에 `HANJIGAP_DEV_MEMBER` 없음, `HANJIGAP_ENV=prod`
- [ ] Cloudflare 계정 2단계 인증
- [ ] Access 정책은 두 사람 메일만, Action Allow 하나
- [ ] 터널 토큰이 새어 나갔다면: 터널 화면에서 토큰 새로 발급(Refresh) 후 `cloudflared.exe service uninstall` → 새 토큰으로 `service install`
