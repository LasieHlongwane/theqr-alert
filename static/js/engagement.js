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
    // SEND ENGAGEMENT EVENT
    // =========================================

    function sendEngagement(
        eventType,
        extraData = {}
    ) {

        if (
            !ALLOWED_EVENTS.has(
                eventType
            )
        ) {

            console.debug(
                "[LaC Engagement] Invalid event:",
                eventType
            );

            return;
        }


        const config =
            window.LAC_ENGAGEMENT_CONFIG || {};


        const storedAccessPointId =
            Number(
                localStorage.getItem(
                    "lac_access_point_id"
                )
            );


        const payload = {

            event_type:
                eventType,

            zone_id:
                config.zoneId || null,

            access_point_id:
                config.accessPointId
                ||
                storedAccessPointId
                ||
                null,

            content_item_id:
                config.contentItemId || null,

            category:
                config.category || null,

            ...extraData

        };


        const body =
            JSON.stringify(payload);


        // =====================================
        // SEND BEACON
        //
        // Best for links such as WhatsApp,
        // Call and Maps because the browser
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

                method:
                    "POST",

                headers: {

                    "Content-Type":
                        "application/json"

                },

                body:
                    body,

                keepalive:
                    true

            }
        )
        .catch(
            function (error) {

                console.debug(
                    "[LaC Engagement] "
                    + "fetch error:",
                    error
                );

            }
        );

    }


    // Make tracker available globally.

    window.LaCTrack =
        sendEngagement;


    // =========================================
    // AUTOMATIC CLICK TRACKING
    // =========================================

    document.addEventListener(
        "click",
        function (event) {

            const element =
                event.target.closest(
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
