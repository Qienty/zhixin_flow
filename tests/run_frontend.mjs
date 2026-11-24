// Run with node tests/run_frontend.mjs. No browser, network or user data required.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

const root = new URL("../", import.meta.url);
const read = (file) => fs.readFile(new URL(file, root), "utf8");
const appSource = await read("frontend/js/app.js");
const clientSource = await read("frontend/js/api.js");
const i18nSource = await read("frontend/js/i18n.js");
const practiceSource = await read("frontend/js/practices.js");
const expressionSource = await read("frontend/js/expression.js");
const handoffSource = await read("frontend/js/handoff.js");
const utilSource = await read("frontend/js/util.js");
const plain = (source) => source.replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
const tests = [];
const test = (name, run) => tests.push({ name, run });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = (body, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

function client(fetch, { href = "https://test.invalid/qienty/", storage = null, inject = null } = {}) {
  const timers = new Map();
  let nextTimer = 0;
  const events = [];
  const context = vm.createContext({
    URL, URLSearchParams, Date, AbortController, TypeError,
    location: { href }, fetch,
    window: { dispatchEvent: (event) => events.push(event.type), ...(inject === null ? {} : { __TETHER_API_BASE__: inject }) },
    localStorage: storage || { getItem: () => null, setItem() {}, removeItem() {} },
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
    hidden: false, disabled: false, value: "", textContent: "", innerHTML: "", style: {}, dataset: {}, childNodes: [],
    classList: { add: (s) => classes.add(s), remove: (s) => classes.delete(s), contains: (s) => classes.has(s), toggle() {} },
    querySelector: () => null, querySelectorAll: () => [], matches: () => false,
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {}, focus() {}, scrollIntoView() {}, contains: () => false,
    replaceChildren(...children) { this.childNodes = children; }, appendChild() {},
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
    weeklyCard: async () => { calls.push("weekly"); return { days: 7, since: today, until: today,
      entries: { count: 0, severe: 0, days: 0, top_emotion: null }, previous: { count: 0, severe: 0 },
      practices: { completed: 0, paired: 0, before_average: null, after_average: null, drops: 0, better: 0, same: 0, worse: 0 } }; },
    aiSelftest: async () => { calls.push("selftest"); return { ok: true, ms: 5, model: "test-model", provider: "custom", fallback: false }; },
    stats: async () => { calls.push("stats"); return { entries: 8 }; },
    cancelPending: () => calls.push("cancel"), logout: async () => {}, ...overrides,
  };
  const window = { matchMedia: () => ({ matches: false, addEventListener() {} }), scrollTo() {},
    addEventListener: (name, fn) => events.set(name, fn) };
  const document = { hidden: false, activeElement: null, body: element(), documentElement: element(),
    querySelector: node, querySelectorAll: () => [], contains: () => false,
    createElement: () => ({ ...element(), querySelector: node }),
    addEventListener: (name, fn) => events.set(name, fn) };
  const context = vm.createContext({
    api, document, window, Date, Intl, AbortController, URL, URLSearchParams, crypto: webcrypto,
    location: { hash: "#now", reload: () => calls.push("reload") },
    history: { pushState() {}, back() {}, replaceState() {} },
    setTimeout: () => 1, clearTimeout() {}, setInterval() {}, clearInterval() {}, requestAnimationFrame: () => 1,
    t: (key) => key, getLang: () => "zh", tzOffset: () => 480, messages,
    isNativeShell: () => false, setAuthToken: () => {}, restoreAuthToken: () => "",
  });
  vm.runInContext(plain(utilSource) + '\n' + plain(expressionSource) + '\n' + plain(handoffSource) + '\n' + plain(practiceSource) + '\n' + plain(appSource).replace(/\nboot\(\);\s*$/, "") + `
    globalThis.renders = 0;
    render = () => { ++globalThis.renders; }; renderAuth = () => {}; renderLogList = () => {};
    toast = (message) => messages.push(message);
    globalThis.hooks = { state, iv, sheet, loadedAt, ACTIONS, overlay, expression, practice, handoff, wheel };
  `, context);
  Object.assign(context.hooks.state, { account: { user: { id: 1 } }, home: { local_today: today }, logReady: true, insights: { old: true } });
  context.hooks.overlay.hidden = true;
  return { context, hooks: context.hooks, calls, events, messages, node, document };
}

test("JavaScript syntax and Chinese/English key parity", async () => {
  for (const file of ["app", "api", "i18n", "practices", "expression", "handoff", "util", "theme-init", "register-sw"]) new vm.Script(plain(await read(`frontend/js/${file}.js`)));
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

function expressionFixture({ account = { user: { id: 1 } }, confirm = true, create, changed, language = "zh", ai = { enabled: true, model: "test" }, clarifyExpression, nextSteps } = {}) {
  const calls = [], downloads = [], messages = [], confirmed = [], wheelOpens = [], practiceOpens = [];
  const display = { html: "", closed: false, paints: [] };
  const context = vm.createContext({ crypto: webcrypto, Date, document: { documentElement: {}, querySelector: () => null } });
  vm.runInContext(plain(i18nSource) + '\n' + plain(expressionSource) + '\nglobalThis.make = createExpressionController;', context);
  context.setLang(language);
  const controller = context.make({
    api: {
      createEntry: async payload => { calls.push(payload); return create ? create(payload) : { entry: { id: 1 } }; },
      clarifyExpression: async text => { calls.push({ clarify: text }); if (!clarifyExpression) throw new Error("unexpected clarify"); return clarifyExpression(text); },
      nextSteps: async text => { calls.push({ steps: text }); if (!nextSteps) throw new Error("unexpected next steps"); return nextSteps(text); },
    },
    t: context.t, esc: text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('\"', '&quot;'),
    getAccount: () => account, getEmotions: () => [{ key: "anxiety", label: "焦虑" }], tzOffset: () => 480,
    overlay: { querySelector: () => null }, open: html => { display.html = html; display.paints.push(html); }, close: () => { display.closed = true; },
    confirm: async options => { confirmed.push(options && options.title); return confirm; },
    toast: message => messages.push(message), render() {},
    changed: changed || (async () => {}), download: text => downloads.push(text),
    getAI: () => ai, openWheel: () => wheelOpens.push(true), goPractice: () => practiceOpens.push(true),
  });
  const input = (key, value) => controller.input({ dataset: { expressionField: key }, value, closest: () => null });
  const emotion = value => controller.change({ matches: () => true, value });
  const intent = value => controller.input({ dataset: { expressionIntent: "" }, value });
  return { controller, calls, downloads, messages, display, input, emotion, intent, confirmed, wheelOpens, practiceOpens };
}

test("Guest expression, mode changes and downloads never upload content", async () => {
  const f = expressionFixture({ account: null });
  f.input("text", "今天的作业让我很烦 <script>alert(1)</script>");
  await f.controller.handle("open");
  assert.ok(f.display.html.includes('&lt;script&gt;'));
  assert.ok(!f.display.html.includes('<script>'));
  assert.ok(!f.display.html.includes('data-act="e-save"'));
  await f.controller.handle("mode", { dataset: { mode: "clarify" } });
  f.input("need", "想先把话说完");
  await f.controller.handle("mode", { dataset: { mode: "space" } });
  assert.ok(!f.display.html.includes('data-expression-field="need"'));
  await f.controller.handle("mode", { dataset: { mode: "clarify" } });
  assert.ok(f.display.html.includes('想先把话说完'));
  await f.controller.handle("download");
  await f.controller.handle("save");
  assert.equal(f.calls.length, 0);
  assert.equal(f.downloads.length, 1);
  assert.ok(f.downloads[0].includes('想先把话说完'));
  assert.equal(f.controller.isDirty(), true);
});

test("Account saving requires content, an explicit feeling, and confirmation", async () => {
  const f = expressionFixture();
  await f.controller.handle("save");
  f.input("text", "  想慢慢说  ");
  await f.controller.handle("save");
  f.emotion("invented");
  await f.controller.handle("save");
  assert.equal(f.calls.length, 0);
  f.emotion("anxiety");
  await f.controller.handle("save");
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].cause, "想慢慢说");
  assert.equal(f.calls[0].intervention_completed, false);
  assert.equal(f.controller.isDirty(), false);
  assert.equal(f.display.closed, true);
  const cancelled = expressionFixture({ confirm: false });
  cancelled.input("text", "留在这里"); cancelled.emotion("anxiety");
  await cancelled.controller.handle("save");
  assert.equal(cancelled.calls.length, 0);
  assert.equal(cancelled.controller.isDirty(), true);
  assert.equal(cancelled.controller.busy, false);
});

test("Optional main text can be skipped when saving reflection answers", async () => {
  const f = expressionFixture();
  f.input("need", "希望有人先听我说完"); f.emotion("anxiety");
  await f.controller.handle("save");
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].cause, "");
  assert.ok(f.calls[0].note.includes("希望有人先听我说完"));
  assert.equal(f.controller.isDirty(), false);
});

test("Unconfirmed expression saves freeze their payload and retry idempotently", async () => {
  let first = true;
  const f = expressionFixture({ create: async () => { if (first) { first = false; throw new Error('lost response'); } return { entry: { id: 1 } }; } });
  f.input("text", "原稿"); f.input("need", "不要替我决定"); f.emotion("anxiety");
  await f.controller.handle("open");
  await f.controller.handle("save");
  assert.equal(f.controller.isDirty(), true);
  assert.equal(f.controller.busy, false);
  assert.ok(f.display.html.includes('readonly'));
  f.input("text", "不能覆盖待确认的请求");
  await f.controller.handle("mode", { dataset: { mode: "next" } });
  await f.controller.handle("save");
  assert.deepEqual(f.calls[0], f.calls[1]);
  assert.equal(f.calls[1].cause, "原稿");
  assert.ok(f.calls[1].note.includes('不要替我决定'));
  assert.equal(f.controller.isDirty(), false);
});

test("Expression double-save is guarded; a refresh error does not undo saving", async () => {
  const wait = deferred();
  const f = expressionFixture({ create: () => wait.promise, changed: async () => { throw new Error('offline'); } });
  f.input("text", "一次就好"); f.emotion("anxiety");
  const first = f.controller.handle("save");
  await f.controller.handle("save");
  assert.equal(f.calls.length, 1);
  assert.equal(f.controller.busy, true);
  wait.resolve({ entry: { id: 1 } });
  await first;
  assert.equal(f.controller.isDirty(), false);
  assert.ok(f.messages.some(message => message.includes('内容已保存，列表刷新失败')));
});

test("Closing keeps the in-memory draft; explicit clearing removes all answers", async () => {
  const f = expressionFixture();
  f.input("text", "本页文字"); f.input("support", "陪我聊一会");
  f.controller.detach();
  assert.equal(f.controller.isDirty(), true);
  assert.ok(f.controller.card().includes('本页文字'));
  await f.controller.handle("clear");
  assert.equal(f.controller.isDirty(), false);
  assert.equal(f.calls.length, 0);
  const cancelled = expressionFixture({ confirm: false });
  cancelled.input("text", "还想留着");
  await cancelled.controller.handle("clear");
  assert.equal(cancelled.controller.isDirty(), true);
});

test("Reflection answers stay within the API limits in both languages", async () => {
  for (const language of ["zh", "en"]) {
    const f = expressionFixture({ language });
    f.input("text", "字".repeat(700));
    for (const key of ["facts", "need", "step", "support"]) f.input(key, "字".repeat(200));
    f.emotion("anxiety");
    await f.controller.handle("save");
    assert.equal(f.calls[0].cause.length, 500);
    assert.ok(f.calls[0].note.length <= 1000);
  }
});

test("Picking a feeling offers a choice without starting a guide or saving", async () => {
  const a = app({ intervention: async () => ({ branch: "anxiety", steps: [] }), createEntry: async () => { throw new Error('must not save'); } });
  await a.context.pickEmotion("anxiety");
  assert.ok(a.hooks.overlay.innerHTML.includes('data-act="record-direct"'));
  assert.ok(a.hooks.overlay.innerHTML.includes('data-act="guide-start"'));
  assert.ok(!a.hooks.overlay.innerHTML.includes('data-act="iv-next"'));
  assert.equal(a.hooks.iv.draft.emotion, "anxiety");
});

test("Cancelling analysis preserves an existing result and does not report failure", async () => {
  const a = app({ analyzeInsights: async () => { throw Object.assign(new Error('cancelled'), { name: 'AbortError' }); } });
  a.hooks.state.insights = { ai: { status: 'ready', summary: 'keep this result' } };
  await a.context.runAnalysis();
  assert.equal(a.hooks.state.insights.ai.summary, 'keep this result');
  assert.equal(a.hooks.state.insights.ai.status, 'ready');
  assert.equal(a.hooks.state.aiError, '');
  assert.equal(a.hooks.state.aiBusy, false);
});

test("A failed first log load exposes a retry instead of permanent skeletons", async () => {
  let fail = true;
  const a = app({ entries: async () => { if (fail) throw new Error('<offline>'); return { items: [], total: 0 }; } });
  a.hooks.state.logReady = false;
  await assert.rejects(a.context.loadEntries(), /offline/);
  const html = a.context.entryListHtml([]);
  assert.ok(html.includes('data-act="retry-log"'));
  assert.ok(html.includes('&lt;offline&gt;'));
  assert.ok(!html.includes('skeleton'));
  fail = false; await a.hooks.ACTIONS['retry-log']();
  assert.equal(a.hooks.state.logReady, true); assert.equal(a.hooks.state.logError, '');
});

test("A background refresh does not remove input focused during its request", async () => {
  const waiting = deferred();
  const a = app({ home: () => waiting.promise });
  const result = a.context.ensureViewData('now', { force: true });
  a.document.activeElement = { matches: () => true };
  waiting.resolve({ total: 12, items: [], local_today: a.hooks.state.home.local_today });
  await result;
  assert.equal(a.hooks.state.home.total, 12);
  assert.equal(a.context.renders, 0);
});

test("Session expiry closes a busy expression and ignores its late save result", async () => {
  const waiting = deferred(), entered = deferred();
  const a = app({ createEntry: () => { entered.resolve(); return waiting.promise; } });
  a.hooks.state.meta = { emotions: [{ key: 'anxiety' }] };
  // The controller captured the real confirmation function; approve its layer below.
  a.hooks.expression.input({ dataset: { expressionField: 'text' }, value: 'recover this draft', closest: () => null });
  a.hooks.expression.change({ matches: () => true, value: 'anxiety' });
  await a.hooks.expression.handle('open');
  const saving = a.hooks.expression.handle('save');
  a.context.settleConfirm(true);
  await entered.promise;
  assert.equal(a.hooks.expression.busy, true);
  a.events.get('tether:unauthorized')();
  assert.equal(a.hooks.overlay.hidden, true);
  assert.equal(a.hooks.expression.busy, false);
  assert.equal(a.hooks.expression.isDirty(), true);
  waiting.resolve({ entry: { id: 1 } }); await saving;
  assert.equal(a.hooks.expression.isDirty(), true);
  assert.equal(a.hooks.overlay.hidden, true);
  assert.equal(a.hooks.state.account, null);
});

test("Expression suspend retains a downloadable draft without late repaint", async () => {
  const waiting = deferred(), entered = deferred(), f = expressionFixture({ create: () => { entered.resolve(); return waiting.promise; } });
  f.input('text', 'retain me'); f.emotion('anxiety'); await f.controller.handle('open');
  const saving = f.controller.handle('save'); await entered.promise;
  f.controller.suspend(); const paints = f.display.paints.length;
  waiting.reject(new Error('expired')); await saving;
  await f.controller.handle('download');
  assert.equal(f.display.paints.length, paints); assert.equal(f.controller.busy, false);
  assert.equal(f.downloads.length, 1); assert.ok(f.downloads[0].includes('retain me'));
});

test("Successful expression saving does not repaint a retry form", async () => {
  const f = expressionFixture(); f.input('text', 'one save'); f.emotion('anxiety');
  await f.controller.handle('open'); const before = f.display.paints.length;
  await f.controller.handle('save');
  assert.equal(f.display.paints.length, before + 1); // only the busy state
  assert.equal(f.display.closed, true);
});

test("Closing nested crisis resources restores the same guide nodes and draft", async () => {
  const waiting = deferred(), a = app({ crisis: () => waiting.promise });
  const original = { textareaValue: 'unchanged' };
  a.hooks.iv.draft = { client_id: 'stable' }; a.hooks.overlay.hidden = false;
  a.hooks.overlay.childNodes = [original];
  const opening = a.context.openCrisis();
  a.context.closeOverlay();
  assert.equal(a.hooks.overlay.childNodes[0], original);
  assert.equal(a.hooks.iv.draft.client_id, 'stable');
  const restored = a.hooks.overlay.innerHTML;
  waiting.resolve({ label: 'late', lines: [] }); await opening;
  assert.equal(a.hooks.overlay.innerHTML, restored);
  assert.equal(a.hooks.overlay.hidden, false);
});

test("Guide timers pause while nested resources are open", async () => {
  const waiting = deferred(), a = app({ crisis: () => waiting.promise });
  const frames = [], elapsed = [];
  a.context.performance = { now: () => 0 };
  a.context.requestAnimationFrame = callback => frames.push(callback);
  a.hooks.iv.draft = { client_id: 'timer' }; a.hooks.overlay.hidden = false;
  a.context.ticker(10, a.hooks.iv.token, seconds => elapsed.push(seconds));
  frames.shift()(1000);
  const opening = a.context.openCrisis();
  frames.shift()(5000);
  assert.deepEqual(elapsed, [1]);
  a.context.closeOverlay(); frames.shift()(6000);
  assert.deepEqual(elapsed, [1, 2]);
  waiting.resolve({ label: 'late', lines: [] }); await opening;
});

test("AI summary, explanation and separate reminders are safely rendered", async () => {
  const a = app(), payload = '<img src=x onerror=alert(1)>';
  const ai = a.context.aiCard({ ai: { status: 'ready', summary: payload, reminders: [payload] } });
  assert.equal((ai.match(/&lt;img/g) || []).length, 2);
  const pattern = a.context.patternCard({ key: 'x', title: 'x', body: '', evidence: [] }, false, true, '', payload);
  assert.ok(pattern.includes('&lt;img')); assert.ok(!pattern.includes('<img'));
});

test("Repeated guide keyboard shortcuts never advance and timezone labels use minutes", async () => {
  const a = app(); let advanced = 0;
  a.node('#confirm').hidden = true; a.hooks.overlay.hidden = false;
  a.hooks.overlay.querySelector = () => ({ click() { ++advanced; } });
  for (const key of ['Enter', ' ']) a.events.get('keydown')({ key, repeat: true, target: { matches: () => false }, preventDefault() {} });
  assert.equal(advanced, 0);
  for (const [minutes, expected] of [[330, 'UTC+05:30'], [345, 'UTC+05:45'], [-210, 'UTC-03:30'], [0, 'UTC+00:00']]) assert.equal(a.context.offsetLabel(minutes), expected);
});

test("Service worker precache failure rejects install before taking over", async () => {
  const handlers = new Map(), calls = []; let install;
  const context = vm.createContext({ URL,
    self: { registration: { scope: 'https://test.invalid/qienty/' }, addEventListener: (type, fn) => handlers.set(type, fn), skipWaiting: () => calls.push('skip'), clients: { claim: () => calls.push('claim') } },
    caches: { open: async () => ({ addAll: async () => { throw new Error('missing shell'); } }), keys: async () => ['old'], delete: async () => calls.push('delete') },
  });
  vm.runInContext(await read('frontend/sw.js'), context);
  handlers.get('install')({ waitUntil: promise => { install = promise; } });
  await assert.rejects(install, /missing shell/);
  assert.deepEqual(calls, []);
});

test("Home keeps the record entry and the weekly evidence card", async () => {
  const a = app();
  Object.assign(a.hooks.state, {
    meta: { emotions: [] },
    home: { items: [], total: 0, watch: false, local_today: "2026-11-22" },
  });
  // 跨语言功能已移除：首页只剩记录与情绪入口
  const home = a.context.viewNow();
  assert.ok(home.includes('data-act="e-open"'), "自由表达入口还在");
  assert.ok(!home.includes("cross-entry"), "跨语言入口已移除");
  assert.ok(home.includes("emotion-section"), "情绪入口还在");
  a.hooks.state.weekly = {
    days: 7, since: "2026-11-16", until: "2026-11-22",
    entries: { count: 2, severe: 1, days: 2, top_emotion: "anger" },
    previous: { count: 1, severe: 0 },
    practices: { completed: 1, paired: 1, before_average: 5, after_average: 3, drops: 1, better: 1, same: 0, worse: 0 },
  };
  a.hooks.state.insights = { sample: { entries: 2 } };
  const insights = a.context.viewInsights();
  assert.ok(insights.includes("week-lines"));
  assert.ok(insights.includes("week.entries"));
  assert.ok(insights.includes("week.practices"));
  assert.ok(insights.includes("week.paired"));
  assert.ok(insights.includes("week.drops"));
  assert.ok(insights.includes("week.compare_up") || insights.includes("week.compare_down"));
});

test("Weekly card shows an empty state instead of zeros", async () => {
  const a = app();
  a.hooks.state.weekly = {
    days: 7, since: "2026-11-16", until: "2026-11-22",
    entries: { count: 0, severe: 0, days: 0, top_emotion: null },
    previous: { count: 0, severe: 0 },
    practices: { completed: 0, paired: 0, before_average: null, after_average: null, drops: 0, better: 0, same: 0, worse: 0 },
  };
  a.hooks.state.insights = { sample: { entries: 0 } };
  const insights = a.context.viewInsights();
  assert.ok(insights.includes("week.empty_title"));
  assert.ok(!insights.includes("week-lines"));
});

test("Interface language prefers a saved choice, then an explicit account setting, then Chinese", async () => {
  const a = app();
  // 浏览器语言不参与：面向国内，任何系统语言下首次打开都是中文
  assert.equal(a.context.pickInitialLang(null, null, false), "zh");
  assert.equal(a.context.pickInitialLang("en", null, false), "en");  // 本机选过英文就给英文
  assert.equal(a.context.pickInitialLang("zh", "en", true), "zh");    // 本机选过的最优先
  assert.equal(a.context.pickInitialLang(null, "zh", true), "zh");    // 账号明确设过
  assert.equal(a.context.pickInitialLang(null, "en", true), "en");    // 账号明确选了英文
  assert.equal(a.context.pickInitialLang(null, "en", false), "zh", "账号只是默认值，不跟随");
});

test("AI connection status is shown in Me and stays honest when unconfigured", async () => {
  const a = app();
  Object.assign(a.hooks.state, {
    meta: { emotions: [], regions: [], ai: { enabled: true, model: "deepseek-flash", provider: "deepseek", fallback: true } },
    account: { pro: false, user: { username: "x" } },
  });
  const mine = a.context.viewMe();
  assert.ok(mine.includes("ai.connected"));
  assert.ok(mine.includes('data-act="ai-selftest"'));

  a.hooks.state.meta.ai = { enabled: false };
  const off = a.context.viewMe();
  assert.ok(off.includes("ai.disconnected"));
  assert.ok(!off.includes('data-act="ai-selftest"'), "没连上就不给无效按钮");
});

test("Install entry appears in the browser and disappears once installed", async () => {
  const a = app();
  a.context.isStandalone = () => false;
  a.context.isIosSafari = () => false;
  const browserCard = a.context.installCard();
  assert.ok(browserCard.includes("me.install_title"));
  assert.ok(browserCard.includes('data-act="install-app"'));
  a.context.isStandalone = () => true;
  assert.equal(a.context.installCard(), "");
  a.context.isStandalone = () => false;
  a.context.isIosSafari = () => true;
  const iosCard = a.context.installCard();
  assert.ok(iosCard.includes("me.install_ios"));
  assert.ok(!iosCard.includes('data-act="install-app"'), "iOS 没有系统安装框，不该给按钮");
});

test("Web keeps session in cookies and never asks for a token", async () => {
  let seen = null;
  const c = client(async (url, init) => { seen = init.headers; return response({ account: { user: { id: 1 } } }); });
  assert.equal(c.context.isNativeShell(), false);
  await c.api.login("someone", "secret");
  assert.equal(seen.Authorization, undefined);
  assert.equal(seen["X-Tether-Token-Auth"], undefined);
});

test("Native shell gets a token, sends it as Bearer, and forgets it on logout", async () => {
  const store = new Map();
  const storage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, value),
    removeItem: (key) => store.delete(key),
  };
  const sent = [];
  const c = client(async (url, init) => {
    sent.push({ url, headers: init.headers });
    if (url.includes("/api/auth/login")) return response({ account: { user: { id: 1 } }, token: "tok-abc", token_type: "Bearer" });
    return response({ ok: true });
  }, { href: "capacitor://localhost/", storage });

  assert.equal(c.context.isNativeShell(), true);
  const session = await c.api.login("someone", "secret");
  assert.equal(sent[0].headers["X-Tether-Token-Auth"], "bearer", "壳内登录要显式要 token");
  assert.equal(session.token, "tok-abc");
  c.context.setAuthToken(session.token);
  assert.equal(store.get("tether.token"), "tok-abc", "token 存进壳内存储");

  await c.api.account();
  assert.equal(sent[1].headers.Authorization, "Bearer tok-abc");

  // 冷启动：新的运行环境从存储里恢复 token
  const restarted = client(async (url, init) => { sent.push({ url, headers: init.headers }); return response({ ok: true }); },
    { href: "capacitor://localhost/", storage });
  assert.equal(restarted.context.restoreAuthToken(), "tok-abc");
  await restarted.api.home();
  assert.equal(sent.at(-1).headers.Authorization, "Bearer tok-abc");

  restarted.context.setAuthToken("");
  assert.equal(store.has("tether.token"), false, "退出要清掉存储里的 token");
});

test("API base stays same-origin unless the shell injects a server address", async () => {
  const same = client(async (url) => { assert.ok(url.startsWith("/qienty/api/home?")); return response({ items: [] }); });
  await same.api.home();

  const root = client(async (url) => { assert.ok(url.startsWith("/api/home?")); return response({ items: [] }); }, { href: "https://test.invalid/" });
  await root.api.home();

  const injected = client(async (url) => { assert.ok(url.startsWith("https://api.example.com/api/home?"), url); return response({ items: [] }); },
    { href: "capacitor://localhost/", inject: "https://api.example.com/" });
  await injected.api.home();

  // 注入为空串、或只有空格时，仍然按同源处理（根部署 → 相对路径）
  const blank = client(async (url) => { assert.ok(url.startsWith("/api/home?"), url); return response({ items: [] }); },
    { href: "https://test.invalid/", inject: "   " });
  await blank.api.home();
});

test("Microphone button only renders where speech input actually works", async () => {
  const a = app();
  // 桌面浏览器没有 SpeechRecognition：不渲染按钮，不留死控件
  assert.equal(a.context.voiceSupport(), false);
  assert.equal(a.context.micButton(), "");

  // 支持语音的浏览器：渲染
  a.context.window.SpeechRecognition = function SpeechRecognition() {};
  assert.equal(a.context.voiceSupport(), true);
  const browserButton = a.context.micButton();
  assert.ok(browserButton.includes('data-act="mic"'));
  assert.ok(browserButton.includes("note.voice"));

  // 壳内：即使 webkitSpeechRecognition 存在也不算数，必须有原生桥
  a.context.isNativeShell = () => true;
  a.context.window.webkitSpeechRecognition = function SpeechRecognition() {};
  assert.equal(a.context.voiceSupport(), false);
  assert.equal(a.context.micButton(), "");
  a.context.window.__TETHER_SPEECH__ = { start() {}, stop() {} };
  assert.equal(a.context.voiceSupport(), true);
  assert.ok(a.context.micButton().includes('data-act="mic"'));
});

test("Voice language setting is hidden where speech input cannot work", async () => {
  const a = app();
  Object.assign(a.hooks.state, { meta: { emotions: [], regions: [] }, account: { pro: false, user: { username: "x" } } });
  const withoutVoice = a.context.viewMe();
  assert.ok(!withoutVoice.includes('data-act="set-voice"'), "没有语音能力时不该出现语音语言选项");

  a.context.window.SpeechRecognition = function SpeechRecognition() {};
  const withVoice = a.context.viewMe();
  assert.ok(withVoice.includes('data-act="set-voice"'));
});

test("System back gesture closes the overlay and ignores our own history pop", async () => {
  const a = app();
  Object.assign(a.hooks.state, { account: { user: { id: 1 } }, meta: { emotions: [], regions: [] } });
  a.context.openOverlay('<div class="sheet">hi</div>', { focus: false, label: "x" });
  assert.equal(a.hooks.overlay.hidden, false);

  // 手机返回手势 / 安卓返回键 → popstate → 先关浮层
  a.events.get("popstate")({ state: { view: "now" } });
  assert.equal(a.hooks.overlay.hidden, true, "返回应该先关浮层，而不是退出应用");

  // 我们再打开一次，然后主动关闭：关闭会自己发一次 history.back()，
  // 那一下回来的 popstate 必须被认领，否则会把刚切到的视图弹回去。
  a.context.openOverlay('<div class="sheet">again</div>', { focus: false, label: "x" });
  assert.equal(a.hooks.overlay.hidden, false);
  a.context.closeOverlay();
  assert.equal(a.hooks.overlay.hidden, true);
  a.hooks.state.view = "log";
  a.events.get("popstate")({ state: { view: "now" } });
  assert.equal(a.hooks.state.view, "log", "自己发出的 pop 不该触发页面回退");
});

test("Records page turns a page of eight instead of scrolling forever", async () => {
  const seen = [];
  const entry = (id) => ({ id, local_date: "2026-11-22", severity: 3, emotion: "calm", cause: "x",
    occurred_at: "2026-11-22T10:00:00+00:00", local_hour: 10, tz_offset: 480, tags: [], note: "" });
  const a = app({
    entries: async (params) => {
      seen.push(params);
      const start = params.offset || 0;
      return { items: Array.from({ length: Math.max(0, Math.min(8, 25 - start)) }, (_, i) => entry(start + i + 1)), total: 25, next_cursor: null };
    },
  });
  Object.assign(a.hooks.state, { view: "log", meta: { emotions: [] } });
  a.context.indexEmotions();

  await a.context.loadEntries({ page: 0 });
  assert.deepEqual([seen.at(-1).limit, seen.at(-1).offset], [8, 0]);
  assert.equal(a.hooks.state.entries.length, 8);
  assert.equal(a.context.logPageCount(), 4, "25 条按每页 8 条算 4 页");

  await a.context.loadEntries({ page: 3 });
  assert.equal(seen.at(-1).offset, 24, "第 4 页从第 25 条开始");
  assert.equal(a.hooks.state.entries.length, 1);
  assert.equal(a.hooks.state.logPage, 3);

  const html = a.context.entryListHtml(a.hooks.state.entries);
  assert.equal((html.match(/class="log-pager"/g) || []).length, 2, "翻页器在列表上下各一个");
  assert.ok(html.includes("log.page_of"));
  assert.ok(html.includes('data-act="previous"'));

  // 已经是最后一页：再点下一页不会发请求
  const before = seen.length;
  await a.context.pageLog(true);
  assert.equal(seen.length, before, "到最后一页后不再请求");
});

test("Appointment summary renders facts and keeps questions out of the account", async () => {
  const sent = [];
  const payload = {
    days: 30, since: "2026-10-26", until: "2026-11-24", generated_at: "2026-11-24T12:00:00+00:00",
    entries: {
      entries: 28, days_logged: 20, avg_severity: 3.0, severe_count: 9,
      emotion_distribution: [{ label: "愤怒", count: 12 }, { label: "焦虑", count: 9 }],
      top_causes: [{ label: "被临时加需求", count: 6 }],
    },
    hours: [{ key: "morning", label: "上午", range: "5—12 点", count: 10 }],
    week: { entries: 14, severe: 4, previous_entries: 14, previous_severe: 5 },
    practices: { completed: 1, paired: 1, before_average: 4, after_average: 2, drops: 1, feedback: {}, titles: [] },
    highlights: [{ date: "2026-11-15", emotion: "愤怒", severity: 5, cause: "被临时加需求" }],
    notes: "这是你自己记录的整理，不是诊断。",
  };
  const a = app({ handoff: async (days) => { sent.push(days); return payload; } });
  await a.hooks.handoff.handle("open", { dataset: {} });
  assert.equal(sent.at(-1), 30);
  const html = a.hooks.overlay.innerHTML;
  // 测试沙箱里的 t() 只回 key，所以这里验结构与各区块是否渲染；
  // 真实数字由后端用例和浏览器验收覆盖。
  assert.ok(html.includes("handoff-sheet"));
  assert.ok(html.includes("handoff.entries_value"), "概览");
  assert.ok(html.includes("handoff.sec_triggers"), "触发场景区块");
  assert.ok(html.includes("handoff.sec_high"), "最高强度区块");
  assert.ok(html.includes('data-act="h-print"'));
  assert.ok(html.includes('data-act="h-download"'));
  assert.equal((html.match(/data-handoff-q="\d"/g) || []).length, 3, "三个可填的问题");

  // 换窗口会重新拉取
  await a.hooks.handoff.handle("window", { dataset: { days: "90" } });
  assert.equal(sent.at(-1), 90);

  // 填的问题只留在页面状态里（不进账号）
  a.hooks.handoff.input({ dataset: { handoffQ: "0" }, value: "为什么我一到下午就难受？" });
  assert.equal(a.calls.filter((call) => String(call).includes("entries")).length, 0, "填问题不触发任何写入");
  await a.hooks.handoff.handle("close", { dataset: {} });
});

test("A failed window switch keeps the old window, its numbers and its highlight", async () => {
  const payload = {
    days: 30, since: "2026-11-01", until: "2026-11-30",
    entries: { entries: 10, days_logged: 6, avg_severity: 3, severe_count: 2, emotion_distribution: [], top_causes: [] },
    hours: [], week: {}, practices: {}, highlights: [], notes: "n",
  };
  let calls = 0;
  const a = app({
    handoff: async () => {
      calls += 1;
      if (calls > 1) throw new Error("网络连接失败");
      return payload;
    },
  });
  await a.hooks.handoff.handle("open", { dataset: {} });
  await a.hooks.handoff.handle("window", { dataset: { days: "90" } });

  const html = a.hooks.overlay.innerHTML;
  assert.ok(html.includes("网络连接失败"), "失败要给出提示");
  assert.ok(/class="chip on"[^>]*data-days="30"/.test(html), "失败后高亮应留在原来的 30 天");
  assert.ok(!/class="chip on"[^>]*data-days="90"/.test(html), "不能把 90 天标为当前窗口");
  assert.ok(html.includes('data-act="h-download"'), "旧数据仍可下载/打印");
  await a.hooks.handoff.handle("close", { dataset: {} });
});

test("Escaping and colour helpers keep valid values and neutralise unsafe ones", async () => {
  const context = vm.createContext({});
  vm.runInContext(plain(utilSource) + "\nglobalThis.util = { esc, hexToRgb, safeColor, safeHref, brighten };", context);
  const { esc, hexToRgb, safeColor, safeHref, brighten } = context.util;

  assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
  assert.equal(esc(null), "");

  assert.equal(safeColor("#8C6A3F"), "#8C6A3F");
  assert.equal(safeColor("#abc"), "#abc");
  assert.equal(safeColor('#fff" onmouseover="alert(1)'), "#8d948c");
  assert.equal(safeColor(undefined), "#8d948c");

  assert.equal(safeHref("https://988lifeline.org"), "https://988lifeline.org");
  assert.equal(safeHref("javascript:alert(1)"), "#");
  assert.equal(safeHref("http://insecure.example"), "#");

  assert.equal(hexToRgb("#f00").join(","), "255,0,0");
  assert.equal(hexToRgb("#0f0").join(","), "0,255,0");
  assert.equal(brighten("#f5f0e8"), "#f5f0e8", "浅色原样返回");
  assert.match(brighten("#7C9A87"), /^#[0-9a-f]{6}$/, "深色被提亮成合法色值");
});

test("Service worker precaches every imported module and every entry exists on disk", async () => {
  const sw = await read("frontend/sw.js");
  const block = /const SHELL = \[([\s\S]*?)\];/.exec(sw);
  assert.ok(block, "sw.js 里找不到 SHELL 清单");
  const shell = [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  const webRoot = new URL("frontend/", root); // 清单里的路径相对站点根

  // 列进去的东西必须真的在磁盘上：改名/删文件时不能让 install 静默失败
  for (const entry of shell) {
    if (entry === "./") continue;
    const info = await fs.stat(new URL(entry, webRoot)).catch(() => null);
    assert.ok(info && info.isFile(), `预缓存条目在磁盘上不存在：${entry}`);
  }

  // 反过来：页面 import 的每个模块都必须在清单里，否则离线冷启动白屏
  const imports = [];
  for (const file of ["app", "api", "i18n", "practices", "expression", "handoff", "util", "theme-init", "register-sw"]) {
    const text = await read(`frontend/js/${file}.js`);
    for (const match of text.matchAll(/^import\s[^;]*?from\s+"(\.[^"]+)";/gm)) imports.push(match[1]);
  }
  assert.ok(imports.length > 0, "没解析到任何 import");
  for (const spec of imports) {
    const entry = "./js/" + spec.replace(/^\.\//, "");
    assert.ok(shell.includes(entry), `模块未被预缓存，离线冷启动会白屏：${spec}`);
  }
});

test("AI clarification needs consent, stays read-only until the user asks to fill", async () => {
  const payload = { facts: "你开完会后一直没缓过来 <script>alert(1)</script>", needs: ["希望被听完", "不想被劝"],
    emotion: "anxiety", question: "你最在意哪部分？", quota: { limit: 1, used: 1, remaining: 0 } };
  const f = expressionFixture({ clarifyExpression: async () => payload });
  f.input("text", "今天开会被否了，很难受");
  await f.controller.handle("open");
  await f.controller.handle("mode", { dataset: { mode: "clarify" } });
  assert.ok(f.display.html.includes('data-act="e-clarify"'), "有 AI 时给出按钮");
  assert.ok(f.display.html.includes("模型服务"), "按钮下面要说明会发送");

  await f.controller.handle("clarify");
  assert.equal(f.confirmed.length, 1, "每次调用前都要确认一次");
  assert.deepEqual(f.calls, [{ clarify: "今天开会被否了，很难受" }]);
  assert.ok(f.display.html.includes("你开完会后一直没缓过来"));
  assert.ok(f.display.html.includes("&lt;script&gt;"), "模型输出必须转义");
  assert.ok(!f.display.html.includes("<script>"), "模型输出不能当 HTML");
  assert.ok(f.display.html.includes("今天的次数用完了"), "免费额度用完后给出提示");
  assert.equal(f.calls.filter(call => call.emotion).length, 0, "AI 结果不能自己变成记录");

  // 只读：没点「填进」之前，下载里只有正文，没有 AI 的整理结果
  await f.controller.handle("download");
  assert.ok(!f.downloads.at(-1).includes("你开完会后一直没缓过来"));
  assert.ok(!f.downloads.at(-1).includes("希望被听完"));

  // 点「填进整理框」才写入草稿
  await f.controller.handle("ai-fill");
  await f.controller.handle("download");
  assert.ok(f.downloads.at(-1).includes("你开完会后一直没缓过来"));
  assert.ok(f.downloads.at(-1).includes("希望被听完"));
});

test("Declining the AI consent sends nothing", async () => {
  let called = 0;
  const f = expressionFixture({ confirm: false, clarifyExpression: async () => { called++; return {}; } });
  f.input("text", "今天很累");
  await f.controller.handle("open");
  await f.controller.handle("mode", { dataset: { mode: "clarify" } });
  await f.controller.handle("clarify");
  assert.equal(called, 0);
  assert.equal(f.confirmed.length, 1);
});

test("Next-step mode offers local options first and asks AI only for written intent", async () => {
  const f = expressionFixture({ nextSteps: async text => ({ steps: [{ text: "先走十分钟", practice_id: "" }], note: "愿意再做", quota: { limit: 1, used: 1, remaining: 0 } }) });
  f.input("text", "今天很累");
  await f.controller.handle("open");
  await f.controller.handle("mode", { dataset: { mode: "next" } });
  assert.ok(f.display.html.includes('data-act="e-wheel"'), "本地给转盘入口");
  assert.ok(f.display.html.includes('data-act="e-practice"'), "本地给练习入口");

  await f.controller.handle("wheel");
  assert.equal(f.wheelOpens.length, 1);
  assert.equal(f.calls.length, 0, "转盘不触发模型调用");

  await f.controller.handle("steps");
  assert.equal(f.calls.length, 0, "没写想做的事就不调用");

  f.intent("想出门走十分钟");
  await f.controller.handle("steps");
  assert.deepEqual(f.calls, [{ steps: "想出门走十分钟" }]);
  assert.ok(f.display.html.includes("先走十分钟"));
});

test("Without AI configured the two modes fall back to self-written prompts", async () => {
  const f = expressionFixture({ ai: { enabled: false } });
  f.input("text", "今天很累");
  await f.controller.handle("open");
  await f.controller.handle("mode", { dataset: { mode: "clarify" } });
  assert.ok(!f.display.html.includes('data-act="e-clarify"'), "没配 AI 不显示按钮");
  assert.ok(f.display.html.includes("没有配置 AI"));
  assert.ok(f.display.html.includes('data-expression-field="facts"'), "整理框仍然可用");
  assert.ok(f.display.html.includes('data-expression-field="need"'));
});

let failures = 0;
for (const { name, run } of tests) {
  try { await run(); console.log(`PASS ${name}`); }
  catch (error) { failures++; console.error(`FAIL ${name}\n${error.stack}`); }
}
console.log(`${tests.length - failures}/${tests.length} checks passed`);
if (failures) process.exitCode = 1;
