import json
import os

from flask import current_app
from pywebpush import webpush, WebPushException

from models import db, PushSubscriber


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
# SEND NOTIFICATION TO ALL ACTIVE SUBSCRIBERS IN A ZONE
# =========================================================

def send_zone_push_notification(
    zone_id,
    title,
    body,
    url="/app",
    tag=None,
):

    # -----------------------------------------------------
    # GET ACTIVE SUBSCRIBERS FOR THIS ZONE ONLY
    # -----------------------------------------------------

    subscribers = (
        PushSubscriber.query
        .filter_by(
            zone_id=zone_id,
            active=True,
        )
        .all()
    )


    # -----------------------------------------------------
    # NO SUBSCRIBERS
    # -----------------------------------------------------

    if not subscribers:

        current_app.logger.info(
            "[LaC Push] No active subscribers "
            "for zone_id=%s",
            zone_id,
        )

        return {
            "total": 0,
            "sent": 0,
            "failed": 0,
        }


    # -----------------------------------------------------
    # SEND TO EACH SUBSCRIBER
    # -----------------------------------------------------

    sent_count = 0
    failed_count = 0


    for subscriber in subscribers:

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


    # -----------------------------------------------------
    # RESULT
    # -----------------------------------------------------

    result = {

        "total":
            len(subscribers),

        "sent":
            sent_count,

        "failed":
            failed_count,

    }


    current_app.logger.info(
        "[LaC Push] Zone notification "
        "zone_id=%s "
        "total=%s "
        "sent=%s "
        "failed=%s",
        zone_id,
        result["total"],
        result["sent"],
        result["failed"],
    )


    return result
