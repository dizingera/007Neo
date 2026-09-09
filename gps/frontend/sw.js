/* Service Worker - nur für die Oberfläche, nie für Daten.

   Zweck ist eng: Das Android-Tablet in der Kabine soll AgriPilot als Kachel auf
   dem Startbildschirm haben und beim Öffnen sofort ein Bild zeigen, statt einer
   weißen Seite, während das WLAN im Hof gerade wackelt.

   Was hier NICHT passiert, ist wichtiger als was passiert: /api und /ws werden
   niemals zwischengespeichert. Eine zwischengespeicherte Antwort auf
   /api/state wäre eine alte Position, ein alter Fix-Status und eine alte
   Lenkbedingung - genau die Sorte Anzeige, die jemanden dazu bringt, sich auf
   ein Bild zu verlassen, das nicht mehr gilt. Live-Daten kommen ausschließlich
   aus dem Netz oder gar nicht.

   Zwischengespeichert wird nur die Hülle: Seite, Stil, Programm, Symbole. */

const CACHE = 'agripilot-huelle-v1';
const HUELLE = [
  '/',
  '/index.html',
  '/app.js',
  '/style.css',
  '/manifest.webmanifest',
  '/icon-192.png',
  '/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(HUELLE)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Alte Stände wegräumen, sonst zeigt das Tablet nach einem Update wochenlang
  // die alte Oberfläche zu einem neuen Server.
  event.waitUntil(
    caches.keys()
      .then((namen) => Promise.all(
        namen.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const istDaten = url.pathname.startsWith('/api') || url.pathname.startsWith('/ws');
  if (event.request.method !== 'GET' || istDaten || url.origin !== self.location.origin) {
    return;   // unangetastet ans Netz
  }

  // Hülle: erst das Netz fragen (ein Update soll sofort ankommen), bei
  // Ausfall das Zwischengespeicherte. Umgekehrt hätte man nach jedem Update
  // erst einmal die alte Oberfläche.
  event.respondWith(
    fetch(event.request)
      .then((antwort) => {
        const kopie = antwort.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, kopie));
        return antwort;
      })
      .catch(() => caches.match(event.request)
        .then((treffer) => treffer || caches.match('/index.html')))
  );
});
