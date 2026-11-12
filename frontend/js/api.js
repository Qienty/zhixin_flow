/* API 客户端：同源请求，无需配置 base URL */

let lang = "zh";

export function setApiLang(value) {
  lang = value === "en" ? "en" : "zh";
}

function qs(params = {}) {
  const search = new URLSearchParams({ lang, ...params });
  for (const [k, v] of [...search.entries()]) {
    if (v === undefined || v === null || v === "") search.delete(k);
  }
  return search.toString();
}

async function call(path, { method = "GET", body, params } = {}) {
  const url = `${path}?${qs(params)}`;
  const init = { method, headers: {} };
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
    const message = (data && (data.error || data.detail)) || `HTTP ${res.status}`;
    throw new Error(message);
  }
  return data;
}

export const api = {
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

  stats: (days) => call("/api/stats/basic", { params: { days } }),
  insights: (days) => call("/api/insights", { params: { days } }),
  /* 唯一会阻塞几秒的接口：让模型重写这一屏的结论 */
  analyzeInsights: (days, force = false) =>
    call("/api/insights/analyze", { method: "POST", body: { days, force } }),

  account: () => call("/api/account"),
  setPlan: (plan) => call("/api/account/subscription", { method: "POST", body: { plan } }),
  setSettings: (payload) => call("/api/account/settings", { method: "PATCH", body: payload }),

  crisis: (region) => call("/api/crisis", { params: { region } }),
  seed: (tzOffset) => call("/api/demo/seed", { method: "POST", params: { tz_offset: tzOffset, reset: 1 } }),

  exportUrl: (format) => `/api/export?${qs({ format })}`,
};

export function tzOffset() {
  return -new Date().getTimezoneOffset();
}
