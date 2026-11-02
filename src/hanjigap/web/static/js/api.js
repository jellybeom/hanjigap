// 서버 API 부르기. 오류는 서버가 준 한글 메시지({"error": ...})를 그대로 쓴다.
// 운영에서 Cloudflare Access 로그인이 만료되면 API 요청이 로그인 화면으로 넘겨진다(리디렉트).
// 그대로 따라가면 '연결 실패'처럼 보이므로 따라가지 않고 '로그인 만료'로 알려 준다.
export const LOGIN_EXPIRED = "로그인이 만료됐어요. 새로고침해서 다시 로그인하세요";

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body || {};
  }
}

function url(path, params) {
  const u = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== null && v !== undefined && v !== "") u.searchParams.set(k, v);
  }
  return u.pathname + u.search;
}

async function request(method, path, { params, json, form } = {}) {
  const options = {
    method,
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    redirect: "manual", // 한지갑 API는 리디렉트하지 않는다. 리디렉트 = Access 로그인 만료
  };
  if (json !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(json);
  } else if (form !== undefined) {
    options.body = form;
  }
  let res;
  try {
    res = await fetch(url(path, params), options);
  } catch {
    throw new ApiError(0, "서버에 연결하지 못했어요. 인터넷 연결을 확인하세요");
  }
  if (res.type === "opaqueredirect") throw new ApiError(401, LOGIN_EXPIRED);
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const message = (body && body.error) || `요청이 실패했어요 (${res.status})`;
    throw new ApiError(res.status, message, body);
  }
  return body;
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, json) => request("POST", path, { json }),
  put: (path, json) => request("PUT", path, { json }),
  patch: (path, json) => request("PATCH", path, { json }),
  del: (path) => request("DELETE", path),
  upload: (path, form) => request("POST", path, { form }),
};