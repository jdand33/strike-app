self.addEventListener("install", e => {
    e.waitUntil(
        caches.open("strike-cache").then(cache => {
            return cache.addAll(["/", "/static/manifest.json"]);
        })
    );
});