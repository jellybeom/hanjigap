# 한지갑 Worker

바깥(휴대폰·다른 PC)에서 `https://hanjigap.<하위이름>.workers.dev` 로 한지갑에 들어오게 하는 Cloudflare Worker입니다.

```
브라우저 → workers.dev (Cloudflare Access: 이메일 코드 로그인) → 이 Worker → Workers VPC → Tunnel → 미니PC 127.0.0.1:8000
```

- `src/diag.js`: W1 진단용. 로그인 토큰 전달 · 미니PC 연결 · Host 검사 · 로그인 전달을 확인합니다. 데이터는 보여 주지 않습니다.
- `wrangler.jsonc`: Worker 설정. `service_id`는 `wrangler vpc service create`가 준 ID로 바꿉니다.

```powershell
cd worker
npm ci                 # wrangler 설치 (버전 고정, package-lock.json)
npx wrangler login     # 처음 한 번
npx wrangler deploy    # 올리기
```
