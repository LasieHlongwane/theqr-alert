import os
import re
import uuid
import base64
import hashlib
import hmac
import json
import time
import requests
from datetime import (
    date,
    datetime,
    timedelta,
    time, 
)
from zoneinfo import ZoneInfo
from push_service import (
    send_push_notification,
    send_zone_push_notification,
)
from urllib.parse import quote
from flask import send_from_directory
import cloudinary.uploader
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    current_app,
    jsonify,
    redirect,
    session,
    render_template,
    request,
    url_for,
)
from flask_migrate import Migrate
from sqlalchemy import or_
from pricing import (
    PRICING_MODEL_CAMPAIGN,
)
from image_utils import (
    upload_lac_image,
    allowed_image_file,
)
from models import (
    db,
    Zone,
    AccessPoint,
    ContentItem,
    QRScan,
    PendingSubmission,
    PendingSubmissionImage,
    ZoneCategoryAppearance,
    ContentDistributionZone,
    PushSubscriberPreference,
    Category,
    PushSubscriber,
    ListingClaim,
    EngagementEvent,
)
from categories import (
    BUSINESS_CATEGORIES,
    CONSUMER_CATEGORIES,
    normalize_category,
    get_category_aliases,
    get_business_category,
    get_consumer_category,
    get_business_category_choices,
)
from admin import admin_bp
from pricing import (
    calculate_kalxa_price,
    KalxaPricingError,
    PRICING_MODEL_PRESENCE,
    PRICING_MODEL_CAMPAIGN,
    get_pricing_model,
)

# ============================================================
# KALXA LOCAL TIMEZONE
# ============================================================

KALXA_TIMEZONE = ZoneInfo(
    "Africa/Johannesburg"
)
YOCO_SECRET_KEY = os.getenv("YOCO_SECRET_KEY")
YOCO_PUBLIC_KEY = os.getenv("YOCO_PUBLIC_KEY")
YOCO_WEBHOOK_SECRET = os.getenv("YOCO_WEBHOOK_SECRET")
YOCO_MODE = os.getenv("YOCO_MODE", "test")

YOCO_CHECKOUT_URL = "https://payments.yoco.com/api/checkouts"
# =========================================================
# ENVIRONMENT
# =========================================================

load_dotenv()


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

app.config[
    "MAX_CONTENT_LENGTH"
] = 5 * 1024 * 1024

# =========================================================
# WEB PUSH / VAPID CONFIGURATION
# =========================================================

ENGAGEMENT_EVENT_TYPES = {
    "category_view",
    "listing_view",
    "whatsapp_click",
    "call_click",
    "share_click",
    "directions_click",
}

# =========================================================
# PHONE / WHATSAPP HELPERS
# =========================================================
# =========================================================
# SERVICE WORKER
# =========================================================

@app.route(
    "/service-worker.js"
)
def service_worker():

    response = send_from_directory(
        app.static_folder,
        "service-worker.js",
        mimetype=
            "application/javascript",
    )


    response.headers[
        "Cache-Control"
    ] = (
        "no-cache, "
        "no-store, "
        "must-revalidate"
    )


    response.headers[
        "Pragma"
    ] = "no-cache"


    response.headers[
        "Expires"
    ] = "0"


    response.headers[
        "Service-Worker-Allowed"
    ] = "/"


    return response

def normalize_phone_number(value):

    if not value:
        return ""

    digits = re.sub(
        r"\D",
        "",
        value,
    )

    if digits.startswith("0"):
        digits = (
            "27"
            + digits[1:]
        )

    return digits


@app.template_filter(
    "whatsapp_link"
)
def whatsapp_link(value):

    number = normalize_phone_number(
        value
    )

    if not number:
        return "#"

    message = quote(
        "Hi, I saw your listing on LaC "
        "and I would like more information."
    )

    return (
        f"https://wa.me/{number}"
        f"?text={message}"
    )


@app.template_filter(
    "phone_link"
)
def phone_link(value):

    number = normalize_phone_number(
        value
    )

    if not number:
        return "#"

    return f"tel:+{number}"


# =========================================================
# IMAGE UPLOAD HELPERS
# =========================================================

ALLOWED_IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
}


def allowed_image_file(filename):

    return (
        "."
        in filename
        and filename.rsplit(
            ".",
            1,
        )[1].lower()
        in ALLOWED_IMAGE_EXTENSIONS
    )


# =========================================================
# SECRET KEY
# =========================================================

secret_key = os.environ.get(
    "SECRET_KEY"
)

if not secret_key:
    raise RuntimeError(
        "SECRET_KEY environment variable is required."
    )

app.secret_key = secret_key


# =========================================================
# DATABASE
# =========================================================

database_url = os.environ.get(
    "DATABASE_URL"
)

if not database_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is required."
    )

app.config[
    "SQLALCHEMY_DATABASE_URI"
] = database_url

app.config[
    "SQLALCHEMY_TRACK_MODIFICATIONS"
] = False

app.config[
    "SQLALCHEMY_ENGINE_OPTIONS"
] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}


db.init_app(
    app
)

migrate = Migrate(
    app,
    db,
)

app.register_blueprint(
    admin_bp
)

# =========================================================
# WEB PUSH / VAPID CONFIGURATION
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
    "https://lac-acess-delivered.onrender.com/",
)
# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    zones = (
        Zone.query
        .filter_by(active=True)
        .order_by(Zone.name.asc())
        .all()
    )

    zone_access_points = []

    for zone in zones:

        access_point = (
            AccessPoint.query
            .filter_by(
                zone_id=zone.id,
                active=True,
            )
            .order_by(
                AccessPoint.id.asc()
            )
            .first()
        )

        if access_point:

            zone_access_points.append({
                "zone": zone,
                "access_point": access_point,
            })

    return render_template(
        "qr_entry.html",
        zone_access_points=zone_access_points,
    )

@app.route("/app")
def pwa_app():
    return render_template(
        "pwa_launcher.html"
    )


# ============================================================
# YOCO - CREATE CHECKOUT
# ============================================================
@app.route(
    "/payment/yoco/<code>",
    methods=["POST"],
)
def create_yoco_checkout(code):

    # --------------------------------------------------------
    # 1. FIND THE KALXA SUBMISSION
    # --------------------------------------------------------

    submission = (
        PendingSubmission.query
        .filter_by(
            tracking_code=code
        )
        .first_or_404()
    )

    # --------------------------------------------------------
    # 2. MAKE SURE THIS IS COMMERCIAL CONTENT
    # --------------------------------------------------------

    if submission.pricing_model not in {
        PRICING_MODEL_PRESENCE,
        PRICING_MODEL_CAMPAIGN,
    }:
        flash(
            "This submission does not require a Kalxa package payment.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 3. DON'T ALLOW PAYMENT AGAIN
    # --------------------------------------------------------

    if submission.payment_status == "paid":
        flash(
            "Payment for this submission has already been confirmed.",
            "success",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    if submission.payment_status == "waived":
        flash(
            "Payment is not required for this submission.",
            "success",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    if submission.payment_status == "refunded":
        flash(
            "This payment has been refunded. Please contact Kalxa for assistance.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    if submission.payment_status != "unpaid":
        flash(
            "Payment cannot currently be started for this submission.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 4. VALIDATE AMOUNT FROM DATABASE
    # --------------------------------------------------------

    if submission.amount_due is None:
        flash(
            "This submission does not have a valid amount due.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    try:

        amount_cents = int(
            submission.amount_due * 100
        )

    except (TypeError, ValueError, ArithmeticError):

        current_app.logger.exception(
            "[Kalxa Yoco] Invalid amount_due "
            "submission_id=%s amount_due=%s",
            submission.id,
            submission.amount_due,
        )

        flash(
            "Unable to prepare this payment.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    if amount_cents <= 0:

        flash(
            "The payment amount is invalid.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 5. MAKE SURE YOCO IS CONFIGURED
    # --------------------------------------------------------

    if not YOCO_SECRET_KEY:

        current_app.logger.error(
            "[Kalxa Yoco] YOCO_SECRET_KEY is not configured."
        )

        flash(
            "Online payment is temporarily unavailable.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 6. BUILD RETURN URLS
    # --------------------------------------------------------

    success_url = url_for(
        "yoco_payment_return",
        code=submission.tracking_code,
        _external=True,
        _scheme="https",
    )

    cancel_url = url_for(
        "submission_success",
        code=submission.tracking_code,
        _external=True,
        _scheme="https",
    )

    # --------------------------------------------------------
    # 7. BUILD YOCO CHECKOUT PAYLOAD
    # --------------------------------------------------------

    package_name = (
        "Kalxa Presence"
        if submission.pricing_model == PRICING_MODEL_PRESENCE
        else "Kalxa Campaign"
    )

    payload = {
        "amount": amount_cents,
        "currency": "ZAR",

        "successUrl": success_url,
        "cancelUrl": cancel_url,

        "metadata": {
            "kalxaReference": submission.tracking_code,
            "submissionId": str(submission.id),
            "pricingModel": submission.pricing_model,
            "durationDays": str(
                submission.commercial_duration_days or ""
            ),
        },

        "lineItems": [
            {
                "displayName": package_name,
                "description": (
                    f"{submission.commercial_duration_days} day "
                    f"{submission.pricing_model} package"
                ),
                "quantity": 1,
                "pricingDetails": {
                    "price": amount_cents,
                },
            }
        ],
    }

    # --------------------------------------------------------
    # 8. AUTHENTICATE WITH YOCO
    # --------------------------------------------------------

    headers = {
        "Authorization": f"Bearer {YOCO_SECRET_KEY}",
        "Content-Type": "application/json",

        # Same Kalxa submission gets the same idempotency key.
        "Idempotency-Key": (
            f"kalxa-{submission.id}-{submission.tracking_code}"
        ),
    }

    # --------------------------------------------------------
    # 9. CREATE YOCO CHECKOUT
    # --------------------------------------------------------

    try:

        response = requests.post(
            YOCO_CHECKOUT_URL,
            json=payload,
            headers=headers,
            timeout=20,
        )

    except requests.RequestException as exc:

        current_app.logger.exception(
            "[Kalxa Yoco] Network error creating checkout "
            "submission_id=%s error=%s",
            submission.id,
            exc,
        )

        flash(
            "Unable to connect to the payment service. Please try again.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 10. CHECK YOCO RESPONSE
    # --------------------------------------------------------

    if not response.ok:

        current_app.logger.error(
            "[Kalxa Yoco] Checkout creation failed "
            "submission_id=%s status=%s response=%s",
            submission.id,
            response.status_code,
            response.text[:1000],
        )

        flash(
            "Yoco could not start the payment. Please try again.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 11. READ YOCO CHECKOUT RESPONSE
    # --------------------------------------------------------

    try:
        checkout = response.json()

    except ValueError:

        current_app.logger.error(
            "[Kalxa Yoco] Invalid JSON response "
            "submission_id=%s response=%s",
            submission.id,
            response.text[:1000],
        )

        flash(
            "The payment service returned an invalid response.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    redirect_url = checkout.get("redirectUrl")
    checkout_id = checkout.get("id")

    # --------------------------------------------------------
    # 12. VALIDATE CHECKOUT RESPONSE
    # --------------------------------------------------------

    if not checkout_id:

        current_app.logger.error(
            "[Kalxa Yoco] Missing checkout ID "
            "submission_id=%s response=%s",
            submission.id,
            checkout,
        )

        flash(
            "Yoco did not return a valid checkout reference.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    if not redirect_url:

        current_app.logger.error(
            "[Kalxa Yoco] Missing redirectUrl "
            "submission_id=%s checkout_id=%s response=%s",
            submission.id,
            checkout_id,
            checkout,
        )

        flash(
            "Yoco did not provide a payment page.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 13. SAVE YOCO CHECKOUT ID
    # --------------------------------------------------------

    # This creates the permanent link:
    #
    # Kalxa PendingSubmission
    #       ↓
    # yoco_checkout_id
    #       ↓
    # Yoco payment
    #
    # Later the webhook receives payment_id, retrieves the
    # payment from Yoco, reads checkout_id, and finds this
    # exact Kalxa submission.

    try:

        submission.yoco_checkout_id = checkout_id

        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        current_app.logger.exception(
            "[Kalxa Yoco] Unable to save checkout ID "
            "submission_id=%s checkout_id=%s error=%s",
            submission.id,
            checkout_id,
            exc,
        )

        flash(
            "The payment was created, but Kalxa could not "
            "save the payment reference. Please try again.",
            "error",
        )

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    # --------------------------------------------------------
    # 14. LOG CHECKOUT CREATION
    # --------------------------------------------------------

    # IMPORTANT:
    #
    # Creating a Yoco checkout does NOT mean payment happened.
    #
    # payment_status stays:
    #
    #     unpaid
    #
    # The webhook will later verify the actual Yoco payment.

    current_app.logger.info(
        "[Kalxa Yoco] Checkout created and saved "
        "submission_id=%s reference=%s "
        "checkout_id=%s amount_cents=%s mode=%s",
        submission.id,
        submission.tracking_code,
        checkout_id,
        amount_cents,
        YOCO_MODE,
    )

    # --------------------------------------------------------
    # 15. SEND CUSTOMER TO YOCO
    # --------------------------------------------------------

    return redirect(
        redirect_url,
        code=303,
    )


def parse_optional_time(
    value,
):
    """
    Convert an HTML <input type="time"> value into
    datetime.time.

    Examples:

        "18:00"
            -> time(18, 0)

        "02:30"
            -> time(2, 30)

        ""
        None
            -> None
    """

    value = (
        str(value or "")
        .strip()
    )

    if not value:
        return None


    for time_format in (
        "%H:%M",
        "%H:%M:%S",
    ):

        try:

            return datetime.strptime(
                value,
                time_format,
            ).time()

        except ValueError:
            continue


    raise ValueError(
        "Invalid time value."
    )

def get_campaign_state(
    item,
    now=None,
):
    """
    Calculate the current state of a Kalxa campaign.

    This function does NOT modify the database.

    It calculates the state dynamically whenever the page
    is loaded.

    Possible states:

        upcoming
        tomorrow
        today
        tonight
        live
        ending
        ended
        active
        undated

    Examples:

        3 DAYS TO GO
        TOMORROW
        TODAY
        TONIGHT
        HAPPENING NOW
        ENDS TODAY
        ENDED

    The underlying state is separate from the consumer label.
    Category-specific wording can therefore be added later.
    """

    # ========================================================
    # CURRENT KALXA TIME
    # ========================================================

    if now is None:

        now = datetime.now(
            KALXA_TIMEZONE
        )

    elif now.tzinfo is None:

        # Test/admin supplied naive datetime.
        # Interpret it as South African local time.

        now = now.replace(
            tzinfo=KALXA_TIMEZONE
        )

    else:

        now = now.astimezone(
            KALXA_TIMEZONE
        )


    today = now.date()


    # ========================================================
    # CATEGORY
    # ========================================================

    canonical_category = normalize_category(
        item.category
    )


    # ========================================================
    # CAMPAIGN DATES
    # ========================================================

    target_date = (
        item.get_campaign_target_date()
    )

    start_datetime = (
        item.get_campaign_start_datetime()
    )

    end_datetime = (
        item.get_campaign_end_datetime()
    )


    # ========================================================
    # MAKE MODEL DATETIMES TIMEZONE-AWARE
    #
    # ContentItem helpers currently return naive datetimes
    # built from Date + Time columns.
    #
    # Those values represent Kalxa local campaign time.
    # ========================================================

    if start_datetime is not None:

        if start_datetime.tzinfo is None:

            start_datetime = (
                start_datetime.replace(
                    tzinfo=KALXA_TIMEZONE
                )
            )

        else:

            start_datetime = (
                start_datetime.astimezone(
                    KALXA_TIMEZONE
                )
            )


    if end_datetime is not None:

        if end_datetime.tzinfo is None:

            end_datetime = (
                end_datetime.replace(
                    tzinfo=KALXA_TIMEZONE
                )
            )

        else:

            end_datetime = (
                end_datetime.astimezone(
                    KALXA_TIMEZONE
                )
            )


    # ========================================================
    # NO CAMPAIGN DATE
    #
    # Some Discovery / Business listings are intentionally
    # ongoing and therefore have no campaign dates.
    # ========================================================

    if target_date is None:

        return {
            "state": "undated",
            "label": None,
            "css_class": "campaign-undated",
            "days_to_go": None,
            "is_live": False,
            "is_ended": False,
            "starts_at": None,
            "ends_at": end_datetime,
        }


    # ========================================================
    # ENDED
    #
    # Explicit end datetime always has the strongest
    # authority.
    # ========================================================

    if (
        end_datetime is not None
        and now > end_datetime
    ):

        return {
            "state": "ended",
            "label": "ENDED",
            "css_class": "campaign-ended",
            "days_to_go": 0,
            "is_live": False,
            "is_ended": True,
            "starts_at": start_datetime,
            "ends_at": end_datetime,
        }


    # ========================================================
    # DAYS UNTIL TARGET
    # ========================================================

    days_to_go = (
        target_date - today
    ).days


    # ========================================================
    # FUTURE — MORE THAN ONE DAY
    #
    # Example:
    #
    # Sep 9 -> Sep 12
    #
    # 3 DAYS TO GO
    # ========================================================

    if days_to_go > 1:

        return {
            "state": "upcoming",
            "label": f"{days_to_go} DAYS TO GO",
            "css_class": "campaign-countdown",
            "days_to_go": days_to_go,
            "is_live": False,
            "is_ended": False,
            "starts_at": start_datetime,
            "ends_at": end_datetime,
        }


    # ========================================================
    # TOMORROW
    # ========================================================

    if days_to_go == 1:

        return {
            "state": "tomorrow",
            "label": "TOMORROW",
            "css_class": "campaign-tomorrow",
            "days_to_go": 1,
            "is_live": False,
            "is_ended": False,
            "starts_at": start_datetime,
            "ends_at": end_datetime,
        }


    # ========================================================
    # TODAY — BEFORE START TIME
    # ========================================================

    if days_to_go == 0:

        if (
            start_datetime is not None
            and now < start_datetime
        ):

            # ------------------------------------------------
            # EVENT TODAY
            #
            # If it has an actual start time, TODAY becomes
            # TONIGHT for evening events.
            #
            # 17:00 is our initial Kalxa evening threshold.
            # This can later become configurable.
            # ------------------------------------------------

            if (
                canonical_category == "events"
                and item.start_time is not None
                and item.start_time >= time(17, 0)
            ):

                return {
                    "state": "tonight",
                    "label": "🔥 TONIGHT",
                    "css_class": "campaign-tonight",
                    "days_to_go": 0,
                    "is_live": False,
                    "is_ended": False,
                    "starts_at": start_datetime,
                    "ends_at": end_datetime,
                }


            return {
                "state": "today",
                "label": "🔥 TODAY",
                "css_class": "campaign-today",
                "days_to_go": 0,
                "is_live": False,
                "is_ended": False,
                "starts_at": start_datetime,
                "ends_at": end_datetime,
            }


        # ====================================================
        # STARTED TODAY
        #
        # If an explicit end datetime exists and we are
        # between start and end, it is live.
        # ====================================================

        if (
            start_datetime is not None
            and end_datetime is not None
            and start_datetime <= now <= end_datetime
        ):

            return {
                "state": "live",
                "label": "🔴 HAPPENING NOW",
                "css_class": "campaign-live",
                "days_to_go": 0,
                "is_live": True,
                "is_ended": False,
                "starts_at": start_datetime,
                "ends_at": end_datetime,
            }


        # ====================================================
        # STARTED, BUT NO EXPLICIT END DATETIME
        #
        # We know the campaign has started, but we cannot
        # truthfully claim it is still happening indefinitely.
        #
        # For events we keep TODAY rather than manufacturing
        # an end time.
        # ====================================================

        if canonical_category == "events":

            return {
                "state": "today",
                "label": "🔥 TODAY",
                "css_class": "campaign-today",
                "days_to_go": 0,
                "is_live": False,
                "is_ended": False,
                "starts_at": start_datetime,
                "ends_at": None,
            }


        return {
            "state": "active",
            "label": "🔥 AVAILABLE NOW",
            "css_class": "campaign-active",
            "days_to_go": 0,
            "is_live": True,
            "is_ended": False,
            "starts_at": start_datetime,
            "ends_at": end_datetime,
        }


    # ========================================================
    # TARGET DATE HAS PASSED
    #
    # We reach here when:
    #
    #     days_to_go < 0
    #
    # An explicit end date may still mean the campaign is
    # active.
    #
    # Example:
    #
    # Special:
    # start = Sep 5
    # end   = Sep 12
    # today = Sep 9
    # ========================================================

    if (
        end_datetime is not None
        and now <= end_datetime
    ):

        # ----------------------------------------------------
        # LAST DAY
        # ----------------------------------------------------

        if end_datetime.date() == today:

            return {
                "state": "ending",
                "label": "ENDS TODAY",
                "css_class": "campaign-ending",
                "days_to_go": 0,
                "is_live": True,
                "is_ended": False,
                "starts_at": start_datetime,
                "ends_at": end_datetime,
            }


        # ----------------------------------------------------
        # CAMPAIGN CURRENTLY ACTIVE
        # ----------------------------------------------------

        return {
            "state": "active",
            "label": "🔥 AVAILABLE NOW",
            "css_class": "campaign-active",
            "days_to_go": 0,
            "is_live": True,
            "is_ended": False,
            "starts_at": start_datetime,
            "ends_at": end_datetime,
        }


    # ========================================================
    # EVENT WITHOUT END DATE
    #
    # If its target date has already passed, it is ended.
    # ========================================================

    if canonical_category == "events":

        return {
            "state": "ended",
            "label": "ENDED",
            "css_class": "campaign-ended",
            "days_to_go": 0,
            "is_live": False,
            "is_ended": True,
            "starts_at": start_datetime,
            "ends_at": end_datetime,
        }


    # ========================================================
    # ONGOING CAMPAIGN WITHOUT END DATE
    #
    # A non-event campaign may intentionally have no end.
    # ========================================================

    return {
        "state": "active",
        "label": "🔥 AVAILABLE NOW",
        "css_class": "campaign-active",
        "days_to_go": 0,
        "is_live": True,
        "is_ended": False,
        "starts_at": start_datetime,
        "ends_at": end_datetime,
    }


def should_show_campaign_state(item):
    """
    Decide whether a ContentItem should display Kalxa's
    dynamic Campaign State badge.

    Campaign states are intended for:

    1. Paid/waived campaign content.
    2. Time-specific categories such as events.
    3. Legacy/admin campaign-style content that has
       meaningful campaign dates.

    Ordinary long-lived Presence / Discovery listings
    should not receive urgency badges simply because they
    happen to contain a date.
    """

    if not item:
        return False


    canonical_category = normalize_category(
        item.category
    )


    # ========================================================
    # EVENTS
    #
    # Events are inherently time-specific, including older
    # admin-created events that predate pricing_model.
    # ========================================================

    if canonical_category == "events":

        return bool(
            item.event_date
            or item.start_date
        )


    # ========================================================
    # COMMERCIAL CAMPAIGNS
    # ========================================================

    if (
        item.pricing_model
        == PRICING_MODEL_CAMPAIGN
    ):

        return bool(
            item.start_date
            or item.end_date
        )


    # ========================================================
    # EVERYTHING ELSE
    # ========================================================

    return False


def attach_campaign_state(item):
    """
    Attach a calculated campaign state to a ContentItem for
    template presentation.

    This does NOT write anything to PostgreSQL.

    campaign_state exists only on the Python object for the
    current request.
    """

    if not item:
        return item


    if should_show_campaign_state(item):

        item.campaign_state = (
            get_campaign_state(item)
        )

    else:

        item.campaign_state = None


    return item

def attach_campaign_states(items):
    """
    Attach campaign state information to a collection of
    ContentItems.
    """

    if not items:
        return []


    for item in items:

        attach_campaign_state(
            item
        )


    return items
# ============================================================
# YOCO - CUSTOMER RETURN
# ============================================================

@app.route(
    "/payment/yoco/return/<code>"
)
def yoco_payment_return(code):

    submission = (
        PendingSubmission.query
        .filter_by(
            tracking_code=code
        )
        .first_or_404()
    )

    # IMPORTANT:
    #
    # Reaching this URL does NOT prove payment.
    #
    # The Yoco webhook will be responsible for securely
    # confirming the transaction and changing:
    #
    # payment_status = "unpaid"
    #
    # to:
    #
    # payment_status = "paid"

    flash(
        "Your payment was submitted. Kalxa is confirming the transaction.",
        "success",
    )

    return redirect(
        url_for(
            "submission_status",
            code=submission.tracking_code,
        )
    )


@app.route(
    "/webhooks/yoco",
    methods=["POST"],
)
def yoco_webhook():

    # --------------------------------------------------------
    # 1. MAKE SURE WEBHOOK VERIFICATION IS CONFIGURED
    # --------------------------------------------------------

    if not YOCO_WEBHOOK_SECRET:

        current_app.logger.error(
            "[Kalxa Yoco Webhook] "
            "YOCO_WEBHOOK_SECRET is not configured."
        )

        return "", 500

    # --------------------------------------------------------
    # 2. READ RAW REQUEST BODY
    # --------------------------------------------------------
    #
    # IMPORTANT:
    # Signature verification must happen against the exact
    # raw body Yoco sent. Do not parse and re-serialize it
    # before verifying the signature.

    raw_body = request.get_data()

    webhook_id = request.headers.get("webhook-id")
    webhook_timestamp = request.headers.get(
        "webhook-timestamp"
    )
    webhook_signature = request.headers.get(
        "webhook-signature"
    )

    if not all(
        (
            webhook_id,
            webhook_timestamp,
            webhook_signature,
        )
    ):

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Missing webhook verification headers."
        )

        return "", 400

    # --------------------------------------------------------
    # 3. REPLAY PROTECTION
    # --------------------------------------------------------
    #
    # Yoco recommends accepting timestamps within roughly
    # 3 minutes.

    try:
        timestamp_int = int(webhook_timestamp)

    except (TypeError, ValueError):

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Invalid webhook timestamp."
        )

        return "", 400

    current_timestamp = int(time.time())

    if abs(current_timestamp - timestamp_int) > 180:

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Webhook timestamp outside allowed window "
            "webhook_id=%s",
            webhook_id,
        )

        return "", 400

    # --------------------------------------------------------
    # 4. BUILD YOCO SIGNED CONTENT
    # --------------------------------------------------------

    try:
        raw_body_text = raw_body.decode("utf-8")

    except UnicodeDecodeError:

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Webhook body is not valid UTF-8."
        )

        return "", 400

    signed_content = (
        f"{webhook_id}."
        f"{webhook_timestamp}."
        f"{raw_body_text}"
    )

    # --------------------------------------------------------
    # 5. DECODE YOCO WEBHOOK SECRET
    # --------------------------------------------------------
    #
    # Expected format:
    #
    # whsec_XXXXXXXXXXXXXXXXXXXXXXXXX=

    try:

        if not YOCO_WEBHOOK_SECRET.startswith("whsec_"):
            raise ValueError(
                "Invalid webhook secret prefix."
            )

        encoded_secret = YOCO_WEBHOOK_SECRET.split(
            "_",
            1,
        )[1]

        secret_bytes = base64.b64decode(
            encoded_secret
        )

    except Exception:

        current_app.logger.exception(
            "[Kalxa Yoco Webhook] "
            "Unable to decode webhook secret."
        )

        return "", 500

    # --------------------------------------------------------
    # 6. CALCULATE EXPECTED SIGNATURE
    # --------------------------------------------------------

    calculated_signature = hmac.new(
        secret_bytes,
        signed_content.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    expected_signature = base64.b64encode(
        calculated_signature
    ).decode("utf-8")

    # --------------------------------------------------------
    # 7. VERIFY SIGNATURE
    # --------------------------------------------------------
    #
    # Yoco may send multiple signatures separated by spaces.
    #
    # Example:
    #
    # v1,abc... v1,xyz...

    signature_valid = False

    for signature in webhook_signature.split():

        if not signature.startswith("v1,"):
            continue

        supplied_signature = signature[
            len("v1,"):
        ]

        if hmac.compare_digest(
            expected_signature,
            supplied_signature,
        ):
            signature_valid = True
            break

    if not signature_valid:

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Invalid webhook signature "
            "webhook_id=%s",
            webhook_id,
        )

        return "", 401

    # --------------------------------------------------------
    # 8. PARSE VERIFIED JSON
    # --------------------------------------------------------

    try:
        event = json.loads(raw_body_text)

    except json.JSONDecodeError:

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Invalid JSON after signature verification "
            "webhook_id=%s",
            webhook_id,
        )

        return "", 400

    event_type = event.get("type")

    # --------------------------------------------------------
    # 9. IGNORE EVENTS WE DON'T HANDLE YET
    # --------------------------------------------------------
    #
    # For the MVP we only need successful payments.
    #
    # Returning 200 tells Yoco:
    #
    # "We received this event successfully."

    if event_type != "payment.succeeded":

        current_app.logger.info(
            "[Kalxa Yoco Webhook] "
            "Ignoring unsupported event "
            "webhook_id=%s event_type=%s",
            webhook_id,
            event_type,
        )

        return "", 200

    # --------------------------------------------------------
    # 10. READ PAYMENT PAYLOAD
    # --------------------------------------------------------

    payload = event.get("payload") or {}

    payment_status = payload.get("status")
    payment_id = payload.get("id")
    payment_amount = payload.get("amount")
    payment_currency = payload.get("currency")

    metadata = payload.get("metadata") or {}

    checkout_id = metadata.get("checkoutId")

    # --------------------------------------------------------
    # 11. VALIDATE REQUIRED PAYMENT DATA
    # --------------------------------------------------------

    if (
        payment_status != "succeeded"
        or not payment_id
        or payment_amount is None
        or payment_currency != "ZAR"
        or not checkout_id
    ):

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Incomplete/invalid payment event "
            "webhook_id=%s payment_id=%s "
            "status=%s currency=%s checkout_id=%s",
            webhook_id,
            payment_id,
            payment_status,
            payment_currency,
            checkout_id,
        )

        return "", 400

    # --------------------------------------------------------
    # 12. FIND EXACT KALXA SUBMISSION
    # --------------------------------------------------------

    submission = (
        PendingSubmission.query
        .filter_by(
            yoco_checkout_id=checkout_id
        )
        .first()
    )

    if submission is None:

        current_app.logger.error(
            "[Kalxa Yoco Webhook] "
            "No submission found for checkout "
            "webhook_id=%s checkout_id=%s "
            "payment_id=%s",
            webhook_id,
            checkout_id,
            payment_id,
        )

        # Return non-2xx so Yoco may retry.
        #
        # This is useful if the event arrived before our
        # database transaction became visible.

        return "", 404

    # --------------------------------------------------------
    # 13. MAKE WEBHOOK IDEMPOTENT
    # --------------------------------------------------------
    #
    # Yoco may deliver the same event more than once.
    #
    # If we've already processed this exact payment,
    # acknowledge it without changing anything.

    if (
        submission.payment_status == "paid"
        and submission.yoco_payment_id == payment_id
    ):

        current_app.logger.info(
            "[Kalxa Yoco Webhook] "
            "Duplicate successful payment ignored "
            "submission_id=%s payment_id=%s "
            "webhook_id=%s",
            submission.id,
            payment_id,
            webhook_id,
        )

        return "", 200

    # --------------------------------------------------------
    # 14. PREVENT PAYMENT ID FROM BEING REUSED
    # --------------------------------------------------------

    existing_payment = (
        PendingSubmission.query
        .filter(
            PendingSubmission.yoco_payment_id
            == payment_id,
            PendingSubmission.id
            != submission.id,
        )
        .first()
    )

    if existing_payment:

        current_app.logger.error(
            "[Kalxa Yoco Webhook] "
            "Payment ID already belongs to another submission "
            "payment_id=%s existing_submission_id=%s "
            "current_submission_id=%s",
            payment_id,
            existing_payment.id,
            submission.id,
        )

        return "", 409

    # --------------------------------------------------------
    # 15. VERIFY EXPECTED KALXA AMOUNT
    # --------------------------------------------------------

    if submission.amount_due is None:

        current_app.logger.error(
            "[Kalxa Yoco Webhook] "
            "Submission has no amount_due "
            "submission_id=%s",
            submission.id,
        )

        return "", 409

    try:

        expected_amount_cents = int(
            submission.amount_due * 100
        )

        received_amount_cents = int(
            payment_amount
        )

    except (TypeError, ValueError, ArithmeticError):

        current_app.logger.exception(
            "[Kalxa Yoco Webhook] "
            "Unable to compare payment amounts "
            "submission_id=%s",
            submission.id,
        )

        return "", 400

    if received_amount_cents != expected_amount_cents:

        current_app.logger.error(
            "[Kalxa Yoco Webhook] "
            "PAYMENT AMOUNT MISMATCH "
            "submission_id=%s expected=%s received=%s "
            "payment_id=%s",
            submission.id,
            expected_amount_cents,
            received_amount_cents,
            payment_id,
        )

        # Never mark the submission paid when amounts differ.

        return "", 409

    # --------------------------------------------------------
    # 16. VERIFY SUBMISSION IS STILL PAYABLE
    # --------------------------------------------------------

    if submission.payment_status != "unpaid":

        current_app.logger.warning(
            "[Kalxa Yoco Webhook] "
            "Successful payment received for submission "
            "with unexpected payment_status "
            "submission_id=%s status=%s payment_id=%s",
            submission.id,
            submission.payment_status,
            payment_id,
        )

        return "", 409

    # --------------------------------------------------------
    # 17. MARK PAYMENT AS CONFIRMED
    # --------------------------------------------------------
    #
    # IMPORTANT:
    #
    # This confirms PAYMENT ONLY.
    #
    # It does NOT:
    #
    # - approve the submission
    # - create/publish ContentItem
    # - start the commercial period
    #
    # Admin moderation remains independent.

    try:

        submission.payment_status = "paid"
        submission.yoco_payment_id = payment_id

        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        current_app.logger.exception(
            "[Kalxa Yoco Webhook] "
            "Database error confirming payment "
            "submission_id=%s payment_id=%s error=%s",
            submission.id,
            payment_id,
            exc,
        )

        # Yoco should retry because we were unable to persist
        # the payment confirmation.

        return "", 500

    # --------------------------------------------------------
    # 18. SUCCESS
    # --------------------------------------------------------

    current_app.logger.info(
        "[Kalxa Yoco Webhook] PAYMENT CONFIRMED "
        "submission_id=%s reference=%s "
        "payment_id=%s checkout_id=%s "
        "amount_cents=%s mode=%s webhook_id=%s",
        submission.id,
        submission.tracking_code,
        payment_id,
        checkout_id,
        received_amount_cents,
        payload.get("mode"),
        webhook_id,
    )

    return "", 200
# =========================================================
# CATEGORY HELPERS
# =========================================================

def get_active_categories():

    return (
        Category.query
        .filter_by(
            active=True
        )
        .order_by(
            Category.display_order,
            Category.name,
        )
        .all()
    )


def get_active_category_by_slug(
    slug,
):

    return (
        Category.query
        .filter_by(
            slug=slug,
            active=True,
        )
        .first()
    )

def get_active_content(
    zone_id,
    category_slug,
):

    # =====================================================
    # CURRENT DATE / TIME
    # =====================================================

    today = date.today()
    now = datetime.utcnow()


    # =====================================================
    # CLEAN REQUESTED CATEGORY
    # =====================================================

    requested_category = (
        str(category_slug or "")
        .strip()
        .lower()
    )

    if not requested_category:
        return []


    # =====================================================
    # CANONICAL CATEGORY
    # =====================================================

    canonical_category = normalize_category(
        requested_category
    )

    if not canonical_category:
        return []


    # =====================================================
    # CATEGORY ALIASES
    #
    # Allows legacy and canonical values to coexist.
    #
    # Example:
    #
    # restaurants:
    #
    #     restaurants
    #     foods
    #     check-out-our-specials
    #     local-restaurants
    # =====================================================

    category_aliases = set(
        get_category_aliases(
            canonical_category
        )
        or []
    )

    category_aliases.add(
        requested_category
    )

    category_aliases.add(
        canonical_category
    )

    category_aliases.discard(None)
    category_aliases.discard("")

    if not category_aliases:
        return []


    # =====================================================
    # FEATURED PRIORITY
    # =====================================================

    featured_priority = db.case(
        (
            ContentItem.featured.is_(True),
            1,
        ),
        else_=0,
    )


    # =====================================================
    # ZONE VISIBILITY
    #
    # Visible when:
    #
    # 1. Listing belongs to this zone
    #
    # OR
    #
    # 2. Campaign has been distributed into this zone
    # =====================================================

    zone_visibility = db.or_(

        ContentItem.zone_id == zone_id,

        db.and_(

            ContentItem.pricing_model
            == PRICING_MODEL_CAMPAIGN,

            ContentItem.distribution_zone_links.any(
                ContentDistributionZone.zone_id
                == zone_id
            ),

        ),

    )


    # =====================================================
    # COMMERCIAL VISIBILITY
    #
    # IMPORTANT:
    #
    # There are THREE main groups.
    #
    # A. Legacy/admin content
    #    pricing_model is NULL
    #
    # B. Explicitly waived content
    #
    # C. Paid commercial content with an active
    #    commercial period
    #
    # We also preserve OLD ADMIN CONTENT that existed
    # before the commercial system was introduced.
    #
    # Such content may have:
    #
    #     pricing_model = presence/campaign
    #
    # but:
    #
    #     amount_due = NULL
    #     paid_at = NULL
    #     commercial_starts_at = NULL
    #     commercial_expires_at = NULL
    #
    # That is treated as legacy/admin content rather than
    # a failed customer payment.
    # =====================================================

    legacy_admin_commercial_content = db.and_(

        ContentItem.pricing_model.in_(
            (
                PRICING_MODEL_PRESENCE,
                PRICING_MODEL_CAMPAIGN,
            )
        ),

        ContentItem.amount_due.is_(None),

        ContentItem.amount_paid.is_(None),

        ContentItem.paid_at.is_(None),

        ContentItem.commercial_starts_at.is_(None),

        ContentItem.commercial_expires_at.is_(None),

    )


    paid_commercial_content = db.and_(

        ContentItem.pricing_model.in_(
            (
                PRICING_MODEL_PRESENCE,
                PRICING_MODEL_CAMPAIGN,
            )
        ),

        ContentItem.payment_status == "paid",

        ContentItem.commercial_starts_at.is_not(
            None
        ),

        ContentItem.commercial_expires_at.is_not(
            None
        ),

        ContentItem.commercial_starts_at <= now,

        ContentItem.commercial_expires_at >= now,

    )


    commercial_visibility = db.or_(

        # ---------------------------------------------
        # ORIGINAL NON-COMMERCIAL CONTENT
        # ---------------------------------------------

        ContentItem.pricing_model.is_(None),


        # ---------------------------------------------
        # ADMIN / PILOT WAIVED CONTENT
        # ---------------------------------------------

        ContentItem.payment_status == "waived",


        # ---------------------------------------------
        # OLD ADMIN CONTENT CREATED BEFORE COMMERCIAL
        # PAYMENT METADATA WAS fully populated
        # ---------------------------------------------

        legacy_admin_commercial_content,


        # ---------------------------------------------
        # REAL PAID COMMERCIAL CONTENT
        # ---------------------------------------------

        paid_commercial_content,

    )


    # =====================================================
    # BASE QUERY
    # =====================================================

    query = (
        ContentItem.query
        .filter(

            # ---------------------------------------------
            # PUBLISHED ONLY
            # ---------------------------------------------

            ContentItem.active.is_(True),


            # ---------------------------------------------
            # LEGACY + CANONICAL TAXONOMY
            # ---------------------------------------------

            ContentItem.category.in_(
                category_aliases
            ),


            # ---------------------------------------------
            # ZONE
            # ---------------------------------------------

            zone_visibility,


            # ---------------------------------------------
            # COMMERCIAL STATUS
            # ---------------------------------------------

            commercial_visibility,

        )
    )


    # =====================================================
    # EVENTS
    # =====================================================

    if canonical_category == "events":

        # -------------------------------------------------
        # FUTURE / CURRENT EVENT VISIBILITY
        #
        # Event remains visible when:
        #
        # 1. end_date has not passed
        #
        # OR
        #
        # 2. event_date has not passed when no end_date
        #
        # OR
        #
        # 3. legacy event has no dates
        #
        # -------------------------------------------------

        event_natural_visibility = db.or_(

            db.and_(

                ContentItem.end_date.is_not(
                    None
                ),

                ContentItem.end_date >= today,

            ),


            db.and_(

                ContentItem.end_date.is_(
                    None
                ),

                ContentItem.event_date.is_not(
                    None
                ),

                ContentItem.event_date >= today,

            ),


            db.and_(

                ContentItem.end_date.is_(
                    None
                ),

                ContentItem.event_date.is_(
                    None
                ),

            ),

        )


        query = query.filter(
            event_natural_visibility
        )


        # =================================================
        # EVENT ORDERING
        #
        # Featured first.
        #
        # Then closest upcoming event.
        #
        # Then newest listing.
        # =================================================

        items = (
           query
           .order_by(

            featured_priority.desc(),

            ContentItem.event_date
            .asc()
            .nullslast(),

            ContentItem.created_at
            .desc(),

           )
           .all()
        )


        return attach_campaign_states(
          items
        )


    # =====================================================
    # NON-EVENT NATURAL VISIBILITY
    # =====================================================

    natural_content_visibility = db.and_(

        # -------------------------------------------------
        # START
        # -------------------------------------------------

        db.or_(

            ContentItem.start_date.is_(None),

            ContentItem.start_date <= today,

        ),


        # -------------------------------------------------
        # END
        # -------------------------------------------------

        db.or_(

            ContentItem.end_date.is_(None),

            ContentItem.end_date >= today,

        ),

    )


    query = query.filter(
        natural_content_visibility
    )


    # =====================================================
    # NON-EVENT ORDERING
    #
    # Featured first.
    #
    # Then earliest applicable start.
    #
    # Then newest listing.
    # =====================================================
    items = (
      query
      .order_by(

        featured_priority.desc(),

        ContentItem.event_date
        .asc()
        .nullslast(),

        ContentItem.created_at
        .desc(),

      )
      .all()
    )
    return attach_campaign_states(
      items
    )


# CONTENT EXPIRY HELPERS
# =========================================================

def get_content_expiry_date(item):

    if item.category == "events":

        return (
            item.event_end_date
            or item.event_date
        )

    return item.end_date


def content_is_expired(
    item,
    today=None,
):

    today = (
        today
        or date.today()
    )

    expiry_date = (
        get_content_expiry_date(
            item
        )
    )

    if not expiry_date:
        return False

    return (
        expiry_date < today
    )

# =========================================================
# QR ACCESS POINT
# =========================================================
@app.route("/q/<access_code>")
def qr_access(access_code):

    # =====================================================
    # FIND ACCESS POINT
    # =====================================================

    access_point = (
        AccessPoint.query
        .filter_by(
            code=access_code,
            active=True,
        )
        .first_or_404()
    )

    zone = access_point.zone


    # =====================================================
    # RECORD PHYSICAL QR SCAN
    # =====================================================

    try:

        scan = QRScan(
            access_point_id=access_point.id,
            event_type="scan",
            user_agent=request.headers.get(
                "User-Agent",
                "",
            ),
        )

        db.session.add(scan)
        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        app.logger.exception(
            "Failed to record QR scan for %s: %s",
            access_point.code,
            exc,
        )


    # =====================================================
    # CATEGORY-SPECIFIC QR
    #
    # IMPORTANT:
    #
    # Existing access points may still contain legacy
    # category slugs.
    #
    # Example:
    #
    #     upcoming-event-🥹🔥
    #     check-out-our-specials
    #     foods
    #
    # We validate them through the compatibility-aware
    # category lookup, then redirect using the original
    # stored slug.
    #
    # qr_category() can resolve both legacy and canonical
    # category URLs.
    # =====================================================

    if access_point.default_category:

        category_slug = (
            str(
                access_point.default_category
                or ""
            )
            .strip()
            .lower()
        )

        category_record = (
            get_category_by_slug(
                category_slug,
                active_only=True,
            )
        )

        if not category_record:
            abort(404)

        return redirect(
            url_for(
                "qr_category",
                code=access_point.code,
                category=category_slug,
            )
        )


    # =====================================================
    # GENERAL QR
    #
    # Keep the REAL active Category database rows.
    #
    # These are still required because:
    #
    # - category images depend on Category.id
    # - zone appearance depends on Category.id
    # - existing admin configuration uses Category rows
    # - legacy QR/category URLs still exist
    #
    # Consumer navigation is built as a separate layer.
    # =====================================================

    categories = get_active_categories()

    today = date.today()


    # =====================================================
    # ZONE-SPECIFIC CATEGORY APPEARANCES
    # =====================================================

    zone_category_appearances = (
        ZoneCategoryAppearance.query
        .filter_by(
            zone_id=zone.id,
        )
        .all()
    )


    # =====================================================
    # APPEARANCE LOOKUP
    #
    # Key:
    #     real Category.id
    #
    # Value:
    #     ZoneCategoryAppearance
    # =====================================================

    zone_category_appearance_lookup = {

        appearance.category_id:
            appearance

        for appearance
        in zone_category_appearances
    }


    # =====================================================
    # RESOLVE CATEGORY BACKGROUND IMAGES
    #
    # Priority:
    #
    # 1. Zone-specific category image
    # 2. Default Category image
    #
    # Images remain attached to REAL Category.id values.
    # =====================================================

    category_background_images = {}


    for category in categories:

        appearance = (
            zone_category_appearance_lookup.get(
                category.id
            )
        )


        # -------------------------------------------------
        # IMAGE 1
        # -------------------------------------------------

        image_1 = (

            appearance.image_url

            if (
                appearance
                and appearance.image_url
            )

            else category.image_url
        )


        # -------------------------------------------------
        # IMAGE 2
        # -------------------------------------------------

        image_2 = (

            appearance.image_url_2

            if (
                appearance
                and appearance.image_url_2
            )

            else category.image_url_2
        )


        # -------------------------------------------------
        # IMAGE 3
        # -------------------------------------------------

        image_3 = (

            appearance.image_url_3

            if (
                appearance
                and appearance.image_url_3
            )

            else category.image_url_3
        )


        category_background_images[
            category.id
        ] = [

            image

            for image in [
                image_1,
                image_2,
                image_3,
            ]

            if image
        ]


    # =====================================================
    # CATEGORY LOOKUP
    #
    # Allows listings using either:
    #
    #     old public slug
    #
    # or:
    #
    #     canonical taxonomy key
    #
    # to resolve back to a real Category row.
    # =====================================================

    category_lookup = {}


    for category in categories:

        # -------------------------------------------------
        # REAL / LEGACY SLUG
        # -------------------------------------------------

        category_lookup[
            category.slug
        ] = category


        # -------------------------------------------------
        # CANONICAL TAXONOMY KEY
        # -------------------------------------------------

        canonical_key = normalize_category(
            category.slug
        )

        if canonical_key not in category_lookup:

            category_lookup[
                canonical_key
            ] = category


    # =====================================================
    # CONSUMER CATEGORY LOOKUP
    #
    # This remains useful for Featured and What's New
    # cards because those listings may contain either
    # legacy or canonical category values.
    #
    # Example:
    #
    #     check-out-our-specials
    #
    # becomes:
    #
    #     {
    #         "key": "restaurants",
    #         "title": "HUNGRY?",
    #         "subtitle": "Find something good to eat",
    #         "icon": "🍔"
    #     }
    # =====================================================

    consumer_category_lookup = {}


    for category in categories:

        canonical_key = normalize_category(
            category.slug
        )

        presentation = get_consumer_category(
            canonical_key
        )

        presentation_data = {

            "key":
                canonical_key,

            "title":
                presentation["title"],

            "subtitle":
                presentation["subtitle"],

            "icon":
                presentation["icon"],
        }


        # -------------------------------------------------
        # REAL DATABASE SLUG
        # -------------------------------------------------

        consumer_category_lookup[
            category.slug
        ] = presentation_data


        # -------------------------------------------------
        # CANONICAL KEY
        #
        # This is important for newly submitted listings
        # whose ContentItem.category may already contain
        # the canonical taxonomy.
        # -------------------------------------------------

        if (
            canonical_key
            not in consumer_category_lookup
        ):

            consumer_category_lookup[
                canonical_key
            ] = presentation_data


    # =====================================================
    # BUILD UNIQUE CONSUMER CATEGORY DIRECTORY
    #
    # This is the important taxonomy separation.
    #
    # DATABASE:
    #
    #     foods
    #     check-out-our-specials
    #
    # CONSUMER:
    #
    #     HUNGRY?
    #
    # Only ONE consumer card is created for each canonical
    # category.
    #
    # The first active Category row encountered becomes
    # the representative database row for:
    #
    # - images
    # - zone appearance
    # - fallback icon
    #
    # The public link itself uses the CANONICAL key.
    # =====================================================

    consumer_categories = []

    seen_consumer_category_keys = set()


    for category in categories:

        canonical_key = normalize_category(
            category.slug
        )


        if (
            canonical_key
            in seen_consumer_category_keys
        ):

            continue


        seen_consumer_category_keys.add(
            canonical_key
        )


        presentation = get_consumer_category(
            canonical_key
        )


        consumer_categories.append({

            # ---------------------------------------------
            # CANONICAL DATABASE / BUSINESS TAXONOMY
            # ---------------------------------------------

            "key":
                canonical_key,


            # ---------------------------------------------
            # REAL REPRESENTATIVE CATEGORY ROW
            # ---------------------------------------------

            "category":
                category,


            # ---------------------------------------------
            # CONSUMER PRESENTATION
            # ---------------------------------------------

            "title":
                presentation["title"],

            "subtitle":
                presentation["subtitle"],

            "icon":
                presentation["icon"],


            # ---------------------------------------------
            # CANONICAL PUBLIC CATEGORY URL
            # ---------------------------------------------

            "url":
                url_for(
                    "qr_category",
                    code=access_point.code,
                    category=canonical_key,
                ),

        })


    # =====================================================
    # CATEGORY STATS
    #
    # Stats are calculated ONCE per canonical consumer
    # category.
    #
    # Because get_active_content() is alias-aware,
    # asking for "restaurants" can include:
    #
    #     restaurants
    #     foods
    #     check-out-our-specials
    #
    # without displaying duplicate category cards.
    # =====================================================

    category_stats = {}


    # -----------------------------------------------------
    # NEW LISTING CUTOFF
    # -----------------------------------------------------

    new_cutoff = (
        datetime.utcnow()
        - timedelta(days=7)
    )


    for consumer_category in consumer_categories:

        canonical_key = (
            consumer_category["key"]
        )

        representative_category = (
            consumer_category["category"]
        )


        try:

            active_items = get_active_content(
                zone_id=zone.id,
                category_slug=canonical_key,
            )

        except Exception as exc:

            app.logger.exception(
                "Unable to calculate category stats. "
                "zone=%s category=%s error=%s",
                zone.id,
                canonical_key,
                exc,
            )

            active_items = []


        # -------------------------------------------------
        # REMOVE ANY DUPLICATE ITEMS
        #
        # Normally get_active_content() should already
        # return unique ContentItem rows, but this keeps
        # the home stats defensive during migration.
        # -------------------------------------------------

        unique_active_items = []

        seen_active_item_ids = set()


        for item in active_items:

            if item.id in seen_active_item_ids:
                continue

            seen_active_item_ids.add(
                item.id
            )

            unique_active_items.append(
                item
            )


        item_count = len(
            unique_active_items
        )


        # -------------------------------------------------
        # NEW ITEMS
        # -------------------------------------------------

        new_count = 0


        for item in unique_active_items:

            created_at = getattr(
                item,
                "created_at",
                None,
            )

            if (
                created_at
                and created_at >= new_cutoff
            ):

                new_count += 1


        # -------------------------------------------------
        # CONSUMER-AWARE BADGE WORDING
        # -------------------------------------------------

        if canonical_key == "events":

            badge_icon = "📅"
            badge_label = "UPCOMING"


        elif canonical_key == "rentals":

            badge_icon = "🏠"
            badge_label = "AVAILABLE"


        elif canonical_key == "accommodation":

            badge_icon = "🛏️"
            badge_label = "AVAILABLE"


        elif canonical_key == "jobs":

            badge_icon = "💼"
            badge_label = "OPEN"


        elif canonical_key == "emergency":

            badge_icon = "🚨"
            badge_label = "INFO"


        elif canonical_key == "announcements":

            badge_icon = "📢"
            badge_label = "UPDATE"


        else:

            badge_icon = "🔥"
            badge_label = "LIVE"


        stats_data = {

            "count":
                item_count,

            "new_count":
                new_count,

            "label":
                badge_label,

            "icon":
                badge_icon,

            "canonical_key":
                canonical_key,
        }


        # -------------------------------------------------
        # CANONICAL LOOKUP
        #
        # New access.html can use:
        #
        # category_stats["restaurants"]
        # -------------------------------------------------

        category_stats[
            canonical_key
        ] = stats_data


        # -------------------------------------------------
        # REPRESENTATIVE LEGACY LOOKUP
        #
        # Keep this temporarily so existing template code
        # using category.slug does not immediately break.
        # -------------------------------------------------

        category_stats[
            representative_category.slug
        ] = stats_data


    # =====================================================
    # NEW NEAR YOU
    #
    # Iterate through canonical consumer categories rather
    # than every legacy Category row.
    #
    # This reduces duplicate queries and duplicate results.
    # =====================================================

    new_items_pool = []

    seen_new_item_ids = set()


    for consumer_category in consumer_categories:

        canonical_key = (
            consumer_category["key"]
        )


        try:

            category_items = (
                get_active_content(
                    zone_id=zone.id,
                    category_slug=canonical_key,
                )
            )

        except Exception as exc:

            app.logger.exception(
                "Unable to load New Near You items. "
                "zone=%s category=%s error=%s",
                zone.id,
                canonical_key,
                exc,
            )

            continue


        for item in category_items:

            if item.id in seen_new_item_ids:
                continue

            seen_new_item_ids.add(
                item.id
            )

            new_items_pool.append(
                item
            )


    # =====================================================
    # SORT NEWEST FIRST
    # =====================================================

    new_items_pool.sort(

        key=lambda item: (
            item.created_at
            or datetime.min
        ),

        reverse=True,
    )


    # =====================================================
    # LIMIT HOME "WHAT'S NEW" RAIL
    # =====================================================

    new_items = (
        new_items_pool[:8]
    )


    # =====================================================
    # FEATURED LOCAL CONTENT
    #
    # Featured is intentionally independent from:
    #
    # - listing_level
    # - promotion
    # - notification eligibility
    #
    # Any visible listing can be featured.
    # =====================================================

    featured_items_pool = []

    seen_featured_item_ids = set()


    for consumer_category in consumer_categories:

        canonical_key = (
            consumer_category["key"]
        )


        try:

            category_items = (
                get_active_content(
                    zone_id=zone.id,
                    category_slug=canonical_key,
                )
            )

        except Exception as exc:

            app.logger.exception(
                "Unable to load featured items. "
                "zone=%s category=%s error=%s",
                zone.id,
                canonical_key,
                exc,
            )

            continue


        for item in category_items:

            if not item.featured:
                continue

            if item.id in seen_featured_item_ids:
                continue

            seen_featured_item_ids.add(
                item.id
            )

            featured_items_pool.append(
                item
            )


    # =====================================================
    # NEWEST FEATURED CONTENT FIRST
    # =====================================================

    featured_items_pool.sort(

        key=lambda item: (
            item.created_at
            or datetime.min
        ),

        reverse=True,
    )


    # =====================================================
    # LIMIT FEATURED CAROUSEL
    # =====================================================

    featured_items = (
        featured_items_pool[:6]
    )


    # =====================================================
    # RENDER ACCESS PAGE
    # =====================================================

    return render_template(

        "access.html",

        zone=zone,

        access_point=access_point,


        # -------------------------------------------------
        # REAL DATABASE CATEGORY ROWS
        #
        # Keep these because notification subscriptions
        # still use existing real category slugs during
        # the migration.
        # -------------------------------------------------

        categories=categories,


        # -------------------------------------------------
        # UNIQUE CONSUMER NAVIGATION
        #
        # This is now the preferred source for the main
        # Explore section.
        #
        # Each entry contains:
        #
        # {
        #     "key": "restaurants",
        #     "category": <Category>,
        #     "title": "HUNGRY?",
        #     "subtitle": "...",
        #     "icon": "🍔",
        #     "url": "/q/.../restaurants"
        # }
        # -------------------------------------------------

        consumer_categories=(
            consumer_categories
        ),


        # -------------------------------------------------
        # CONSUMER PRESENTATION LOOKUP
        #
        # Used by Featured / What's New cards.
        # Supports both old slugs and canonical keys.
        # -------------------------------------------------

        consumer_category_lookup=(
            consumer_category_lookup
        ),


        # -------------------------------------------------
        # CATEGORY STATS
        # -------------------------------------------------

        category_stats=(
            category_stats
        ),


        # -------------------------------------------------
        # WHAT'S NEW
        # -------------------------------------------------

        new_items=(
            new_items
        ),


        # -------------------------------------------------
        # CATEGORY OBJECT LOOKUP
        # -------------------------------------------------

        category_lookup=(
            category_lookup
        ),


        # -------------------------------------------------
        # FEATURED
        # -------------------------------------------------

        featured_items=(
            featured_items
        ),


        # -------------------------------------------------
        # CATEGORY IMAGES
        #
        # Still keyed by the representative real
        # Category.id.
        # -------------------------------------------------

        category_background_images=(
            category_background_images
        ),
    )
# =========================================================
# PUSH NOTIFICATION UNSUBSCRIBE
# =========================================================

@app.route(
    "/push/unsubscribe",
    methods=["POST"],
)
def push_unsubscribe():

    data = request.get_json(
      silent=True,
    )

    if data is None:

      try:
        data = json.loads(
            request.get_data(
                as_text=True
            )
        )
      except Exception:
        data = {}

    endpoint = data.get(
        "endpoint"
    )


    if not endpoint:

        return jsonify({
            "success": False,
            "error": "Endpoint is required",
        }), 400


    subscriber = (
        PushSubscriber.query
        .filter_by(
            endpoint=endpoint
        )
        .first()
    )


    if subscriber:

        subscriber.active = (
            False
        )

        db.session.commit()


    return jsonify({
        "success": True,
    }), 200

@app.route(
    "/q/<code>/<category>"
)
def qr_category(
    code,
    category,
):

    # =====================================================
    # FIND ACCESS POINT
    # =====================================================

    access_point = (
        AccessPoint.query
        .filter_by(
            code=code,
            active=True,
        )
        .first_or_404()
    )

    zone = access_point.zone


    # =====================================================
    # CLEAN REQUESTED CATEGORY
    #
    # The URL may contain either:
    #
    # LEGACY / CURRENT PUBLIC SLUG
    #
    #     check-out-our-specials
    #     foods
    #     upcoming-event-🥹🔥
    #     beauty-salon
    #     discount-deals
    #     property
    #     transport
    #
    # OR CANONICAL BUSINESS TAXONOMY
    #
    #     restaurants
    #     events
    #     beauty
    #     retail_specials
    #     rentals
    #     delivery
    #
    # =====================================================

    category_slug = (
        str(
            category
            or ""
        )
        .strip()
        .lower()
    )

    if not category_slug:
        abort(404)


    # =====================================================
    # CANONICAL BUSINESS TAXONOMY
    # =====================================================

    canonical_category = (
        normalize_category(
            category_slug
        )
    )

    if not canonical_category:
        abort(404)


    # =====================================================
    # VALIDATE CATEGORY
    #
    # get_category_by_slug() must support:
    #
    # - existing Category table slugs
    # - canonical taxonomy keys
    #
    # This keeps old public URLs alive while the database
    # is gradually moved toward canonical taxonomy.
    # =====================================================

    category_record = (
        get_category_by_slug(
            category_slug,
            active_only=True,
        )
    )

    if not category_record:
        abort(404)


    # =====================================================
    # CONSUMER PRESENTATION
    #
    # Example:
    #
    # URL / DATABASE
    #     check-out-our-specials
    #
    # CANONICAL
    #     restaurants
    #
    # CONSUMER
    #     🍔 HUNGRY?
    #     Find something good to eat
    #
    # =====================================================

    consumer_category = (
        get_consumer_category(
            canonical_category
        )
    )


    # =====================================================
    # GET ACTIVE CONTENT
    #
    # get_active_content() handles all aliases belonging
    # to the same canonical category.
    #
    # Example:
    #
    # restaurants can include ContentItem.category values:
    #
    #     check-out-our-specials
    #     foods
    #     local-restaurants
    #     restaurants
    #
    # =====================================================

    items = (
        get_active_content(
            zone_id=access_point.zone_id,
            category_slug=category_slug,
        )
    )


    # =====================================================
    # RECORD CATEGORY VIEW
    #
    # Keep the actual requested/public category slug in
    # QRScan for now.
    #
    # This preserves continuity with historical analytics.
    #
    # EngagementEvent / engagement.js can separately use
    # canonical_category.
    # =====================================================

    try:

        category_event = QRScan(

            access_point_id=(
                access_point.id
            ),

            event_type=(
                "category_view"
            ),

            category_selected=(
                category_slug
            ),

            user_agent=(
                request.headers.get(
                    "User-Agent",
                    "",
                )
            ),

        )

        db.session.add(
            category_event
        )

        db.session.commit()


    except Exception as exc:

        db.session.rollback()

        app.logger.exception(
            "Failed to record category view. "
            "access_point=%s "
            "category=%s "
            "canonical_category=%s "
            "error=%s",
            access_point.code,
            category_slug,
            canonical_category,
            exc,
        )


    # =====================================================
    # DISPLAY CATEGORY PAGE
    #
    # category
    #     Existing Category database object.
    #
    # canonical_category
    #     Stable internal/business taxonomy.
    #
    # consumer_category
    #     Customer-facing language.
    #
    # =====================================================

    return render_template(

        "category.html",

        zone=zone,

        category=category_record,

        canonical_category=(
            canonical_category
        ),

        consumer_category=(
            consumer_category
        ),

        items=items,

        access_point=access_point,

        today=date.today(),

    )
# =========================================================
# PUBLIC VAPID KEY
# =========================================================
@app.route(
    "/api/engagement",
    methods=["POST"],
)
def record_engagement():

    data = request.get_json(
        silent=True,
    ) or {}

    event_type = (
        data.get("event_type")
        or ""
    ).strip()

    if event_type not in ENGAGEMENT_EVENT_TYPES:

        return jsonify({
            "ok": False,
            "error": "Invalid event type.",
        }), 400


    zone_id = data.get("zone_id")

    access_point_id = data.get(
        "access_point_id"
    )

    content_item_id = data.get(
        "content_item_id"
    )

    category = (
        data.get("category")
        or None
    )


    try:

        event = EngagementEvent(

            event_type=event_type,

            zone_id=zone_id,

            access_point_id=(
                access_point_id
            ),

            content_item_id=(
                content_item_id
            ),

            category=category,

        )

        db.session.add(event)

        db.session.commit()


        return jsonify({
            "ok": True,
        }), 201


    except Exception as exc:

        db.session.rollback()

        current_app.logger.exception(
            "[LaC Engagement] "
            "Unable to record event "
            "type=%s error=%s",
            event_type,
            exc,
        )

        return jsonify({
            "ok": False,
        }), 500


@app.route(
    "/push/public-key",
    methods=["GET"],
)
def push_public_key():

    if not VAPID_PUBLIC_KEY:

        return jsonify({
            "success": False,
            "error":
                "VAPID public key is not configured.",
        }), 500


    return jsonify({

        "success":
            True,

        "public_key":
            VAPID_PUBLIC_KEY,

    }), 200

# =========================================================
# PUSH SUBSCRIBE
# =========================================================
# =========================================================
# PUSH SUBSCRIBE
# =========================================================
@app.route(
    "/push/subscribe",
    methods=["POST"],
)
def push_subscribe():

    data = (
        request.get_json(silent=True)
        or {}
    )

    # =====================================================
    # READ REQUEST
    # =====================================================

    zone_id = data.get("zone_id")

    subscription = (
        data.get("subscription")
        or {}
    )

    categories = (
        data.get("categories")
        or []
    )

    endpoint = subscription.get(
        "endpoint"
    )

    keys = (
        subscription.get("keys")
        or {}
    )

    p256dh = keys.get(
        "p256dh"
    )

    auth_key = keys.get(
        "auth"
    )

    # =====================================================
    # VALIDATE ZONE ID
    # =====================================================

    try:
        zone_id = int(zone_id)

    except (TypeError, ValueError):

        return jsonify({
            "ok": False,
            "error": "A valid zone is required.",
        }), 400

    zone = db.session.get(
        Zone,
        zone_id,
    )

    if (
        not zone
        or not zone.active
    ):

        return jsonify({
            "ok": False,
            "error": "The selected zone is unavailable.",
        }), 400

    # =====================================================
    # VALIDATE PUSH SUBSCRIPTION
    # =====================================================

    if not endpoint:

        return jsonify({
            "ok": False,
            "error": "Push endpoint is required.",
        }), 400

    if not p256dh or not auth_key:

        return jsonify({
            "ok": False,
            "error": "Push subscription keys are missing.",
        }), 400

    if not isinstance(
        categories,
        list,
    ):

        return jsonify({
            "ok": False,
            "error": "Categories must be a list.",
        }), 400

    # =====================================================
    # CLEAN CATEGORY SLUGS
    # =====================================================

    cleaned_categories = []

    for category_slug in categories:

        if not isinstance(
            category_slug,
            str,
        ):
            continue

        category_slug = (
            category_slug.strip()
        )

        if (
            category_slug
            and category_slug
            not in cleaned_categories
        ):

            cleaned_categories.append(
                category_slug
            )

    if not cleaned_categories:

        return jsonify({
            "ok": False,
            "error": (
                "Choose at least one "
                "notification category."
            ),
        }), 400

    # =====================================================
    # VERIFY CATEGORIES AGAINST LIVE CATEGORY SYSTEM
    #
    # IMPORTANT:
    # Store the REAL public category slug.
    #
    # Example:
    # upcoming-event-🥹🔥
    #
    # This ensures the saved preference matches
    # ContentItem.category exactly during push targeting.
    # =====================================================

    active_categories = (
        Category.query
        .filter(
            Category.active.is_(True)
        )
        .all()
    )

    valid_category_slugs = {
        category_record.slug
        for category_record
        in active_categories
    }

    cleaned_categories = [
        category_slug
        for category_slug
        in cleaned_categories
        if category_slug
        in valid_category_slugs
    ]

    if not cleaned_categories:

        return jsonify({
            "ok": False,
            "error": (
                "No valid notification "
                "categories were selected."
            ),
        }), 400

    # =====================================================
    # CREATE / UPDATE SUBSCRIBER
    # =====================================================

    try:

        subscriber = (
            PushSubscriber.query
            .filter_by(
                endpoint=endpoint
            )
            .first()
        )

        if subscriber is None:

            subscriber = PushSubscriber(

                zone_id=
                    zone.id,

                endpoint=
                    endpoint,

                p256dh=
                    p256dh,

                auth_key=
                    auth_key,

                active=
                    True,
            )

            db.session.add(
                subscriber
            )

            # Need subscriber.id before creating
            # preference rows.
            db.session.flush()

        else:

            # The browser subscription already exists.
            # Update its current zone and keys.

            subscriber.zone_id = (
                zone.id
            )

            subscriber.p256dh = (
                p256dh
            )

            subscriber.auth_key = (
                auth_key
            )

            subscriber.active = (
                True
            )

        # =================================================
        # REPLACE CATEGORY PREFERENCES
        #
        # Current selections become the source of truth.
        # =================================================

        PushSubscriberPreference.query.filter_by(
            subscriber_id=
                subscriber.id
        ).delete(
            synchronize_session=False
        )

        for category_slug in (
            cleaned_categories
        ):

            preference = (
                PushSubscriberPreference(

                    subscriber_id=
                        subscriber.id,

                    category=
                        category_slug,
                )
            )

            db.session.add(
                preference
            )

        # =================================================
        # COMMIT SUBSCRIBER + PREFERENCES ATOMICALLY
        # =================================================

        db.session.commit()

        current_app.logger.info(
            "[Kalxa Push] Subscription preferences saved "
            "subscriber_id=%s "
            "zone_id=%s "
            "categories=%s",
            subscriber.id,
            subscriber.zone_id,
            ",".join(
                cleaned_categories
            ),
        )

        return jsonify({

            "ok":
                True,

            "subscriber_id":
                subscriber.id,

            "zone_id":
                subscriber.zone_id,

            "categories":
                cleaned_categories,

        }), 200

    except Exception as exc:

        db.session.rollback()

        current_app.logger.exception(
            "[Kalxa Push] Unable to save "
            "subscription preferences "
            "zone_id=%s error=%s",
            zone_id,
            exc,
        )

        return jsonify({
            "ok": False,
            "error": (
                "Unable to save "
                "notification preferences."
            ),
        }), 500
# =========================================================
# PUBLIC LISTING DETAIL
# =========================================================
@app.route(
    "/listing/<int:item_id>"
)
def listing_detail(
    item_id,
):

    # =====================================================
    # LOAD LISTING
    # =====================================================

    item = (
        ContentItem.query
        .get_or_404(
            item_id
        )
    )


    # =====================================================
    # ZONE
    # =====================================================

    zone = item.zone


    # =====================================================
    # CATEGORY
    #
    # ContentItem.category stores the category slug.
    #
    # Example:
    # item.category = "restaurants"
    #
    # Category.slug = "restaurants"
    # =====================================================

    category = (
        Category.query
        .filter_by(
            slug=item.category
        )
        .first()
    )


    # =====================================================
    # SAFETY FALLBACK
    #
    # If an old ContentItem contains a category slug
    # that no longer exists, do not crash the page.
    # =====================================================

    if category is None:

        current_app.logger.warning(
            "[Kalxa Listing] "
            "Category not found "
            "for listing_id=%s category=%s",
            item.id,
            item.category,
        )


    # =====================================================
    # ACCESS POINT ATTRIBUTION
    #
    # Example:
    #
    # /listing/45?ap=1
    # =====================================================

    access_point = None


    access_point_id = request.args.get(
        "ap",
        type=int,
    )


    if access_point_id:

        access_point = (
            AccessPoint.query
            .filter_by(
                id=access_point_id,
                zone_id=item.zone_id,
            )
            .first()
        )


    # =====================================================
    # RENDER LISTING
    # =====================================================

    return render_template(
        "listing_detail.html",

        item=item,

        zone=zone,

        category=category,

        access_point=access_point,
    )

# =========================================================
# FIND LIVE ACCESS POINT FOR CONTENT
# =========================================================

def find_live_access_point(
    published_content,
):

    if not published_content:
        return None

    # -----------------------------------------------------
    # 1. CATEGORY-SPECIFIC ACCESS POINT
    # -----------------------------------------------------

    access_point = (
        AccessPoint.query
        .filter(
            AccessPoint.zone_id
            == published_content.zone_id,

            AccessPoint.active.is_(
                True
            ),

            AccessPoint.qr_type
            == "category",

            AccessPoint.default_category
            == published_content.category,
        )
        .order_by(
            AccessPoint.id.asc()
        )
        .first()
    )

    if access_point:
        return access_point

    # -----------------------------------------------------
    # 2. GENERAL ACCESS POINT
    # -----------------------------------------------------

    access_point = (
        AccessPoint.query
        .filter(
            AccessPoint.zone_id
            == published_content.zone_id,

            AccessPoint.active.is_(
                True
            ),

            AccessPoint.qr_type
            == "general",
        )
        .order_by(
            AccessPoint.id.asc()
        )
        .first()
    )

    if access_point:
        return access_point

    # -----------------------------------------------------
    # 3. ANY ACTIVE ACCESS POINT
    # -----------------------------------------------------

    return (
        AccessPoint.query
        .filter(
            AccessPoint.zone_id
            == published_content.zone_id,

            AccessPoint.active.is_(
                True
            ),
        )
        .order_by(
            AccessPoint.id.asc()
        )
        .first()
    )

# =========================================================
# CONTENT WORKFLOW CONFIGURATION
# =========================================================

VALID_LIFETIME_TYPES = {
    "time_specific",
    "until_unavailable",
    "ongoing",
    "recurring",
}


# ---------------------------------------------------------
# Each category can contain multiple CONTENT TYPES.
#
# The content type determines:
# - how long the listing lives
# - whether it may trigger notifications later
#
# notification_eligible DOES NOT send a notification yet.
# It only records whether this type is allowed to use
# notifications once the PWA push system is built.
# ---------------------------------------------------------

CONTENT_WORKFLOWS = {

    # =====================================================
    # PROPERTY
    # =====================================================

    "property": {

        "room": {
            "lifetime_type":
                "until_unavailable",
            "notification_eligible":
                True,
        },

        "rental": {
            "lifetime_type":
                "until_unavailable",
            "notification_eligible":
                True,
        },

        "property_sale": {
            "lifetime_type":
                "until_unavailable",
            "notification_eligible":
                True,
        },

        "hotel_lodge": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "accommodation_special": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },
    },


    # =====================================================
    # EVENTS
    # =====================================================

    "events": {

        "event": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "entertainment": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "church_event": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "sports_event": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "community_event": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "business_event": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },
    },


    # =====================================================
    # GROCERY / RETAIL SPECIALS
    # =====================================================

    "discount-deals": {

        "grocery_special": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "product_discount": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "weekend_special": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "clearance": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },
    },


    # =====================================================
    # FOOD / RESTAURANTS
    # =====================================================

    "local-restaurants": {

        "restaurant": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "takeaway": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "daily_special": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "weekend_special": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "food_deal": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },
    },


    # =====================================================
    # JOBS / OPPORTUNITIES
    # =====================================================

    "jobs": {

        "job": {
            "lifetime_type":
                "until_unavailable",
            "notification_eligible":
                True,
        },

        "learnership": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "internship": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "training": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "tender": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "business_opportunity": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },
    },


    # Optional future category slug.
    "opportunities": {

        "job": {
            "lifetime_type":
                "until_unavailable",
            "notification_eligible":
                True,
        },

        "learnership": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "internship": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "training": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "tender": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },

        "business_opportunity": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },
    },


    # =====================================================
    # SERVICES
    # =====================================================

    "services": {

        "service_provider": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "plumber": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "mechanic": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "electrician": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "builder": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "cleaning_service": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },
    },


    # =====================================================
    # BEAUTY / SALON
    # =====================================================

    "beauty-salon": {

        "salon": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "barber": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "beauty_service": {
            "lifetime_type":
                "ongoing",
            "notification_eligible":
                False,
        },

        "beauty_special": {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        },
    },
}
@app.route(
    "/claim/<int:item_id>",
    methods=["GET", "POST"],
)
def claim_listing(
    item_id,
):

    # =====================================================
    # FIND LISTING
    # =====================================================

    item = (
        ContentItem.query
        .filter_by(
            id=item_id,
            active=True,
            archived=False,
        )
        .first_or_404()
    )


    # =====================================================
    # CHECK WHETHER LISTING CAN BE CLAIMED
    # =====================================================

    if not item.can_be_claimed():

        return render_template(
            "claim_listing_unavailable.html",
            item=item,
        ), 409


    # =====================================================
    # CHECK FOR EXISTING PENDING CLAIM
    # =====================================================

    if item.has_pending_claim():

        return render_template(
            "claim_listing_pending.html",
            item=item,
        ), 409


    # =====================================================
    # HANDLE CLAIM SUBMISSION
    # =====================================================

    if request.method == "POST":

        claimant_name = (
            request.form.get(
                "claimant_name",
                "",
            )
            .strip()
        )

        business_name = (
            request.form.get(
                "business_name",
                "",
            )
            .strip()
        )

        phone = (
            request.form.get(
                "phone",
                "",
            )
            .strip()
        )

        email = (
            request.form.get(
                "email",
                "",
            )
            .strip()
        )

        proof_notes = (
            request.form.get(
                "proof_notes",
                "",
            )
            .strip()
        )


        # =================================================
        # VALIDATION
        # =================================================

        errors = []


        if not claimant_name:

            errors.append(
                "Please enter your name."
            )


        if not business_name:

            errors.append(
                "Please enter the business name."
            )


        if not phone:

            errors.append(
                "Please enter a contact number."
            )


        if errors:

            return render_template(
                "claim_listing.html",
                item=item,
                errors=errors,
                form_data=request.form,
            ), 400


        # =================================================
        # CREATE PENDING CLAIM
        # =================================================

        claim = ListingClaim(
            content_item_id=item.id,

            claimant_name=claimant_name,

            business_name=business_name,

            phone=phone,

            email=(
                email
                if email
                else None
            ),

            proof_notes=(
                proof_notes
                if proof_notes
                else None
            ),

            status="pending",
        )


        try:

            db.session.add(
                claim
            )

            db.session.commit()


        except Exception as exc:

            db.session.rollback()

            app.logger.exception(
                "Failed to create listing claim. "
                "content_item_id=%s error=%s",
                item.id,
                exc,
            )

            errors = [
                (
                    "We could not submit your claim "
                    "right now. Please try again."
                )
            ]

            return render_template(
                "claim_listing.html",
                item=item,
                errors=errors,
                form_data=request.form,
            ), 500


        # =================================================
        # SUCCESS
        # =================================================

        return redirect(
            url_for(
                "claim_listing_success",
                claim_id=claim.id,
            )
        )


    # =====================================================
    # DISPLAY CLAIM FORM
    # =====================================================

    return render_template(
        "claim_listing.html",
        item=item,
        errors=[],
        form_data={},
    )

@app.route(
    "/claim/success/<int:claim_id>"
)
def claim_listing_success(
    claim_id,
):

    claim = (
        ListingClaim.query
        .filter_by(
            id=claim_id,
        )
        .first_or_404()
    )


    return render_template(
        "claim_listing_success.html",
        claim=claim,
        item=claim.content_item,
    )
# =========================================================
# WORKFLOW HELPERS
# =========================================================

def get_content_workflow(
    category_slug,
    content_type,
):

    # =====================================================
    # CLEAN INPUT
    # =====================================================

    category_slug = (
        str(
            category_slug
            or ""
        )
        .strip()
        .lower()
    )

    content_type = (
        str(
            content_type
            or ""
        )
        .strip()
        .lower()
    )


    # =====================================================
    # NORMALIZE TO BUSINESS TAXONOMY
    #
    # Examples:
    #
    # upcoming-event-🥹🔥
    #       -> events
    #
    # check-out-our-specials
    #       -> restaurants
    #
    # beauty-salon
    #       -> beauty
    #
    # property
    #       -> rentals
    #
    # transport
    #       -> delivery
    # =====================================================

    canonical_category = normalize_category(
        category_slug
    )


    # =====================================================
    # CATEGORY WORKFLOW
    # =====================================================

    category_workflows = (
        CONTENT_WORKFLOWS.get(
            canonical_category,
            {},
        )
    )


    workflow = (
        category_workflows.get(
            content_type
        )
    )


    if workflow:
        return workflow


    # =====================================================
    # SAFE CATEGORY FALLBACKS
    # =====================================================


    # -----------------------------------------------------
    # EVENTS
    # -----------------------------------------------------

    if canonical_category == "events":

        return {
            "lifetime_type":
                "time_specific",

            "notification_eligible":
                True,
        }


    # -----------------------------------------------------
    # RETAIL SPECIALS
    # -----------------------------------------------------

    if canonical_category == "retail_specials":

        return {
            "lifetime_type":
                "time_specific",

            "notification_eligible":
                True,
        }


    # -----------------------------------------------------
    # RENTALS
    # -----------------------------------------------------

    if canonical_category == "rentals":

        return {
            "lifetime_type":
                "availability_based",

            "notification_eligible":
                False,
        }


    # -----------------------------------------------------
    # JOBS / OPPORTUNITIES
    # -----------------------------------------------------

    if canonical_category == "jobs":

        return {
            "lifetime_type":
                "time_specific",

            "notification_eligible":
                True,
        }


    # -----------------------------------------------------
    # ANNOUNCEMENTS
    # -----------------------------------------------------

    if canonical_category == "announcements":

        return {
            "lifetime_type":
                "time_specific",

            "notification_eligible":
                False,
        }


    # -----------------------------------------------------
    # DEFAULT ONGOING BUSINESS PRESENCE
    #
    # restaurants
    # beauty
    # accommodation
    # delivery
    # services
    # building
    # emergency
    # -----------------------------------------------------

    return {
        "lifetime_type":
            "ongoing",

        "notification_eligible":
            False,
    }

def get_legacy_lifetime_type(
    item,
):

    """
    Used for older ContentItem / PendingSubmission records
    created before lifetime_type existed.
    """

    if getattr(
        item,
        "lifetime_type",
        None,
    ):
        return item.lifetime_type

    if (
        getattr(
            item,
            "category",
            None,
        )
        == "events"
    ):
        return "time_specific"

    if getattr(
        item,
        "end_date",
        None,
    ):
        return "time_specific"

    return "ongoing"


# =========================================================
# PUBLIC CONTENT SUBMISSION
# =========================================================
@app.route(
    "/submit",
    methods=["GET", "POST"],
)
def submit_content():

    # =====================================================
    # LOAD FORM OPTIONS
    # =====================================================
    #
    # IMPORTANT:
    #
    # zones
    # -----
    # Still come from the database.
    #
    # business_categories
    # -------------------
    # Come from categories.py.
    #
    # These are the stable business taxonomy values used
    # by NEW submissions.
    #
    # Example:
    #
    # restaurants -> Restaurant & Food
    # events      -> Events & Entertainment
    # beauty      -> Salon, Barber & Beauty
    #
    # Consumer-facing wording such as HUNGRY? and
    # WHAT'S ON? is NOT used here.
    # =====================================================

    zones = (
        Zone.query
        .filter_by(
            active=True
        )
        .order_by(
            Zone.name
        )
        .all()
    )

    business_categories = (
        BUSINESS_CATEGORIES
    )


    # =====================================================
    # PRESELECT ZONE
    #
    # Example:
    #
    # /submit?zone_id=1
    #
    # If the visitor entered the submission form from a
    # Kalxa zone page, that zone becomes the default area.
    # =====================================================

    selected_zone_id = (
        request.args.get(
            "zone_id",
            type=int,
        )
    )

    selected_zone = None


    if selected_zone_id:

        selected_zone = (
            db.session.get(
                Zone,
                selected_zone_id,
            )
        )

        # Never trust the URL alone.
        # The zone must exist and still be active.

        if (
            not selected_zone
            or not selected_zone.active
        ):

            selected_zone_id = None
            selected_zone = None


    # =====================================================
    # TEMPLATE RENDER HELPER
    # =====================================================
    #
    # Keeping this in one place prevents validation branches
    # from accidentally passing the old public Category
    # records back to submit.html.
    #
    # We temporarily also expose the same dictionary under
    # "categories". This makes the transition easier while
    # submit.html is updated in the next step.
    # =====================================================

    def render_submit_form():

        return render_template(
            "submit.html",

            zones=
                zones,

            business_categories=
                business_categories,

            # Temporary compatibility alias.
            #
            # The updated submit.html should use
            # business_categories directly.
            categories=
                business_categories,

            selected_zone_id=
                selected_zone_id,

            selected_zone=
                selected_zone,
        )


    # =====================================================
    # POST
    # =====================================================

    if request.method == "POST":

        # =================================================
        # BASIC FORM DATA
        # =================================================

        zone_id = (
            request.form.get(
                "zone_id",
                type=int,
            )
        )


        # =================================================
        # STABLE BUSINESS CATEGORY
        # =================================================
        #
        # New forms should POST canonical values:
        #
        # events
        # restaurants
        # beauty
        # retail_specials
        # accommodation
        # rentals
        # delivery
        # services
        # jobs
        # emergency
        # announcements
        # building
        #
        # normalize_category() also allows an old category
        # value to reach this route during the transition.
        # =================================================

        raw_category = (
            request.form.get(
                "category",
                "",
            )
            .strip()
        )

        category_key = (
            normalize_category(
                raw_category
            )
        )


        content_type = (
            request.form.get(
                "content_type",
                "",
            )
            .strip()
            .lower()
            or None
        )

        title = (
            request.form.get(
                "title",
                "",
            )
            .strip()
        )

        description = (
            request.form.get(
                "description",
                "",
            )
            .strip()
            or None
        )

        business_name = (
            request.form.get(
                "business_name",
                "",
            )
            .strip()
            or None
        )

        venue = (
            request.form.get(
                "venue",
                "",
            )
            .strip()
            or None
        )

        start_time_raw = (
          request.form.get(
            "start_time"
          )
          or ""
        ).strip()


        end_time_raw = (
          request.form.get(
            "end_time"
          )
          or ""
        ).strip()

        try:

          start_time = parse_optional_time(
            start_time_raw
          )

          end_time = parse_optional_time(
            end_time_raw
          )

        except ValueError:

          flash(
            "Please enter valid campaign times.",
            "error",
          )

          return redirect(
            request.url
          )

        price = (
            request.form.get(
                "price",
                "",
            )
            .strip()
            or None
        )


        # =================================================
        # PUBLIC CONTACT / ACTION INFORMATION
        # =================================================

        contact = (
            request.form.get(
                "contact",
                "",
            )
            .strip()
            or None
        )

        whatsapp_number = (
            request.form.get(
                "whatsapp_number",
                "",
            )
            .strip()
            or None
        )

        directions_url = (
            request.form.get(
                "directions_url",
                "",
            )
            .strip()
            or None
        )

        ticket_url = (
            request.form.get(
                "ticket_url",
                "",
            )
            .strip()
            or None
        )


        # =================================================
        # SUBMITTER INFORMATION
        # =================================================

        submitter_name = (
            request.form.get(
                "submitter_name",
                "",
            )
            .strip()
        )

        submitter_email = (
            request.form.get(
                "submitter_email",
                "",
            )
            .strip()
            or None
        )

        submitter_phone = (
            request.form.get(
                "submitter_phone",
                "",
            )
            .strip()
            or None
        )


        # =================================================
        # REQUIRED FIELDS
        # =================================================

        if (
            not zone_id
            or not category_key
            or not title
            or not submitter_name
        ):

            flash(
                "Please complete all required fields.",
                "error",
            )

            return render_submit_form()


        # =================================================
        # VALIDATE HOME ZONE
        # =================================================

        zone = (
            db.session.get(
                Zone,
                zone_id,
            )
        )

        if (
            not zone
            or not zone.active
        ):

            flash(
                "Please select a valid area.",
                "error",
            )

            return render_submit_form()


        # =================================================
        # RECONSTRUCT SELECTED ZONE FROM POST
        # =================================================
        #
        # This keeps the locked-zone display correct if
        # validation later returns the user to the form.
        # =================================================

        selected_zone_id = (
            zone.id
        )

        selected_zone = (
            zone
        )


        # =================================================
        # VALIDATE BUSINESS TAXONOMY CATEGORY
        # =================================================
        #
        # OLD:
        #
        # get_active_category_by_slug(category_slug)
        #
        # That validated against the consumer/public
        # Category table and therefore tied submission
        # taxonomy to navigation wording.
        #
        # NEW:
        #
        # Validate against BUSINESS_CATEGORIES.
        # =================================================

        if (
            category_key
            not in BUSINESS_CATEGORIES
        ):

            flash(
                "Please select a valid business/content category.",
                "error",
            )

            return render_submit_form()


        # =================================================
        # INTERNAL WORKFLOW CATEGORY
        # =================================================
        #
        # The stable category is now also the starting point
        # for workflow/pricing.
        #
        # Example:
        #
        # category_key = "events"
        # category_key = "restaurants"
        # category_key = "beauty"
        #
        # No consumer-facing wording is involved here.
        # =================================================

        workflow_category = (
            category_key
        )


        # =================================================
        # DETERMINE WORKFLOW
        # =================================================

        workflow = (
            get_content_workflow(
                workflow_category,
                content_type,
            )
        )

        lifetime_type = (
            workflow.get(
                "lifetime_type"
            )
        )


        # =================================================
        # NOTIFICATION ELIGIBILITY
        # =================================================
        #
        # Every newly approved public submission is allowed
        # to enter the notification workflow.
        #
        # The actual push system still decides who should
        # receive the notification.
        # =================================================

        notification_eligible = True


        if (
            lifetime_type
            not in VALID_LIFETIME_TYPES
        ):

            flash(
                "The selected listing type has an invalid "
                "lifecycle configuration.",
                "error",
            )

            return render_submit_form()


        availability_status = (
            "available"
        )


        # =================================================
        # DETERMINE COMMERCIAL PRICING MODEL
        # =================================================
        #
        # Pricing now receives the stable taxonomy key.
        #
        # Examples:
        #
        # events
        # restaurants
        # beauty
        # retail_specials
        # =================================================

        pricing_model = (
            get_pricing_model(
                workflow_category,
                content_type,
            )
        )

        commercial_duration_days = None
        amount_due = None
        distribution_zone_ids = []


        # =================================================
        # COMMERCIAL PACKAGE
        # =================================================

        if pricing_model:

            raw_duration = (
                request.form.get(
                    "commercial_duration_days",
                    "",
                )
                .strip()
            )

            try:

                commercial_duration_days = int(
                    raw_duration
                )

            except (
                TypeError,
                ValueError,
            ):

                flash(
                    "Please select a valid Kalxa "
                    "package duration.",
                    "error",
                )

                return render_submit_form()


            # =============================================
            # CAMPAIGN
            # =============================================

            if (
                pricing_model
                == PRICING_MODEL_CAMPAIGN
            ):

                raw_distribution_zone_ids = (
                    request.form.getlist(
                        "distribution_zone_ids"
                    )
                )

                for raw_zone_id in (
                    raw_distribution_zone_ids
                ):

                    try:

                        distribution_zone_id = int(
                            raw_zone_id
                        )

                    except (
                        TypeError,
                        ValueError,
                    ):

                        continue


                    if (
                        distribution_zone_id
                        not in distribution_zone_ids
                    ):

                        distribution_zone_ids.append(
                            distribution_zone_id
                        )


                # -----------------------------------------
                # HOME ZONE MUST BE INCLUDED
                # -----------------------------------------

                if (
                    zone.id
                    not in distribution_zone_ids
                ):

                    flash(
                        "Your campaign must include "
                        "its home area.",
                        "error",
                    )

                    return render_submit_form()


                # -----------------------------------------
                # AT LEAST ONE ZONE
                # -----------------------------------------

                if not distribution_zone_ids:

                    flash(
                        "Please select at least one "
                        "campaign area.",
                        "error",
                    )

                    return render_submit_form()


                # -----------------------------------------
                # MAXIMUM 3 ZONES
                # -----------------------------------------

                if (
                    len(
                        distribution_zone_ids
                    )
                    > 3
                ):

                    flash(
                        "Kalxa Campaign currently supports "
                        "a maximum of 3 areas.",
                        "error",
                    )

                    return render_submit_form()


                # -----------------------------------------
                # VALIDATE SELECTED ZONES
                # -----------------------------------------

                valid_distribution_zones = (
                    Zone.query
                    .filter(
                        Zone.id.in_(
                            distribution_zone_ids
                        ),
                        Zone.active.is_(True),
                    )
                    .all()
                )

                valid_distribution_zone_ids = {
                    distribution_zone.id
                    for distribution_zone
                    in valid_distribution_zones
                }


                if (
                    len(
                        valid_distribution_zone_ids
                    )
                    !=
                    len(
                        distribution_zone_ids
                    )
                ):

                    flash(
                        "One or more selected campaign "
                        "areas are invalid.",
                        "error",
                    )

                    return render_submit_form()


                zone_count = (
                    len(
                        distribution_zone_ids
                    )
                )


            # =============================================
            # PRESENCE
            # =============================================

            elif (
                pricing_model
                == PRICING_MODEL_PRESENCE
            ):

                distribution_zone_ids = []

                zone_count = 1


            # =============================================
            # INVALID PRICING MODEL
            # =============================================

            else:

                flash(
                    "The selected listing has an invalid "
                    "Kalxa pricing model.",
                    "error",
                )

                return render_submit_form()


            # =============================================
            # SERVER-SIDE PRICE CALCULATION
            # =============================================

            try:

                amount_due = (
                    calculate_kalxa_price(

                        pricing_model=
                            pricing_model,

                        duration_days=
                            commercial_duration_days,

                        zone_count=
                            zone_count,
                    )
                )

            except KalxaPricingError as error:

                flash(
                    str(error),
                    "error",
                )

                return render_submit_form()


        # =================================================
        # DATE PARSER
        # =================================================

        def parse_form_date(
            field_name
        ):

            value = (
                request.form.get(
                    field_name,
                    "",
                )
                .strip()
            )

            if not value:

                return None

            return datetime.strptime(
                value,
                "%Y-%m-%d",
            ).date()


        # =================================================
        # PARSE DATES
        # =================================================

        try:

            publish_from = (
                parse_form_date(
                    "publish_from"
                )
            )

            event_date = (
                parse_form_date(
                    "event_date"
                )
            )

            event_end_date = (
                parse_form_date(
                    "event_end_date"
                )
            )

            start_date = (
                parse_form_date(
                    "start_date"
                )
            )

            end_date = (
                parse_form_date(
                    "end_date"
                )
            )

        except ValueError:

            flash(
                "One or more dates are invalid.",
                "error",
            )

            return render_submit_form()


        # =================================================
        # TIME-SPECIFIC CONTENT
        # =================================================

        if (
            lifetime_type
            == "time_specific"
        ):

            # =============================================
            # EVENTS
            # =============================================

            if (
                workflow_category
                == "events"
            ):

                if not event_date:

                    flash(
                        "Event Date is required "
                        "for events.",
                        "error",
                    )

                    return render_submit_form()


                if (
                    publish_from
                    and
                    publish_from > event_date
                ):

                    flash(
                        "Publish From cannot be after "
                        "Event Date.",
                        "error",
                    )

                    return render_submit_form()


                if (
                    event_end_date
                    and
                    event_end_date < event_date
                ):

                    flash(
                        "Event End Date cannot be before "
                        "Event Date.",
                        "error",
                    )

                    return render_submit_form()


                start_date = None
                end_date = None


            # =============================================
            # OTHER TIME-SPECIFIC CONTENT
            # =============================================

            else:

                if not end_date:

                    flash(
                        "An end date is required for "
                        "time-specific listings.",
                        "error",
                    )

                    return render_submit_form()


                if (
                    start_date
                    and
                    end_date
                    and
                    end_date < start_date
                ):

                    flash(
                        "End date cannot be before "
                        "start date.",
                        "error",
                    )

                    return render_submit_form()


                publish_from = None
                event_date = None
                event_end_date = None


        # =================================================
        # UNTIL UNAVAILABLE
        # =================================================

        elif (
            lifetime_type
            == "until_unavailable"
        ):

            publish_from = None
            event_date = None
            event_end_date = None
            start_date = None
            end_date = None


        # =================================================
        # ONGOING
        # =================================================

        elif (
            lifetime_type
            == "ongoing"
        ):

            publish_from = None
            event_date = None
            event_end_date = None
            start_date = None
            end_date = None


        # =================================================
        # RECURRING
        # =================================================

        elif (
            lifetime_type
            == "recurring"
        ):

            publish_from = None
            event_date = None
            event_end_date = None

            if (
                start_date
                and
                end_date
                and
                end_date < start_date
            ):

                flash(
                    "End date cannot be before "
                    "start date.",
                    "error",
                )

                return render_submit_form()


        # =================================================
        # IMAGE UPLOADS
        # =================================================

        uploaded_images = (
            request.files.getlist(
                "images"
            )
        )

        uploaded_images = [
            image
            for image
            in uploaded_images
            if (
                image
                and image.filename
            )
        ]


        if (
            len(uploaded_images)
            > 3
        ):

            flash(
                "You can upload a maximum "
                "of 3 images.",
                "error",
            )

            return render_submit_form()


        # =================================================
        # CREATE PENDING SUBMISSION
        # =================================================

        submission = PendingSubmission(

            # ---------------------------------------------
            # LOCATION
            # ---------------------------------------------

            zone_id=
                zone.id,


            category=
                category_key,


            # ---------------------------------------------
            # WORKFLOW
            # ---------------------------------------------

            content_type=
                content_type,

            lifetime_type=
                lifetime_type,

            availability_status=
                availability_status,

            notification_eligible=
                notification_eligible,


            # ---------------------------------------------
            # LISTING DATA
            # ---------------------------------------------

            title=
                title,

            description=
                description,

            business_name=
                business_name,

            venue=
                venue,

            price=
                price,

            contact=
                contact,


            # ---------------------------------------------
            # PUBLIC ACTION / CONTACT DATA
            # ---------------------------------------------

            whatsapp_number=
                whatsapp_number,

            directions_url=
                directions_url,

            ticket_url=
                ticket_url,


            # ---------------------------------------------
            # SUBMITTER
            # ---------------------------------------------

            submitter_name=
                submitter_name,

            submitter_email=
                submitter_email,

            submitter_phone=
                submitter_phone,


            # ---------------------------------------------
            # NATURAL CONTENT DATES
            # ---------------------------------------------

            publish_from=
                publish_from,

            event_date=
                event_date,

            event_end_date=
                event_end_date,

            start_time=
                start_time,

            start_date=
                start_date,

            end_time=
                end_time,

            end_date=
                end_date,



            # ---------------------------------------------
            # COMMERCIAL PACKAGE
            # ---------------------------------------------

            pricing_model=
                pricing_model,

            commercial_duration_days=
                commercial_duration_days,

            amount_due=
                amount_due,

            payment_status=(
                "unpaid"
                if pricing_model
                else "waived"
            ),

            distribution_zone_ids=
                distribution_zone_ids,


            # ---------------------------------------------
            # MODERATION
            # ---------------------------------------------

            status=
                "pending",
        )


        # =================================================
        # TRACKING CODE
        # =================================================

        if not submission.tracking_code:

            submission.tracking_code = (
                uuid.uuid4()
                .hex[:12]
                .upper()
            )


        # =================================================
        # SAVE SUBMISSION + IMAGES
        # =================================================

        try:

            db.session.add(
                submission
            )

            db.session.flush()

            first_image_url = None


            for (
                index,
                uploaded_image,
            ) in enumerate(
                uploaded_images,
                start=1,
            ):

                image_url = (
                    upload_lac_image(
                        uploaded_image,
                        folder=
                            "lac/submissions",
                    )
                )


                if not image_url:

                    continue


                if not first_image_url:

                    first_image_url = (
                        image_url
                    )


                submission_image = (
                    PendingSubmissionImage(

                        submission_id=
                            submission.id,

                        image_url=
                            image_url,

                        display_order=
                            index,
                    )
                )

                db.session.add(
                    submission_image
                )


            if first_image_url:

                submission.image_url = (
                    first_image_url
                )


            db.session.commit()


            current_app.logger.info(
                "[Kalxa Submission] Created "
                "submission_id=%s "
                "zone_id=%s "
                "category=%s "
                "notification_eligible=%s "
                "pricing_model=%s "
                "payment_status=%s",
                submission.id,
                submission.zone_id,
                submission.category,
                submission.notification_eligible,
                submission.pricing_model,
                submission.payment_status,
            )


        except ValueError as error:

            db.session.rollback()

            flash(
                str(error),
                "error",
            )

            return render_submit_form()


        except Exception as error:

            db.session.rollback()

            current_app.logger.exception(
                "[Kalxa Submission] "
                "Unable to create submission: %s",
                error,
            )

            flash(
                "Unable to submit your listing. "
                "Please try again.",
                "error",
            )

            return render_submit_form()


        # =================================================
        # SUCCESS
        # =================================================

        return redirect(
            url_for(
                "submission_success",
                code=
                    submission.tracking_code,
            )
        )


    # =====================================================
    # GET
    # =====================================================

    return render_submit_form()
# =========================================================
# SUBMISSION SUCCESS
# =========================================================
@app.route(
    "/submit/success/<code>"
)
def submission_success(code):

    submission = (
        PendingSubmission.query
        .filter_by(
            tracking_code=code
        )
        .first_or_404()
    )

    # ---------------------------------------------
    # COMMERCIAL PAYMENT INFORMATION
    # ---------------------------------------------

    is_commercial = (
        submission.pricing_model in {
            PRICING_MODEL_PRESENCE,
            PRICING_MODEL_CAMPAIGN,
        }
        and submission.amount_due is not None
    )

    payment_required = (
        is_commercial
        and submission.payment_status == "unpaid"
    )

    amount_due = submission.amount_due

    return render_template(
        "submission_success.html",
        submission=submission,
        is_commercial=is_commercial,
        payment_required=payment_required,
        amount_due=amount_due,
    )

# =========================================================
# SUBMISSION STATUS
# =========================================================

@app.route(
    "/submission/status/<code>"
)
def submission_status(code):

    submission = (
        PendingSubmission.query
        .filter_by(
            tracking_code=code
        )
        .first_or_404()
    )

    published_content = None
    live_access_point = None

    if submission.published_content_id:

        published_content = (
            db.session.get(
                ContentItem,
                submission.published_content_id,
            )
        )

    if published_content:

        live_access_point = (
            find_live_access_point(
                published_content
            )
        )

    return render_template(
        "submission_status.html",

        submission=
            submission,

        published_content=
            published_content,

        live_access_point=
            live_access_point,
    )


# =========================================================
# BUSINESS / ORGANISER DASHBOARD
# =========================================================

@app.route(
    "/submission/dashboard/<code>"
)
def submission_dashboard(code):

    submission = (
        PendingSubmission.query
        .filter_by(
            tracking_code=code
        )
        .first_or_404()
    )

    published_content = None
    live_access_point = None
    category_record = None

    listing_expired = False
    listing_closed = False

    expiry_date = None
    days_remaining = None

    lifetime_type = (
        get_legacy_lifetime_type(
            submission
        )
    )

    # -----------------------------------------------------
    # CATEGORY
    # -----------------------------------------------------

    category_record = (
        Category.query
        .filter_by(
            slug=submission.category
        )
        .first()
    )

    # -----------------------------------------------------
    # APPROVED CONTENT
    # -----------------------------------------------------

    if submission.published_content_id:

        published_content = (
            db.session.get(
                ContentItem,
                submission.published_content_id,
            )
        )

    # -----------------------------------------------------
    # LIVE ACCESS POINT
    # -----------------------------------------------------

    if published_content:

        live_access_point = (
            find_live_access_point(
                published_content
            )
        )

        lifetime_type = (
            get_legacy_lifetime_type(
                published_content
            )
        )

        # -------------------------------------------------
        # AVAILABILITY STATUS
        # -------------------------------------------------

        closed_statuses = {
            "taken",
            "sold",
            "filled",
            "closed",
            "expired",
        }

        listing_closed = (
            published_content
            .availability_status
            in closed_statuses
        )

        # -------------------------------------------------
        # EXPIRY
        #
        # Only time-specific content automatically expires.
        # -------------------------------------------------

        if (
            lifetime_type
            == "time_specific"
        ):

            if (
                published_content.category
                == "events"
            ):

                expiry_date = (
                    published_content
                    .event_end_date
                    or
                    published_content
                    .event_date
                )

            else:

                expiry_date = (
                    published_content
                    .end_date
                )

            if expiry_date:

                days_remaining = (
                    expiry_date
                    - date.today()
                ).days

                listing_expired = (
                    expiry_date
                    < date.today()
                )

        # -------------------------------------------------
        # UNTIL-UNAVAILABLE / ONGOING
        #
        # These intentionally have no automatic expiry date.
        # -------------------------------------------------

        else:

            expiry_date = None
            days_remaining = None
            listing_expired = False

    # -----------------------------------------------------
    # TEMPLATE
    # -----------------------------------------------------

    return render_template(
        "submission_dashboard.html",

        submission=
            submission,

        published_content=
            published_content,

        live_access_point=
            live_access_point,

        category_record=
            category_record,

        lifetime_type=
            lifetime_type,

        expiry_date=
            expiry_date,

        days_remaining=
            days_remaining,

        listing_expired=
            listing_expired,

        listing_closed=
            listing_closed,
    )

#TESTING ONLY



# =========================================================
# ADMIN - TEST PUSH NOTIFICATION
# =========================================================

@app.route(
    "/admin/push/test/<int:subscriber_id>",
    methods=["POST"],
)
def admin_test_push(
    subscriber_id,
):

    # -----------------------------------------------------
    # ADMIN LOGIN REQUIRED
    # -----------------------------------------------------

    if not session.get(
        "lac_admin"
    ):

        return jsonify({

            "success":
                False,

            "error":
                "Admin login required.",

        }), 401


    # -----------------------------------------------------
    # FIND SUBSCRIBER
    # -----------------------------------------------------

    subscriber = db.session.get(
        PushSubscriber,
        subscriber_id,
    )


    if not subscriber:

        return jsonify({

            "success":
                False,

            "error":
                "Subscriber not found.",

        }), 404


    # -----------------------------------------------------
    # SEND TEST
    # -----------------------------------------------------

    success = send_push_notification(

        subscriber=
            subscriber,

        title=
            "LaC Notifications Are Live 🔔",

        body=
            "Your LaC local notification system is working.",

        url=
            "/app",

        tag=
            "lac-test",

    )


    if not success:

        return jsonify({

            "success":
                False,

            "error":
                "Push failed. Check Render logs.",

        }), 500


    return jsonify({

        "success":
            True,

        "message":
            "Test notification sent.",

        "subscriber_id":
            subscriber.id,

        "zone_id":
            subscriber.zone_id,

    }), 200
# =========================================================
# HEALTH CHECK
# =========================================================

@app.route(
    "/health"
)
def health():

    return {
        "status": "ok"
    }, 200


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=True,
    )
