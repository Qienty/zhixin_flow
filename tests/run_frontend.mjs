// Run with node tests/run_frontend.mjs. No browser, network or user data required.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";

const root = new URL("../", import.meta.url);
const read = (file) => fs.readFile(new URL(file, root), "utf8");
const appSource = await read("frontend/js/app.js");
const clientSource = await read("frontend/js/api.js");
const i18nSource = await read("frontend/js/i18n.js");
const plain = (source) => source.replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
const tests = [];
const test = (name, run) => tests.push({ name, run });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = (body, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

function client(fetch) {
  const timers = new Map();
  let nextTimer = 0;
  const events = [];
  const context = vm.createContext({
    URL, URLSearchParams, Date, AbortController, TypeError,
    location: { href: "https://test.invalid/qienty/" }, fetch,
    window: { dispatchEvent: (event) => events.push(event.type) },
    CustomEvent: class { constructor(type) { this.type = type; } },
    setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; },
    clearTimeout: (id) => timers.delete(id),
  });
  vm.runInContext(plain(i18nSource) + "\n" + plain(clientSource) + "\nglobalThis.client = api;", context);
  return { context, api: context.client, timers, events };
}

function element() {
  const classes = new Set();
  return {
    hidden: false, disabled: false, value: "", textContent: "", innerHTML: "", style: {},
    classList: { add: (s) => classes.add(s), remove: (s) => classes.delete(s), contains: (s) => classes.has(s), toggle() {} },
    querySelector: () => null, querySelectorAll: () => [], matches: () => false,
    setAttribute() {}, removeAttribute() {}, focus() {}, scrollIntoView() {},
  };
}

function app(overrides = {}) {
  const events = new Map();
  const nodes = new Map();
  const node = (key) => { if (!nodes.has(key)) nodes.set(key, element()); return nodes.get(key); };
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const calls = [];
  const messages = [];
  const api = {
    home: async () => { calls.push("home"); return { total: 8, items: [], local_today: today }; },
    account: async () => { calls.push("account"); return { pro: false, user: { id: 1 } }; },
    entries: async () => { calls.push("entries"); return { items: [{ id: 8 }], total: 8, next_cursor: null }; },
    insights: async () => { calls.push("insights"); return { sample: { entries: 8 } }; },
    stats: async () => { calls.push("stats"); return { entries: 8 }; },
    cancelPending: () => calls.push("cancel"), logout: async () => {}, ...overrides,
  };
  const window = { matchMedia: () => ({ matches: false, addEventListener() {} }), scrollTo() {},
    addEventListener: (name, fn) => events.set(name, fn) };
  const document = { hidden: false, activeElement: null, body: element(), documentElement: element(),
    querySelector: node, querySelectorAll: () => [], contains: () => false,
    addEventListener: (name, fn) => events.set(name, fn) };
  const context = vm.createContext({
    api, document, window, Date, Intl, AbortController, URL, URLSearchParams,
    location: { hash: "#now", reload: () => calls.push("reload") },
    history: { pushState() {}, back() {}, replaceState() {} },
    setTimeout: () => 1, clearTimeout() {}, setInterval() {}, requestAnimationFrame: () => 1,
    t: (key) => key, getLang: () => "zh", tzOffset: () => 480, messages,
  });
  vm.runInContext(plain(appSource).replace(/\nboot\(\);\s*$/, "") + `
    render = () => {}; renderAuth = () => {}; renderLogList = () => {};
    toast = (message) => messages.push(message);
    globalThis.hooks = { state, iv, sheet, loadedAt, ACTIONS, overlay };
  `, context);
  Object.assign(context.hooks.state, { account: { user: { id: 1 } }, home: { local_today: today }, logReady: true, insights: { old: true } });
  context.hooks.overlay.hidden = true;
  return { context, hooks: context.hooks, calls, events, messages, node, document };
}

test("JavaScript syntax and Chinese/English key parity", async () => {
  for (const file of ["app", "api", "i18n", "theme-init", "register-sw"]) new vm.Script(plain(await read(`frontend/js/${file}.js`)));
  const context = vm.createContext({});
  vm.runInContext(plain(i18nSource) + "\nglobalThis.tables = dict;", context);
  assert.deepEqual(Object.keys(context.tables.zh).sort(), Object.keys(context.tables.en).sort());
});

test("Request timeout cancels fetch and releases timer", async () => {
  let signal;
  const c = client((url, init) => new Promise((resolve, reject) => {
    signal = init.signal;
    signal.addEventListener("abort", () => reject(new Error("aborted")));
  }));
  const result = c.api.createEntry({ client_id: "stable-id" });
  const rejected = assert.rejects(result, { name: "TimeoutError" });
  assert.equal([...c.timers.values()][0].delay, 30000);
  [...c.timers.values()][0].fn();
  await rejected;
  assert.equal(signal.aborted, true);
  assert.equal(c.timers.size, 0);
});

test("Timeout also covers a response body that stalls", async () => {
  const reading = deferred();
  const c = client(async (url, { signal }) => ({ ok: true, status: 200,
    text: () => { reading.resolve(); return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")))); } }));
  const result = c.api.home();
  const rejected = assert.rejects(result, { name: "TimeoutError" });
  await reading.promise;
  [...c.timers.values()][0].fn();
  await rejected;
});

test("AI has a longer deadline and supports explicit cancellation", async () => {
  const controller = new AbortController();
  const c = client((url, { signal }) => new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")))));
  const result = c.api.analyzeInsights(false, controller.signal);
  const rejected = assert.rejects(result, { name: "AbortError" });
  assert.equal([...c.timers.values()][0].delay, 300000);
  controller.abort();
  await rejected;
  assert.equal(c.timers.size, 0);
});

test("API keeps subpath, dispatches expired session, and never retries writes", async () => {
  let calls = 0;
  const c = client(async (url) => { calls++; assert.ok(url.startsWith("/qienty/api/entries?")); return response({ error: "login required" }, 401); });
  await assert.rejects(c.api.createEntry({}), { status: 401 });
  assert.equal(calls, 1);
  assert.deepEqual(c.events, ["tether:unauthorized"]);
  assert.equal(c.timers.size, 0);
});

test("Loaded pages refresh on the same calendar day", async () => {
  const a = app();
  for (const view of ["now", "log", "insights", "me"]) {
    a.hooks.state.view = view;
    a.hooks.loadedAt[view] = Date.now();
    assert.equal(await a.context.ensureViewData(view, { force: true }), true);
  }
  for (const call of ["home", "account", "entries", "insights", "stats"]) assert.ok(a.calls.includes(call));
  assert.equal(a.hooks.state.entries[0].id, 8);
  assert.equal(a.hooks.state.stats.entries, 8);
});

test("Refresh deduplicates pending requests and protects drafts and analysis", async () => {
  const waiting = deferred();
  let count = 0;
  const a = app({ home: async () => { count++; return waiting.promise; } });
  const first = a.context.ensureViewData("now", { force: true });
  const second = a.context.ensureViewData("now", { force: true });
  assert.equal(count, 1);
  waiting.resolve({ total: 0, local_today: a.hooks.state.home.local_today });
  await Promise.all([first, second]);
  a.hooks.overlay.hidden = false;
  assert.equal(await a.context.ensureViewData("now", { force: true }), false);
  a.hooks.overlay.hidden = true;
  a.hooks.state.aiBusy = true;
  assert.equal(await a.context.ensureViewData("now", { force: true }), false);
  assert.equal(count, 1);
});

test("Visibility and reconnect refresh; hidden tabs stay idle", async () => {
  const a = app();
  a.document.hidden = true;
  a.events.get("visibilitychange")();
  a.events.get("online")();
  assert.equal(a.calls.length, 0);
  a.document.hidden = false;
  a.events.get("visibilitychange")();
  await a.context.ensureViewData("now");
  assert.ok(a.calls.includes("home"));
  a.calls.length = 0;
  a.events.get("online")();
  await a.context.ensureViewData("now");
  assert.ok(a.calls.includes("home"));
});

test("Failed refresh preserves loaded data and allows retry", async () => {
  let fail = true;
  const a = app({ entries: async () => { if (fail) throw new Error("offline"); return { items: [{ id: 9 }], total: 1 }; } });
  a.hooks.state.entries = [{ id: 2 }];
  assert.equal(await a.context.ensureViewData("log", { force: true }), false);
  assert.equal(a.hooks.state.entries[0].id, 2);
  fail = false;
  assert.equal(await a.context.ensureViewData("log", { force: true }), true);
  assert.equal(a.hooks.state.entries[0].id, 9);
});

test("Late home and account responses cannot restore an expired session", async () => {
  const home = deferred(), account = deferred();
  const a = app({ home: () => home.promise, account: () => account.promise });
  const pending = a.context.ensureViewData("now", { force: true });
  a.events.get("tether:unauthorized")();
  home.resolve({ total: 99, items: [{ id: 99 }] });
  account.resolve({ pro: true });
  await pending;
  assert.equal(a.hooks.state.account, null);
  assert.equal(a.hooks.state.home.total, 0);
});

test("Logout failure is visible and retry succeeds", async () => {
  let fail = true;
  const a = app({ logout: async () => { if (fail) throw new Error("offline"); } });
  await a.context.doLogout();
  assert.equal(a.calls.includes("reload"), false);
  assert.ok(a.messages.includes("auth.logout_failed"));
  assert.equal(a.node('[data-act="logout"]').disabled, false);
  fail = false;
  await a.context.doLogout();
  assert.ok(a.calls.includes("reload"));
  assert.ok(a.calls.includes("cancel"));
});

test("Timed out save retains input and retries with the original client ID", async () => {
  const saved = [];
  const a = app({ createEntry: async (payload) => {
    saved.push(payload);
    if (saved.length === 1) throw Object.assign(new Error("timeout"), { name: "TimeoutError" });
    return { entry: { id: 42, ...payload } };
  } });
  const button = element(), textarea = element();
  textarea.value = "my unsaved reason";
  a.hooks.overlay.querySelector = (selector) => selector === '[data-act="save-note"]' ? button : textarea;
  a.hooks.sheet.isDraft = true;
  a.hooks.sheet.severity = 4;
  a.hooks.iv.draft = { client_id: "keep-this-id", emotion: "anger", tz_offset: 480 };
  await a.context.saveNote();
  assert.equal(textarea.value, "my unsaved reason");
  assert.equal(a.hooks.iv.draft.client_id, "keep-this-id");
  assert.equal(a.hooks.iv.saving, false);
  assert.equal(button.classList.contains("busy"), false);
  await a.context.saveNote();
  assert.equal(saved.length, 2);
  assert.equal(saved[0].client_id, saved[1].client_id);
  assert.equal(saved[1].cause, "my unsaved reason");
});

test("Cancelling analysis releases the button and lets the user retry", async () => {
  let attempts = 0;
  const a = app({ analyzeInsights: (force, signal) => {
    attempts++;
    if (attempts > 1) return Promise.resolve({ ai: { status: "ready" } });
    return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(Object.assign(new Error("cancelled"), { name: "AbortError" }))));
  } });
  a.hooks.state.insights = { ai: { status: "pending" } };
  const pending = a.context.runAnalysis();
  assert.equal(a.hooks.state.aiBusy, true);
  a.hooks.ACTIONS["cancel-analysis"]();
  await pending;
  assert.equal(a.hooks.state.aiBusy, false);
  await a.context.runAnalysis();
  assert.equal(a.hooks.state.insights.ai.status, "ready");
  assert.equal(attempts, 2);
});

test("An old insights read cannot overwrite a newer AI analysis", async () => {
  const old = deferred();
  const a = app({ insights: () => old.promise, analyzeInsights: async () => ({ ai: { status: "ready", summary: "new" } }) });
  a.hooks.state.insights = { ai: { status: "pending" } };
  const reading = a.context.loadInsights();
  await a.context.runAnalysis();
  old.resolve({ ai: { status: "pending" } });
  await reading;
  assert.equal(a.hooks.state.insights.ai.summary, "new");
});

let failures = 0;
for (const { name, run } of tests) {
  try { await run(); console.log(`PASS ${name}`); }
  catch (error) { failures++; console.error(`FAIL ${name}\n${error.stack}`); }
}
console.log(`${tests.length - failures}/${tests.length} checks passed`);
if (failures) process.exitCode = 1;
