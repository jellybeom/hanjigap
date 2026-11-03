# 한지갑 Worker

바깥(휴대폰·다른 PC)에서 `https://hanjigap.<하위이름>.workers.dev` 로 한지갑에 들어오게 하는 Cloudflare Worker입니다.

```
브라우저 → workers.dev (Cloudflare Access: 이메일 코드 로그인) → 이 Worker → Workers VPC → Tunnel → 미니PC 127.0.0.1:8000
```

- `src/index.js`: 중계. Access를 거친 요청만 받아 경로·헤더(로그인 토큰)·본문을 그대로 한지갑 서버로 넘깁니다.
  미니PC에 닿지 못하면 안내 화면(API는 한글 오류)을 돌려줍니다.
- `test/proxy.test.js`: 중계 시험 (`npm test`)
- `wrangler.jsonc`: Worker 설정. `service_id`는 VPC 서비스(minipc 터널 → 127.0.0.1:8000)의 ID입니다.

```powershell
cd worker
npm ci                 # wrangler 설치 (버전 고정, package-lock.json)
npm test               # 중계 시험
npx wrangler login     # 처음 한 번
npx wrangler deploy    # 올리기
```

로그인 확인은 Cloudflare Access(1차) · 이 Worker의 `ctx.access`(2차) · 한지갑 서버의 토큰 검증(3차)으로 세 번 합니다.
