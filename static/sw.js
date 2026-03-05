// NexaShop — Service Worker (Push Notifications)
const CACHE_NAME = 'nexashop-v1';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

// Réception d'une notification push depuis le serveur
self.addEventListener('push', e => {
  let data = { title: 'NexaShop', body: 'Nouvelle notification', icon: '/icon.png', tag: 'nexashop' };
  try {
    if (e.data) data = { ...data, ...e.data.json() };
  } catch {}

  const options = {
    body:    data.body,
    icon:    data.icon  || '/icon-192.png',
    badge:   data.badge || '/icon-72.png',
    tag:     data.tag   || 'nexashop',
    data:    data.url   ? { url: data.url } : {},
    actions: data.actions || [],
    vibrate: [200, 100, 200],
    requireInteraction: data.requireInteraction || false,
  };

  e.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// Clic sur la notification — ouvrir l'app
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.focus();
          client.postMessage({ type: 'NAVIGATE', url });
          return;
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
