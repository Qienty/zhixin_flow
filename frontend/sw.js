/* Tether service worker
   目的只有两个：装到主屏后能离线打开外壳、二次启动不用干等网络。
   策略刻意选了「网络优先 + 缓存兜底」而不是缓存优先——
   这是个本地跑的 demo，改一行 JS 就该立刻生效，不该被 SW 的缓存挡住。
   /api 一律不碰：情绪记录不进缓存，离线时让请求自然失败，前端已经有 toast。 */

const VERSION = "tether-__ASSET_HASH__";

/* 部署在子路径下时（如 /qienty/），接口是 /qienty/api/… 而不是 /api/…，
   所以要按 scope 推前缀，否则接口响应会被当成静态资源缓存进壳里。 */
const SCOPE_PATH = new URL(self.registration.scope).pathname.replace(/\/$/, "");

const CACHE_PREFIX = `tether:${SCOPE_PATH}:`;
const CACHE_NAME = CACHE_PREFIX + VERSION;

const SHELL = [
  "./",
  "./index.html",
  "./css/app.css",
  "./js/app.js",
  "./js/api.js",
  "./js/i18n.js",
  "./js/theme-init.js",
  "./js/register-sw.js",
  "./icon-192.png",
  "./icon-512.png",
  "./apple-touch-icon-180.png",
  "./manifest.json",
  "./icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => null)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => (k.startsWith(CACHE_PREFIX) || k === "tether-v2") && k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(request) {
  try {
    const res = await fetch(request);
    if (res && res.ok) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(request, res.clone());
    }
    return res;
  } catch (err) {
    const cache = await caches.open(CACHE_NAME);
    const hit = await cache.match(request);
    if (hit) return hit;
    // 单页应用：导航请求离线时一律回首页外壳
    if (request.mode === "navigate" && [`${SCOPE_PATH}/`, `${SCOPE_PATH}/index.html`].includes(new URL(request.url).pathname)) {
      const shell = await cache.match("./index.html");
      if (shell) return shell;
    }
    return new Response("offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === `${SCOPE_PATH}/api` || url.pathname.startsWith(`${SCOPE_PATH}/api/`)) return;
  event.respondWith(networkFirst(request));
});
