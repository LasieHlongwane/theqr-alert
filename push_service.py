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

    # -----------------------------------------------------
    # VALIDATE SUBSCRIBER
    # -----------------------------------------------------

    if not subscriber:
        return False

    if not subscriber.active:
        return False


    # -----------------------------------------------------
    # VALIDATE VAPID CONFIGURATION
    # -----------------------------------------------------

    if not VAPID_PRIVATE_KEY:

        current_app.logger.error(
            "[LaC Push] VAPID_PRIVATE_KEY is missing."
        )

        return False


    if not VAPID_SUBJECT:

        current_app.logger.error(
            "[LaC Push] VAPID_SUBJECT is missing."
        )

        return False


    # -----------------------------------------------------
    # BUILD BROWSER SUBSCRIPTION
    # -----------------------------------------------------

    subscription_info = {

        "endpoint":
            subscriber.endpoint,

        "keys": {

            "p256dh":
                subscriber.p256dh,

            "auth":
                subscriber.auth_key,

        },

    }


    # -----------------------------------------------------
    # BUILD NOTIFICATION PAYLOAD
    # -----------------------------------------------------

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
            tag or "lac-local-alert",

    }


    # -----------------------------------------------------
    # SEND WEB PUSH
    # -----------------------------------------------------

    try:

        webpush(

            subscription_info=
                subscription_info,

            data=
                json.dumps(payload),

            vapid_private_key=
                VAPID_PRIVATE_KEY,

            vapid_claims={
                "sub":
                    VAPID_SUBJECT,
            },

            ttl=86400,

        )


        current_app.logger.info(
            "[LaC Push] SUCCESS "
            "subscriber_id=%s "
            "zone_id=%s",
            subscriber.id,
            subscriber.zone_id,
        )


        return True


    # -----------------------------------------------------
    # WEB PUSH ERROR
    # -----------------------------------------------------

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
            "[LaC Push] FAILED "
            "subscriber_id=%s "
            "status=%s "
            "error=%s",
            subscriber.id,
            status_code,
            exc,
        )


        # -------------------------------------------------
        # SUBSCRIPTION EXPIRED / REMOVED
        # -------------------------------------------------

        if status_code in (
            404,
            410,
        ):

            subscriber.active = False

            try:

                db.session.commit()

            except Exception as db_exc:

                db.session.rollback()

                current_app.logger.exception(
                    "[LaC Push] Unable to deactivate "
                    "expired subscriber "
                    "subscriber_id=%s error=%s",
                    subscriber.id,
                    db_exc,
                )


        return False


    # -----------------------------------------------------
    # UNEXPECTED ERROR
    # -----------------------------------------------------

    except Exception as exc:

        current_app.logger.exception(
            "[LaC Push] Unexpected error "
            "subscriber_id=%s "
            "error=%s",
            subscriber.id,
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
