(function () {

    "use strict";


    // =========================================
    // ALLOWED EVENTS
    // =========================================

    const ALLOWED_EVENTS = new Set([
        "category_view",
        "listing_view",
        "whatsapp_click",
        "call_click",
        "share_click",
        "directions_click"
    ]);


    // =========================================
    // GET ACCESS POINT ID
    // =========================================

    function getStoredAccessPointId() {

        try {

            const storedId =
                localStorage.getItem(
                    "lac_access_point_id"
                );

            if (!storedId) {
                return null;
            }

            const parsedId =
                Number(storedId);

            return Number.isFinite(parsedId)
                && parsedId > 0
                ? parsedId
                : null;

        } catch (error) {

            console.debug(
                "[LaC Engagement] "
                + "Could not read localStorage:",
                error
            );

            return null;

        }

    }


    // =========================================
    // SEND ENGAGEMENT EVENT
    // =========================================

    function sendEngagement(
        eventType,
        extraData = {}
    ) {

        // =====================================
        // VALIDATE EVENT
        // =====================================

        if (
            !ALLOWED_EVENTS.has(eventType)
        ) {

            console.debug(
                "[LaC Engagement] Invalid event:",
                eventType
            );

            return;

        }


        // =====================================
        // READ PAGE CONFIGURATION
        // =====================================

        const config =
            window.LAC_ENGAGEMENT_CONFIG || {};


        const accessPointId =
            config.accessPointId
            ||
            getStoredAccessPointId()
            ||
            null;


        // =====================================
        // BUILD PAYLOAD
        // =====================================

        const payload = {

            event_type:
                eventType,

            zone_id:
                config.zoneId ?? null,

            access_point_id:
                accessPointId,

            content_item_id:
                config.contentItemId ?? null,

            category:
                config.category ?? null,

            ...extraData

        };


        const body =
            JSON.stringify(payload);


        console.debug(
            "[LaC Engagement] Sending event:",
            payload
        );


        // =====================================
        // LISTING AND CATEGORY VIEWS
        //
        // Use fetch so the browser sends the
        // request while the page is open.
        // =====================================

        if (
            eventType === "listing_view"
            ||
            eventType === "category_view"
        ) {

            fetch(
                "/api/engagement",
                {

                    method: "POST",

                    headers: {

                        "Content-Type":
                            "application/json"

                    },

                    body: body,

                    credentials: "same-origin",

                    cache: "no-store",

                    keepalive: true

                }
            )
            .then(
                async function (response) {

                    if (!response.ok) {

                        console.error(
                            "[LaC Engagement] "
                            + "Server rejected event:",
                            response.status,
                            await response.text()
                        );

                        return;

                    }


                    console.debug(
                        "[LaC Engagement] "
                        + "Event recorded successfully:",
                        eventType
                    );

                }
            )
            .catch(
                function (error) {

                    console.error(
                        "[LaC Engagement] "
                        + "Request failed:",
                        error
                    );

                }
            );

            return;

        }


        // =====================================
        // CLICK EVENTS
        //
        // Use sendBeacon because the browser
        // may immediately leave the page.
        // =====================================

        if (
            navigator.sendBeacon
        ) {

            try {

                const blob =
                    new Blob(
                        [body],
                        {
                            type:
                                "application/json"
                        }
                    );


                const sent =
                    navigator.sendBeacon(
                        "/api/engagement",
                        blob
                    );


                if (sent) {

                    console.debug(
                        "[LaC Engagement] "
                        + "Event queued:",
                        eventType
                    );

                    return;

                }

            } catch (error) {

                console.debug(
                    "[LaC Engagement] "
                    + "sendBeacon error:",
                    error
                );

            }

        }


        // =====================================
        // FETCH FALLBACK
        // =====================================

        fetch(
            "/api/engagement",
            {

                method: "POST",

                headers: {

                    "Content-Type":
                        "application/json"

                },

                body: body,

                credentials: "same-origin",

                keepalive: true

            }
        )
        .catch(
            function (error) {

                console.error(
                    "[LaC Engagement] "
                    + "Fetch fallback failed:",
                    error
                );

            }
        );

    }


    // =========================================
    // MAKE TRACKER AVAILABLE GLOBALLY
    // =========================================

    window.LaCTrack =
        sendEngagement;


    // =========================================
    // AUTOMATIC CLICK TRACKING
    // =========================================

    document.addEventListener(
        "click",
        function (event) {

            const target =
                event.target;


            if (
                !target
                ||
                typeof target.closest !== "function"
            ) {

                return;

            }


            const element =
                target.closest(
                    "[data-lac-event]"
                );


            if (!element) {

                return;

            }


            const eventType =
                element.dataset.lacEvent;


            if (!eventType) {

                return;

            }


            sendEngagement(
                eventType
            );

        }
    );


})();
