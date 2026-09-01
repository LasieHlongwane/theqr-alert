
const CACHE_VERSION = "lac-v1";

const STATIC_CACHE =
    `${CACHE_VERSION}-static`;


// =========================================================
// FILES TO CACHE
// =========================================================
//
// Keep this list limited to files that are safe to cache.
//
// Dynamic QR pages, listings and category feeds are NOT
// permanently cached here because their content changes.
//
// =========================================================

const STATIC_ASSETS = [

    "/",

    "/static/manifest.json",

    "/static/css/style.css",

    "/static/icons/lac-192.png",

    "/static/icons/lac-512.png"

];


// =========================================================
// INSTALL
// =========================================================

self.addEventListener(
    "install",
    function(event) {

        console.log(
            "[LaC Service Worker] Installing..."
        );


        event.waitUntil(

            caches
                .open(STATIC_CACHE)
                .then(
                    function(cache) {

                        return cache.addAll(
                            STATIC_ASSETS
                        );

                    }
                )
                .then(
                    function() {

                        console.log(
                            "[LaC Service Worker] Static assets cached."
                        );


                        return self.skipWaiting();

                    }
                )
                .catch(
                    function(error) {

                        console.error(
                            "[LaC Service Worker] Install error:",
                            error
                        );

                    }
                )

        );

    }
);


// =========================================================
// ACTIVATE
// =========================================================
//
// Remove old LaC caches when the service-worker version
// changes.
//
// =========================================================

self.addEventListener(
    "activate",
    function(event) {

        console.log(
            "[LaC Service Worker] Activating..."
        );


        event.waitUntil(

            caches
                .keys()
                .then(
                    function(cacheNames) {

                        return Promise.all(

                            cacheNames.map(
                                function(cacheName) {

                                    if (
                                        cacheName.startsWith(
                                            "lac-"
                                        )
                                        &&
                                        cacheName !==
                                            STATIC_CACHE
                                    ) {

                                        console.log(
                                            "[LaC Service Worker] Removing old cache:",
                                            cacheName
                                        );


                                        return caches.delete(
                                            cacheName
                                        );

                                    }


                                    return Promise.resolve();

                                }
                            )

                        );

                    }
                )
                .then(
                    function() {

                        return self.clients.claim();

                    }
                )

        );

    }
);


// =========================================================
// FETCH
// =========================================================
//
// Strategy:
//
// STATIC FILES
// Cache first.
//
// HTML / DYNAMIC REQUESTS
// Network first.
//
// This matters for LaC because:
// - events change
// - deals expire
// - jobs expire
// - listings may be approved/deactivated
//
// We do not want users seeing stale local information.
//
// =========================================================

self.addEventListener(
    "fetch",
    function(event) {

        const request =
            event.request;


        // Only handle GET requests.

        if (
            request.method !== "GET"
        ) {

            return;

        }


        const requestUrl =
            new URL(
                request.url
            );


        // Ignore requests to other domains.

        if (
            requestUrl.origin !==
            self.location.origin
        ) {

            return;

        }


        // =================================================
        // STATIC ASSETS
        // =================================================

        if (
            requestUrl.pathname.startsWith(
                "/static/"
            )
        ) {

            event.respondWith(

                caches
                    .match(request)
                    .then(
                        function(cachedResponse) {

                            if (
                                cachedResponse
                            ) {

                                return cachedResponse;

                            }


                            return fetch(
                                request
                            )
                            .then(
                                function(networkResponse) {

                                    if (
                                        !networkResponse
                                        ||
                                        networkResponse.status !==
                                            200
                                    ) {

                                        return networkResponse;

                                    }


                                    const responseCopy =
                                        networkResponse.clone();


                                    caches
                                        .open(
                                            STATIC_CACHE
                                        )
                                        .then(
                                            function(cache) {

                                                cache.put(
                                                    request,
                                                    responseCopy
                                                );

                                            }
                                        );


                                    return networkResponse;

                                }
                            );

                        }
                    )

            );


            return;

        }


        // =================================================
        // HTML / DYNAMIC CONTENT
        // =================================================
        //
        // Network-first keeps local information fresh.
        //
        // If the network fails completely, try cache.
        //
        // =================================================

        if (
            request.mode ===
            "navigate"
        ) {

            event.respondWith(

                fetch(
                    request
                )
                .catch(
                    function() {

                        return caches.match(
                            request
                        );

                    }
                )

            );


            return;

        }

    }
);


// =========================================================
// PUSH NOTIFICATIONS
// =========================================================
//
// We are adding this foundation now.
//
// Later the Flask push engine will send payloads like:
//
// {
//     "title": "New event in KwaMhlanga",
//     "body": "KwaMhlanga Saturday Groove",
//     "url": "/listing/123"
// }
//
// =========================================================
// =========================================================
// PUSH NOTIFICATION
// =========================================================

self.addEventListener(
    "push",
    function(event) {

        let data = {

            title:
                "LaC Local Alert",

            body:
                "Something new is happening near you.",

            url:
                "/app",

            icon:
                "/static/icons/lac-192.png",

            badge:
                "/static/icons/lac-192.png",

            tag:
                "lac-local-alert"

        };


        if (event.data) {

            try {

                const incoming =
                    event.data.json();


                data = {
                    ...data,
                    ...incoming
                };


            } catch (error) {

                data.body =
                    event.data.text();

            }

        }


        const options = {

            body:
                data.body,

            icon:
                data.icon,

            badge:
                data.badge,

            tag:
                data.tag,

            data: {

                url:
                    data.url || "/app"

            }

        };


        event.waitUntil(

            self.registration
                .showNotification(
                    data.title,
                    options
                )

        );

    }
);


// =========================================================
// NOTIFICATION CLICK
// =========================================================

self.addEventListener(
    "notificationclick",
    function(event) {

        event.notification.close();


        const targetUrl =
            event.notification.data?.url
            ||
            "/app";


        event.waitUntil(

            clients
                .matchAll({

                    type:
                        "window",

                    includeUncontrolled:
                        true

                })
                .then(
                    function(clientList) {

                        for (
                            const client
                            of clientList
                        ) {

                            if (
                                "focus"
                                in client
                            ) {

                                return client
                                    .focus()
                                    .then(
                                        function() {

                                            if (
                                                "navigate"
                                                in client
                                            ) {

                                                return client
                                                    .navigate(
                                                        targetUrl
                                                    );

                                            }

                                        }
                                    );

                            }

                        }


                        if (
                            clients.openWindow
                        ) {

                            return clients
                                .openWindow(
                                    targetUrl
                                );

                        }

                    }
                )

        );

    }
);
