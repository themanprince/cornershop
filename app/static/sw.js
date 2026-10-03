// Cornershop Service Worker
const CACHE_NAME = 'cornershop-v1';
const STATIC_ASSETS = [
  '/static/styles.css',
  '/static/manifest.json',
  '/static/icons/apple-touch-icon.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon.svg',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        // Log caching issue if any external asset fails, but don't fail install
        console.warn('Pre-caching non-fatal warning:', err);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests (e.g. POST to cart, checkout, payments, auth)
  if (request.method !== 'GET') {
    return;
  }

  // Skip OAuth, Paystack, and payment routes
  if (
    url.pathname.startsWith('/auth/') ||
    url.pathname.startsWith('/payments/') ||
    url.pathname === '/login' ||
    url.pathname === '/logout'
  ) {
    return;
  }

  // Static assets: cache-first with network fallback
  if (
    url.pathname.startsWith('/static/') ||
    url.hostname === 'cdn.jsdelivr.net'
  ) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return networkResponse;
        });
      })
    );
    return;
  }

  // HTML navigation requests: strictly Network-First
  // This guarantees fresh cart counts, current login state, and flash messages.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => {
        // Return a basic offline message if user loses connectivity
        return new Response(
          `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Offline | Cornershop</title>
  <link href="/static/styles.css" rel="stylesheet">
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; text-align: center; padding: 3rem 1rem; color: #212529; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    p { color: #6c757d; }
    .btn { display: inline-block; padding: 0.5rem 1.25rem; background: #ffc107; color: #212529; text-decoration: none; border-radius: 6px; font-weight: 600; margin-top: 1rem; }
  </style>
</head>
<body>
  <h1>You are currently offline</h1>
  <p>Please check your internet connection and try again.</p>
  <a href="javascript:location.reload()" class="btn">Retry</a>
</body>
</html>`,
          { headers: { 'Content-Type': 'text/html' } }
        );
      })
    );
  }
});
