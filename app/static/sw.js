// Service Worker — Portail Socrate PWA
const CACHE_NAME = 'socrate-v5';
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/img/logo-socrate-transparent.png',
  '/static/img/sceau-header.png',
  '/static/offline.html',
];
// Limite la taille du cache "runtime" (pages HTML visitées) pour éviter
// que le SW gonfle indéfiniment.
const RUNTIME_MAX_ENTRIES = 50;

async function trimCache(cacheName, maxEntries) {
  try {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length <= maxEntries) return;
    const toDelete = keys.length - maxEntries;
    for (let i = 0; i < toDelete; i++) {
      await cache.delete(keys[i]);
    }
  } catch (e) { /* silent */ }
}

// Installation : précache des assets statiques + page offline. Chaque
// fichier est mis en cache indépendamment (pas cache.addAll(), qui est
// tout-ou-rien : un seul échec — proxy d'entreprise, requête coupée —
// viderait tout le précache, y compris offline.html, le filet de
// sécurité dont dépend le fallback réseau plus bas.
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      Promise.all(
        STATIC_ASSETS.map((url) => cache.add(url).catch(() => {}))
      )
    )
  );
  self.skipWaiting();
});

// Activation — nettoyage des anciens caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch — stratégies différenciées
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Ne pas intercepter les requêtes non-GET, API, WebSocket, uploads
  if (
    req.method !== 'GET' ||
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/ws/') ||
    url.pathname.startsWith('/uploads/') ||
    url.pathname.includes('/download') ||
    url.pathname.includes('/preview')
  ) {
    return;
  }

  // Static assets : cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((cached) =>
        cached ||
        fetch(req).then((resp) => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
          }
          return resp;
        }).catch(() => cached || Response.error())
      )
    );
    return;
  }

  // Pages HTML : network-first, fallback cache, fallback offline.html
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(req, clone).then(() => trimCache(CACHE_NAME, RUNTIME_MAX_ENTRIES));
            });
          }
          return resp;
        })
        .catch(() =>
          caches.match(req).then((cached) => {
            if (cached) return cached;
            return caches.match('/static/offline.html').then((offline) =>
              // Filet de sécurité ultime : si même offline.html n'est pas en
              // cache (précache jamais abouti), event.respondWith() ne doit
              // jamais recevoir undefined — ça fait planter le SW avec
              // "Failed to convert value to 'Response'" et casse la
              // navigation entière au lieu d'un simple message hors-ligne.
              offline || new Response(
                '<!doctype html><html lang="fr"><meta charset="utf-8">' +
                '<title>Hors ligne</title><body style="font-family:sans-serif;text-align:center;padding:3rem">' +
                '<h1>Connexion indisponible</h1><p>Impossible de contacter le serveur. Réessayez dans un instant.</p></body></html>',
                { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
              )
            );
          })
        )
    );
    return;
  }

  // Reste : network puis cache
  event.respondWith(
    fetch(req).catch(() => caches.match(req).then((cached) => cached || Response.error()))
  );
});

// Pastille sur l'icône de l'app (Badging API — Android/Chrome, pas iOS Safari)
// Recalculée depuis le serveur car le SW ne connaît pas le compteur en direct.
async function updateAppBadge() {
  if (!('setAppBadge' in self.navigator)) return;
  try {
    const [chatRes, msgRes] = await Promise.all([
      fetch('/chat/api/unread', { credentials: 'same-origin' }),
      fetch('/messages/api/unread', { credentials: 'same-origin' }),
    ]);
    const chatTotal = chatRes.ok ? (await chatRes.json()).total || 0 : 0;
    const msgTotal = msgRes.ok ? (await msgRes.json()).total || 0 : 0;
    const total = chatTotal + msgTotal;
    if (total > 0) await self.navigator.setAppBadge(total);
    else await self.navigator.clearAppBadge();
  } catch (e) { /* silent */ }
}

// Push notifications
self.addEventListener('push', (event) => {
  if (!event.data) return;

  let data = {};
  try { data = event.data.json(); } catch (e) { data = { title: 'Portail Socrate', body: event.data.text() }; }

  event.waitUntil(
    Promise.all([
      self.registration.showNotification(data.title || 'Portail Socrate', {
        body: data.body || '',
        icon: '/static/img/icon-192.png',
        badge: '/static/img/icon-192.png',
        data: { url: data.url || '/' },
        vibrate: [200, 100, 200],
      }),
      updateAppBadge(),
    ])
  );
});

// Clic sur notification → ouvrir l'app
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clientList) => {
      const url = event.notification.data?.url || '/';
      for (const client of clientList) {
        if (client.url.endsWith(url) && 'focus' in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});

// Permettre au client de forcer la mise à jour
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
