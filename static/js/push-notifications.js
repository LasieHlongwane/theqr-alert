document.addEventListener(
    "DOMContentLoaded",
    function () {

        // =================================================
        // ELEMENTS
        // =================================================

        const enableButton =
            document.getElementById(
                "lac-enable-notifications"
            );

        const statusElement =
            document.getElementById(
                "lac-notification-status"
            );

        const preferencePanel =
            document.getElementById(
                "lac-notification-preferences"
            );

        const saveButton =
            document.getElementById(
                "lac-save-notification-preferences"
            );

        const selectAllButton =
            document.getElementById(
                "lac-select-all-categories"
            );

        const preferenceStatus =
            document.getElementById(
                "lac-preference-status"
            );


        if (
            !enableButton
            ||
            !preferencePanel
            ||
            !saveButton
        ) {
            return;
        }


        // =================================================
        // CONFIG
        // =================================================

        const config =
            window.LAC_PUSH_CONFIG || {};


        const zoneId =
            config.zoneId;


        let pushSubscription =
            null;


        // =================================================
        // HELPERS
        // =================================================

        function setStatus(
            message
        ) {

            if (statusElement) {

                statusElement.textContent =
                    message;

            }

        }


        function setPreferenceStatus(
            message
        ) {

            if (preferenceStatus) {

                preferenceStatus.textContent =
                    message;

            }

        }


        function urlBase64ToUint8Array(
            base64String
        ) {

            const padding =
                "=".repeat(
                    (
                        4
                        -
                        base64String.length % 4
                    )
                    % 4
                );


            const base64 = (
                base64String
                + padding
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


        async function fetchWithTimeout(
            url,
            options = {},
            timeout = 15000
        ) {

            const controller =
                new AbortController();


            const timer =
                setTimeout(
                    () =>
                        controller.abort(),
                    timeout
                );


            try {

                return await fetch(
                    url,
                    {
                        ...options,
                        signal:
                            controller.signal,
                    }
                );

            }

            finally {

                clearTimeout(
                    timer
                );

            }

        }


        function getSelectedCategories() {

            const selected =
                document.querySelectorAll(
                    'input[name="lac_notification_category"]:checked'
                );


            return Array.from(
                selected
            ).map(
                input =>
                    input.value
            );

        }


        // =================================================
        // SERVICE WORKER
        // =================================================

        async function getServiceWorkerRegistration() {

            if (
                !(
                    "serviceWorker"
                    in navigator
                )
            ) {

                throw new Error(
                    "Service workers are not supported."
                );

            }


            await navigator.serviceWorker.register(
                "/service-worker.js",
                {
                    scope: "/",
                }
            );


            return await (
                navigator.serviceWorker.ready
            );

        }


        // =================================================
        // GET / CREATE PUSH SUBSCRIPTION
        // =================================================

        async function getPushSubscription() {

            const registration =
                await getServiceWorkerRegistration();


            let subscription =
                await (
                    registration
                    .pushManager
                    .getSubscription()
                );


            if (subscription) {

                return subscription;

            }


            const response =
                await fetchWithTimeout(
                    "/push/public-key"
                );


            if (!response.ok) {

                throw new Error(
                    "Unable to load push configuration."
                );

            }


            const data =
                await response.json();


            const publicKey =
                (
                    data.public_key
                    ||
                    data.publicKey
                    ||
                    data.key
                );


            if (!publicKey) {

                throw new Error(
                    "Push public key is missing."
                );

            }


            subscription =
                await (
                    registration
                    .pushManager
                    .subscribe({
                        userVisibleOnly:
                            true,

                        applicationServerKey:
                            urlBase64ToUint8Array(
                                publicKey
                            ),
                    })
                );


            return subscription;

        }


        // =================================================
        // ENABLE NOTIFICATIONS
        // =================================================

        enableButton.addEventListener(
            "click",
            async function () {

                if (
                    !(
                        "Notification"
                        in window
                    )
                ) {

                    setStatus(
                        "Notifications are not supported on this browser."
                    );

                    return;

                }


                if (!zoneId) {

                    setStatus(
                        "Unable to identify your local zone."
                    );

                    return;

                }


                try {

                    enableButton.disabled =
                        true;


                    setStatus(
                        "Checking notification permission..."
                    );


                    let permission =
                        Notification.permission;


                    if (
                        permission
                        !== "granted"
                    ) {

                        permission =
                            await (
                                Notification
                                .requestPermission()
                            );

                    }


                    if (
                        permission
                        !== "granted"
                    ) {

                        setStatus(
                            "Notification permission was not enabled."
                        );

                        return;

                    }


                    setStatus(
                        "Choose the local updates you want."
                    );


                    preferencePanel.hidden =
                        false;


                    preferencePanel.scrollIntoView({
                        behavior:
                            "smooth",

                        block:
                            "nearest",
                    });


                }

                catch (error) {

                    console.error(
                        "[LaC Push]",
                        error
                    );


                    setStatus(
                        "Unable to enable notifications."
                    );

                }

                finally {

                    enableButton.disabled =
                        false;

                }

            }
        );


        // =================================================
        // SELECT ALL
        // =================================================

        if (selectAllButton) {

            selectAllButton.addEventListener(
                "click",
                function () {

                    const checkboxes =
                        document.querySelectorAll(
                            'input[name="lac_notification_category"]'
                        );


                    const allSelected =
                        Array.from(
                            checkboxes
                        ).every(
                            checkbox =>
                                checkbox.checked
                        );


                    checkboxes.forEach(
                        checkbox => {

                            checkbox.checked =
                                !allSelected;

                        }
                    );


                    selectAllButton.textContent =
                        allSelected
                            ? "Select All"
                            : "Clear All";

                }
            );

        }


        // =================================================
        // SAVE PREFERENCES
        // =================================================

        saveButton.addEventListener(
            "click",
            async function () {

                const categories =
                    getSelectedCategories();


                if (
                    categories.length
                    === 0
                ) {

                    setPreferenceStatus(
                        "Choose at least one category."
                    );

                    return;

                }


                try {

                    saveButton.disabled =
                        true;


                    setPreferenceStatus(
                        "Saving your preferences..."
                    );


                    pushSubscription =
                        await (
                            getPushSubscription()
                        );


                    const response =
                        await fetchWithTimeout(

                            "/push/subscribe",

                            {
                                method:
                                    "POST",

                                headers: {
                                    "Content-Type":
                                        "application/json",
                                },

                                body:
                                    JSON.stringify({

                                        zone_id:
                                            zoneId,

                                        subscription:
                                            pushSubscription
                                                .toJSON(),

                                        categories:
                                            categories,

                                    }),
                            }

                        );


                    const responseText =
                        await response.text();


                    let data = {};


                    try {

                        data =
                            JSON.parse(
                                responseText
                            );

                    }

                    catch (error) {

                        console.error(
                            "[LaC Push] Invalid JSON response:",
                            responseText
                        );

                    }


                    if (
                        !response.ok
                        ||
                        !data.ok
                    ) {

                        throw new Error(
                            data.error
                            ||
                            "Unable to save preferences."
                        );

                    }


                    // -------------------------------------
                    // STORE LOCAL COPY
                    // -------------------------------------

                    localStorage.setItem(
                        "lac_notification_categories",
                        JSON.stringify(
                            data.categories
                            || categories
                        )
                    );


                    localStorage.setItem(
                        "lac_notifications_enabled",
                        "true"
                    );


                    setPreferenceStatus(
                        "✓ Your notification preferences are saved."
                    );


                    setStatus(
                        "✓ Local notifications enabled."
                    );


                    enableButton.textContent =
                        "Manage Notification Preferences";


                }

                catch (error) {

                    console.error(
                        "[LaC Push]",
                        error
                    );


                    setPreferenceStatus(
                        error.message
                        ||
                        "Unable to save notification preferences."
                    );

                }

                finally {

                    saveButton.disabled =
                        false;

                }

            }
        );


        // =================================================
        // RESTORE SAVED PREFERENCES
        // =================================================

        function restorePreferences() {

            let saved = [];


            try {

                saved =
                    JSON.parse(
                        localStorage.getItem(
                            "lac_notification_categories"
                        )
                        ||
                        "[]"
                    );

            }

            catch (error) {

                saved = [];

            }


            if (
                !Array.isArray(
                    saved
                )
            ) {

                saved = [];

            }


            saved.forEach(
                category => {

                    const checkbox =
                        document.querySelector(
                            'input[name="lac_notification_category"][value="' +
                            CSS.escape(category) +
                            '"]'
                        );


                    if (checkbox) {

                        checkbox.checked =
                            true;

                    }

                }
            );


            if (
                Notification.permission
                === "granted"
                &&
                saved.length > 0
            ) {

                setStatus(
                    "✓ Local notifications enabled."
                );


                enableButton.textContent =
                    "Manage Notification Preferences";

            }

        }


        // =================================================
        // MANAGE EXISTING PREFERENCES
        // =================================================

        if (
            localStorage.getItem(
                "lac_notifications_enabled"
            )
            === "true"
        ) {

            enableButton.addEventListener(
                "click",
                function () {

                    if (
                        Notification.permission
                        === "granted"
                    ) {

                        preferencePanel.hidden =
                            false;

                    }

                }
            );

        }


        restorePreferences();

    }
);
