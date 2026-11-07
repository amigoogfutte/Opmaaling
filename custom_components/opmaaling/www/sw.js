// Gemmer appen på enheden, så den virker uden internet.
const CACHE = 'opmaaling-v44';
const FILES = ['./index.html', './manifest.webmanifest', './icon-180.png', './icon-192.png', './icon-512.png', './lib/jspdf.umd.min.js',
  './raaglas/4-mm-abstrach.jpg', './raaglas/4-mm-barroch.jpg', './raaglas/4-mm-chincilla.jpg', './raaglas/4-mm-cotswold.jpg', './raaglas/4-mm-graa-parsol.jpg', './raaglas/4-mm-gross-artic.jpg', './raaglas/4-mm-gross-kathedral.jpg', './raaglas/4-mm-klein-kathedral.jpg', './raaglas/4-mm-ornament-504.jpg', './raaglas/4-mm-ornament-520.jpg', './raaglas/4-mm-ornament-597.jpg', './raaglas/4-mm-pacific.jpg', './raaglas/4-mm-satimat.jpg', './raaglas/4-mm-silvit.jpg', './raaglas/4-mm-small-artic.jpg', './raaglas/6-mm-antelio-klar.jpg', './raaglas/6-mm-antelio-silver.jpg', './raaglas/6-mm-spejtraad.jpg', './raaglas/7-mm-georgian-traad.jpg', './raaglas/7-mm-treclar-traad.jpg'];
const SCOPE = new URL('./', self.location).pathname;

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Kun appens egne filer håndteres. API-kald og login går altid direkte til serveren.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin || !url.pathname.startsWith(SCOPE)) return;
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {}); }
      return res;
    }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || caches.match('./index.html')))
  );
});
