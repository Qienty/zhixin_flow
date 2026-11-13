/* Tether — 前端主逻辑（无依赖、无构建） */

import { api, setApiLang, tzOffset } from "./api.js";
import { t, setLang, getLang, applyStaticText } from "./i18n.js";

/* ------------------------------------------------------------- 常量与状态 */

const SEV_COLOR = { 1: "#7C9A87", 2: "#9C9A5C", 3: "#C08A3E", 4: "#B4523F", 5: "#8E3626" };

const BRANCH_LABEL = {
  anger: { zh: "30 秒降唤醒", en: "30s down-shift" },
  sadness: { zh: "陪伴 + 手放胸口", en: "company + hand" },
  despair: { zh: "托底 + 热线", en: "safety + lines" },
  anxiety: { zh: "延长呼气", en: "long exhale" },
  none: { zh: "", en: "" },
};

const SEV_LABEL = {
  zh: { 1: "很轻", 2: "有点", 3: "中间", 4: "很强", 5: "到顶了" },
  en: { 1: "faint", 2: "mild", 3: "mid", 4: "strong", 5: "peak" },
};

const VIEWS = ["now", "log", "insights", "me"];
const PAGE_SIZE = 40;
const ENTRY_LIMIT = 1000; // 后端上限；超出之后计数改用服务端 total

const state = {
  view: "now",
  meta: null,
  account: null,
  entries: [],
  total: 0,
  filter: { emotion: "all", sort: "severity", unfilled: false },
  logLimit: PAGE_SIZE,
  insightsDays: 14,
  insights: null,
  stats: null,
  /* AI 分析是异步的：进来先显示规则版 + 骨架，模型回来再替换 */
  aiBusy: false,
  aiError: "",
};

/* ------------------------------------------------------------------ 工具 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* 映射表和正则提到模块级：esc 是列表渲染里的热点，
原来每遇到一个待转义字符就新建一次对象字面量。 */
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const ESC_RE = /[&<>"']/g;

function esc(value) {
  return String(value ?? "").replace(ESC_RE, (c) => ESC_MAP[c]);
}

/* --- 颜色 ---------------------------------------------------------------- */

function hexToRgb(hex) {
  const raw = String(hex || "#999999").replace("#", "");
  const full = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw;
  const num = parseInt(full, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

/* 情绪色与强度色来自后端，是为浅底调的。深色下整体提亮、略降饱和，
否则墨绿纸底换成暖炭底之后这些点会糊成一团。
换算按原色缓存：一次列表重绘里同一个色值会被问上百次，而 HSL 来回换算不便宜。
浅色直接原样返回，所以缓存里只会有深色的结果，不需要把主题编进 key。 */
const colorCache = new Map();

function adapt(hex) {
  if (theme.resolved !== "dark") return hex;
  let hit = colorCache.get(hex);
  if (hit === undefined) {
    hit = brighten(hex);
    colorCache.set(hex, hit);
  }
  return hit;
}

function brighten(hex) {
  const [r, g, b] = hexToRgb(hex).map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (l >= 0.6) return hex;
  const d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const s = d === 0 ? 0 : Math.min(0.72, d / (1 - Math.abs(2 * l - 1)));
  return hslToHex(h, s, 0.64);
}

function hslToHex(h, s, l) {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const seg = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]][Math.floor(h / 60) % 6];
  return "#" + seg.map((v) => Math.round((v + m) * 255).toString(16).padStart(2, "0")).join("");
}

const rgbaCache = new Map();

function rgba(hex, alpha) {
  const key = `${theme.resolved}|${hex}|${alpha}`;
  let hit = rgbaCache.get(key);
  if (hit === undefined) {
    const [r, g, b] = hexToRgb(adapt(hex));
    hit = `rgba(${r},${g},${b},${alpha})`;
    rgbaCache.set(key, hit);
  }
  return hit;
}

/* 情绪定义用 Map 索引，不在每一行里对 7 个元素做一次 find */
let emotionIndex = new Map();
const EMOTION_FALLBACK = { label: "", hint: "", color: "#8d948c", branch: "none" };

function indexEmotions() {
  emotionIndex = new Map(((state.meta && state.meta.emotions) || []).map((e) => [e.key, e]));
}

function emotionMeta(key) {
  const hit = emotionIndex.get(key) || { ...EMOTION_FALLBACK, key, label: key };
  return { ...hit, color: adapt(hit.color) };
}

const sevColor = (n) => adapt(SEV_COLOR[n] || "#A6ACA4");
const sevLabel = (n) => SEV_LABEL[getLang()][n] || "";

/* --- 时间 ---------------------------------------------------------------- */

const locale = () => (getLang() === "zh" ? "zh-CN" : "en-US");

function fmtTime(iso) {
  return timeLabels(iso).time;
}

function fmtDay(iso) {
  return timeLabels(iso).day;
}

/* toLocaleTimeString / toLocaleDateString 每次调用都要重建一个 Intl 格式化器，
一条记录要两次，上千条就是几千次——这是长列表里最贵的一块。
格式化器按语言缓存，格式化结果再按 ISO 串缓存（记录的时间不会变）。
代号里带分钟桶，因为「今天 / 昨天」跨零点之后会变。 */
let fmt = { gen: "" };
const labelCache = new Map();

const dayStamp = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

function formatters() {
  const lang = getLang();
  const gen = `${lang}|${Math.floor(Date.now() / 60000)}`;
  if (fmt.gen === gen) return fmt;
  const loc = lang === "zh" ? "zh-CN" : "en-US";
  const now = new Date();
  fmt = {
    gen,
    time: new Intl.DateTimeFormat(loc, { hour: "2-digit", minute: "2-digit", hour12: lang !== "zh" }),
    date: new Intl.DateTimeFormat(loc, { month: "short", day: "numeric" }),
    today: dayStamp(now),
    yesterday: dayStamp(new Date(now.getTime() - 864e5)),
  };
  labelCache.clear();
  return fmt;
}

function timeLabels(iso) {
  const f = formatters();
  const hit = labelCache.get(iso);
  if (hit) return hit;
  const d = new Date(iso);
  const stamp = dayStamp(d);
  const zh = getLang() === "zh";
  let day;
  if (stamp === f.today) day = zh ? "今天" : "Today";
  else if (stamp === f.yesterday) day = zh ? "昨天" : "Yesterday";
  else day = f.date.format(d);
  const out = { day, time: f.time.format(d) };
  labelCache.set(iso, out);
  return out;
}

function sevMeter(n, color = sevColor(n)) {
  const bars = [1, 2, 3, 4, 5].map((i) => `<i class="${n && i <= n ? "on" : ""}"></i>`).join("");
  return `<span class="sev-meter" style="--sev:${color}">${bars}</span>`;
}

/* --- 反馈 ---------------------------------------------------------------- */

let toastTimer = null;
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2300);
}

/* 视图整块重绘，不能把 #view 本身做成 live region（屏幕阅读器会复述整页），
所以换成一个只播报标题的隐藏区域。 */
function announce(text) {
  const node = $("#announcer");
  if (node) node.textContent = text;
}

/* ---------------------------------------------------------------- 主题 */

const theme = { pref: "auto", resolved: "light" };

/* 语音识别语言。中/英/俄三档，和界面语言解耦：俄语用户可以用俄语说话。 */
const VOICE_LANGS = [
  { key: "zh-CN", label: "中文" },
  { key: "en-US", label: "EN" },
  { key: "ru-RU", label: "Рус" },
];
const voice = { lang: "" };
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

const THEME_ICON = {
  light: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`,
  dark: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>`,
  auto: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor"/></svg>`,
};

const THEME_BAR = { light: "#F6F3EC", dark: "#141815" };

function applyTheme() {
  theme.resolved = theme.pref === "auto" ? (darkQuery.matches ? "dark" : "light") : theme.pref;
  document.documentElement.dataset.theme = theme.resolved;
  /* 手机状态栏 / 地址栏的颜色跟着实际主题走。原来是两条带 media 的 theme-color，
  系统是深色而用户手动选了浅色时，状态栏还会按系统那条走，接缝很明显。 */
  const bar = document.querySelector('meta[name="theme-color"]');
  if (bar) bar.setAttribute("content", THEME_BAR[theme.resolved]);
  const btn = $("#theme-btn");
  if (btn) {
    btn.innerHTML = THEME_ICON[theme.pref];
    btn.setAttribute("aria-label", t(`theme.${theme.pref}`));
    btn.title = t(`theme.${theme.pref}`);
  }
}

function cycleTheme() {
  const order = ["auto", "light", "dark"];
  theme.pref = order[(order.indexOf(theme.pref) + 1) % order.length];
  try {
    localStorage.setItem("tether.theme", theme.pref);
  } catch {
    /* 无痕模式：只在本次会话生效 */
  }
  applyTheme();
  render();
  toast(t(`theme.${theme.pref}`));
}

darkQuery.addEventListener("change", () => {
  if (theme.pref !== "auto") return;
  applyTheme();
  render();
});

/* --------------------------------------------------------------- 数据层 */

/* 后端按 (severity IS NULL), severity DESC 排；本地新增/改动后用同一规则重排，
否则新记录会插在「最需要被注意的」最前面。 */
function sortEntries() {
  state.entries.sort((a, b) => {
    const an = a.severity == null;
    const bn = b.severity == null;
    if (an !== bn) return an ? 1 : -1;
    if (!an && b.severity !== a.severity) return b.severity - a.severity;
    return new Date(b.occurred_at) - new Date(a.occurred_at);
  });
}

async function loadMeta() {
  state.meta = await api.meta();
  indexEmotions();
  // 颜色缓存按 hex 存，换语言重拉 meta 不会变色，但情绪集合变了就该清
  colorCache.clear();
  rgbaCache.clear();
}

async function loadAccount() {
  state.account = await api.account();
  const chip = $("#plan-chip");
  const pro = !!(state.account && state.account.pro);
  chip.hidden = !pro;
  chip.textContent = pro ? "PRO" : "";
}

async function loadEntries() {
  const res = await api.entries({ sort: "severity", limit: ENTRY_LIMIT });
  state.entries = res.items;
  state.total = res.total;
}

/* 记录一变，总结就过期。原来只在切换统计窗口时重拉，
于是「记几条 → 回总结页」看到的还是进页面时那份数字。 */
function invalidateInsights() {
  state.insights = null;
  state.stats = null;
  state.aiError = "";
}

/* 快速来回点 14 / 30 天时，慢的那个响应不该覆盖后点的那个 */
let insightsSeq = 0;
/* AI 分析同理：切了窗口或重发请求，旧的那份回来就该丢掉 */
let aiSeq = 0;

async function loadInsights() {
  const seq = ++insightsSeq;
  const days = state.insightsDays;
  const [ins, stats] = await Promise.all([api.insights(days), api.stats(days)]);
  if (seq !== insightsSeq) return false;
  state.insights = ins;
  state.stats = stats;
  /* 后端不在这里同步等模型：它只告诉前端「还没分析过」，
     真正的生成交给 runAnalysis，页面因此能立刻出骨架。 */
  if (ins.ai && ins.ai.status === "pending") runAnalysis();
  return true;
}

async function runAnalysis({ force = false } = {}) {
  if (!state.insights) return;
  const days = state.insightsDays;
  const seq = ++aiSeq;
  state.aiBusy = true;
  state.aiError = "";
  if (state.view === "insights") render();
  try {
    const payload = await api.analyzeInsights(days, force);
    if (seq !== aiSeq || days !== state.insightsDays) return;
    state.insights = payload;
    if (payload.ai && payload.ai.status === "error") state.aiError = payload.ai.message || "";
  } catch (err) {
    if (seq !== aiSeq) return;
    state.aiError = err.message;
  } finally {
    if (seq === aiSeq) {
      state.aiBusy = false;
      if (state.view === "insights") render();
    }
  }
}

async function refresh({ entries = true, account = true, insights = false } = {}) {
  const jobs = [];
  if (entries) jobs.push(loadEntries());
  if (account) jobs.push(loadAccount());
  if (insights) jobs.push(loadInsights());
  await Promise.all(jobs);
}

/* ---------------------------------------------------------------- 路由 */

function hashView() {
  const raw = (location.hash || "").replace("#", "");
  return VIEWS.includes(raw) ? raw : "now";
}

/* 每个视图记住自己的滚动位置：从记录列表翻到一半去看总结，回来还在原处。
视图是整块 innerHTML 重绘，浏览器自带的滚动恢复帮不上忙。 */
const scrollMemo = { now: 0, log: 0, insights: 0, me: 0 };

function setView(view, { scroll = "restore" } = {}) {
  if (state.view !== view) scrollMemo[state.view] = window.scrollY;
  state.view = view;
  $$(".tab").forEach((tab) => {
    const on = tab.dataset.view === view;
    tab.classList.toggle("on", on);
    if (on) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  render();
  announce(t(`nav.${view}`));
  if (scroll === "top") window.scrollTo(0, 0);
  else if (scroll === "restore") window.scrollTo(0, scrollMemo[view] || 0);
}

async function nav(view) {
  if (view === state.view) {
    if (!overlay.hidden) {
      closeOverlay();
      return;
    }
    // 再点一次当前 tab 就回顶部，和手机上原生应用的习惯一致
    scrollMemo[view] = 0;
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: smooth ? "smooth" : "auto" });
    return;
  }
  history.pushState({ view }, "", `#${view}`);
  setView(view);
  await ensureViewData(view);
}

async function ensureViewData(view) {
  try {
    if (view === "insights" && !state.insights) {
      await loadInsights();
      if (state.view === "insights") render();
    }
    if (view === "log" && !state.entries.length && state.total === 0) {
      await loadEntries();
      if (state.view === "log") render();
    }
  } catch (err) {
    toast(err.message);
  }
}

window.addEventListener("popstate", (event) => {
  const st = event.state || {};
  if (pendingPop > 0) {
    /* 这一下 pop 是关浮层时我们自己发的，只为了把浮层那条历史记录摘掉。
    如果不认领它：用户按 Esc 之后马上点别的 tab，pop 会在导航之后才到，
    于是刚打开的视图又被退回上一个。 */
    pendingPop -= 1;
    return;
  }
  if (overlayInHistory) {
    // 后退键优先关浮层，而不是退出应用
    destroyOverlay();
    // 浮层里的改动已经同步进 state.entries，这里只补一下账号里的记录数
    render();
    loadAccount().catch(() => {});
    return;
  }
  const view = st.view || hashView();
  if (view !== state.view) {
    setView(view);
    ensureViewData(view);
  }
});

/* ---------------------------------------------------------------- 渲染 */

function loadingBlock() {
  return `<div class="stack gap-12" style="padding-top:20px">
    <div class="skeleton" style="width:52%"></div>
    <div class="skeleton" style="width:80%;height:22px"></div>
    <div class="skeleton" style="height:96px;border-radius:18px"></div>
  </div>`;
}

function render() {
  const view = $("#view");
  if (!state.meta) {
    view.innerHTML = loadingBlock();
    return;
  }
  if (state.view === "now") view.innerHTML = viewNow();
  else if (state.view === "log") view.innerHTML = viewLog();
  else if (state.view === "insights") view.innerHTML = viewInsights();
  else view.innerHTML = viewMe();
}

/* ------------------------------------------------------------- 现在视图 */

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return t("now.greeting_night");
  if (hour < 12) return t("now.greeting_morning");
  if (hour < 18) return t("now.greeting_afternoon");
  return t("now.greeting_evening");
}

function emotionTile(emotion, primary) {
  const tint = rgba(emotion.color, primary ? 0.13 : 0.08);
  const branch = BRANCH_LABEL[emotion.branch] ? BRANCH_LABEL[emotion.branch][getLang()] : "";
  return `<button class="emo-tile" data-act="pick" data-emotion="${emotion.key}"
      style="--emo:${emotion.color};--emo-tint:${tint}">
      <span class="emo-label"><span class="emo-dot"></span>${esc(emotion.label)}</span>
      <span class="emo-hint">${esc(emotion.hint)}</span>
      ${primary && branch ? `<span class="emo-branch">${esc(branch)}</span>` : ""}
    </button>`;
}

function entryRow(entry) {
  const meta = emotionMeta(entry.emotion);
  const sev = entry.severity;
  const tags = [];
  if (entry.intervention_branch && entry.intervention_branch !== "none") {
    tags.push(
      `<span class="tag ${entry.intervention_completed ? "moss" : ""}">${
        entry.intervention_completed ? t("entry.iv_done") : t("entry.iv_skip")
      }</span>`
    );
  }
  if (entry.relief) tags.push(`<span class="tag">${t(`entry.relief_${entry.relief}`)}</span>`);

  return `<button class="entry ${sev >= 4 ? "sev-high" : ""}" data-act="edit-entry" data-id="${entry.id}"
      style="--sev:${sevColor(sev)};--emo:${meta.color}">
      <span class="sev-col">
        <span class="sev-num ${sev ? "" : "none"}">${sev || "—"}</span>
        ${sevMeter(sev)}
      </span>
      <span>
        <span class="entry-head">
          <span class="entry-emotion"><span class="dot"></span>${esc(meta.label)}</span>
          <span class="entry-time">${fmtDay(entry.occurred_at)} ${fmtTime(entry.occurred_at)}</span>
        </span>
        <span class="entry-cause ${entry.cause ? "" : "missing"}">${esc(entry.cause) || t("entry.unfilled")}</span>
        ${tags.length ? `<span class="entry-meta">${tags.join("")}</span>` : ""}
      </span>
    </button>`;
}

function viewNow() {
  const primaryKeys = ["anger", "sadness", "despair"];
  const emotions = (state.meta && state.meta.emotions) || [];
  const primary = emotions.filter((e) => primaryKeys.includes(e.key)).map((e) => ({ ...e, color: adapt(e.color) }));
  const secondary = emotions.filter((e) => !primaryKeys.includes(e.key)).map((e) => ({ ...e, color: adapt(e.color) }));
  const watch = state.entries.filter((e) => (e.severity || 0) >= 4).slice(0, 3);
  const empty = state.entries.length === 0;

  return `<div class="stack gap-24">
    <div class="stack gap-4">
      <span class="eyebrow">${greeting()}</span>
      <h1 class="display">${t("now.question")}</h1>
      <p class="small">${t("now.hint")}</p>
    </div>

    <div class="stack gap-12">
      <div class="row-between">
        <span class="eyebrow">${t("now.primary_label")}</span>
        <span class="tiny">${t("now.primary_note")}</span>
      </div>
      <div class="emo-primary">${primary.map((e) => emotionTile(e, true)).join("")}</div>
      <div class="emo-secondary">${secondary.map((e) => emotionTile(e, false)).join("")}</div>
    </div>

    ${
      empty
        ? `<div class="empty stack gap-12" style="align-items:center">
          <h2 class="display sm">${t("now.empty_title")}</h2>
          <p class="small" style="max-width:34ch">${t("now.empty_body")}</p>
          <button class="btn soft sm" data-act="seed">${t("now.load_demo")}</button>
            <p class="tiny">${t("now.demo_note")}</p>
          </div>`
        : watch.length
        ? `<div class="stack gap-12">
          <div class="row-between">
            <span class="eyebrow">${t("now.watch")}</span>
            <span class="tiny">${t("now.watch_note")}</span>
          </div>
          <div class="entry-list">${watch.map(entryRow).join("")}</div>
          <button class="btn ghost sm" data-act="nav" data-view="log">${t("now.all_logs")}</button>
        </div>`
        : `<div class="stack gap-12">
            <div class="row-between">
<span class="eyebrow">${t("now.recent")}</span>
<button class="chip" data-act="nav" data-view="log">${t("now.all_logs")}</button>
</div>
<div class="entry-list">${state.entries.slice(0, 3).map(entryRow).join("")}</div>
</div>`
    }

    <p class="tiny" style="text-align:center;padding-top:4px">${t("now.footer")}</p>
  </div>`;
}

/* ------------------------------------------------------------- 记录视图 */

/* 本地只缓存了前 ENTRY_LIMIT 条。没有筛选的时候，计数用服务端给的 total，
否则记录过万之后标题会一直停在 1000 条。 */
function countLabel(list) {
  const plain = state.filter.emotion === "all" && !state.filter.unfilled;
  const n = plain && state.total > state.entries.length ? state.total : list.length;
  return t("log.count", { n });
}

function filteredEntries() {
  let list = state.entries;
  if (state.filter.emotion !== "all") list = list.filter((e) => e.emotion === state.filter.emotion);
  if (state.filter.unfilled) list = list.filter((e) => !e.cause || !e.severity);
  if (state.filter.sort === "time") {
    list = list.slice().sort((a, b) => new Date(b.occurred_at) - new Date(a.occurred_at));
  }
  return list;
}

/* 按时间排序时插日期分隔，按严重程度排序时不插——那时顺序本来就不是按天走的 */
function entryListHtml(list) {
  if (!list.length) {
    return `<div class="empty stack gap-8">
      <h2 class="display sm">${t("log.empty_title")}</h2>
      <p class="small">${t("log.empty_body")}</p>
    </div>`;
  }
  const page = list.slice(0, state.logLimit);
  let html = "";
  let lastDay = null;
  for (const entry of page) {
    if (state.filter.sort === "time") {
      const day = fmtDay(entry.occurred_at);
      if (day !== lastDay) {
        html += `${lastDay ? "</div>" : ""}<div class="day-head">${esc(day)}</div><div class="entry-list">`;
        lastDay = day;
      }
    }
    html += entryRow(entry);
  }
  if (state.filter.sort === "time") html += "</div>";
  else html = `<div class="entry-list">${html}</div>`;

  const rest = list.length - page.length;
  if (rest > 0) {
    html += `<button class="btn ghost block" style="margin-top:12px" data-act="more">${t("log.more", {
      n: Math.min(rest, PAGE_SIZE),
    })}</button>`;
  }
  return html;
}

function viewLog() {
  const emotions = (state.meta && state.meta.emotions) || [];
  const list = filteredEntries();

  const filterChips = [
    `<button class="chip ${state.filter.emotion === "all" ? "on" : ""}" data-act="filter" data-emotion="all">${t("log.all")}</button>`,
    ...emotions.map(
      (e) =>
        `<button class="chip ${state.filter.emotion === e.key ? "on" : ""}" data-act="filter" data-emotion="${e.key}"><span class="dot" style="background:${adapt(e.color)}"></span>${esc(e.label)}</button>`
    ),
  ].join("");

  return `<div class="stack gap-16">
    <div class="row-between">
      <h1 class="display sm">${t("log.title")}</h1>
            <span class="tiny" id="log-count">${countLabel(list)}</span>
    </div>

    <div class="chip-row" id="log-filters">${filterChips}</div>

    <div class="chip-row" id="log-sorts">
      <button class="chip ${state.filter.sort === "severity" ? "on" : ""}" data-act="sort" data-sort="severity">${t("log.sort_severity")}</button>
      <button class="chip ${state.filter.sort === "time" ? "on" : ""}" data-act="sort" data-sort="time">${t("log.sort_time")}</button>
      <button class="chip ${state.filter.unfilled ? "on" : ""}" data-act="unfilled">${t("log.unfilled")}</button>
    </div>

    <div id="log-body">${entryListHtml(list)}</div>
  </div>`;
}

/* 筛选只换列表，不整页重绘——否则每点一下筛选都会把页面滚回顶部 */
function renderLogList() {
  const body = $("#log-body");
  if (!body) {
    render();
    return;
  }
  const list = filteredEntries();
  body.innerHTML = entryListHtml(list);
    $("#log-count").textContent = countLabel(list);
  $$("#log-filters .chip").forEach((chip) =>
    chip.classList.toggle("on", chip.dataset.emotion === state.filter.emotion)
  );
  $$("#log-sorts .chip").forEach((chip) => {
    if (chip.dataset.sort) chip.classList.toggle("on", chip.dataset.sort === state.filter.sort);
    else chip.classList.toggle("on", state.filter.unfilled);
  });
}

/* ------------------------------------------------------------- 总结视图 */

function statTile(value, label) {
  return `<div class="stat"><div class="stat-value">${value}</div><div class="stat-label">${label}</div></div>`;
}

/* 每日最高强度走势：用已经在本地的记录算，不多发一次请求 */
function sparkline(days) {
  const byDay = new Map();
  for (const e of state.entries) {
    if (!e.severity) continue;
    byDay.set(e.local_date, Math.max(byDay.get(e.local_date) || 0, e.severity));
  }
  if (!byDay.size) return "";

  const cells = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i -= 1) {
    const d = new Date(today.getTime() - i * 864e5);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const sev = byDay.get(key) || 0;
    const height = sev ? 6 + (sev / 5) * 40 : 3;
    cells.push(
      `<i class="${sev >= 4 ? "hot" : sev ? "" : "empty"}" style="--h:${height}px" title="${key} · ${sev || "—"}"></i>`
    );
  }
  const start = new Date(today.getTime() - (days - 1) * 864e5);
  return `<div class="card stack gap-8">
    <div class="row-between">
      <span class="eyebrow">${t("ins.spark")}</span>
      <span class="tiny">${t("ins.spark_note")}</span>
    </div>
    <div class="spark">${cells.join("")}</div>
    <div class="spark-axis">
      <span class="tiny">${start.toLocaleDateString(locale(), { month: "short", day: "numeric" })}</span>
      <span class="tiny">${t("ins.spark_today")}</span>
    </div>
  </div>`;
}

function distRow(d, maxCount) {
  const width = Math.max(6, (d.count / maxCount) * 100);
  return `<div class="bar-row" style="--emo:${adapt(d.color)}">
    <span class="bar-label"><span class="dot"></span>${esc(d.label)}</span>
    <span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span>
    <span class="bar-count">${d.count}</span>
  </div>`;
}

function card(eyebrow, inner, extraClass = "") {
  return `<div class="card stack gap-12 ${extraClass}">
    <span class="eyebrow">${eyebrow}</span>
    ${inner}
  </div>`;
}

function basicBlock(stats) {
  if (!stats) return "";
  const dist = stats.emotion_distribution || [];
  const causes = stats.top_causes || [];
  const maxCount = Math.max(1, ...dist.map((d) => d.count));
  const causeTags = causes.map((c) => `<span class="tag">${esc(c.label)} · ${c.count}</span>`).join("");

  return `<div class="stack gap-16">
    <div class="stat-row">
      ${statTile(stats.entries, t("ins.stat_entries"))}
      ${statTile(stats.avg_severity ?? "—", t("ins.stat_avg"))}
      ${statTile(stats.days_logged, t("ins.stat_days"))}
    </div>
    ${sparkline(state.insightsDays)}
    ${dist.length ? card(t("ins.dist"), dist.map((d) => distRow(d, maxCount)).join("")) : ""}
    ${causes.length ? card(t("ins.causes"), `<div class="chip-row">${causeTags}</div>`) : ""}
  </div>`;
}

function patternCard(p, sample = false, focus = false) {
  const color = emotionMeta(p.dominant_emotion).color;
  const where = p.band
    ? getLang() === "zh"
      ? `${p.top_slot_label} ${p.band.start}—${p.band.end} 点`
      : `${p.top_slot_label} ${p.band.start}:00–${p.band.end}:00`
    : `${p.top_slot_label} · ${p.top_slot_range}`;
  return `<div class="pattern" style="--acc:${color}">
    <div class="row-between">
      <span class="eyebrow">${esc(where)}</span>
      <span class="row" style="gap:6px">
        ${focus ? `<span class="tag ai">${t("ai.focus")}</span>` : ""}
        ${sample ? `<span class="tag moss">${t("ins.sample")}</span>` : ""}
        <span class="tag">${p.count}</span>
      </span>
    </div>
    <p class="pattern-quote">${esc(p.text || "")}</p>
    <div class="pattern-foot">
      <span class="tag moss">${esc(p.dominant_emotion_label)}</span>
      ${p.kind_label ? `<span class="tag">${esc(p.kind_label)}</span>` : ""}
    </div>
  </div>`;
}

function trendCard(trend) {
  const arrow = { down: "↓", up: "↑", flat: "→" }[trend.direction] || "→";
  const delta = trend.delta > 0 ? `+${trend.delta}` : `${trend.delta}`;
  return `<div class="card stack gap-8">
    <div class="row-between">
      <span class="eyebrow">${t("ins.trend")}</span>
      <span class="delta-pill ${trend.direction}">${arrow} ${delta}</span>
    </div>
    <div class="row" style="gap:18px">
      <div class="stack gap-4">
        <span class="display sm" style="margin:0">${trend.this_week}</span>
        <span class="tiny">${t("ins.this_week")}</span>
      </div>
      <div class="stack gap-4" style="opacity:.6">
        <span class="display sm" style="margin:0">${trend.last_week}</span>
        <span class="tiny">${t("ins.last_week")}</span>
      </div>
    </div>
    ${trend.text ? `<p class="body-lg">${esc(trend.text)}</p>` : ""}
  </div>`;
}

function efficacyRow(e) {
  return `<div class="stack gap-4">
    <div class="row-between">
      <span class="small">${esc(e.label)}</span>
      <span class="small num">${e.helped}/${e.tried}</span>
    </div>
    <div class="progress-line"><i style="width:${e.rate}%"></i></div>
    ${e.text ? `<p class="tiny">${esc(e.text)}</p>` : ""}
  </div>`;
}

function reminderRow(text, i) {
  return `<div class="row gap-8" style="align-items:flex-start">
    <span class="tag moss">${i + 1}</span>
    <p class="small" style="color:var(--ink-2)">${esc(text)}</p>
  </div>`;
}

/* 样本不够时不硬凑结论，只显示离「看得出模式」还差多少 */
function sampleGate(sample) {
  const have = sample ? sample.entries : 0;
  const min = sample ? sample.needed : 20;
  const missing = sample ? sample.missing : 20;
  return `<div class="card stack gap-12">
    <div class="row-between">
      <span class="eyebrow">${t("ins.patterns")}</span>
      <span class="tag warn">${t("ins.need_more", { n: missing })}</span>
    </div>
    <div class="progress-line"><i style="width:${Math.min(100, (have / min) * 100)}%"></i></div>
    <p class="small">${t("ins.need_more_body", { min, have })}</p>
    <p class="tiny">${t("ins.demo")}</p>
  </div>`;
}

function patternsHead() {
  return `<div class="row-between">
    <span class="eyebrow">${t("ins.patterns")}</span>
    <span class="tiny">${t("ins.pattern_note")}</span>
  </div>`;
}

function lockedBlock(patterns) {
  const sample = patterns[0];
  return `<div class="stack gap-16">
    ${patternsHead()}
    ${sample ? patternCard(sample, true) : `<div class="card flat"><p class="small">${t("ins.no_pattern")}</p></div>`}
    <div class="card sheen stack gap-12">
      <div class="row" style="gap:8px">
        <span class="lock-badge">PRO</span>
        <span class="eyebrow">${t("ins.locked_title")}</span>
      </div>
      <p class="small" style="color:var(--ink-2)">${t("ins.locked_body")}</p>
      <button class="btn block" data-act="paywall">${t("ins.unlock")}</button>
    </div>
  </div>`;
}

/* ------------------------------------------------------------- AI 区块 */

function aiHeader(ins) {
  const ai = ins.ai || {};
  const canRerun = !state.aiBusy && ai.status === "ready";
  return `<div class="row-between">
    <span class="row" style="gap:8px">
      <span class="ai-dot" aria-hidden="true"></span>
      <span class="eyebrow">${t("ai.badge")}</span>
      <span class="tiny">${esc(ai.model || "")}</span>
    </span>
    ${canRerun ? `<button class="btn ghost sm" data-act="reanalyze">${t("ai.regenerate")}</button>` : ""}
  </div>`;
}

/* AI 只写正文，数字仍在下面的卡片里由代码渲染——所以骨架里也不许出数 */
function aiCard(ins) {
  const ai = ins.ai || {};
  const patterns = ins.patterns || [];

  if (state.aiBusy) {
    return `<div class="card sheen stack gap-12">
      <div class="row-between">
        <span class="eyebrow">${t("ai.summary")}</span>
        <span class="tag moss">${t("ai.analyzing", { days: state.insightsDays })}</span>
      </div>
      <div class="skeleton" style="width:62%;height:22px"></div>
      <div class="skeleton" style="width:94%"></div>
      <div class="skeleton" style="width:88%"></div>
      <div class="skeleton" style="width:76%"></div>
      <p class="tiny">${t("ai.analyzing_note")}</p>
    </div>`;
  }

  if (ai.status === "error") {
    return `<div class="card flat stack gap-10">
      <div class="row-between">
        <span class="eyebrow">${t("ai.badge")}</span>
        <span class="tag warn">${t("ai.fallback")}</span>
      </div>
      <p class="small">${t("ai.failed")}</p>
      ${state.aiError ? `<p class="tiny" style="word-break:break-word">${esc(state.aiError)}</p>` : ""}
      <div class="row"><button class="btn ghost sm" data-act="reanalyze">${t("ai.retry")}</button></div>
    </div>`;
  }

  /* off / insufficient 直接不出现：宁可少一块，也不给半成品 */
  if (ai.status !== "ready") return "";

  const focus = ai.focus || {};
  const focusPattern = patterns.find((p) => p.key === focus.key);
  const sample = ins.sample || {};

  return `<div class="card sheen stack gap-12">
    <div class="row-between">
      <span class="eyebrow">${t("ai.summary")}</span>
      <span class="tag moss">${t("ai.records", { n: sample.entries || 0 })}</span>
    </div>
    ${ai.headline ? `<p class="display sm" style="margin:0">${esc(ai.headline)}</p>` : ""}
    ${ai.summary ? `<p class="body-lg">${esc(ai.summary)}</p>` : ""}
    ${
      focusPattern && focus.why
        ? `<div class="ai-focus">
            <span class="tag ai">${t("ai.focus")}</span>
            <span class="small">${esc(focusPattern.label)} —— ${esc(focus.why)}</span>
          </div>`
        : ""
    }
    ${
      ai.next_step
        ? `<div class="ai-next">
            <span class="tag moss">${t("ai.next")}</span>
            <p class="small">${esc(ai.next_step)}</p>
          </div>`
        : ""
    }
    <p class="tiny">${t("ai.privacy")}</p>
  </div>`;
}

function proBlock(ins) {
  const patterns = ins.patterns || [];
  if (!(ins.sample && ins.sample.enough)) return sampleGate(ins.sample);
  if (ins.locked) return lockedBlock(patterns);

  const ai = ins.ai || {};
  const showAi = !!ai.enabled && ai.status !== "pro";
  const focusKey = (ai.focus && ai.focus.key) || "";
  const body = patterns.length
    ? `<div class="stack gap-12">${patterns.map((p) => patternCard(p, false, p.key === focusKey)).join("")}</div>`
    : `<div class="card flat"><p class="small">${t("ins.no_pattern")}</p></div>`;
  const efficacy = ins.efficacy || [];
  const reminders = ins.reminders || [];

  return `<div class="stack gap-16">
    ${showAi ? `<div class="stack gap-10">${aiHeader(ins)}${aiCard(ins)}</div>` : ""}
    <div class="stack gap-12">${patternsHead()}${body}</div>
    ${ins.peak ? `<div class="card flat"><p class="body-lg">${esc(ins.peak.text)}</p></div>` : ""}
    ${ins.trend ? trendCard(ins.trend) : ""}
    ${efficacy.length ? card(t("ins.efficacy"), efficacy.map(efficacyRow).join("")) : ""}
    ${reminders.length ? card(t("ins.reminders"), reminders.map(reminderRow).join("")) : ""}
  </div>`;
}

function viewInsights() {
  const ins = state.insights;
  return `<div class="stack gap-16">
    <div class="row-between">
      <h1 class="display sm">${t("ins.title")}</h1>
      <div class="chip-row">
        <button class="chip ${state.insightsDays === 14 ? "on" : ""}" data-act="window" data-days="14">${t("ins.window14")}</button>
        <button class="chip ${state.insightsDays === 30 ? "on" : ""}" data-act="window" data-days="30">${t("ins.window30")}</button>
      </div>
    </div>
    ${
      !ins
        ? `<div class="stack gap-12">
            <div class="skeleton" style="height:76px;border-radius:16px"></div>
            <div class="skeleton" style="height:106px;border-radius:22px"></div>
            <div class="skeleton" style="height:150px;border-radius:22px"></div>
          </div>`
        : `<div class="stack gap-24">
          <div class="stack gap-12">
            <span class="eyebrow">${t("ins.basic")}</span>
            ${basicBlock(state.stats)}
          </div>
          ${proBlock(ins)}
        </div>`
    }
  </div>`;
}

/* ---------------------------------------------------------------- 我的 */

function viewMe() {
  const acc = state.account || { pro: false, entries: 0, user: {} };
  const regions = (state.meta && state.meta.regions) || [];
  const username = (acc.user && acc.user.username) || "";
  return `<div class="stack gap-16">
    <h1 class="display sm">${t("me.title")}</h1>

    <div class="card stack gap-8">
      <span class="eyebrow">${t("me.account")}</span>
      <div class="list-row">
        <div><div class="label">${esc(username)}</div></div>
      </div>
      <button class="list-row" data-act="change-password">
        <div><div class="label">${t("me.change_password")}</div></div>
      </button>
      <button class="list-row" data-act="logout">
        <div><div class="label" style="color:var(--brick)">${t("auth.logout")}</div></div>
      </button>
    </div>

    <div class="plan-card stack gap-12">
      <div class="row-between">
        <span class="eyebrow">${acc.pro ? t("me.plan_pro") : t("me.plan_free")}</span>
        ${acc.pro ? `<span class="lock-badge">PRO</span>` : ""}
      </div>
      <p class="body-lg">${acc.pro ? t("me.plan_pro_body") : t("me.plan_free_body")}</p>
      ${acc.pro ? "" : `<button class="btn block" data-act="paywall">${t("me.redeem")}</button>`}
    </div>

    <div class="card stack gap-8">
      <span class="eyebrow">${t("me.settings")}</span>
      <div class="list-row">
        <div><div class="label">${t("me.lang")}</div></div>
        <div class="chip-row">
          <button class="chip ${getLang() === "zh" ? "on" : ""}" data-act="lang" data-lang="zh">中文</button>
          <button class="chip ${getLang() === "en" ? "on" : ""}" data-act="lang" data-lang="en">EN</button>
        </div>
      </div>
      <div class="list-row">
        <div><div class="label">${t("me.theme")}</div></div>
        <div class="chip-row">
          ${["auto", "light", "dark"]
          .map(
(key) =>
`<button class="chip ${theme.pref === key ? "on" : ""}" data-act="set-theme" data-theme="${key}">${t(`theme.${key}`)}</button>`
)
.join("")}
        </div>
      </div>
      <div class="list-row">
        <div><div class="label">${t("me.voice")}</div></div>
        <div class="chip-row">
          ${VOICE_LANGS.map(
            (v) =>
              `<button class="chip ${(voice.lang || (getLang() === "zh" ? "zh-CN" : "en-US")) === v.key ? "on" : ""}"
                data-act="set-voice" data-lang="${v.key}">${v.label}</button>`
          ).join("")}
        </div>
      </div>
      <div class="list-row">
        <div><div class="label">${t("me.region")}</div></div>
        <select data-act="region" aria-label="${t("me.region")}" style="width:auto;min-width:128px">
          ${regions
          .map((r) => `<option value="${r.key}" ${acc.region === r.key ? "selected" : ""}>${esc(r.label)}</option>`)
          .join("")}
        </select>
      </div>
    </div>

    <div class="card stack gap-8">
      <span class="eyebrow">${t("me.data")}</span>
      <button class="list-row" data-act="export" data-format="json">
        <div><div class="label">${t("me.export_json")}</div><div class="sub">${t("me.entry_count", { n: acc.entries ?? 0 })}</div></div>
        <span class="tiny">↓</span>
      </button>
      <button class="list-row" data-act="export" data-format="csv">
        <div><div class="label">${t("me.export_csv")}</div><div class="sub">UTF-8 · Excel</div></div>
        <span class="tiny">↓</span>
      </button>
      <button class="list-row" data-act="seed">
        <div><div class="label">${t("me.seed")}</div><div class="sub">${t("me.entry_count", { n: 28 })}</div></div>
        <span class="tiny">→</span>
      </button>
      <button class="list-row" data-act="reset">
        <div><div class="label" style="color:var(--brick)">${t("me.reset")}</div></div>
        <span class="tiny">×</span>
      </button>
    </div>

    <button class="card list-row" data-act="open-crisis" style="text-align:left">
      <div><div class="label">${t("crisis.title")}</div><div class="sub">${t("crisis.note")}</div></div>
      <span class="tiny">→</span>
    </button>

    <div class="stack gap-4" style="padding:4px 4px 0">
      <span class="eyebrow">${t("me.about")}</span>
      <p class="small">${esc(state.meta ? state.meta.disclaimer : "")}</p>
      <p class="tiny">${t("me.version")}</p>
    </div>
  </div>`;
}

/* --------------------------------------------------------------- 浮层壳 */

const overlay = $("#overlay");
const FOCUSABLE = 'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

let overlayInHistory = false;
let pendingPop = 0; // 关浮层时我们自己发出去、等着被认领的 history.back()
let lastFocused = null;
let lockedScrollY = 0;

function openOverlay(inner, { focus = true, label = "" } = {}) {
  const first = overlay.hidden;
  if (first) {
    lastFocused = document.activeElement;
    lockedScrollY = window.scrollY;
    document.body.style.top = `-${lockedScrollY}px`;
    document.body.classList.add("locked");
    // 浮层进历史栈：手机上的返回手势先关浮层，而不是退出应用
    history.pushState({ view: state.view, overlay: true }, "", location.hash || "#now");
    overlayInHistory = true;
  }
  /* role 直接挂在 #overlay 上。原来套了一层 display:contents 的 div，
  而 display:contents 会让元素连盒子一起消失，部分浏览器会把它的 role 一起丢掉。 */
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", label || t("common.close"));
  overlay.innerHTML = inner;
  overlay.hidden = false;
  if (focus) focusFirst();
}

function focusFirst() {
  const target =
    overlay.querySelector("[data-autofocus]") ||
    overlay.querySelector(".btn, .chip, textarea") ||
    overlay.querySelector(FOCUSABLE);
  if (target) target.focus({ preventScroll: true });
}

function destroyOverlay() {
  iv.token += 1;
  iv.nodes = null;
  overlayInHistory = false;
  overlay.hidden = true;
  overlay.innerHTML = "";
  overlay.removeAttribute("role");
  overlay.removeAttribute("aria-modal");
  overlay.removeAttribute("aria-label");
  document.body.classList.remove("locked");
  document.body.style.top = "";
  window.scrollTo(0, lockedScrollY);
  if (lastFocused && document.contains(lastFocused)) lastFocused.focus({ preventScroll: true });
  lastFocused = null;
}

function closeOverlay() {
  const popNeeded = overlayInHistory;
  destroyOverlay();
  if (popNeeded) {
    pendingPop += 1;
    history.back();
  }
}

function sheetClose(extra = "") {
  return `<button class="icon-btn" data-act="close" aria-label="${t("common.close")}" style="${extra}">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
  </button>`;
}

/* ------------------------------------------------------------ 确认对话框 */

/* 原生 window.confirm 在嵌入式 webview / 跨域 iframe 里会被静默吞掉——直接返回 false，
   于是「删除」和「清空数据」点了像没反应。改成应用内的一层确认卡，顺便和设计体系对齐。

   刻意不复用 #overlay：删除按钮在记录编辑浮层里，那层可能有用户还没保存的原因。
   另起一层叠在上面，取消后底下原封不动。 */
let confirmLayer = null;
let confirmResolver = null;
let confirmRestoreFocus = null;

function ensureConfirmLayer() {
  if (confirmLayer) return confirmLayer;
  confirmLayer = document.createElement("div");
  confirmLayer.className = "confirm-layer";
  confirmLayer.hidden = true;
  confirmLayer.innerHTML = `
    <div class="confirm-veil" data-act="confirm-cancel"></div>
    <div class="confirm-card" role="alertdialog" aria-modal="true">
      <h2 class="confirm-title"></h2>
      <p class="confirm-body small"></p>
      <div class="row" style="gap:10px;margin-top:18px">
        <button class="btn ghost block" data-act="confirm-cancel"></button>
        <button class="btn danger-solid block" data-act="confirm-ok"></button>
      </div>
    </div>`;
  document.body.appendChild(confirmLayer);
  return confirmLayer;
}

function settleConfirm(value) {
  const resolve = confirmResolver;
  confirmResolver = null;
  if (confirmLayer) confirmLayer.hidden = true;
  document.documentElement.classList.remove("confirm-open");
  /* 解开底下那层的惰性，焦点还回去 */
  if (overlay) overlay.inert = false;
  if (confirmRestoreFocus && document.contains(confirmRestoreFocus)) {
    confirmRestoreFocus.focus({ preventScroll: true });
  }
  confirmRestoreFocus = null;
  if (resolve) resolve(value);
}

function askConfirm({ title, body = "", ok, cancel = t("common.cancel") }) {
  settleConfirm(false); // 上一个没结算的按取消处理，避免两个确认卡叠着
  const layer = ensureConfirmLayer();
  confirmRestoreFocus = document.activeElement;
  layer.querySelector(".confirm-title").textContent = title;
  const bodyNode = layer.querySelector(".confirm-body");
  bodyNode.textContent = body;
  bodyNode.hidden = !body;
  /* 必须写 button[...]：遮罩层也带 data-act="confirm-cancel"，
     只按属性选会命中遮罩，文案写不进去、focus 也会落空。 */
  const okBtn = layer.querySelector('button[data-act="confirm-ok"]');
  const cancelBtn = layer.querySelector('button[data-act="confirm-cancel"]');
  okBtn.textContent = ok;
  cancelBtn.textContent = cancel;
  layer.hidden = false;
  document.documentElement.classList.add("confirm-open");
  /* 底下那层设成 inert：它只是被盖住了，不该还能用 Tab 走到、也不该被回车误触 */
  if (!overlay.hidden) overlay.inert = true;
  /* 等一帧再聚焦：层刚显示出来时同步 focus 会被丢掉 */
  requestAnimationFrame(() => cancelBtn.focus({ preventScroll: true }));
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

/* ------------------------------------------------------------- 干预流程 */

const iv = { entryId: null, data: null, step: 0, token: 0, relief: null, crisis: null, nodes: null, lastPct: -1 };

/* 建记录是写操作，必须防重入：网络慢的时候双击情绪瓦片会落下两条记录 */
let picking = false;

async function pickEmotion(emotionKey, node) {
  if (picking) return;
  picking = true;
  if (node) node.classList.add("pending");
  try {
    const res = await api.createEntry({ emotion: emotionKey, tz_offset: tzOffset() });
    state.entries.push(res.entry);
    sortEntries();
    state.total += 1;
    invalidateInsights();
    iv.entryId = res.entry.id;
    iv.data = res.intervention;
    iv.step = -1; // -1 = 先说那一句话，再进步骤
    iv.relief = null;
    iv.crisis = null;
    renderStep();
    picking = false; // 浮层已经盖住瓦片，危机资源可以慢慢补
    if (res.intervention.branch === "despair") {
      // 危机资源异步补进来，但脚本第一步不等它
      try {
        iv.crisis = await api.crisis((state.account && state.account.region) || "CN");
        const slot = overlay.querySelector("[data-crisis]");
        if (slot) slot.innerHTML = crisisBlock();
      } catch {
        iv.crisis = null;
      }
    }
  } catch (err) {
    toast(err.message);
  } finally {
    picking = false;
    if (node) node.classList.remove("pending");
  }
}

function nextStep() {
  iv.step += 1;
  renderStep();
}

function stepDots(current, total) {
  return `<div class="step-dots" aria-hidden="true">${[...Array(total)]
    .map((_, i) => `<i class="${i === current ? "on" : i < current ? "done" : ""}"></i>`)
    .join("")}</div>`;
}

/* 记下情绪之后的第一屏：只有一句话。

   这一步是为了「不管什么情绪，都先被回应一次」——愤怒给听息，挺好给祝福。
   不给表单、不给建议列表，就一句。想继续再点「开始」进 30 秒引导。 */
function renderSay() {
  const data = iv.data;
  const meta = emotionMeta(data.emotion);
  const tint = rgba(meta.color, 0.22);
  openOverlay(`<div class="focus say">
    <div class="focus-head">
      <span class="eyebrow" style="color:${meta.color}">${esc(meta.label)}</span>
      <button class="chip" data-act="iv-skip">${t("iv.skip")}</button>
    </div>
    <div class="focus-body">
      <div class="say-mark" style="--tint:${tint}"></div>
      <h2 class="focus-title say-title">${esc(data.say)}</h2>
      ${data.say_note ? `<p class="focus-text">${esc(data.say_note)}</p>` : ""}
      ${data.branch === "despair" ? `<div data-crisis>${crisisBlock()}</div>` : ""}
    </div>
    <div class="focus-foot stack gap-12">
      <button class="btn block" data-act="iv-next" data-autofocus>${t("iv.say_next")}</button>
      <p class="focus-hint">${t("iv.key_hint")}</p>
    </div>
  </div>`, { label: `${meta.label} · ${data.say}` });
}

function renderStep() {
  const data = iv.data;
  const steps = data.steps || [];
  iv.token += 1;

  if (iv.step < 0) {
    renderSay();
    return;
  }

  if (iv.step >= steps.length) {
    renderClosing();
    return;
  }

  const step = steps[iv.step];
  const token = iv.token;
  const isBreath = step.kind === "breath";
  const showCircle = step.kind === "prompt" || isBreath;
  const meta = emotionMeta(data.emotion);
  const tint = rgba(meta.color, 0.16);

  openOverlay(`<div class="focus ${data.branch === "despair" ? "despair" : ""}">
    <div class="focus-head">
      <div class="stack" style="gap:6px">
        <span class="eyebrow">${esc(data.branch_hint)}</span>
        ${stepDots(iv.step, steps.length)}
      </div>
      <button class="chip" data-act="iv-skip">${t("iv.skip")}</button>
    </div>

    <div class="focus-body">
      ${
        showCircle
      ? `<div class="breath-wrap">
<div class="breath-halo"></div>
<div class="breath-halo inner"></div>
<div class="breath-circle" style="--tint:${tint};--shadow-c:${rgba(meta.color, 0.42)}">
  <div class="phase" data-phase></div>
  <div class="count" data-count>${step.seconds || ""}</div>
</div>
</div>`
: ""
      }
      <h2 class="focus-title">${esc(step.title)}</h2>
      <p class="focus-text">${esc(step.body)}</p>
      ${step.foot ? `<p class="focus-foot-note">${esc(step.foot)}</p>` : ""}
      ${data.branch === "despair" ? `<div data-crisis>${crisisBlock()}</div>` : ""}
    </div>

    <div class="focus-foot stack gap-12">
      ${step.seconds ? `<div class="progress-line live"><i data-progress style="width:0%"></i></div>` : `<div style="height:6px"></div>`}
      <button class="btn block" data-act="iv-next" data-autofocus>${
        iv.step === steps.length - 1 ? t("iv.finish") : t("iv.next")
      }</button>
      <p class="focus-hint">${t("iv.key_hint")}</p>
    </div>
  </div>`, { label: `${data.branch_hint} · ${step.title}` });

  // 节点引用抓一次就够：原来每帧都要在浮层里做一次 querySelector
  iv.nodes = {
    progress: overlay.querySelector("[data-progress]"),
    count: overlay.querySelector("[data-count]"),
    phase: overlay.querySelector("[data-phase]"),
    circle: overlay.querySelector(".breath-circle"),
  };
  iv.lastPct = -1;

  if (isBreath) runBreath(step, token);
  else if (step.seconds) runCountdown(step, token);
}

/* 每帧写一次 width 就触发一次布局；取整到 1% 之后，30 秒的引导只写 100 次。
CSS 里 .progress-line.live 关掉了 transition——否则每帧都新开一段 0.35s 动画，
进度条会一直追在真实进度后面。 */
function setProgress(ratio) {
  const bar = iv.nodes && iv.nodes.progress;
  if (!bar) return;
  const pct = Math.round(Math.min(1, Math.max(0, ratio)) * 100);
  if (pct === iv.lastPct) return;
  iv.lastPct = pct;
  bar.style.width = `${pct}%`;
}

/* 计时统一走 rAF + 真实时间戳。原来用 await sleep(1000) 累加，
后台标签页的定时器被节流后 30 秒引导会拖成一分多钟，呼吸节律也跟着跑偏。 */
function ticker(totalSeconds, token, onFrame) {
  const start = performance.now();
  const total = totalSeconds * 1000;
  function frame(now) {
    if (token !== iv.token) return;
    const elapsed = Math.min(total, now - start);
    onFrame(elapsed / 1000, elapsed >= total);
    if (elapsed >= total) {
      nextStep();
      return;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function runCountdown(step, token) {
  const total = step.seconds;
  const { count: countEl, phase: phaseEl } = iv.nodes;
  if (phaseEl) phaseEl.textContent = "";
  ticker(total, token, (elapsed) => {
    const left = Math.max(0, Math.ceil(total - elapsed));
    if (countEl && countEl.textContent !== String(left)) countEl.textContent = left;
    setProgress(elapsed / total);
  });
}

function runBreath(step, token) {
  const { circle, phase: phaseEl, count: countEl } = iv.nodes;
  const pattern = step.pattern || { inhale: 4, exhale: 6 };
  const span = pattern.inhale + pattern.exhale;
  const cycles = step.cycles || Math.max(1, Math.round((step.seconds || 30) / span));

  const phases = [];
  let at = 0;
  for (let c = 0; c < cycles; c += 1) {
    for (const [name, seconds] of [["inhale", pattern.inhale], ["exhale", pattern.exhale]]) {
      phases.push({ name, seconds, start: at, end: at + seconds });
      at += seconds;
    }
  }
  const total = at;
  let active = -1;

  ticker(total, token, (elapsed) => {
    let index = phases.findIndex((p) => elapsed < p.end);
    if (index === -1) index = phases.length - 1;
    const phase = phases[index];
    if (index !== active) {
      active = index;
      if (circle) {
        circle.style.transitionDuration = `${phase.seconds}s`;
        circle.style.transform = phase.name === "inhale" ? "scale(1.02)" : "scale(0.80)";
      }
      if (phaseEl) phaseEl.textContent = t(phase.name);
    }
    const left = Math.max(1, Math.ceil(phase.end - elapsed));
    if (countEl && countEl.textContent !== String(left)) countEl.textContent = left;
    setProgress(elapsed / total);
  });
}

function crisisLines(lines) {
  return lines
    .map(
      (line) => `<div class="crisis-line">
        <div>
          <div class="name">${esc(line.name)}</div>
          <div class="contact">${esc(line.contact)}</div>
        </div>
        ${
          line.number
        ? `<a class="btn sm" href="tel:${esc(line.number)}">${t("iv.call")}</a>`
            : `<a class="btn sm ghost" href="${esc(line.url)}" target="_blank" rel="noopener" aria-label="${esc(line.name)}">↗</a>`
        }
      </div>`
    )
    .join("");
}

function crisisBlock() {
  if (!iv.crisis) {
    return `<div class="crisis-card stack gap-8" style="margin-top:16px">
      <span class="eyebrow">${t("crisis.title")}</span>
      <div class="skeleton" style="width:70%"></div>
      <div class="skeleton" style="width:45%"></div>
    </div>`;
  }
  return `<div class="crisis-card" style="margin-top:18px">
    <div class="row-between" style="margin-bottom:6px">
      <span class="eyebrow">${t("crisis.title")} · ${esc(iv.crisis.label)}</span>
      <span class="tag warn">${t("crisis.emergency")} ${esc(iv.crisis.emergency)}</span>
    </div>
    ${crisisLines(iv.crisis.lines)}
    <p class="tiny" style="margin-top:10px">${esc(iv.crisis.free_note)}</p>
    <button class="btn ghost sm" data-act="open-crisis" style="margin-top:12px;width:100%">${t("iv.crisis_cta")}</button>
  </div>`;
}

function renderClosing() {
  const data = iv.data;
  const meta = emotionMeta(data.emotion);
  const reliefChip = (value, label) =>
    `<button class="chip ${iv.relief === value ? "on" : ""}" data-act="relief" data-relief="${value}">${label}</button>`;

  openOverlay(`<div class="focus">
    <div class="focus-head">
      <span class="eyebrow">${esc(data.branch_hint)}</span>
      ${sheetClose()}
    </div>
    <div class="focus-body">
      <div class="breath-wrap" style="height:120px">
        <div class="breath-circle" style="width:96px;height:96px;transform:scale(1);--tint:${rgba(meta.color, 0.14)}">
          <div class="phase">${esc(meta.label)}</div>
        </div>
      </div>
      <h2 class="focus-title">${esc(data.title)}</h2>
      <p class="focus-text">${esc(data.closing)}</p>
      ${
        data.branch === "none"
          ? ""
        : `<div class="stack gap-8" style="margin-top:22px">
          <span class="eyebrow">${t("iv.relief_q")}</span>
          <div class="chip-row">
  ${reliefChip("better", t("iv.relief_better"))}
  ${reliefChip("same", t("iv.relief_same"))}
  ${reliefChip("worse", t("iv.relief_worse"))}
</div>
</div>`
      }
    </div>
    <div class="focus-foot stack gap-8">
      <button class="btn block" data-act="note" data-autofocus>${t("note.add")}</button>
      <button class="btn ghost block" data-act="close">${t("note.skip")}</button>
    </div>
  </div>`, { label: data.title });
}

async function setRelief(value) {
  iv.relief = value;
  // 先更新选中态，不等网络——这一步不该让用户盯着没反应的按钮
  $$(".chip[data-relief]", overlay).forEach((chip) => chip.classList.toggle("on", chip.dataset.relief === value));
  try {
    await api.recordIntervention(iv.entryId, { completed: true, relief: value });
    const entry = state.entries.find((e) => e.id === iv.entryId);
    if (entry) {
      entry.relief = value;
      entry.intervention_completed = true;
    }
  } catch (err) {
    toast(err.message);
  }
}

/* ----------------------------------------------------------- 记录编辑表 */

const sheet = { id: null, severity: null, fromIntervention: false };

async function openEditSheet(entryId, fromIntervention = false) {
  let entry = state.entries.find((e) => e.id === entryId);
  if (!entry) {
    try {
      entry = await api.entry(entryId);
    } catch (err) {
      toast(err.message);
      return;
    }
  }
  sheet.id = entry.id;
  sheet.severity = entry.severity;
  sheet.fromIntervention = fromIntervention;
  const meta = emotionMeta(entry.emotion);
  const cause = entry.cause || "";

  openOverlay(`<div class="sheet center">
    <div class="row-between" style="margin-bottom:16px">
      <div class="stack" style="gap:2px">
        <span class="eyebrow" style="color:${meta.color}">${esc(meta.label)}</span>
        <span class="tiny">${fmtDay(entry.occurred_at)} ${fmtTime(entry.occurred_at)}</span>
      </div>
      ${sheetClose()}
    </div>

    <div class="stack gap-12" style="margin-bottom:18px">
      <div class="row-between">
        <span class="field-label">${t("sev.title")}</span>
        <span class="tiny">${t("sev.sub")}</span>
      </div>
      <div class="sev-picker">
        ${[1, 2, 3, 4, 5]
        .map(
          (n) => `<button class="sev-opt ${sheet.severity === n ? "on" : ""}" data-act="sev" data-sev="${n}"
          aria-pressed="${sheet.severity === n}" ${!fromIntervention && n === (sheet.severity || 1) ? "data-autofocus" : ""}
          style="--sev:${sevColor(n)};--sev-tint:${rgba(SEV_COLOR[n], 0.12)}">
          <span class="sev-num">${n}</span>
<span class="sev-label">${sevLabel(n)}</span>
</button>`
          )
          .join("")}
      </div>
    </div>

    <div class="stack gap-8">
      <span class="field-label">${t("note.title")}</span>
      <div class="input-wrap">
        <span class="char-count" data-count-for="cause">${cause.length}/500</span>
        <textarea data-input="cause" ${fromIntervention ? "data-autofocus" : ""} placeholder="${t("note.placeholder")}" maxlength="500">${esc(cause)}</textarea>
        <button class="mic-btn" data-act="mic" aria-label="${t("note.voice")}">
          <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><rect x="9.2" y="3" width="5.6" height="10.5" rx="2.8" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6 11.5a6 6 0 0 0 12 0M12 17.5V21" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </button>
      </div>
      <p class="tiny">${t("note.sub")}</p>
      <div class="chip-row" style="margin-top:2px">
        ${[1, 2, 3, 4, 5, 6]
          .map((i) => {
            const text = t(`note.quick${i}`);
            return `<button class="chip ${cause.includes(text) ? "on" : ""}" data-act="quick" data-text="${esc(text)}">${esc(text)}</button>`;
          })
          .join("")}
      </div>
    </div>

    <div class="stack gap-8" style="margin-top:22px">
      <button class="btn block" data-act="save-note">${t("note.save")}</button>
      <button class="btn danger sm" data-act="delete-entry" data-id="${entry.id}">${t("common.delete")}</button>
    </div>
  </div>`, { label: `${meta.label} · ${t("note.title")}` });
}

function syncCharCount() {
  const textarea = overlay.querySelector('[data-input="cause"]');
  const counter = overlay.querySelector('[data-count-for="cause"]');
  if (!textarea || !counter) return;
  const len = textarea.value.length;
  counter.textContent = `${len}/500`;
  counter.classList.toggle("near", len > 450);
  $$(".chip[data-text]", overlay).forEach((chip) =>
    chip.classList.toggle("on", textarea.value.includes(chip.dataset.text))
  );
}

async function saveNote() {
  const button = overlay.querySelector('[data-act="save-note"]');
  if (button && button.classList.contains("busy")) return;
  const textarea = overlay.querySelector('[data-input="cause"]');
  const cause = textarea ? textarea.value.trim() : "";
  const payload = { cause };
  if (sheet.severity) payload.severity = sheet.severity;
  if (button) button.classList.add("busy");
  try {
    const updated = await api.patchEntry(sheet.id, payload);
    if (sheet.fromIntervention) {
      try {
        await api.recordIntervention(sheet.id, { completed: true, relief: iv.relief });
      } catch {
        /* 已记录过，忽略 */
      }
      /* PATCH 的返回值早于这次写入，干预状态得自己补上，
      否则列表里的「干预完成」标签要等下一次全量拉取才出现。 */
      updated.intervention_completed = true;
      if (iv.relief) updated.relief = iv.relief;
    }
    const index = state.entries.findIndex((e) => e.id === sheet.id);
    if (index >= 0) state.entries[index] = { ...state.entries[index], ...updated };
    sortEntries();
    invalidateInsights();
    closeOverlay();
    render();
    toast(t("note.saved"));
    // 本地已经改好，不必再整表重拉；只有账号里的记录数需要跟进
    loadAccount().catch(() => {});
  } catch (err) {
    if (button) button.classList.remove("busy");
    toast(err.message);
  }
}

/* --------------------------------------------------------------- 危机表 */

async function openCrisis() {
  const region = (state.account && state.account.region) || "US";
  openOverlay(`<div class="sheet center"><div class="stack gap-12">
    <div class="row-between"><h2 class="section-title">${t("crisis.title")}</h2>${sheetClose()}</div>
    <div class="skeleton" style="width:60%"></div>
    <div class="skeleton" style="height:70px;border-radius:16px"></div>
  </div></div>`, { label: t("crisis.title") });
  let data;
  try {
    data = await api.crisis(region);
  } catch (err) {
    toast(err.message);
    closeOverlay();
    return;
  }
  openOverlay(
    `<div class="sheet center">
      <div class="row-between" style="margin-bottom:14px">
        <div class="stack" style="gap:2px">
          <h2 class="section-title">${t("crisis.title")}</h2>
          <span class="tiny">${esc(data.label)}</span>
        </div>
        ${sheetClose()}
      </div>
      <p class="small" style="margin-bottom:14px">${t("crisis.sub")}</p>
      <div class="crisis-card">${crisisLines(data.lines)}</div>
      <div class="card flat" style="margin-top:14px">
        <div class="row-between">
          <span class="small">${t("crisis.emergency")}</span>
          <span class="tag warn">${esc(data.emergency)}</span>
        </div>
      </div>
      <a class="btn ghost block" style="margin-top:12px" href="https://findahelpline.com" target="_blank" rel="noopener">${t("crisis.find")} →</a>
      <p class="tiny" style="margin-top:14px">${t("crisis.note")}</p>
    </div>`,
    { focus: false, label: `${t("crisis.title")} · ${data.label}` }
  );
}

/* --------------------------------------------------------------- 付费墙 */


/* --------------------------------------------------------------- 语音 */

let recognition = null;

function startVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    toast(t("note.voice_off"));
    return;
  }
  const textarea = overlay.querySelector('[data-input="cause"]');
  const btn = overlay.querySelector(".mic-btn");
  if (recognition) {
    // 再点一次就停：录音状态必须能退出
    try {
      recognition.stop();
    } catch {
      /* 已经停了 */
    }
    if (btn && btn.classList.contains("rec")) {
      btn.classList.remove("rec");
      return;
    }
  } else {
    recognition = new SR();
    recognition.interimResults = false;
    recognition.continuous = false;
  }
  /* 识别语言单独一项：界面上没俄语，但俄语用户仍可以用俄语说话。
     没手动选过就跟着界面语言走。 */
  recognition.lang = voice.lang || (getLang() === "zh" ? "zh-CN" : "en-US");
  recognition.onresult = (event) => {
    const said = Array.from(event.results)
      .map((r) => r[0].transcript)
      .join(" ")
      .trim();
    if (textarea && said) {
      textarea.value = `${textarea.value ? textarea.value + " " : ""}${said}`.slice(0, 500);
      syncCharCount();
    }
  };
  recognition.onend = () => btn && btn.classList.remove("rec");
  recognition.onerror = () => btn && btn.classList.remove("rec");
  try {
    recognition.start();
    if (btn) btn.classList.add("rec");
  } catch {
    toast(t("note.voice_off"));
  }
}

/* --------------------------------------------------------------- 动作 */

async function doSeed() {
  try {
    const res = await api.seed(tzOffset());
    invalidateInsights();
    await refresh({ entries: true, account: true, insights: state.view === "insights" });
    render();
    toast(`${t("me.seed_done")} · ${res.entries}`);
  } catch (err) {
    toast(err.message);
  }
}

async function doReset() {
  const ok = await askConfirm({
    title: t("me.reset"),
    body: t("me.reset_confirm"),
    ok: t("me.reset_ok"),
  });
  if (!ok) return;
  try {
    await api.resetAll();
    invalidateInsights();
    await refresh({ entries: true, account: true });
    render();
    toast(t("common.deleted"));
  } catch (err) {
    toast(err.message);
  }
}

async function switchLang(lang) {
  if (lang === getLang()) return;
  try {
    localStorage.setItem("tether.lang", lang);
  } catch {
    /* 无痕模式 */
  }
  setLang(lang);
  setApiLang(lang);
  applyStaticText();
  applyTheme(); // 主题按钮的 aria-label 也要跟着换语言
  try {
    await api.setSettings({ lang });
  } catch {
    /* 本地优先 */
  }
  try {
    await loadMeta();
    invalidateInsights();
    labelCache.clear(); // 日期标签里有「今天 / 昨天」，换语言要重算
    await refresh({ entries: true, account: true, insights: state.view === "insights" });
  } catch (err) {
    toast(err.message);
  }
  render();
}


async function removeEntry(id) {
  const ok = await askConfirm({
    title: t("common.delete"),
    body: t("common.delete_confirm"),
    ok: t("common.delete"),
  });
  if (!ok) return;
  try {
    await api.deleteEntry(id);
    state.entries = state.entries.filter((e) => e.id !== id);
        state.total = Math.max(0, state.total - 1);
    invalidateInsights();
    closeOverlay();
    render();
    toast(t("common.deleted"));
    loadAccount().catch(() => {});
  } catch (err) {
    toast(err.message);
  }
}

/* 关浮层：干预里写过的 relief / 强度都已经同步进 state.entries 了，
所以这里只重绘 + 补一下账号里的记录数，不再整表重拉一遍。 */
function closeAndSync() {
  closeOverlay();
  render();
  loadAccount().catch(() => {
    /* 离线时保留本地状态 */
  });
}

const ACTIONS = {
  nav: (node, event) => {
    event.preventDefault();
    return nav(node.dataset.view);
  },
  pick: (node) => pickEmotion(node.dataset.emotion, node),
  "iv-next": () => nextStep(),
  "iv-skip": () => closeAndSync(),
  relief: (node) => setRelief(node.dataset.relief),
  note: () => openEditSheet(iv.entryId, true),
  close: () => closeAndSync(),
  "edit-entry": (node) => openEditSheet(Number(node.dataset.id), false),
  sev: (node) => {
    sheet.severity = Number(node.dataset.sev);
    $$(".sev-opt", overlay).forEach((opt) => {
      const on = Number(opt.dataset.sev) === sheet.severity;
      opt.classList.toggle("on", on);
      opt.setAttribute("aria-pressed", String(on));
    });
  },
  quick: (node) => {
    const textarea = overlay.querySelector('[data-input="cause"]');
    if (!textarea) return;
    const text = node.dataset.text;
    // 点第二个短语时追加，而不是把第一个顶掉
    if (textarea.value.includes(text)) {
      textarea.value = textarea.value.replace(text, "").replace(/\s{2,}/g, " ").trim();
    } else {
      textarea.value = `${textarea.value ? textarea.value.trim() + " " : ""}${text}`.slice(0, 500);
    }
    syncCharCount();
    textarea.focus();
  },
  mic: () => startVoice(),
  "save-note": () => saveNote(),
  "delete-entry": (node) => removeEntry(Number(node.dataset.id)),
  filter: (node) => {
    state.filter.emotion = node.dataset.emotion;
    state.logLimit = PAGE_SIZE;
    renderLogList();
  },
  sort: (node) => {
    state.filter.sort = node.dataset.sort;
    state.logLimit = PAGE_SIZE;
    renderLogList();
  },
  unfilled: () => {
    state.filter.unfilled = !state.filter.unfilled;
    state.logLimit = PAGE_SIZE;
    renderLogList();
  },
  more: () => {
    state.logLimit += PAGE_SIZE;
    renderLogList();
  },
    window: async (node) => {
    const days = Number(node.dataset.days);
    if (days === state.insightsDays) return;
    state.insightsDays = days;
    invalidateInsights();
    render();
try {
    // 返回 false 说明期间又切过一次窗口，这份结果已经作废
      if (!(await loadInsights())) return;
    } catch (err) {
    toast(err.message);
    }
    render();
  },
  paywall: () => openRedeem(),
  "redeem-submit": () => doRedeem(),
  "auth-mode": (node) => renderAuth(node.dataset.mode),
  logout: () => doLogout(),
  "change-password": () => openPasswordSheet(),
  "password-submit": () => doChangePassword(),
  reanalyze: () => runAnalysis({ force: true }),
  "confirm-ok": () => settleConfirm(true),
  "confirm-cancel": () => settleConfirm(false),
  lang: (node) => switchLang(node.dataset.lang),
  "set-voice": (node) => {
    voice.lang = node.dataset.lang;
    try {
      localStorage.setItem("tether.voice", voice.lang);
    } catch {
      /* 无痕模式 */
    }
    render();
  },
  theme: () => cycleTheme(),
  "set-theme": (node) => {
    theme.pref = node.dataset.theme;
    try {
      localStorage.setItem("tether.theme", theme.pref);
    } catch {
      /* 无痕模式 */
    }
    applyTheme();
    render();
  },
  export: (node) => {
    window.location.href = api.exportUrl(node.dataset.format);
  },
  seed: () => doSeed(),
  reset: () => doReset(),
  "open-crisis": () => openCrisis(),
};

document.addEventListener("click", async (event) => {
  const node = event.target.closest("[data-act]");
  if (!node) {
    // 点浮层背景（不是卡片本身）关闭
    if (event.target === overlay) closeAndSync();
    return;
  }
  const action = ACTIONS[node.dataset.act];
  if (action) await action(node, event);
});

document.addEventListener("input", (event) => {
  if (event.target.matches('[data-input="cause"]')) syncCharCount();
});

document.addEventListener("change", async (event) => {
  const node = event.target.closest("[data-act='region']");
  if (!node) return;
  try {
    await api.setSettings({ region: node.value });
    await loadAccount();
    toast(t("me.region_saved"));
  } catch (err) {
    toast(err.message);
  }
});

document.addEventListener("keydown", (event) => {
  /* 确认卡叠在 sheet 之上时，Esc / Tab 先归它管，
     否则会直接把底下的编辑浮层关掉。 */
  if (confirmLayer && !confirmLayer.hidden) {
    if (event.key === "Escape") {
      event.preventDefault();
      settleConfirm(false);
      return;
    }
    if (event.key === "Tab") {
      const items = $$("button", confirmLayer).filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    return;
  }

  if (overlay.hidden) return;

  if (event.key === "Escape") {
    event.preventDefault();
    closeAndSync();
    return;
  }

  // 焦点锁在浮层里：Tab 不应该跑到背后的页面上
  if (event.key === "Tab") {
    const items = $$(FOCUSABLE, overlay).filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
    return;
  }

  // 引导过程里不想找按钮：空格 / 回车直接下一步
  if (event.key === " " || event.key === "Enter") {
    if (event.target.matches("textarea, input, select, button, a")) return;
    const next = overlay.querySelector('[data-act="iv-next"]');
    if (next) {
      event.preventDefault();
      next.click();
    }
  }
});

/* --------------------------------------------------------------- 登录门 */

/* 没登录就只给这一屏：不渲染 tab、不拉记录。
   登录成功后整页重载一次，让所有状态从干净的地方开始。 */
function renderAuth(mode = "login") {
  const isLogin = mode === "login";
  document.body.classList.add("gated");
  const view = $("#view");
  if (!view) return;
  view.innerHTML = `<div class="auth-wrap stack gap-16">
    <div class="stack gap-6" style="text-align:center">
      <h1 class="display sm" style="margin:0">${isLogin ? t("auth.login_title") : t("auth.register_title")}</h1>
      <p class="small">${t("auth.lede")}</p>
    </div>
    <form class="card stack gap-14" data-auth-form data-mode="${mode}">
      <label class="stack gap-6">
        <span class="field-label">${t("auth.username")}</span>
        <input type="text" name="username" autocomplete="username" autocapitalize="none"
               spellcheck="false" required minlength="2" maxlength="24" data-autofocus />
        ${isLogin ? "" : `<span class="tiny">${t("auth.hint_user")}</span>`}
      </label>
      <label class="stack gap-6">
        <span class="field-label">${t("auth.password")}</span>
        <input type="password" name="password" autocomplete="${isLogin ? "current-password" : "new-password"}" required minlength="6" />
        <span class="tiny">${t("auth.hint_pass")}</span>
      </label>
      <button class="btn block" type="submit">${isLogin ? t("auth.login") : t("auth.register")}</button>
    </form>
    <button class="btn ghost block" data-act="auth-mode" data-mode="${isLogin ? "register" : "login"}">
      ${isLogin ? t("auth.to_register") : t("auth.to_login")}
    </button>
  </div>`;
  const first = view.querySelector("[data-autofocus]");
  if (first) first.focus({ preventScroll: true });
}

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-auth-form]");
  if (!form) return;
  event.preventDefault();
  const mode = form.dataset.mode;
  const username = form.querySelector('[name="username"]').value.trim();
  const password = form.querySelector('[name="password"]').value;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  button.textContent = t("auth.logging_in");
  try {
    if (mode === "login") await api.login(username, password);
    else await api.register(username, password);
    location.reload();
  } catch (err) {
    toast(err.message);
    button.disabled = false;
    button.textContent = mode === "login" ? t("auth.login") : t("auth.register");
  }
});

/* 会话在别的设备被踢掉、或过期时，任何一次请求都可能带回 401 */
window.addEventListener("tether:unauthorized", () => {
  if (!document.body.classList.contains("gated")) renderAuth();
});

async function doLogout() {
  try {
    await api.logout();
  } catch {
    /* 网络断了也要让本地退出 */
  }
  location.reload();
}

async function doRedeem() {
  const input = overlay.querySelector('[name="code"]');
  const button = overlay.querySelector("[data-act='redeem-submit']");
  const code = (input && input.value) || "";
  if (!code.trim()) return;
  button.disabled = true;
  try {
    const res = await api.redeem(code.trim());
    state.account = res.account;
    invalidateInsights();
    closeOverlay();
    render();
    toast(t("redeem.done"));
  } catch (err) {
    toast(err.message);
    button.disabled = false;
  }
}

function openRedeem() {
  openOverlay(`<div class="sheet center">
    <div class="row-between" style="margin-bottom:6px">
      <span class="eyebrow">Tether Pro</span>
      ${sheetClose()}
    </div>
    <h2 class="display sm" style="margin-bottom:8px">${t("redeem.title")}</h2>
    <p class="body-lg" style="margin-bottom:18px">${t("redeem.lede")}</p>
    <div class="card stack gap-12" style="margin-bottom:16px">
      ${[t("pay.f1"), t("pay.f2"), t("pay.f3")]
        .map(
          (line) => `<div class="row gap-8" style="align-items:flex-start">
            <span style="color:var(--moss);font-weight:700" aria-hidden="true">✓</span>
            <span class="small" style="color:var(--ink-2)">${line}</span>
          </div>`
        )
        .join("")}
    </div>
    <input type="text" name="code" placeholder="${t("redeem.placeholder")}" autocapitalize="characters"
           spellcheck="false" style="text-transform:uppercase;letter-spacing:0.08em" data-autofocus />
    <button class="btn block" data-act="redeem-submit" style="margin-top:12px">${t("redeem.submit")}</button>
  </div>`, { label: t("redeem.title") });
}

function openPasswordSheet() {
  openOverlay(`<div class="sheet center">
    <div class="row-between" style="margin-bottom:6px">
      <span class="eyebrow">${t("me.account")}</span>
      ${sheetClose()}
    </div>
    <h2 class="display sm" style="margin-bottom:14px">${t("me.change_password")}</h2>
    <div class="card stack gap-14">
      <label class="stack gap-6">
        <span class="field-label">${t("me.old_password")}</span>
        <input type="password" name="old_password" autocomplete="current-password" data-autofocus />
      </label>
      <label class="stack gap-6">
        <span class="field-label">${t("me.new_password")}</span>
        <input type="password" name="new_password" autocomplete="new-password" />
      </label>
      <button class="btn block" data-act="password-submit">${t("me.change_password")}</button>
    </div>
  </div>`, { label: t("me.change_password") });
}

async function doChangePassword() {
  const oldPw = (overlay.querySelector('[name="old_password"]') || {}).value || "";
  const newPw = (overlay.querySelector('[name="new_password"]') || {}).value || "";
  try {
    await api.changePassword(oldPw, newPw);
    closeOverlay();
    toast(t("me.password_done"));
  } catch (err) {
    toast(err.message);
  }
}

/* --------------------------------------------------------------- 启动 */

async function boot() {
  let storedLang = null;
  try {
    storedLang = localStorage.getItem("tether.lang");
    theme.pref = localStorage.getItem("tether.theme") || "auto";
    voice.lang = localStorage.getItem("tether.voice") || "";
  } catch {
    /* 无痕模式：用默认值 */
  }
  const initial = storedLang === "en" || storedLang === "zh" ? storedLang : "zh";
  setLang(initial);
  setApiLang(initial);
  applyStaticText();
  applyTheme();
  render();

  /* 先确认登录态。没登录就停在这里：不拉数据、不渲染外壳，
     免得每个接口都 401 再各自弹一遍 toast。 */
  try {
    const mine = await api.me();
    state.account = mine.account;
  } catch (err) {
    /* 只有 401 才算「没登录」。连不上服务时不要把人推到登录页，
       否则离线打开会看到一个莫名其妙的登录框。 */
    if (err.status === 401) {
      renderAuth();
      return;
    }
  }

  try {
    await loadMeta();
    if (state.meta && state.meta.lang && !storedLang && state.meta.lang !== initial) {
      setLang(state.meta.lang);
      setApiLang(state.meta.lang);
      applyStaticText();
      applyTheme();
      await loadMeta();
    }
  } catch (err) {
    $("#view").innerHTML = `<div class="empty stack gap-8" style="margin-top:40px">
      <h2 class="display sm">${t("common.error")}</h2>
      <p class="small">${esc(err.message)}</p>
      <button class="btn soft sm" data-act="retry">${t("common.retry")}</button>
    </div>`;
    ACTIONS.retry = () => location.reload();
    return;
  }

  const view = hashView();
  history.replaceState({ view }, "", `#${view}`);
  await refresh({ entries: true, account: true });
  setView(view, { scroll: "none" });
  await ensureViewData(view);
}

boot();
