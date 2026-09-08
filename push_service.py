import json
import os

from flask import current_app
from pywebpush import (
    webpush,
    WebPushException,
)

from models import (
    db,
    PushSubscriber,
    PushSubscriberPreference,
)


# =========================================================
# VAPID CONFIGURATION
# =========================================================

VAPID_PUBLIC_KEY = os.environ.get(
    "VAPID_PUBLIC_KEY",
    "",
)

VAPID_PRIVATE_KEY = os.environ.get(
    "VAPID_PRIVATE_KEY",
    "",
)

VAPID_SUBJECT = os.environ.get(
    "VAPID_SUBJECT",
    "",
)


# =========================================================
# SEND ONE WEB PUSH NOTIFICATION
# =========================================================

def send_push_notification(
    subscriber,
    title,
    body,
    url="/app",
    icon="/static/icons/lac-192.png",
    badge="/static/icons/lac-notification.png",
    tag=None,
):

    # =====================================================
    # VALIDATE SUBSCRIBER
    # =====================================================

    if not subscriber:

        current_app.logger.warning(
            "[Kalxa Push] Notification skipped: "
            "subscriber is missing."
        )

        return False

    if not subscriber.active:

        current_app.logger.info(
            "[Kalxa Push] Notification skipped: "
            "subscriber is inactive "
            "subscriber_id=%s",
            getattr(
                subscriber,
                "id",
                None,
            ),
        )

        return False

    # =====================================================
    # VALIDATE SUBSCRIPTION DATA
    #
    # A valid Web Push subscription requires:
    #
    # endpoint
    # p256dh
    # auth
    # =====================================================

    endpoint = (
        str(
            subscriber.endpoint
            or ""
        )
        .strip()
    )

    p256dh = (
        str(
            subscriber.p256dh
            or ""
        )
        .strip()
    )

    auth_key = (
        str(
            subscriber.auth_key
            or ""
        )
        .strip()
    )

    if not endpoint:

        current_app.logger.warning(
            "[Kalxa Push] Subscriber has no "
            "push endpoint "
            "subscriber_id=%s",
            subscriber.id,
        )

        return False

    if not p256dh:

        current_app.logger.warning(
            "[Kalxa Push] Subscriber has no "
            "p256dh key "
            "subscriber_id=%s",
            subscriber.id,
        )

        return False

    if not auth_key:

        current_app.logger.warning(
            "[Kalxa Push] Subscriber has no "
            "auth key "
            "subscriber_id=%s",
            subscriber.id,
        )

        return False

    # =====================================================
    # VALIDATE VAPID CONFIGURATION
    # =====================================================

    if not VAPID_PRIVATE_KEY:

        current_app.logger.error(
            "[Kalxa Push] VAPID_PRIVATE_KEY "
            "is missing."
        )

        return False

    if not VAPID_SUBJECT:

        current_app.logger.error(
            "[Kalxa Push] VAPID_SUBJECT "
            "is missing."
        )

        return False

    # =====================================================
    # NORMALIZE NOTIFICATION CONTENT
    # =====================================================

    title = (
        str(
            title
            or "Kalxa Local Alert"
        )
        .strip()
    )

    body = (
        str(
            body
            or ""
        )
        .strip()
    )

    url = (
        str(
            url
            or "/app"
        )
        .strip()
    )

    icon = (
        str(
            icon
            or "/static/icons/lac-192.png"
        )
        .strip()
    )

    badge = (
        str(
            badge
            or "/static/icons/lac-notification.png"
        )
        .strip()
    )

    if tag:

        tag = str(
            tag
        ).strip()

    if not tag:

        tag = "kalxa-local-alert"

    if not title:

        title = "Kalxa Local Alert"

    if not url:

        url = "/app"

    # =====================================================
    # BUILD BROWSER PUSH SUBSCRIPTION
    # =====================================================

    subscription_info = {

        "endpoint":
            endpoint,

        "keys": {

            "p256dh":
                p256dh,

            "auth":
                auth_key,
        },
    }

    # =====================================================
    # BUILD NOTIFICATION PAYLOAD
    #
    # Your service worker receives this JSON and displays
    # the actual browser / Android notification.
    # =====================================================

    payload = {

        "title":
            title,

        "body":
            body,

        "url":
            url,

        "icon":
            icon,

        "badge":
            badge,

        "tag":
            tag,
    }

    # =====================================================
    # SEND WEB PUSH
    # =====================================================

    try:

        webpush(

            subscription_info=
                subscription_info,

            data=
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),

            vapid_private_key=
                VAPID_PRIVATE_KEY,

            vapid_claims={
                "sub":
                    VAPID_SUBJECT,
            },

            # -------------------------------------------------
            # Keep the push available for up to 24 hours if
            # the user's device is temporarily offline.
            # -------------------------------------------------

            ttl=86400,
        )

        current_app.logger.info(
            "[Kalxa Push] SUCCESS "
            "subscriber_id=%s "
            "zone_id=%s "
            "tag=%s "
            "url=%s",
            subscriber.id,
            subscriber.zone_id,
            tag,
            url,
        )

        return True

    # =====================================================
    # WEB PUSH PROVIDER ERROR
    # =====================================================

    except WebPushException as exc:

        response = getattr(
            exc,
            "response",
            None,
        )

        status_code = (
            getattr(
                response,
                "status_code",
                None,
            )
            if response
            else None
        )

        current_app.logger.warning(
            "[Kalxa Push] FAILED "
            "subscriber_id=%s "
            "zone_id=%s "
            "status=%s "
            "error=%s",
            subscriber.id,
            subscriber.zone_id,
            status_code,
            exc,
        )

        # =================================================
        # EXPIRED / REMOVED PUSH SUBSCRIPTION
        #
        # 404 / 410 means the browser push subscription
        # should no longer be used.
        #
        # Deactivate it so future approval notifications
        # don't repeatedly attempt delivery.
        # =================================================

        if status_code in (
            404,
            410,
        ):

            subscriber.active = False

            try:

                db.session.commit()

                current_app.logger.info(
                    "[Kalxa Push] Expired subscriber "
                    "deactivated "
                    "subscriber_id=%s "
                    "status=%s",
                    subscriber.id,
                    status_code,
                )

            except Exception as db_exc:

                db.session.rollback()

                current_app.logger.exception(
                    "[Kalxa Push] Unable to deactivate "
                    "expired subscriber "
                    "subscriber_id=%s "
                    "error=%s",
                    subscriber.id,
                    db_exc,
                )

        return False

    # =====================================================
    # UNEXPECTED ERROR
    #
    # Never allow one Web Push failure to crash the
    # approval workflow.
    # =====================================================

    except Exception as exc:

        current_app.logger.exception(
            "[Kalxa Push] Unexpected delivery error "
            "subscriber_id=%s "
            "zone_id=%s "
            "error=%s",
            getattr(
                subscriber,
                "id",
                None,
            ),
            getattr(
                subscriber,
                "zone_id",
                None,
            ),
            exc,
        )

        return False

# =========================================================
# SEND ZONE / CATEGORY PUSH NOTIFICATION
# =========================================================

def send_zone_push_notification(
    zone_id,
    title,
    body,
    url="/app",
    tag=None,
    category=None,
):

    # =====================================================
    # NORMALIZE / VALIDATE INPUT
    # =====================================================

    try:
        zone_id = int(zone_id)

    except (TypeError, ValueError):

        current_app.logger.error(
            "[Kalxa Push] Invalid zone_id=%s",
            zone_id,
        )

        return {
            "total": 0,
            "sent": 0,
            "failed": 0,
        }

    title = str(title or "").strip()
    body = str(body or "").strip()
    url = str(url or "/app").strip() or "/app"

    if category is not None:

        category = str(
            category
        ).strip()

        if not category:
            category = None

    if tag is not None:

        tag = str(
            tag
        ).strip() or None

    # =====================================================
    # REQUIRE NOTIFICATION CONTENT
    # =====================================================

    if not title:

        current_app.logger.error(
            "[Kalxa Push] Notification rejected because "
            "title is empty. zone_id=%s category=%s",
            zone_id,
            category,
        )

        return {
            "total": 0,
            "sent": 0,
            "failed": 0,
        }

    # =====================================================
    # BASE SUBSCRIBER QUERY
    #
    # Every push recipient must:
    #
    # 1. belong to the target zone
    # 2. still have an active push subscription
    # =====================================================

    query = (
        PushSubscriber.query
        .filter(
            PushSubscriber.zone_id == zone_id,
            PushSubscriber.active.is_(True),
        )
    )

    # =====================================================
    # CATEGORY TARGETING
    #
    # When category is supplied:
    #
    #     zone
    #       +
    #     active subscription
    #       +
    #     category preference
    #
    # must ALL match.
    #
    # Example:
    #
    # Approved event:
    #     zone = KwaMhlanga
    #     category = upcoming-event-🥹🔥
    #
    # Only users who:
    #
    #     enabled notifications
    #     +
    #     belong to KwaMhlanga
    #     +
    #     selected upcoming-event-🥹🔥
    #
    # receive the notification.
    #
    # If category=None, all active subscribers in the
    # zone are targeted. Keep this behaviour for future
    # system/emergency/admin notifications.
    # =====================================================

    if category:

        query = (
            query
            .join(
                PushSubscriberPreference,
                PushSubscriberPreference.subscriber_id
                == PushSubscriber.id,
            )
            .filter(
                PushSubscriberPreference.category
                == category
            )
        )

    # =====================================================
    # LOAD MATCHING SUBSCRIBERS
    # =====================================================

    try:

        subscribers = (
            query
            .distinct()
            .all()
        )

    except Exception as exc:

        current_app.logger.exception(
            "[Kalxa Push] Unable to load subscribers "
            "zone_id=%s category=%s error=%s",
            zone_id,
            category,
            exc,
        )

        return {
            "total": 0,
            "sent": 0,
            "failed": 0,
        }

    # =====================================================
    # NO MATCHING SUBSCRIBERS
    #
    # This is NOT an error.
    #
    # It simply means nobody in this zone currently has
    # notifications enabled for this category.
    # =====================================================

    if not subscribers:

        current_app.logger.info(
            "[Kalxa Push] No matching subscribers "
            "zone_id=%s category=%s",
            zone_id,
            category,
        )

        return {
            "total": 0,
            "sent": 0,
            "failed": 0,
        }

    # =====================================================
    # SEND NOTIFICATION
    #
    # IMPORTANT:
    #
    # Every subscriber is isolated.
    #
    # If subscriber #2 fails, subscriber #3 should still
    # receive the notification.
    # =====================================================

    sent_count = 0
    failed_count = 0

    for subscriber in subscribers:

        try:

            success = send_push_notification(

                subscriber=subscriber,

                title=title,

                body=body,

                url=url,

                tag=tag,
            )

            if success:

                sent_count += 1

            else:

                failed_count += 1

                current_app.logger.warning(
                    "[Kalxa Push] Delivery failed "
                    "subscriber_id=%s "
                    "zone_id=%s "
                    "category=%s",
                    subscriber.id,
                    zone_id,
                    category,
                )

        except Exception as exc:

            failed_count += 1

            # ---------------------------------------------
            # DO NOT STOP THE BATCH
            #
            # One invalid/expired/problematic browser
            # subscription must not prevent notifications
            # from reaching everybody else.
            # ---------------------------------------------

            current_app.logger.exception(
                "[Kalxa Push] Subscriber delivery "
                "raised an exception "
                "subscriber_id=%s "
                "zone_id=%s "
                "category=%s "
                "error=%s",
                subscriber.id,
                zone_id,
                category,
                exc,
            )

    # =====================================================
    # RESULT
    # =====================================================

    result = {

        "total":
            len(subscribers),

        "sent":
            sent_count,

        "failed":
            failed_count,
    }

    # =====================================================
    # DELIVERY SUMMARY
    # =====================================================

    current_app.logger.info(
        "[Kalxa Push] Zone/category notification "
        "completed "
        "zone_id=%s "
        "category=%s "
        "total=%s "
        "sent=%s "
        "failed=%s",
        zone_id,
        category,
        result["total"],
        result["sent"],
        result["failed"],
    )

    return result
