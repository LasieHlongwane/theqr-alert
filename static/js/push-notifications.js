// =========================================================
// LaC PUSH NOTIFICATIONS
// =========================================================

document.addEventListener(
    "DOMContentLoaded",
    function () {

        const enableButton =
            document.getElementById(
                "lac-enable-notifications"
            );

        const statusElement =
            document.getElementById(
                "lac-notification-status"
            );


        // -------------------------------------------------
        // NO BUTTON ON THIS PAGE
        // -------------------------------------------------

        if (!enableButton) {

            console.log(
                "[LaC Push] Notification button not found."
            );

            return;
        }


        // -------------------------------------------------
        // ZONE CONFIG
        // -------------------------------------------------

        const pushConfig =
            window.LAC_PUSH_CONFIG || {};


        const zoneId =
            pushConfig.zoneId;


        console.log(
            "[LaC Push] Loaded. Zone:",
            zoneId
        );


        // =================================================
        // STATUS HELPER
        // =================================================

        function setStatus(
            message,
            type = ""
        ) {

            if (!statusElement) {
                return;
            }

            statusElement.textContent =
                message;

            statusElement.dataset.status =
                type;

        }


        // =================================================
        // BASE64 VAPID KEY → UINT8ARRAY
        // =================================================

        function urlBase64ToUint8Array(
            base64String
        ) {

            const padding =
                "=".repeat(
                    (
                        4 -
                        base64String.length % 4
                    ) % 4
                );


            const base64 =
                (
                    base64String +
                    padding
                )
                .replace(
                    /-/g,
                    "+"
                )
                .replace(
                    /_/g,
                    "/"
                );


            const rawData =
                window.atob(
                    base64
                );


            return Uint8Array.from(
                [...rawData].map(
                    char =>
                        char.charCodeAt(0)
                )
            );

        }


        // =================================================
        // PROMISE TIMEOUT
        // =================================================

        function withTimeout(
            promise,
            milliseconds,
            message
        ) {

            const timeout =
                new Promise(
                    (
                        resolve,
                        reject
                    ) => {

                        setTimeout(
                            () => {

                                reject(
                                    new Error(
                                        message
                                    )
                                );

                            },
                            milliseconds
                        );

                    }
                );


            return Promise.race([
                promise,
                timeout
            ]);

        }


        // =================================================
        // GET / REGISTER SERVICE WORKER
        // =================================================

        async function getServiceWorkerRegistration() {

            if (
                !(
                    "serviceWorker"
                    in navigator
                )
            ) {

                throw new Error(
                    "Service workers are not supported on this browser."
                );

            }


            console.log(
                "[LaC Push] Registering service worker..."
            );


            const registration =
                await withTimeout(

                    navigator.serviceWorker.register(
                        "/service-worker.js",
                        {
                            scope: "/"
                        }
                    ),

                    10000,

                    "Service worker registration timed out."

                );


            console.log(
                "[LaC Push] Service worker registered:",
                registration.scope
            );


            const readyRegistration =
                await withTimeout(

                    navigator.serviceWorker.ready,

                    10000,

                    "Service worker did not become ready."

                );


            console.log(
                "[LaC Push] Service worker ready."
            );


            return readyRegistration;

        }


        // =================================================
        // GET VAPID PUBLIC KEY
        // =================================================

        async function getPublicKey() {

            console.log(
                "[LaC Push] Fetching VAPID public key..."
            );


            const response =
                await fetch(
                    "/push/public-key",
                    {
                        method: "GET",
                        cache: "no-store"
                    }
                );


            if (!response.ok) {

                throw new Error(
                    "Could not load VAPID public key."
                );

            }


            const data =
                await response.json();


            if (
                !data.success ||
                !data.public_key
            ) {

                throw new Error(
                    data.error ||
                    "VAPID public key is missing."
                );

            }


            console.log(
                "[LaC Push] VAPID public key received."
            );


            return data.public_key;

        }


        // =================================================
        // SAVE SUBSCRIPTION TO FLASK
        // =================================================

        async function saveSubscription(
            subscription
        ) {

            console.log(
                "[LaC Push] Saving subscription..."
            );


            const response =
                await fetch(
                    "/push/subscribe",
                    {

                        method:
                            "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body:
                            JSON.stringify({

                                zone_id:
                                    zoneId,

                                subscription:
                                    subscription.toJSON()

                            })

                    }
                );


            const data =
                await response.json();


            if (
                !response.ok ||
                !data.success
            ) {

                throw new Error(
                    data.error ||
                    "Could not save push subscription."
                );

            }


            console.log(
                "[LaC Push] Subscription saved:",
                data
            );


            return data;

        }


        // =================================================
        // ENABLE PUSH
        // =================================================

        async function enableNotifications() {

            enableButton.disabled =
                true;

            enableButton.textContent =
                "Enabling...";


            setStatus(
                "Preparing notifications..."
            );


            try {

                // -----------------------------------------
                // CHECK BROWSER SUPPORT
                // -----------------------------------------

                if (
                    !(
                        "Notification"
                        in window
                    )
                ) {

                    throw new Error(
                        "Notifications are not supported on this browser."
                    );

                }


                if (
                    !(
                        "PushManager"
                        in window
                    )
                ) {

                    throw new Error(
                        "Push notifications are not supported on this browser."
                    );

                }


                if (!zoneId) {

                    throw new Error(
                        "LaC zone information is missing."
                    );

                }


                // -----------------------------------------
                // SERVICE WORKER
                // -----------------------------------------

                setStatus(
                    "Preparing notification service..."
                );


                const registration =
                    await getServiceWorkerRegistration();


                // -----------------------------------------
                // PERMISSION
                // -----------------------------------------

                let permission =
                    Notification.permission;


                console.log(
                    "[LaC Push] Current permission:",
                    permission
                );


                if (
                    permission ===
                    "default"
                ) {

                    setStatus(
                        "Waiting for notification permission..."
                    );


                    permission =
                        await Notification
                            .requestPermission();

                }


                console.log(
                    "[LaC Push] Permission result:",
                    permission
                );


                if (
                    permission !==
                    "granted"
                ) {

                    throw new Error(
                        "Notification permission was not granted."
                    );

                }


                // -----------------------------------------
                // VAPID PUBLIC KEY
                // -----------------------------------------

                setStatus(
                    "Connecting notifications..."
                );


                const publicKey =
                    await getPublicKey();


                const applicationServerKey =
                    urlBase64ToUint8Array(
                        publicKey
                    );


                // -----------------------------------------
                // EXISTING SUBSCRIPTION
                // -----------------------------------------

                let subscription =
                    await registration
                        .pushManager
                        .getSubscription();


                // -----------------------------------------
                // CREATE SUBSCRIPTION
                // -----------------------------------------

                if (!subscription) {

                    console.log(
                        "[LaC Push] Creating browser subscription..."
                    );


                    subscription =
                        await withTimeout(

                            registration
                                .pushManager
                                .subscribe({

                                    userVisibleOnly:
                                        true,

                                    applicationServerKey:
                                        applicationServerKey

                                }),

                            15000,

                            "Browser push subscription timed out."

                        );

                } else {

                    console.log(
                        "[LaC Push] Existing subscription found."
                    );

                }


                // -----------------------------------------
                // SAVE TO DATABASE
                // -----------------------------------------

                await saveSubscription(
                    subscription
                );


                // -----------------------------------------
                // SUCCESS
                // -----------------------------------------

                enableButton.textContent =
                    "Notifications Enabled ✓";


                setStatus(
                    "✓ Local notifications are enabled.",
                    "success"
                );


                console.log(
                    "[LaC Push] Notifications successfully enabled."
                );

            } catch (error) {

                console.error(
                    "[LaC Push] ERROR:",
                    error
                );


                enableButton.disabled =
                    false;


                enableButton.textContent =
                    "Enable Local Notifications";


                setStatus(
                    error.message ||
                    "Could not enable notifications.",
                    "error"
                );

            }

        }


        // =================================================
        // BUTTON CLICK
        // =================================================

        enableButton.addEventListener(
            "click",
            enableNotifications
        );

    }
);
