// =========================================================
// LaC PUSH NOTIFICATIONS
// =========================================================
//
// Responsibilities:
//
// 1. Ask for notification permission.
// 2. Get LaC's public VAPID key.
// 3. Create a browser PushSubscription.
// 4. Send the subscription to Flask.
// 5. Associate the subscription with the current zone.
// 6. Update the notification UI.
//
// =========================================================


// =========================================================
// ELEMENTS
// =========================================================

const lacNotificationButton =
    document.getElementById(
        "lac-enable-notifications"
    );

const lacNotificationStatus =
    document.getElementById(
        "lac-notification-status"
    );


// =========================================================
// CONFIG
// =========================================================

const lacPushConfig =
    window.LAC_PUSH_CONFIG || {};


const lacZoneId =
    lacPushConfig.zoneId;


// =========================================================
// CONVERT VAPID PUBLIC KEY
// =========================================================
//
// PushManager.subscribe() requires the public key
// as a Uint8Array rather than a Base64 string.
//
// =========================================================

function urlBase64ToUint8Array(
    base64String
) {

    const padding =
        "=".repeat(
            (4 - base64String.length % 4) % 4
        );


    const base64 =
        (
            base64String +
            padding
        )
        .replace(/-/g, "+")
        .replace(/_/g, "/");


    const rawData =
        window.atob(
            base64
        );


    return Uint8Array.from(
        [...rawData].map(
            character =>
                character.charCodeAt(0)
        )
    );

}


// =========================================================
// STATUS MESSAGE
// =========================================================

function setNotificationStatus(
    message,
    type = "normal"
) {

    if (!lacNotificationStatus) {
        return;
    }


    lacNotificationStatus.textContent =
        message;


    lacNotificationStatus.dataset.status =
        type;

}


// =========================================================
// GET PUBLIC VAPID KEY
// =========================================================

async function getVapidPublicKey() {

    const response =
        await fetch(
            "/push/public-key"
        );


    const data =
        await response.json();


    if (
        !response.ok ||
        !data.success ||
        !data.public_key
    ) {

        throw new Error(
            data.error ||
            "Could not load notification key."
        );

    }


    return data.public_key;

}


// =========================================================
// SAVE SUBSCRIPTION TO LaC
// =========================================================

async function saveSubscription(
    subscription
) {

    const response =
        await fetch(
            "/push/subscribe",
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json",
                },

                body: JSON.stringify({

                    zone_id:
                        lacZoneId,

                    subscription:
                        subscription.toJSON(),

                }),
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
            "Could not save notification subscription."
        );

    }


    return data;

}


// =========================================================
// ENABLE PUSH NOTIFICATIONS
// =========================================================

async function enableNotifications() {

    // -----------------------------------------------------
    // BASIC BROWSER SUPPORT
    // -----------------------------------------------------

    if (
        !("serviceWorker" in navigator)
    ) {

        setNotificationStatus(
            "This browser does not support service workers.",
            "error"
        );

        return;

    }


    if (
        !("PushManager" in window)
    ) {

        setNotificationStatus(
            "Push notifications are not supported on this device.",
            "error"
        );

        return;

    }


    if (
        !("Notification" in window)
    ) {

        setNotificationStatus(
            "Notifications are not supported on this device.",
            "error"
        );

        return;

    }


    if (!lacZoneId) {

        setNotificationStatus(
            "LaC could not identify your local zone.",
            "error"
        );

        return;

    }


    if (lacNotificationButton) {

        lacNotificationButton.disabled =
            true;

        lacNotificationButton.textContent =
            "Enabling...";

    }


    try {

        // -------------------------------------------------
        // REQUEST PERMISSION
        // -------------------------------------------------

        let permission =
            Notification.permission;


        if (
            permission === "default"
        ) {

            permission =
                await Notification
                    .requestPermission();

        }


        if (
            permission !== "granted"
        ) {

            setNotificationStatus(
                "Notifications were not enabled.",
                "error"
            );


            if (lacNotificationButton) {

                lacNotificationButton.disabled =
                    false;

                lacNotificationButton.textContent =
                    "Enable Local Notifications";

            }


            return;

        }


        // -------------------------------------------------
        // WAIT FOR SERVICE WORKER
        // -------------------------------------------------

        const registration =
            await navigator
                .serviceWorker
                .ready;


        // -------------------------------------------------
        // CHECK EXISTING SUBSCRIPTION
        // -------------------------------------------------

        let subscription =
            await registration
                .pushManager
                .getSubscription();


        // -------------------------------------------------
        // CREATE SUBSCRIPTION IF NEEDED
        // -------------------------------------------------

        if (!subscription) {

            const publicKey =
                await getVapidPublicKey();


            const applicationServerKey =
                urlBase64ToUint8Array(
                    publicKey
                );


            subscription =
                await registration
                    .pushManager
                    .subscribe({

                        userVisibleOnly:
                            true,

                        applicationServerKey:
                            applicationServerKey,

                    });

        }


        // -------------------------------------------------
        // SAVE TO FLASK / POSTGRESQL
        // -------------------------------------------------

        const result =
            await saveSubscription(
                subscription
            );


        console.log(
            "[LaC Push] Subscription saved:",
            result
        );


        // -------------------------------------------------
        // SUCCESS UI
        // -------------------------------------------------

        setNotificationStatus(
            "✓ Local notifications are enabled.",
            "success"
        );


        if (lacNotificationButton) {

            lacNotificationButton.disabled =
                true;

            lacNotificationButton.textContent =
                "Notifications Enabled";

        }


    } catch (error) {

        console.error(
            "[LaC Push] Error:",
            error
        );


        setNotificationStatus(
            "Could not enable notifications. Please try again.",
            "error"
        );


        if (lacNotificationButton) {

            lacNotificationButton.disabled =
                false;

            lacNotificationButton.textContent =
                "Enable Local Notifications";

        }

    }

}


// =========================================================
// CHECK EXISTING STATUS
// =========================================================

async function checkNotificationStatus() {

    if (
        !("serviceWorker" in navigator) ||
        !("PushManager" in window) ||
        !("Notification" in window)
    ) {

        return;

    }


    if (
        Notification.permission !==
        "granted"
    ) {

        return;

    }


    try {

        const registration =
            await navigator
                .serviceWorker
                .ready;


        const subscription =
            await registration
                .pushManager
                .getSubscription();


        if (subscription) {

            // Re-save it so the subscriber follows
            // the current LaC zone.

            await saveSubscription(
                subscription
            );


            setNotificationStatus(
                "✓ Local notifications are enabled.",
                "success"
            );


            if (lacNotificationButton) {

                lacNotificationButton.disabled =
                    true;

                lacNotificationButton.textContent =
                    "Notifications Enabled";

            }

        }


    } catch (error) {

        console.error(
            "[LaC Push] Status check failed:",
            error
        );

    }

}


// =========================================================
// BUTTON
// =========================================================

if (lacNotificationButton) {

    lacNotificationButton.addEventListener(
        "click",
        function(event) {

            event.preventDefault();


            enableNotifications();

        }
    );

}


// =========================================================
// INITIAL PAGE LOAD
// =========================================================

checkNotificationStatus();
