/* API 客户端：同源请求，无需配置 base URL */
import { t } from "./i18n.js";

let lang = "zh";
const pending = new Set();

/* 会话凭据：网页用 HttpOnly Cookie（JS 读不到），原生壳用 Bearer token。
   壳内页面来源是 capacitor:// 之类，跨站 Cookie 不可靠，所以壳里必须走 token。
   检测放这里，避免每个调用点各判断一次。 */
const NATIVE = (() => {
  try {
    if (window.__TETHER_NATIVE__ === true) return true;
    let protocol = (location.protocol || "").toLowerCase();
    if (!protocol) {
      // 少数 WebView 只给 href，从 URL 里补一次协议
      try { protocol = new URL(location.href).protocol.toLowerCase(); } catch { protocol = ""; }
    }
    return protocol === "capacitor:" || protocol === "ionic:" || protocol === "file:";
  } catch {
    return false;
  }
})();

let authToken = "";
const TOKEN_KEY = "tether.token";

export function isNativeShell() {
  return NATIVE;
}

export function setAuthToken(value) {
  authToken = typeof value === "string" ? value : "";
  if (!NATIVE) return;
  try {
    if (authToken) localStorage.setItem(TOKEN_KEY, authToken);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 壳内存储不可用时退化为仅本次会话有效 */
  }
}

export function getAuthToken() {
  return authToken;
}

export function restoreAuthToken() {
  if (!NATIVE) return "";
  try {
    const stored = localStorage.getItem(TOKEN_KEY) || "";
    if (stored) authToken = stored;
    return authToken;
  } catch {
    return "";
  }
}

/* 部署在子路径下（比如 /qienty/）时，接口地址必须跟着页面走。
   直接从当前页面推：根部署得到 ""，子路径部署得到 "/qienty"。
   注意页面地址必须带结尾斜杠（/qienty/），否则这里会推成 "/"。

   原生壳里页面来源是 capacitor://localhost，相对路径会指向壳本地，
   所以壳启动时把真实服务地址注入 window.__TETHER_API_BASE__（例如 https://api.your-domain.tld），
   其余环境一律保持同源相对路径 —— 不影响现在的服务器部署方式。 */
const BASE = (() => {
  try {
    const injected = typeof window !== "undefined" ? window.__TETHER_API_BASE__ : "";
    if (typeof injected === "string" && injected.trim()) {
      const value = injected.trim();
      return value === "/" ? "" : value.replace(/\/+$/, "");
    }
  } catch {
    /* 注入值异常时回落到同源 */
  }
  try {
    const path = new URL(".", location.href).pathname;
    return path === "/" ? "" : path.replace(/\/$/, "");
  } catch {
    return "";
  }
})();

export function setApiLang(value) {
  lang = value === "en" ? "en" : "zh";
}

function qs(params = {}) {
  const search = new URLSearchParams({ lang, tz_offset: tzOffset() });
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  return search.toString();
}

async function call(path, { method = "GET", body, params, signal, timeout = 30000, headers: extraHeaders = null } = {}) {
  const url = `${BASE}${path}?${qs(params)}`;
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  if (signal?.aborted) cancel();
  else signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; cancel(); }, timeout);
  pending.add(controller);
  const init = { method, headers: {}, signal: controller.signal };
  if (authToken) init.headers.Authorization = `Bearer ${authToken}`;
  if (extraHeaders) Object.assign(init.headers, extraHeaders);
  try {
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const res = await fetch(url, init);
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (!res.ok) {
      /* 会话过期或被踢掉：让上层统一回到登录页，而不是每个调用点各弹一次 toast */
      if (res.status === 401 && !path.startsWith("/api/auth/")) {
        window.dispatchEvent(new CustomEvent("tether:unauthorized"));
      }
      const message = (data && (data.error || data.detail)) || `HTTP ${res.status}`;
      const error = new Error(message);
      error.status = res.status;
      throw error;
    }
    if (data === null) throw new Error(`Invalid JSON response (${res.status})`);
    return data;
  } catch (error) {
    if (error.status) throw error;
    if (controller.signal.aborted) {
      const aborted = new Error(t(timedOut ? "request.timeout" : "request.cancelled"));
      aborted.name = timedOut ? "TimeoutError" : "AbortError";
      throw aborted;
    }
    if (error instanceof TypeError) throw new Error(t("request.network"));
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
    pending.delete(controller);
  }
}

export const api = {
  cancelPending: () => { for (const controller of pending) controller.abort(); },
  bootstrap: () => call("/api/bootstrap"),
  home: () => call("/api/home"),
  importEntries: (entries) => call("/api/import", { method: "POST", body: { entries } }),
  meta: () => call("/api/meta"),
  practices: () => call("/api/practices"),
  practiceSessions: (params) => call("/api/practice-sessions", { params }),
  practiceSummary: () => call("/api/practice-summary"),
  practiceSession: (id) => call(`/api/practice-sessions/${id}`),
  startPractice: (body) => call("/api/practice-sessions", { method: "POST", body }),
  updatePractice: (id, body) => call(`/api/practice-sessions/${id}`, { method: "PATCH", body }),
  deletePractice: (id) => call(`/api/practice-sessions/${id}`, { method: "DELETE" }),
  practiceExportUrl: () => `${BASE}/api/practice-export?${qs()}`,

  entries: (params) => call("/api/entries", { params }),
  entry: (id) => call(`/api/entries/${id}`),
  createEntry: (payload) => call("/api/entries", { method: "POST", body: payload }),
  patchEntry: (id, payload) => call(`/api/entries/${id}`, { method: "PATCH", body: payload }),
  deleteEntry: (id) => call(`/api/entries/${id}`, { method: "DELETE" }),
  resetAll: () => call("/api/entries", { method: "DELETE" }),
  intervention: (emotion) => call(`/api/interventions/${emotion}`),
  recordIntervention: (id, payload) =>
    call(`/api/entries/${id}/intervention`, { method: "POST", body: payload }),

  aiSelftest: () => call("/api/ai/selftest", { method: "POST", timeout: 60000 }),
  stats: () => call("/api/stats/basic"),
  /* 就诊摘要：规则计算，不调用模型；窗口 14 / 30 / 90 天 */
  handoff: (days) => call("/api/handoff", { params: { days } }),
  weeklyCard: () => call("/api/weekly-card"),
  insights: () => call("/api/insights"),
  /* 唯一会阻塞几秒的接口：让模型重写这一屏的结论 */
  analyzeInsights: (force = false, signal) =>
    call("/api/insights/analyze", { method: "POST", body: { force }, signal, timeout: 300000 }),

  account: () => call("/api/account"),
  redeem: (code) => call("/api/account/redeem", { method: "POST", body: { code } }),
  setSettings: (payload) => call("/api/account/settings", { method: "PATCH", body: payload }),

  me: () => call("/api/auth/me"),
  /* 壳内要 token：请求头显式声明，服务端才把会话 token 放进响应体。 */
  login: (username, password) =>
    call("/api/auth/login", { method: "POST", body: { username, password }, headers: tokenHandshake() }),
  register: (username, password) =>
    call("/api/auth/register", { method: "POST", body: { username, password }, headers: tokenHandshake() }),
  logout: () => call("/api/auth/logout", { method: "POST" }),
  changePassword: (old_password, new_password) =>
    call("/api/auth/password", { method: "POST", body: { old_password, new_password } }),

  crisis: (region) => call("/api/crisis", { params: { region } }),
  seed: (tzOffset) => call("/api/demo/seed", { method: "POST", params: { tz_offset: tzOffset } }),

  wheel: () => call("/api/wheel"),
  saveWheel: (items) => call("/api/wheel", { method: "PUT", body: { items } }),

  exportUrl: (format) => `${BASE}/api/export?${qs({ format })}`,
};

export function apiBase() {
  return BASE;
}

/* 登录/注册时带上这个头，服务端才会把 token 交给壳。
   网页不发，会话仍然只在 HttpOnly Cookie 里。 */
export function tokenHandshake() {
  return NATIVE ? { "X-Tether-Token-Auth": "bearer" } : null;
}

export function tzOffset() {
  return -new Date().getTimezoneOffset();
}
