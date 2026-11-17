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
  });
  vm.runInContext(plain(expressionSource) + '\n' + plain(practiceSource) + '\n' + plain(appSource).replace(/\nboot\(\);\s*$/, "") + `
    globalThis.renders = 0;
    render = () => { ++globalThis.renders; }; renderAuth = () => {}; renderLogList = () => {};
    toast = (message) => messages.push(message);
    globalThis.hooks = { state, iv, sheet, loadedAt, ACTIONS, overlay, expression, practice, wheel };
  `, context);
  Object.assign(context.hooks.state, { account: { user: { id: 1 } }, home: { local_today: today }, logReady: true, insights: { old: true } });
  context.hooks.overlay.hidden = true;
  return { context, hooks: context.hooks, calls, events, messages, node, document };
}

test("JavaScript syntax and Chinese/English key parity", async () => {
  for (const file of ["app", "api", "i18n", "practices", "expression", "theme-init", "register-sw"]) new vm.Script(plain(await read(`frontend/js/${file}.js`)));
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

function expressionFixture({ account = { user: { id: 1 } }, confirm = true, create, changed, language = "zh" } = {}) {
  const calls = [], downloads = [], messages = [];
  const display = { html: "", closed: false, paints: [] };
  const context = vm.createContext({ crypto: webcrypto, Date, document: { documentElement: {}, querySelector: () => null } });
  vm.runInContext(plain(i18nSource) + '\n' + plain(expressionSource) + '\nglobalThis.make = createExpressionController;', context);
  context.setLang(language);
  const controller = context.make({
    api: { createEntry: async payload => { calls.push(payload); return create ? create(payload) : { entry: { id: 1 } }; } },
    t: context.t, esc: text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('\"', '&quot;'),
    getAccount: () => account, getEmotions: () => [{ key: "anxiety", label: "焦虑" }], tzOffset: () => 480,
    overlay: { querySelector: () => null }, open: html => { display.html = html; display.paints.push(html); }, close: () => { display.closed = true; },
    confirm: async () => confirm, toast: message => messages.push(message), render() {},
    changed: changed || (async () => {}), download: text => downloads.push(text),
  });
  const input = (key, value) => controller.input({ dataset: { expressionField: key }, value, closest: () => null });
  const emotion = value => controller.change({ matches: () => true, value });
  return { controller, calls, downloads, messages, display, input, emotion };
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

let failures = 0;
for (const { name, run } of tests) {
  try { await run(); console.log(`PASS ${name}`); }
  catch (error) { failures++; console.error(`FAIL ${name}\n${error.stack}`); }
}
console.log(`${tests.length - failures}/${tests.length} checks passed`);
if (failures) process.exitCode = 1;
