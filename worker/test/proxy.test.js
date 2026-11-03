// 중계 Worker 시험: node --test (worker 폴더에서 'npm test')
import assert from "node:assert/strict";
import { test } from "node:test";

import worker, { NO_ACCESS, UNREACHABLE, forwardHeaders } from "../src/index.js";

const ACCESS = { access: { aud: "aud", getIdentity: async () => ({ email: "x@example.com" }) } };
const ORIGIN = "https://hanjigap.sub.workers.dev";

function fakeEnv(reply) {
  const calls = [];
  return {
    calls,
    HANJIGAP: {
      fetch: async (url, init) => {
        const body = init.body ? await new Response(init.body).text() : undefined;
        calls.push({ url, init, body });
        return reply(url, init);
      },
    },
  };
}

test("Access를 거치지 않은 요청은 어디에도 연결하지 않는다", async () => {
  const env = fakeEnv(() => new Response("x"));
  for (const ctx of [undefined, {}]) {
    const res = await worker.fetch(new Request(`${ORIGIN}/api/me`), env, ctx);
    assert.equal(res.status, 403);
    assert.equal(await res.text(), NO_ACCESS);
  }
  assert.equal(env.calls.length, 0);
});

test("경로·쿼리·로그인 토큰을 넘기고 Host는 workers.dev 주소로", async () => {
  const env = fakeEnv(() => new Response('{"ok":1}', { status: 201, headers: { "x-up": "1", "content-security-policy": "default-src 'self'" } }));
  const req = new Request(`${ORIGIN}/api/ledger?month=2026-09&q=%EC%8B%9D%EB%B9%84`, {
    headers: {
      cookie: "CF_Authorization=tok",
      "cf-access-jwt-assertion": "tok",
      "cf-connecting-ip": "1.2.3.4",
      "cf-ray": "abc",
      accept: "application/json",
    },
  });
  const res = await worker.fetch(req, env, ACCESS);
  const [call] = env.calls;
  assert.equal(call.url, "http://hanjigap.sub.workers.dev/api/ledger?month=2026-09&q=%EC%8B%9D%EB%B9%84");
  assert.equal(call.init.method, "GET");
  assert.equal(call.init.redirect, "manual");
  assert.equal(call.body, undefined);
  const h = call.init.headers;
  assert.equal(h.get("cookie"), "CF_Authorization=tok");
  assert.equal(h.get("cf-access-jwt-assertion"), "tok");
  assert.equal(h.get("accept"), "application/json");
  assert.equal(h.get("cf-connecting-ip"), null);
  assert.equal(h.get("cf-ray"), null);
  // 서버의 답은 그대로 (상태·헤더·본문)
  assert.equal(res.status, 201);
  assert.equal(res.headers.get("x-up"), "1");
  assert.equal(res.headers.get("content-security-policy"), "default-src 'self'");
  assert.equal(await res.text(), '{"ok":1}');
});

test("쓰기 요청은 본문을 그대로 넘긴다", async () => {
  const env = fakeEnv(() => new Response(null, { status: 204 }));
  const body = JSON.stringify({ name: "재희" });
  const req = new Request(`${ORIGIN}/api/members/jaehee`, {
    method: "PATCH",
    headers: { "content-type": "application/json", origin: ORIGIN },
    body,
  });
  const res = await worker.fetch(req, env, ACCESS);
  assert.equal(res.status, 204);
  const [call] = env.calls;
  assert.equal(call.init.method, "PATCH");
  assert.equal(call.body, body);
  assert.equal(call.init.headers.get("origin"), ORIGIN);
});

test("서버의 리디렉트·오류도 그대로 돌려준다", async () => {
  const env = fakeEnv(() => new Response('{"error":"없음"}', { status: 404 }));
  const res = await worker.fetch(new Request(`${ORIGIN}/api/x`), env, ACCESS);
  assert.equal(res.status, 404);
  assert.equal((await res.json()).error, "없음");
});

test("미니PC에 닿지 못하면: API는 한글 JSON, 화면은 안내 페이지", async () => {
  const env = fakeEnv(() => {
    throw new Error("tunnel down");
  });
  const api = await worker.fetch(new Request(`${ORIGIN}/api/me`), env, ACCESS);
  assert.equal(api.status, 502);
  assert.equal(api.headers.get("cache-control"), "no-store");
  assert.deepEqual(await api.json(), { error: UNREACHABLE });

  const page = await worker.fetch(new Request(`${ORIGIN}/`), env, ACCESS);
  assert.equal(page.status, 502);
  assert.match(page.headers.get("content-type"), /text\/html/);
  assert.match(await page.text(), /미니PC의 한지갑에 연결하지 못했어요/);
});

test("forwardHeaders: host와 cf-* 는 빼고 로그인 토큰은 남긴다", () => {
  const out = forwardHeaders(
    new Headers({ "cf-ipcountry": "KR", "cf-access-jwt-assertion": "t", "user-agent": "ua", "x-forwarded-for": "1.1.1.1" }),
  );
  assert.deepEqual([...out.keys()].sort(), ["cf-access-jwt-assertion", "user-agent", "x-forwarded-for"]);
});