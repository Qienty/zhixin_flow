/* API 客户端：同源请求，无需配置 base URL */
import { t } from "./i18n.js";

let lang = "zh";
const pending = new Set();

/* 部署在子路径下（比如 /qienty/）时，接口地址必须跟着页面走。
   直接从当前页面推：根部署得到 ""，子路径部署得到 "/qienty"。
   注意页面地址必须带结尾斜杠（/qienty/），否则这里会推成 "/"。 */
const BASE = (() => {
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

async function call(path, { method = "GET", body, params, signal, timeout = 30000 } = {}) {
  const url = `${BASE}${path}?${qs(params)}`;
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  if (signal?.aborted) cancel();
  else signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; cancel(); }, timeout);
  pending.add(controller);
  const init = { method, headers: {}, signal: controller.signal };
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

  entries: (params) => call("/api/entries", { params }),
  entry: (id) => call(`/api/entries/${id}`),
  createEntry: (payload) => call("/api/entries", { method: "POST", body: payload }),
  patchEntry: (id, payload) => call(`/api/entries/${id}`, { method: "PATCH", body: payload }),
  deleteEntry: (id) => call(`/api/entries/${id}`, { method: "DELETE" }),
  resetAll: () => call("/api/entries", { method: "DELETE" }),
  intervention: (emotion) => call(`/api/interventions/${emotion}`),
  recordIntervention: (id, payload) =>
    call(`/api/entries/${id}/intervention`, { method: "POST", body: payload }),

  stats: () => call("/api/stats/basic"),
  insights: () => call("/api/insights"),
  /* 唯一会阻塞几秒的接口：让模型重写这一屏的结论 */
  analyzeInsights: (force = false, signal) =>
    call("/api/insights/analyze", { method: "POST", body: { force }, signal, timeout: 300000 }),

  account: () => call("/api/account"),
  redeem: (code) => call("/api/account/redeem", { method: "POST", body: { code } }),
  setSettings: (payload) => call("/api/account/settings", { method: "PATCH", body: payload }),

  me: () => call("/api/auth/me"),
  login: (username, password) =>
    call("/api/auth/login", { method: "POST", body: { username, password } }),
  register: (username, password) =>
    call("/api/auth/register", { method: "POST", body: { username, password } }),
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

export function tzOffset() {
  return -new Date().getTimezoneOffset();
}
