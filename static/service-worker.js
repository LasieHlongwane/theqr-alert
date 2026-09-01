// =========================================================
// LaC SERVICE WORKER
// =========================================================

const CACHE_VERSION = "lac-v3";

const STATIC_CACHE = CACHE_VERSION + "-static";


const STATIC_ASSETS = [
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
    function (event) {

        console.log(
            "[LaC SW] Installing..."
        );


        event.waitUntil(

            caches
                .open(STATIC_CACHE)
                .then(
                    function (cache) {

                        return cache.addAll(
                            STATIC_ASSETS
                        );

                    }
                )
                .catch(
                    function (error) {

                        console.error(
                            "[LaC SW] Cache install error:",
                            error
                        );

                    }
                )

        );


        self.skipWaiting();

    }
);


// =========================================================
// ACTIVATE
// =========================================================

self.addEventListener(
    "activate",
    function (event) {

        console.log(
            "[LaC SW] Activating..."
        );


        event.waitUntil(

            caches
                .keys()
                .then(
                    function (cacheNames) {

                        return Promise.all(

                            cacheNames.map(
                                function (cacheName) {

                                    if (
                                        cacheName.indexOf(
                                            "lac-"
                                        ) === 0
                                        &&
                                        cacheName !==
                                            STATIC_CACHE
                                    ) {

                                        return caches.delete(
                                            cacheName
                                        );

                                    }

                                }
                            )

                        );

                    }
                )
                .then(
                    function () {

                        return self.clients.claim();

                    }
                )

        );

    }
);


// =========================================================
// FETCH
// =========================================================

self.addEventListener(
    "fetch",
    function (event) {

        if (
            event.request.method !==
            "GET"
        ) {

            return;

        }


        const requestUrl =
            new URL(
                event.request.url
            );


        // -------------------------------------------------
        // NAVIGATION
        // -------------------------------------------------

        if (
            event.request.mode ===
            "navigate"
        ) {

            event.respondWith(

                fetch(
                    event.request
                )
                .catch(
                    function () {

                        return caches.match(
                            event.request
                        );

                    }
                )

            );

            return;

        }


        // -------------------------------------------------
        // STATIC FILES
        // -------------------------------------------------

        if (
            requestUrl.origin ===
                self.location.origin
            &&
            requestUrl.pathname.indexOf(
                "/static/"
            ) === 0
        ) {

            event.respondWith(

                caches
                    .match(
                        event.request
                    )
                    .then(
                        function (
                            cachedResponse
                        ) {

                            if (
                                cachedResponse
                            ) {

                                return cachedResponse;

                            }


                            return fetch(
                                event.request
                            )
                            .then(
                                function (
                                    networkResponse
                                ) {

                                    if (
                                        !networkResponse
                                        ||
                                        networkResponse.status !==
                                            200
                                    ) {

                                        return networkResponse;

                                    }


                                    const responseClone =
                                        networkResponse.clone();


                                    caches
                                        .open(
                                            STATIC_CACHE
                                        )
                                        .then(
                                            function (
                                                cache
                                            ) {

                                                cache.put(
                                                    event.request,
                                                    responseClone
                                                );

                                            }
                                        );


                                    return networkResponse;

                                }
                            );

                        }
                    )

            );

        }

    }
);


// =========================================================
// PUSH NOTIFICATION
// =========================================================

self.addEventListener(
    "push",
    function (event) {

        console.log(
            "[LaC SW] Push received."
        );


        let notificationData = {

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


        if (
            event.data
        ) {

            try {

                const incoming =
                    event.data.json();


                if (
                    incoming.title
                ) {

                    notificationData.title =
                        incoming.title;

                }


                if (
                    incoming.body
                ) {

                    notificationData.body =
                        incoming.body;

                }


                if (
                    incoming.url
                ) {

                    notificationData.url =
                        incoming.url;

                }


                if (
                    incoming.icon
                ) {

                    notificationData.icon =
                        incoming.icon;

                }


                if (
                    incoming.badge
                ) {

                    notificationData.badge =
                        incoming.badge;

                }


                if (
                    incoming.tag
                ) {

                    notificationData.tag =
                        incoming.tag;

                }


            } catch (error) {

                console.error(
                    "[LaC SW] Could not parse JSON push payload:",
                    error
                );


                notificationData.body =
                    event.data.text();

            }

        }


        const options = {

            body:
                notificationData.body,

            icon:
                notificationData.icon,

            badge:
                notificationData.badge,

            tag:
                notificationData.tag,

            data: {

                url:
                    notificationData.url

            }

        };


        event.waitUntil(

            self.registration
                .showNotification(

                    notificationData.title,

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
    function (event) {

        console.log(
            "[LaC SW] Notification clicked."
        );


        event.notification.close();


        let targetUrl =
            "/app";


        if (
            event.notification.data
            &&
            event.notification.data.url
        ) {

            targetUrl =
                event.notification.data.url;

        }


        event.waitUntil(

            self.clients
                .matchAll({

                    type:
                        "window",

                    includeUncontrolled:
                        true

                })
                .then(
                    function (clientList) {

                        for (
                            let i = 0;
                            i < clientList.length;
                            i++
                        ) {

                            const client =
                                clientList[i];


                            if (
                                "focus"
                                in client
                            ) {

                                return client
                                    .focus()
                                    .then(
                                        function () {

                                            if (
                                                "navigate"
                                                in client
                                            ) {

                                                return client.navigate(
                                                    targetUrl
                                                );

                                            }

                                        }
                                    );

                            }

                        }


                        if (
                            self.clients.openWindow
                        ) {

                            return self.clients.openWindow(
                                targetUrl
                            );

                        }

                    }
                )

        );

    }
);
