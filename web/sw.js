// LENTA app shell: lets the installed app open instantly and show a friendly page when the server
// can't be reached. Media, artwork and everything under /api/ always come straight from the server.
const SHELL = 'lenta-shell-v3';
const FILES = [
  '/', '/index.html', '/manifest.webmanifest', '/css/fonts.css', '/css/app.css', '/css/mobile.css', '/css/theme-redline.css', '/vendor/hls.min.js',
  '/js/app.js', '/js/api.js', '/js/ui.js', '/js/views.js', '/js/admin.js', '/js/auth.js', '/js/editor.js', '/js/extras.js',
  '/js/hovercard.js', '/js/music.js', '/js/player.js', '/js/settings.js', '/js/subtitles.js', '/js/pwa.js', '/js/tv.js',
  '/img/lenta-logo.png', '/img/lenta-mark.png', '/img/lenta-wordmark.png', '/img/icon-192.png', '/img/favicon-32.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Network first, so an updated server is picked up at once; the saved copy is only for when it is unreachable.
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && (FILES.includes(url.pathname) || url.pathname.startsWith('/fonts/'))) {
      const copy = res.clone();
      caches.open(SHELL).then(c => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })
    .then(hit => hit || (e.request.mode === 'navigate' ? caches.match('/index.html') : Response.error()))));
});
