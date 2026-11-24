/* 手机端验收：在真实浏览器引擎里用设备视口跑一遍主要流程。
 *
 * 这个脚本存在的理由：桌面浏览器里「看起来没问题」，不等于手机上没问题。
 * 它量的是可量化的东西——横向溢出、触控目标尺寸、输入框字号（iOS 会放大页面）、
 * 底部导航是否被手势条遮住、弹层是否超出视口、离线壳是否可用。
 *
 * 运行：node tests/mobile_audit.mjs
 * PLAYWRIGHT_MODULE 指定 playwright-core 的绝对路径；EDGE_EXECUTABLE 指定浏览器；
 * TETHER_TEST_URL 指定被测服务。截图写入 .cache/mobile-shots。
 */
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const modulePath = process.env.PLAYWRIGHT_MODULE || path.join(".cache", "mobile-tools", "node_modules", "playwright-core");
const resolvedModule = path.resolve(modulePath);
// ESM 不允许目录导入：README 里的 PLAYWRIGHT_MODULE 指向包目录，这里补成入口文件。
const entry = resolvedModule.endsWith(".mjs") || resolvedModule.endsWith(".js")
  ? resolvedModule
  : path.join(resolvedModule, "index.mjs");
const playwright = await import(pathToFileURL(entry).href);
const chromium = playwright.chromium || playwright.default?.chromium;

const BASE = process.env.TETHER_TEST_URL || "http://127.0.0.1:8000";
const EXECUTABLE = process.env.EDGE_EXECUTABLE || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const SHOTS = path.resolve(".cache", "mobile-shots");

const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
const ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36";

const DEVICES = [
  { name: "iphone-13", width: 390, height: 844, dsf: 3, mobile: true, ua: IPHONE },
  { name: "iphone-se", width: 375, height: 667, dsf: 2, mobile: true, ua: IPHONE },
  { name: "pixel-7", width: 412, height: 915, dsf: 2.6, mobile: true, ua: ANDROID },
  { name: "android-small", width: 360, height: 640, dsf: 3, mobile: true, ua: ANDROID },
  { name: "iphone-13-landscape", width: 844, height: 390, dsf: 3, mobile: true, ua: IPHONE },
];

const findings = [];
const fail = (device, check, detail) => findings.push({ device, check, detail, level: "error" });
const warn = (device, check, detail) => findings.push({ device, check, detail, level: "warn" });

/* 在页面里执行：把可量化的手机端问题一次收齐。 */
const MEASURE = () => {
  const out = { overflowX: 0, targets: [], inputFonts: [], clipped: [], bottomBar: null, viewport: {} };
  const doc = document.scrollingElement || document.documentElement;
  out.viewport = { width: innerWidth, height: innerHeight, scrollWidth: doc.scrollWidth };
  out.overflowX = Math.max(0, doc.scrollWidth - innerWidth);

  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };

  // 触控目标：只查真正可点的控件，内联文字链接按可访问性惯例放宽
  const pressable = document.querySelectorAll("button, a[href], select, textarea, input, [role='button'], [data-act]");
  for (const el of pressable) {
    if (!visible(el)) continue;
    if (getComputedStyle(el).pointerEvents === "none") continue;
    const rect = el.getBoundingClientRect();
    const inline = el.tagName === "A" && getComputedStyle(el).display.includes("inline");
    const min = inline ? 24 : 40;
    if (rect.width + 0.5 < min || rect.height + 0.5 < min) {
      const label = (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 40);
      out.targets.push({ label, w: Math.round(rect.width), h: Math.round(rect.height), min });
    }
  }

  // iOS 在输入框字号 < 16px 时会自动放大整页
  for (const el of document.querySelectorAll("input, textarea, select")) {
    if (!visible(el)) continue;
    const size = parseFloat(getComputedStyle(el).fontSize);
    if (size < 16) out.inputFonts.push({ tag: el.tagName.toLowerCase(), size });
  }

  // 文字被裁掉：只认「不换行 + 溢出隐藏」这一种真实裁切。
  // 排除 flex/grid 容器（居中内容会在两侧溢出，scrollWidth 会虚高）和屏幕阅读器专用元素。
  for (const el of document.querySelectorAll("h1, h2, h3, p, span, button, li, div")) {
    if (!visible(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) continue;
    const directText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!directText) continue;
    const style = getComputedStyle(el);
    if (style.display.includes("flex") || style.display.includes("grid")) continue;
    if (!style.whiteSpace.includes("nowrap")) continue;
    if (style.overflowX !== "hidden") continue;
    if (el.closest(".sr-only, [aria-hidden='true']")) continue;
    if (el.scrollWidth - el.clientWidth > 2 && el.clientWidth > 0) {
      out.clipped.push({ text: (el.textContent || "").trim().slice(0, 40), over: el.scrollWidth - el.clientWidth });
    }
  }

  // 固定底部导航是否落在视口内
  const bar = document.querySelector(".tabbar");
  if (bar && visible(bar) && getComputedStyle(bar).position === "fixed") {
    const rect = bar.getBoundingClientRect();
    out.bottomBar = { top: Math.round(rect.top), bottom: Math.round(rect.bottom), height: Math.round(rect.height), viewportH: innerHeight };
  }

  // 浮层是否超出视口、是否可滚动
  const sheet = document.querySelector("#overlay:not([hidden]) .sheet, #overlay:not([hidden]) .focus");
  if (sheet) {
    const rect = sheet.getBoundingClientRect();
    out.sheet = {
      top: Math.round(rect.top), bottom: Math.round(rect.bottom),
      height: Math.round(rect.height), viewportH: innerHeight,
      scrollable: sheet.scrollHeight <= sheet.clientHeight || getComputedStyle(sheet).overflowY === "auto",
    };
  }
  return out;
};

async function measure(page) {
  return page.evaluate(MEASURE);
}

function auditMeasure(device, label, data) {
  if (data.overflowX > 1) fail(device, `${label}: 横向溢出`, `${data.overflowX}px（scrollWidth ${data.viewport.scrollWidth} > innerWidth ${data.viewport.width}）`);
  if (data.targets.length) {
    const worst = data.targets.slice(0, 6).map((t) => `${t.label}(${t.w}×${t.h} < ${t.min})`).join(", ");
    fail(device, `${label}: 触控目标过小`, `${data.targets.length} 个：${worst}`);
  }
  if (data.inputFonts.length) {
    const sizes = [...new Set(data.inputFonts.map((i) => i.size))].join("/");
    fail(device, `${label}: 输入框字号 < 16px（iOS 会放大页面）`, `${data.inputFonts.length} 个，字号 ${sizes}`);
  }
  if (data.clipped.length) {
    warn(device, `${label}: 文字被裁切`, data.clipped.slice(0, 4).map((c) => `${c.text}(+${c.over}px)`).join(", "));
  }
  if (data.bottomBar && data.bottomBar.bottom > data.bottomBar.viewportH + 1) {
    fail(device, `${label}: 底部导航超出视口`, `bottom ${data.bottomBar.bottom} > viewport ${data.bottomBar.viewportH}`);
  }
  if (data.sheet) {
    if (data.sheet.height > data.sheet.viewportH + 1) fail(device, `${label}: 浮层高于视口`, `${data.sheet.height} > ${data.sheet.viewportH}`);
    if (data.sheet.top < -1) fail(device, `${label}: 浮层顶部被推出屏幕`, `top ${data.sheet.top}`);
    if (!data.sheet.scrollable) warn(device, `${label}: 浮层不可滚动`, `height ${data.sheet.height}`);
  }
}

async function shot(page, device, name) {
  await fs.mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${device}-${name}.png`), fullPage: false });
}

async function runDevice(browser, device) {
  const context = await browser.newContext({
    viewport: { width: device.width, height: device.height },
    deviceScaleFactor: device.dsf,
    isMobile: device.mobile,
    hasTouch: true,
    userAgent: device.ua,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text().slice(0, 160)); });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${String(err).slice(0, 160)}`));

  const d = device.name;
  await page.goto(BASE, { waitUntil: "networkidle" });

  // 0) 装机条件：manifest 能解析、图标真实可下载、display 是 standalone
  const install = await page.evaluate(async () => {
    const link = document.querySelector('link[rel="manifest"]');
    if (!link) return { ok: false, why: "缺少 manifest link" };
    const res = await fetch(link.href);
    if (!res.ok) return { ok: false, why: `manifest ${res.status}` };
    const manifest = await res.json();
    const icons = [];
    for (const icon of manifest.icons || []) {
      const url = new URL(icon.src, link.href).href;
      const head = await fetch(url, { method: "HEAD" });
      icons.push({ sizes: icon.sizes, purpose: icon.purpose, type: icon.type, status: head.status });
    }
    const meta = document.querySelector('meta[name="viewport"]')?.getAttribute("content") || "";
    return {
      ok: true,
      display: manifest.display,
      hasId: Boolean(manifest.id),
      startUrl: manifest.start_url,
      themeColor: (manifest.theme_color || "").toLowerCase(),
      metaTheme: (document.querySelector('meta[name="theme-color"]')?.getAttribute("content") || "").toLowerCase(),
      icons,
      viewportFitCover: meta.includes("viewport-fit=cover"),
      lang: manifest.lang,
    };
  });
  if (!install.ok) fail(d, "装机条件", install.why);
  else {
    if (install.display !== "standalone") fail(d, "装机条件", `display=${install.display}`);
    const sizes = install.icons.map((i) => i.sizes);
    for (const need of ["192x192", "512x512"]) if (!sizes.includes(need)) fail(d, "装机条件", `缺少 ${need} 图标`);
    const broken = install.icons.filter((i) => i.status !== 200);
    if (broken.length) fail(d, "装机条件", `图标无法下载：${broken.map((i) => i.sizes).join(",")}`);
    if (!install.viewportFitCover) fail(d, "手机适配", "viewport 缺少 viewport-fit=cover（刘海屏会留白或被遮挡）");
    const purposes = install.icons.map((i) => i.purpose || "any");
    for (const need of ["192x192", "512x512"]) {
      const exact = install.icons.filter((i) => i.sizes === need);
      if (!exact.some((i) => (i.purpose || "any").split(" ").includes("any"))) fail(d, "装机条件", `${need} 缺少 purpose=any 的图标`);
      if (!exact.some((i) => (i.purpose || "").split(" ").includes("maskable"))) {
        fail(d, "装机条件", `${need} 缺少 purpose=maskable 的图标（Android 主屏裁剪会切掉画面）`);
      }
    }
    if (install.themeColor !== install.metaTheme) {
      warn(d, "手机适配", `manifest.theme_color(${install.themeColor}) 与 theme-color(${install.metaTheme}) 不一致，装上桌面后状态栏会跳色`);
    }
  }

  // 1) 登录页（未登录）
  auditMeasure(d, "登录页", await measure(page));
  await shot(page, d, "01-login");

  // 2) 注册并进入首页：走真实表单，顺便验证手机上的输入体验
  const user = `m${Date.now().toString(36)}${Math.floor(Math.random() * 900 + 100)}`;
  await page.locator('[data-act="auth-mode"][data-mode="register"]').click();
  await page.waitForSelector('form[data-auth-form][data-mode="register"]');
  await page.locator('input[name="username"]').fill(user);
  await page.locator('input[name="password"]').fill("Mobile-audit-9182");
  await page.locator('button[type="submit"]').click();
  await page.waitForSelector(".tabbar", { timeout: 20000 });
  await page.waitForTimeout(900);
  auditMeasure(d, "首页", await measure(page));
  await shot(page, d, "02-home");

  // 3) 自由表达浮层
  const openExpress = page.locator('[data-act="e-open"]').first();
  if (await openExpress.count()) {
    await openExpress.click();
    await page.waitForTimeout(500);
    auditMeasure(d, "表达浮层", await measure(page));
    await shot(page, d, "03-expression");
    await page.locator("#expression-dialog-text").fill("手机端验收：这是一段测试文字。");
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  } else {
    warn(d, "表达浮层", "未找到入口");
  }

  // 4) 自由表达已在上一步验过；直接进入练习页
  // 5) 练习页
  await page.locator('.tab[data-view="practice"]').click();
  await page.waitForTimeout(1200);
  auditMeasure(d, "练习页", await measure(page));
  await shot(page, d, "04-practice");

  // 6) 总结页
  await page.locator('.tab[data-view="insights"]').click();
  await page.waitForTimeout(1200);
  auditMeasure(d, "总结页", await measure(page));
  await shot(page, d, "05-insights");

  // 7) 我的页 + 深色主题
  await page.locator('.tab[data-view="me"]').click();
  await page.waitForTimeout(800);
  auditMeasure(d, "我的页", await measure(page));
  await shot(page, d, "06-me");

  await page.locator('[data-act="theme"]').click();
  await page.waitForTimeout(500);
  auditMeasure(d, "深色主题", await measure(page));
  await shot(page, d, "07-dark");
  await page.locator('[data-act="theme"]').click();
  await page.waitForTimeout(300);

  // 8) 英文界面
  await page.locator('[data-act="lang"][data-lang="en"]').click();
  await page.waitForTimeout(1200);
  await page.locator('.tab[data-view="now"]').click();
  await page.waitForTimeout(800);
  auditMeasure(d, "英文首页", await measure(page));
  await shot(page, d, "08-english");

  // 9) 已安装形态（standalone）与离线壳
  // 断网之后的网络失败是预期的，只统计在线阶段的报错。
  const onlineErrors = [...consoleErrors];
  const shell = await page.evaluate(async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    return { controlled: Boolean(navigator.serviceWorker.controller), registered: Boolean(reg), standalone: matchMedia("(display-mode: standalone)").matches };
  });
  if (!shell.registered) warn(d, "Service Worker", "未注册");

  await context.setOffline(true);
  let offlineOk = false;
  try {
    await page.reload({ waitUntil: "domcontentloaded", timeout: 15000 });
    await page.waitForTimeout(900);
    offlineOk = (await page.locator("#view, .tabbar, .auth-wrap").count()) > 0;
    auditMeasure(d, "离线壳", await measure(page));
    await shot(page, d, "09-offline");
  } catch (err) {
    offlineOk = false;
    fail(d, "离线壳不可用", String(err).slice(0, 120));
  }
  await context.setOffline(false);
  if (!offlineOk) fail(d, "离线打开失败", "断网后页面无法渲染");

  if (onlineErrors.length) fail(d, "控制台报错", onlineErrors.slice(0, 3).join(" | "));

  await context.close();
  return shell;
}

async function main() {
  await fs.mkdir(SHOTS, { recursive: true });
  const browser = await chromium.launch({ executablePath: EXECUTABLE, headless: true });
  const summary = [];
  try {
    for (const device of DEVICES) {
      const shell = await runDevice(browser, device);
      summary.push({ device: device.name, size: `${device.width}×${device.height}`, ...shell });
      console.log(`checked ${device.name} (${device.width}×${device.height})`);
    }
  } finally {
    await browser.close();
  }

  const errors = findings.filter((f) => f.level === "error");
  const warns = findings.filter((f) => f.level === "warn");
  console.log("\n=== 设备 ===");
  for (const row of summary) console.log(`${row.device.padEnd(22)} ${row.size.padEnd(10)} SW=${row.registered ? "yes" : "no"} controlled=${row.controlled ? "yes" : "no"}`);
  console.log("\n=== 问题 ===");
  if (!errors.length && !warns.length) console.log("无");
  for (const f of findings) console.log(`${f.level === "error" ? "FAIL" : "warn"} [${f.device}] ${f.check} — ${f.detail}`);
  console.log(`\n${errors.length} 个错误，${warns.length} 个提醒`);
  console.log(`截图：${SHOTS}`);
  if (errors.length) process.exitCode = 1;
}

await main();
