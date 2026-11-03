// 한지갑 Worker 진단 (W1). 바깥 접속을 Workers + Access + Workers VPC로 할 수 있는지 네 가지를 확인한다.
//   1) Access 로그인 정보(토큰)가 Worker까지 오는지, 서버가 검증할 수 있는 형태인지
//   2) Workers VPC로 미니PC의 127.0.0.1:8000에 닿는지
//   3) 서버의 '다른 사이트 요청 거부' 검사를 통과하는지 (Host)
//   4) 토큰을 넘겼을 때 서버가 로그인한 구성원을 알아보는지 (서버를 운영 모드로 켰을 때)
// 보여 주는 것: 있다/없다, 상태 코드, 토큰의 발급처·대상·만료. 이메일·토큰 값·가계 데이터는 보여 주지 않는다.
// 서버에는 로그인이 필요 없는 /api/health, 그리고 /api/me(구성원 slug만 봄)만 부른다.

export default {
  async fetch(request, env, ctx) {
    if (!ctx.access) {
      // Access를 거치지 않은 요청. 설정이 빠졌다는 뜻이므로 아무것도 하지 않는다.
      return new Response("Access 로그인을 거치지 않은 요청이에요. Worker의 Access 설정을 확인하세요.", {
        status: 403,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    const url = new URL(request.url);
    const identity = await ctx.access.getIdentity().catch(() => null);
    const cookie = request.headers.get("cookie") || "";
    const headerToken = request.headers.get("cf-access-jwt-assertion");
    const cookieToken = (cookie.match(/(?:^|;\s*)CF_Authorization=([^;]+)/) || [])[1] || null;
    const claims = decodeClaims(headerToken || cookieToken);

    const report = {
      "1_로그인": {
        "ctx.access": true,
        "이메일 확인됨": Boolean(identity && identity.email),
        "ctx.access.aud": ctx.access.aud ?? null,
        "토큰 헤더(cf-access-jwt-assertion)": Boolean(headerToken),
        "토큰 쿠키(CF_Authorization)": Boolean(cookieToken),
        "토큰 발급처(iss)": claims ? claims.iss : null,
        "토큰 대상(aud)": claims ? claims.aud : null,
        "토큰 만료까지(시간)": claims && claims.exp ? Math.round((claims.exp * 1000 - Date.now()) / 36e5) : null,
      },
      "2_미니PC": await check(env, url, "GET", "/api/health"),
      "3_Host검사": await check(env, url, "POST", "/api/health", { origin: url.origin }),
      "4_로그인전달": await check(env, url, "GET", "/api/me", forwardAuth(headerToken, cookieToken)),
      "읽는법": {
        "2_미니PC": "status 200 이면 통과",
        "3_Host검사": "status 405 이면 통과 (403 이면 Host 문제)",
        "4_로그인전달": "서버가 운영 모드일 때 status 200 + member 가 나오면 통과 (개발 모드면 항상 200)",
      },
    };
    return new Response(JSON.stringify(report, null, 2), {
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });
  },
};

// 서버에 요청을 보내고 상태 코드와 (있으면) 짧은 결과만 돌려준다.
async function check(env, url, method, path, headers = {}) {
  try {
    // fetch 주소의 호스트는 서버가 보는 Host 헤더가 된다. 실제 연결 대상은 VPC 서비스 설정(127.0.0.1:8000).
    const res = await env.HANJIGAP.fetch(`http://${url.host}${path}`, {
      method,
      headers: { accept: "application/json", ...headers },
      redirect: "manual",
    });
    let body = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    const out = { status: res.status };
    if (body && body.version) out.version = body.version;
    if (body && body.member) out.member = body.member.slug; // 이름·이메일은 빼고 slug만
    if (body && body.error) out.error = body.error;
    return out;
  } catch (e) {
    return { status: null, error: `연결 실패: ${e && e.message ? e.message : e}` };
  }
}

function forwardAuth(headerToken, cookieToken) {
  const h = {};
  if (headerToken) h["cf-access-jwt-assertion"] = headerToken;
  if (cookieToken) h.cookie = `CF_Authorization=${cookieToken}`;
  return h;
}

// JWT 가운데 부분(내용)만 읽는다. 서명 확인은 서버가 한다. 이메일은 꺼내지 않는다.
function decodeClaims(token) {
  if (!token) return null;
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = JSON.parse(atob(part + "=".repeat((4 - (part.length % 4)) % 4)));
    return { iss: json.iss ?? null, aud: json.aud ?? null, exp: json.exp ?? null };
  } catch {
    return { iss: "읽을 수 없음", aud: null, exp: null };
  }
}