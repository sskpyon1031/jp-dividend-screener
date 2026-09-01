/* オフラインでも最後に取得したデータを表示するための最小サービスワーカー。
 *
 * ・外枠(index.html / app.js / style.css など): stale-while-revalidate
 *     … キャッシュを即返しつつ裏で更新するので、次回アクセスで新版に入れ替わる。
 * ・データ(/data/ 配下): ネット優先・失敗時のみキャッシュ。
 *
 * VERSION を上げると旧キャッシュを破棄する。フロントを大きく変えて
 * 即時反映したいときは手動で上げてもよいが、通常は放置で自己回復する。 */
const VERSION = "v11";
const SHELL = "shell-" + VERSION;
const ASSETS = [
  "./", "./index.html", "./style.css", "./app.js",
  "./manifest.webmanifest", "./icon.svg", "./icon-192.png",
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(SHELL)
      .then(c => c.addAll(ASSETS))
      .catch(() => {})            // 一部 404 でも install を止めない
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // データ: ネット優先、失敗したらキャッシュ
  if (url.pathname.includes("/data/")) {
    e.respondWith(
      fetch(req)
        .then(res => {
          const copy = res.clone();
          caches.open(SHELL).then(c => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req, { ignoreSearch: true }))
    );
    return;
  }

  // 外枠: stale-while-revalidate
  e.respondWith(
    caches.match(req).then(cached => {
      const fresh = fetch(req)
        .then(res => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(SHELL).then(c => c.put(req, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || fresh;
    })
  );
});
