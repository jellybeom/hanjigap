// 한지갑 중계 Worker (W2).
// 브라우저 → workers.dev (Cloudflare Access 로그인) → 이 Worker → Workers VPC → Tunnel → 미니PC 127.0.0.1:8000
//
// 하는 일
//   Access를 거친 요청만 받아서 경로·쿼리·헤더(로그인 토큰 포함)·본문을 그대로 한지갑 서버로 넘기고,
//   서버의 답을 그대로 돌려준다. 로그인 확인은 한지갑 서버가 토큰으로 한 번 더 한다.
// 지키는 것
//   ctx.access가 없으면(Access 설정이 빠진 경우) 아무 데도 연결하지 않는다.
//   Host는 workers.dev 주소로 맞춘다. (서버의 '다른 사이트 요청 거부' 검사가 이 값을 본다)
//   Cloudflare가 붙이는 cf-* 헤더는 넘기지 않는다. (로그인 토큰 cf-access-jwt-assertion만 예외)
//   서버·터널에 닿지 못하면 안내 화면(화면 요청) 또는 한글 오류(JSON, API 요청)를 돌려준다.
// 기록
//   이 Worker는 아무것도 기록하지 않는다. (wrangler.jsonc에서 Cloudflare 요청 기록도 끔)

const KEEP_CF_HEADERS = new Set(["cf-access-jwt-assertion"]);
const NO_BODY = new Set(["GET", "HEAD"]);

export const UNREACHABLE =
  "미니PC의 한지갑에 연결하지 못했어요. 미니PC가 켜져 있는지, 한지갑 트레이 아이콘이 켜짐인지 확인해 주세요";
export const NO_ACCESS = "Access 로그인을 거치지 않은 요청이에요. Worker의 Access 설정을 확인하세요.";

export default {
  async fetch(request, env, ctx) {
    if (!ctx || !ctx.access) {
      return new Response(NO_ACCESS, {
        status: 403,
        headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
      });
    }
    const url = new URL(request.url);
    const init = {
      method: request.method,
      headers: forwardHeaders(request.headers),
      redirect: "manual", // 서버의 답(리디렉트 포함)을 그대로 브라우저에 돌려준다
    };
    if (!NO_BODY.has(request.method)) {
      init.body = request.body;
      init.duplex = "half"; // 본문을 흘려보내기 (큰 업로드도 메모리에 다 올리지 않음)
    }
    try {
      // 주소의 호스트 = 서버가 보는 Host. 실제 연결 대상은 VPC 서비스(minipc 터널 → 127.0.0.1:8000)
      return await env.HANJIGAP.fetch(`http://${url.host}${url.pathname}${url.search}`, init);
    } catch {
      return unreachable(url);
    }
  },
};

export function forwardHeaders(incoming) {
  const out = new Headers();
  for (const [name, value] of incoming) {
    const key = name.toLowerCase();
    if (key === "host") continue;
    if (key.startsWith("cf-") && !KEEP_CF_HEADERS.has(key)) continue;
    out.append(name, value);
  }
  return out;
}

function unreachable(url) {
  const headers = { "cache-control": "no-store" };
  if (url.pathname.startsWith("/api/")) {
    return new Response(JSON.stringify({ error: UNREACHABLE }), {
      status: 502,
      headers: { ...headers, "content-type": "application/json; charset=utf-8" },
    });
  }
  return new Response(PAGE, {
    status: 502,
    headers: { ...headers, "content-type": "text/html; charset=utf-8" },
  });
}

const PAGE = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>한지갑 · 연결 안 됨</title>
<style>
  :root { color-scheme: light dark; --bg: #f2f3ee; --card: #ffffff; --ink: #1b1f1c; --muted: #5d665f; --line: #dfe3dc; --brand: #143d30; --on-brand: #ffffff; }
  @media (prefers-color-scheme: dark) { :root { --bg: #111412; --card: #1a1f1c; --ink: #e8ece8; --muted: #9aa49d; --line: #2a312d; --brand: #7fd1ae; --on-brand: #0e1a14; } }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--ink);
         font-family: system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; }
  main { margin: 16px; max-width: 420px; padding: 28px 24px; background: var(--card); border: 1px solid var(--line); border-radius: 20px; }
  h1 { font-size: 20px; margin: 0 0 12px; }
  p { color: var(--muted); line-height: 1.6; margin: 0 0 8px; font-size: 15px; }
  a { display: block; margin-top: 20px; padding: 12px; text-align: center; border-radius: 12px; background: var(--brand); color: var(--on-brand); text-decoration: none; font-weight: 600; }
</style>
</head>
<body>
<main>
  <h1>미니PC의 한지갑에 연결하지 못했어요</h1>
  <p>로그인은 됐지만, 집 미니PC까지 닿지 못했어요.</p>
  <p>미니PC가 켜져 있는지, 알림 영역의 한지갑 아이콘이 '켜짐'인지 확인해 주세요. 재부팅 직후라면 1~2분 뒤에 다시 열어 보세요.</p>
  <a href="">다시 시도</a>
</main>
</body>
</html>
`;