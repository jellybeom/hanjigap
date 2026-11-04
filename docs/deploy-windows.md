# 미니PC 설치 안내 (H3 · Workers 방식)

집 윈도우 미니PC에서 한지갑을 켜 두고, 휴대폰·다른 PC에서 `https://hanjigap.<하위이름>.workers.dev` 로
접속하게 만드는 순서입니다. 도메인을 사지 않고, 돈이 들지 않습니다.
(Cloudflare 화면의 메뉴 이름은 2026년 11월 기준이며, 가끔 바뀝니다)

```
휴대폰·PC 브라우저
   │  https://hanjigap.<하위이름>.workers.dev
   ▼
Cloudflare ─ ① Access     : 이메일 코드 로그인 (두 사람만, 1주 유지) → 통행증(토큰)
           ─ ② Worker     : 통행증이 있는 요청만 집으로 넘기는 중계 (저장소의 worker/)
           ─ ③ VPC 서비스 : "minipc 터널 → 그 PC의 127.0.0.1:8000"
   │  ④ Tunnel (미니PC가 먼저 연 전용 통로, 바깥에서 들어오는 문 없음)
   ▼
미니PC ─ cloudflared (윈도우 서비스) ─ 한지갑 서버 127.0.0.1:8000 (통행증을 한 번 더 검증)
```

**세 가지 원칙**

1. 미니PC는 공유기 포트를 열지 않습니다. 터널은 미니PC에서 **나가는** 연결입니다.
2. 로그인 확인은 세 번: Access → Worker(`ctx.access`) → 한지갑 서버(토큰 서명·AUD·만료·등록 이메일).
3. 터널은 **한 대에서만** 켭니다. 두 대에서 같은 터널을 켜면 요청이 번갈아 나뉩니다.

| 용어 | 한 줄 설명 |
|---|---|
| Access | Cloudflare의 로그인 문. 정책 `부부`에 있는 메일만 통과 |
| Worker | Cloudflare에서 도는 작은 중계 프로그램 (`worker/src/index.js`) |
| wrangler | Worker를 올리는 명령어 도구 (`worker` 폴더에서 `npx wrangler ...`) |
| Tunnel · cloudflared | 집 PC가 Cloudflare로 열어 두는 통로 · 그 통로를 붙잡는 프로그램 |
| VPC 서비스 | Worker가 집에 연락할 때 쓰는 주소 카드 (`env.HANJIGAP`) |
| AUD · 팀 이름 | 한지갑 서버가 통행증을 검증할 때 쓰는 값 (`.env`) |

---

## 1. Cloudflare 준비 (한 번만)

1. **계정**: dash.cloudflare.com 가입 → 메일로 이메일 인증 → 프로필의 보안에서 **2단계 인증** 켜기
2. **Zero Trust**: 왼쪽 메뉴 **Zero Trust** → 팀 이름 정하기 → **Free** 플랜 (결제 수단 입력은 필요, 청구 없음)
   - 팀 이름은 `팀이름.cloudflareaccess.com` 이 됩니다. Zero Trust → **Settings** 에서 다시 볼 수 있습니다.
3. **로그인 방법**: Zero Trust → **Integrations** → **Identity providers** → Add new identity provider → **One-time PIN**
   - 코드는 10분 동안 한 번만 쓸 수 있습니다. 정책에 없는 메일로는 코드를 보내지 않습니다.
4. **정책 `부부`**: Zero Trust → **Access controls** → **Policies** → Add a policy

   | 항목 | 값 |
   |---|---|
   | Policy name | `부부` |
   | Action | **Allow** |
   | Session duration | **1 week** |
   | Include | **Emails** → 상범 메일, 재희 메일 (2개) |

5. **workers.dev 하위 이름**: **Workers & Pages** → Account details → **Subdomain** → Change
   - 처음에는 계정 메일 아이디로 자동으로 정해져 있습니다. 실명·아이디와 무관한 이름으로 바꾸길 권합니다.
   - 바꾸면 주소가 바로(몇 분 안에) 바뀌고, 예전 주소는 열리지 않습니다. 링크·홈 화면 아이콘을 만들기 전에 정하기

---

## 2. Worker 올리기 (개발 PC에서, 한 번만)

> W1·W2에서 이미 했다면 건너뜁니다. 터널 `minipc`, VPC 서비스, Worker `hanjigap` 이 대시보드에 있으면 끝난 것입니다.

```powershell
winget install OpenJS.NodeJS.LTS            # PowerShell 새로 열기
cd C:\...\hanjigap\worker
npm ci                                      # 버전 고정된 wrangler 설치
npm test                                    # 중계 시험 6개 통과 확인
npx wrangler login                          # 브라우저로 Cloudflare 로그인 (처음 한 번)
```

1. **터널 만들기**: 대시보드 **Workers VPC** → **Tunnels** → **Create** → 이름 `minipc` → **Save tunnel**
   (같은 곳: **Networking** → **Tunnels**)
   - 운영체제 Windows · 64-bit 를 고르면 `cloudflared.exe service install 긴토큰` 명령이 보입니다.
     **여기서는 실행하지 않습니다.** (7단계에서 미니PC에서만)
2. **VPC 서비스 만들기**: Workers VPC → **VPC Services** → **Create**
   - Type **HTTP** · Tunnel **minipc** · Host **127.0.0.1** · HTTP port **8000** → 저장 → **Service ID** 복사
   - 또는 명령어: `npx wrangler vpc service create hanjigap --type http --tunnel-id <터널ID> --ipv4 127.0.0.1 --http-port 8000`
3. `worker\wrangler.jsonc` 의 `service_id` 에 그 ID를 넣습니다. (비밀 값 아님, 커밋해도 됨)
4. 올리기: `npx wrangler deploy` → 출력의 `https://hanjigap.<하위이름>.workers.dev` 가 한지갑 주소입니다.
   - 아직 Access를 붙이기 전이라 열면 "Access 로그인을 거치지 않은 요청이에요"가 나오고, 집에는 아무것도 보내지 않습니다.

---

## 3. Access 붙이기 (로그인 문)

1. **Workers & Pages** → `hanjigap` → **Access** 탭 → **Protect this Worker behind Access**
   - 범위: **All traffic**
   - 정책: `부부` (목록에 없으면 **Cloudflare account**)
   - ⚠️ **Email domain** 은 고르지 않습니다. `gmail.com`·`naver.com` 을 넣으면 그 메일을 가진 누구나 들어옵니다.
2. Zero Trust → **Access controls** → **Applications** → 방금 생긴 `hanjigap` Worker 앱 열기
   - Policies: **`부부` 하나만** · Login methods: **One-time PIN** · Session Duration: **1 week** → 저장
   - 같은 화면의 **Application Audience (AUD) Tag** 를 복사해 둡니다. (4단계 `.env`에 사용)

---

## 4. 미니PC에 한지갑 설치

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

**B. 개발 PC에서 쓰던 DB 옮기기** (계좌·카드·차·집·규칙 등록을 그대로)

```powershell
# 개발 PC (프로젝트 루트에서)
uv run hanjigap db backup               # 출력된 백업 파일을 USB로 미니PC에 복사

# 미니PC (프로젝트 루트에서)
uv run hanjigap db restore D:\hanjigap-20261102-150000-manual.db --yes
uv run hanjigap db init                 # 스키마 최신화 (이미 최신이면 '적용 없음')
uv run hanjigap member update sangbeom --email 상범로그인메일
uv run hanjigap member update jaehee --email 재희로그인메일
```

- 로그인 이메일은 **정책 `부부`에 넣은 메일과 똑같아야** 합니다. (대소문자 무관)
- 이메일은 웹에서 바꾸지 않습니다. 바꿀 때도 미니PC에서 `member update`로.
- `uv run hanjigap ...` 은 언제나 **프로젝트 루트**에서 실행합니다. (`.env`·`data`를 실행한 폴더 기준으로 찾음)

### `.env`를 운영 모드로

```ini
HANJIGAP_ENV=prod
HANJIGAP_CF_TEAM_DOMAIN=팀이름                                # 예: icy-brook-0d74
HANJIGAP_CF_ACCESS_AUD=3단계에서-복사한-AUD
HANJIGAP_PUBLIC_URL=https://hanjigap.하위이름.workers.dev
HANJIGAP_PORT=8000
HANJIGAP_BACKUP_COPY_DIR=D:/hanjigap-backup                  # C:만 있으면 C:/hanjigap-backup
```

- `HANJIGAP_DEV_MEMBER` 줄은 **지웁니다**. (남아 있으면 운영 모드 서버가 켜지지 않음)
- 두 번째 사본 폴더는 OneDrive·바탕 화면·문서 아래를 피합니다. (클라우드 동기화)

확인: `uv run hanjigap check` → `[확인] Cloudflare Access 공개키 N개를 받았습니다` 와 `설정 확인 완료`.
`[주의]`·`[실패]` 줄이 있으면 그 줄대로 고칩니다.

---

## 5. 상주 실행

1. 프로젝트 폴더의 `start.vbs` 더블클릭 → 알림 영역에 한지갑 아이콘, "한지갑이 켜졌어요" 알림
2. 부팅 때 자동 실행: `Win+R` → `shell:startup` → 열린 폴더에 `start.vbs`의 **바로가기**를 넣기
3. 다른 봇들과 같은 조건:
   - 윈도우 **자동 로그인**이 되어 있어야 재부팅(윈도우 업데이트) 뒤에도 켜집니다.
   - 전원 옵션에서 **절전 안 함**. (화면 끄기는 괜찮음)

---

## 6. 미니PC에 터널 연결

**먼저 확인:** 개발 PC(노트북)에서 시험하던 `cloudflared tunnel run` 창이 **꺼져 있어야** 합니다.

1. cloudflared 설치 (관리자 PowerShell):

   ```powershell
   winget install --id Cloudflare.cloudflared
   cloudflared --version                   # 2025.7.0 이상
   ```

2. 대시보드 Workers VPC → **Tunnels** → `minipc` → 설치 안내(Windows · 64-bit)의 명령을 **관리자 PowerShell**에서 실행:

   ```powershell
   cloudflared.exe service install 긴토큰
   ```

   - 윈도우 **서비스**로 설치되어 윈도우 로그인 전에도, 재부팅 뒤에도 자동으로 켜집니다.
   - 토큰은 비밀번호와 같습니다. 저장소·메신저·캡처에 담지 않기
3. 대시보드에서 `minipc` 상태가 **Healthy** 이고, 연결(Connector)이 **미니PC 하나만** 보이는지 확인합니다.
4. 터널은 UDP 7844 포트로 나가는 연결(QUIC)을 씁니다. 집 공유기는 보통 그대로 됩니다.

---

## 7. 접속 확인

1. 휴대폰(와이파이 끄고 LTE로) → `https://hanjigap.하위이름.workers.dev`
2. Cloudflare 로그인 화면 → 이메일 입력 → 메일로 온 코드 입력 → 한지갑 홈
3. 재희 메일로도 한 번. 등록하지 않은 메일로는 코드 메일이 오지 않아야 정상
4. 미니PC 트레이 **한지갑 열기**가 같은 주소로 열리는지
5. 미니PC 트레이 **끄기** → 휴대폰에서 새로고침 → "미니PC의 한지갑에 연결하지 못했어요" 화면 → `start.vbs`로 다시 켜기

## 8. 휴대폰 홈 화면에 추가

- **아이폰 (Safari)**: 한지갑을 연 상태에서 공유 버튼 → **홈 화면에 추가**
- **안드로이드 (Chrome)**: 오른쪽 위 ⋮ → **홈 화면에 추가** (또는 **앱 설치**)

홈 화면 아이콘으로 처음 열 때 로그인을 한 번 더 할 수 있습니다. (브라우저와 로그인 보관이 따로인 경우)

**삼성 인터넷**은 자체 다크 모드가 켜져 있으면 한지갑의 색을 덮어씁니다. 삼성 인터넷 설정에서 **밝게 보기**를 고르거나
실험실(Labs)의 **웹사이트 다크 테마 사용**을 켭니다. 이때 한지갑의 '시스템' 모드는 휴대폰이 아니라 삼성 인터넷 설정을 따릅니다.
(Chrome은 따로 설정하지 않아도 됩니다)

---

## 평소 운영

| 할 일 | 방법 |
|---|---|
| 한지갑 업데이트 (미니PC) | 트레이 **끄기** → `git pull` → `uv sync` → `start.vbs` (코드만 바뀌면 `git pull` 뒤 트레이 **다시 시작**) |
| Worker 업데이트 (개발 PC) | `worker` 폴더에서 `npm ci` → `npm test` → `npx wrangler deploy` (미니PC는 그대로) |
| 백업 확인 | 트레이 상태 줄 '마지막 백업', 또는 `uv run hanjigap db backups` |
| 한 달에 한 번 | 정산 끝난 뒤 `db backups` 맨 위 파일 하나를 USB에 복사 |
| 되돌리기 | 트레이 **끄기** → `uv run hanjigap db restore 이름` → `start.vbs` |
| 로그 | 트레이 **로그 보기** (건수·결과만 남고 내용·금액·이름은 없음) |
| 로그아웃 | 한지갑 설정 → 앱 정보 → **로그아웃** (다른 사람 PC에서 열었을 때). 그 사람 메일의 **모든 기기·브라우저**가 함께 로그아웃되고, 다시 들어올 때 메일 코드 필요 (직후 1분쯤은 로그인 오류가 날 수 있음). 휴대폰을 잃어버렸을 때도 다른 기기에서 누르면 됨 |
| 개발 PC로 바깥 접속 시험 (드물게) | 미니PC 서비스(services.msc)에서 **Cloudflared 중지** → 개발 PC에서 `cloudflared tunnel run --token 토큰` → 시험 → Ctrl+C → 미니PC **Cloudflared 시작** (터널을 두 대에서 동시에 켜지 않기) |

## 문제가 생기면

| 보이는 것 | 원인 · 할 일 |
|---|---|
| "미니PC의 한지갑에 연결하지 못했어요" | 한지갑 서버나 터널이 꺼짐 → 미니PC 트레이 아이콘 확인, 서비스(services.msc)에서 **Cloudflared** 실행 중인지, 대시보드 `minipc` 가 Healthy 인지 |
| "Access 로그인을 거치지 않은 요청이에요" | Worker의 Access가 꺼짐 → 3단계 다시 |
| "로그인 정보가 올바르지 않아요" | `.env`의 팀 이름·AUD 확인 → 트레이 다시 시작 |
| "한지갑에 등록되지 않은 이메일이에요" | 정책 메일과 `member` 이메일이 다름 → `member update --email` |
| "로그인이 만료됐어요" | 1주가 지남 → 새로고침해서 코드로 다시 로그인 |
| 코드 메일이 안 옴 | 스팸함 확인, 정책 `부부`의 메일 철자 확인 |
| 가끔 다른 데이터가 보이거나 들쭉날쭉 | 터널이 두 대에서 켜져 있음 → 대시보드 `minipc` 연결 목록 확인, 개발 PC의 cloudflared 끄기 |
| 트레이 아이콘이 흑백 | 메뉴 상태 줄에서 이유 확인 → 로그 보기 |

## 보안 점검

- [ ] `.env`, `data/`, 뱅샐 파일은 저장소에 올라가지 않음 (`.gitignore`)
- [ ] `.env`에 `HANJIGAP_DEV_MEMBER` 없음, `HANJIGAP_ENV=prod`
- [ ] Cloudflare 계정 2단계 인증
- [ ] Worker Access 범위 **All traffic**, 정책은 `부부` 하나 (Email domain 아님)
- [ ] `wrangler.jsonc`: `preview_urls` false · `observability` 꺼짐 (저장소 기본값)
- [ ] 터널 토큰이 새어 나갔다면: 터널 화면에서 토큰 새로 발급(Refresh) → 미니PC에서 `cloudflared.exe service uninstall` → 새 토큰으로 `service install`
- [ ] 개발 PC를 잃어버리거나 넘길 때: `worker` 폴더에서 `npx wrangler logout` (또는 대시보드에서 로그인 토큰 삭제)
- [ ] 개발 PC에는 cloudflared를 **서비스로 설치하지 않음**

---

## 부록: 나중에 도메인으로 옮기기

Workers VPC는 베타입니다. 사용법이 바뀌거나 유료가 되면 도메인 방식으로 옮깁니다. 한지갑 코드는 그대로입니다.

1. Cloudflare에서 도메인 구입 (Domain Registration → Register domains)
2. **같은 터널** `minipc` 에 공개 주소 추가: Networking → Tunnels → `minipc` → 공개 주소(Published application route)
   - Subdomain `money` · Domain 산 도메인 · Service **HTTP** · URL **`127.0.0.1:8000`** (`localhost` 말고 숫자)
   - 이 단계는 3번을 먼저 하고 나서 (로그인 문 없이 주소가 열리는 순간이 없게)
3. Zero Trust → Access controls → Applications → **Self-hosted and private** → 그 주소 · 정책 `부부` · One-time PIN · 1 week
4. 미니PC `.env`: `HANJIGAP_CF_ACCESS_AUD` 를 **새 앱의 AUD** 로, `HANJIGAP_PUBLIC_URL` 을 새 주소로 → 트레이 다시 시작
5. 도메인 쪽 대시보드에서 **Rocket Loader**·**Web Analytics 자동 삽입** 끄기 (화면이 깨지거나 보안 정책 오류)
6. 휴대폰 홈 화면 아이콘을 새 주소로 다시 추가. Worker `hanjigap` 은 Workers & Pages에서 삭제