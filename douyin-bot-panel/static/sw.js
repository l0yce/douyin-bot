const CACHE = "douyin-bot-public-v1";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API 一律走网络，不缓存
  if (e.request.method !== "GET" || url.pathname.includes("/api/")) return;
  // 网络优先，失败回退缓存（保证改版后能拿到新面板）
  e.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(e.request);
      const fetchPromise = fetch(e.request)
        .then((resp) => {
          if (resp && resp.ok) cache.put(e.request, resp.clone());
          return resp;
        })
        .catch(() => cached);
      return fetchPromise;
    })
  );
});

