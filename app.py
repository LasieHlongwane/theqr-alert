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
)
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
from admin import admin_bp
from pricing import (
    calculate_kalxa_price,
    KalxaPricingError,
    PRICING_MODEL_PRESENCE,
    PRICING_MODEL_CAMPAIGN,
    get_pricing_model,
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
    # NORMALIZE CATEGORY SLUG
    # =====================================================

    category_slug = (
        str(
            category_slug
            or ""
        )
        .strip()
        .lower()
    )


    # =====================================================
    # PROMOTION PRIORITY
    #
    # Only Promotion + Featured receives ranking priority.
    # =====================================================

    promotion_priority = db.case(

        (
            (
                ContentItem.listing_level
                == "promotion"
            )
            &
            (
                ContentItem.featured.is_(
                    True
                )
            ),
            1,
        ),

        else_=0,

    )


    # =====================================================
    # ZONE VISIBILITY
    #
    # Content appears when:
    #
    # 1. The requested zone is its home/origin zone
    #
    # OR
    #
    # 2. It is a Campaign distributed into that zone.
    #
    # .any() uses EXISTS-style logic, so one campaign
    # remains one ContentItem even when distributed to
    # several zones.
    # =====================================================

    zone_visibility = db.or_(

        # -------------------------------------------------
        # HOME / ORIGIN ZONE
        # -------------------------------------------------

        ContentItem.zone_id
        == zone_id,


        # -------------------------------------------------
        # PURCHASED CAMPAIGN DISTRIBUTION
        # -------------------------------------------------

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
    # NON-COMMERCIAL:
    #
    # pricing_model = NULL
    #
    # Existing/community/legacy Kalxa content continues
    # using the normal content lifecycle.
    #
    #
    # COMMERCIAL:
    #
    # Must be:
    #
    # paid OR waived
    #
    # AND
    #
    # inside its purchased commercial period.
    # =====================================================

    commercial_visibility = db.or_(

        # -------------------------------------------------
        # LEGACY / COMMUNITY CONTENT
        # -------------------------------------------------

        ContentItem.pricing_model.is_(
            None
        ),


        # -------------------------------------------------
        # VALID COMMERCIAL CONTENT
        # -------------------------------------------------

        db.and_(

            ContentItem.pricing_model.in_(

                (
                    PRICING_MODEL_PRESENCE,
                    PRICING_MODEL_CAMPAIGN,
                )

            ),

            ContentItem.payment_status.in_(

                (
                    "paid",
                    "waived",
                )

            ),

            ContentItem.commercial_starts_at.is_not(
                None
            ),

            ContentItem.commercial_expires_at.is_not(
                None
            ),

            ContentItem.commercial_starts_at
            <= now,

            ContentItem.commercial_expires_at
            >= now,

        ),

    )


    # =====================================================
    # BASE QUERY
    # =====================================================

    query = (
        ContentItem.query
        .filter(

            ContentItem.active.is_(
                True
            ),

            ContentItem.category
            == category_slug,

            zone_visibility,

            commercial_visibility,

        )
    )


    # =====================================================
    # EVENTS
    #
    # Upcoming events must appear BEFORE the event date.
    #
    # Therefore we intentionally do NOT require:
    #
    #     start_date <= today
    #
    # An event remains discoverable until its end_date
    # passes.
    # =====================================================

    if category_slug == "events":

        query = (
            query
            .filter(

                db.or_(

                    ContentItem.end_date.is_(
                        None
                    ),

                    ContentItem.end_date
                    >= today,

                )

            )
        )


        return (
            query
            .order_by(

                promotion_priority.desc(),

                ContentItem.event_date
                .asc()
                .nullslast(),

                ContentItem.created_at
                .desc(),

            )
            .all()
        )


    # =====================================================
    # NON-EVENT CONTENT
    #
    # Normal content lifecycle remains:
    #
    # start_date = NULL or already started
    # end_date   = NULL or not expired
    # =====================================================

    query = (
        query
        .filter(

            db.or_(

                ContentItem.start_date.is_(
                    None
                ),

                ContentItem.start_date
                <= today,

            ),

            db.or_(

                ContentItem.end_date.is_(
                    None
                ),

                ContentItem.end_date
                >= today,

            ),

        )
    )


    # =====================================================
    # NON-EVENT ORDERING
    # =====================================================

    return (
        query
        .order_by(

            promotion_priority.desc(),

            ContentItem.start_date
            .asc()
            .nullslast(),

            ContentItem.created_at
            .desc(),

        )
        .all()
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

    # -----------------------------------------------------
    # FIND ACCESS POINT
    # -----------------------------------------------------

    access_point = (
        AccessPoint.query
        .filter_by(
            code=access_code,
            active=True,
        )
        .first_or_404()
    )

    zone = access_point.zone


    # -----------------------------------------------------
    # RECORD PHYSICAL QR SCAN
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # CATEGORY-SPECIFIC QR
    # -----------------------------------------------------

    if access_point.default_category:

        category_slug = (
            access_point.default_category
            .strip()
            .lower()
        )

        category_record = (
            get_active_category_by_slug(
                category_slug
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


    # -----------------------------------------------------
    # GENERAL QR
    # -----------------------------------------------------

    categories = get_active_categories()

    today = date.today()


    # =====================================================
    # ZONE-SPECIFIC CATEGORY APPEARANCES
    # =====================================================
    #
    # Load all custom category appearances belonging
    # to the current zone.
    #
    # Example:
    #
    # KwaMhlanga + Events
    # KwaMhlanga + Property
    #
    # =====================================================

    zone_category_appearances = (
        ZoneCategoryAppearance.query
        .filter_by(
            zone_id=zone.id,
        )
        .all()
    )


    # -----------------------------------------------------
    # APPEARANCE LOOKUP
    #
    # Key:
    #     category_id
    #
    # Value:
    #     ZoneCategoryAppearance
    # -----------------------------------------------------

    zone_category_appearance_lookup = {

        appearance.category_id:
            appearance

        for appearance
        in zone_category_appearances
    }


    # =====================================================
    # RESOLVE CATEGORY BACKGROUND IMAGES
    # =====================================================
    #
    # Priority per image position:
    #
    # 1. Zone-specific image
    # 2. Default Category image
    #
    # This means a zone can replace only one image and
    # continue using the global defaults for the others.
    #
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


        # -------------------------------------------------
        # REMOVE EMPTY IMAGE POSITIONS
        # -------------------------------------------------

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
    # CATEGORY STATS
    # =====================================================

    category_stats = {}


    # -----------------------------------------------------
    # NEW LISTING CUTOFF
    #
    # A listing is considered NEW when created during
    # the last 7 days.
    # -----------------------------------------------------

    new_cutoff = (
        datetime.utcnow()
        - timedelta(days=7)
    )


    for category in categories:

        try:

            active_items = get_active_content(
                zone_id=zone.id,
                category_slug=category.slug,
            )

        except Exception as exc:

            app.logger.exception(
                "Unable to calculate category stats. "
                "zone=%s category=%s error=%s",
                zone.id,
                category.slug,
                exc,
            )

            active_items = []


        # -------------------------------------------------
        # TOTAL ACTIVE ITEMS
        # -------------------------------------------------

        item_count = len(
            active_items
        )


        # -------------------------------------------------
        # NEW ITEMS
        # -------------------------------------------------

        new_count = 0


        for item in active_items:

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
        # BADGE WORDING
        # -------------------------------------------------

        if category.slug == "events":

            badge_icon = "📅"
            badge_label = "UPCOMING"


        elif category.slug == "property":

            badge_icon = "🏠"
            badge_label = "AVAILABLE"


        else:

            badge_icon = "🔥"
            badge_label = "LIVE"


        category_stats[
            category.slug
        ] = {

            "count":
                item_count,

            "new_count":
                new_count,

            "label":
                badge_label,

            "icon":
                badge_icon,
        }


    # =====================================================
    # NEW NEAR YOU
    # =====================================================
    #
    # Gather active listings from all categories.
    #
    # We use the same get_active_content() helper so
    # expired / archived / inactive content is excluded.
    #
    # =====================================================

    new_items_pool = []


    for category in categories:

        try:

            category_items = (
                get_active_content(
                    zone_id=zone.id,
                    category_slug=category.slug,
                )
            )

        except Exception as exc:

            app.logger.exception(
                "Unable to load New Near You items. "
                "zone=%s category=%s error=%s",
                zone.id,
                category.slug,
                exc,
            )

            continue


        for item in category_items:

            new_items_pool.append(
                item
            )


    # -----------------------------------------------------
    # SORT NEWEST FIRST
    # -----------------------------------------------------

    new_items_pool.sort(

        key=lambda item: (
            item.created_at
            or datetime.min
        ),

        reverse=True,
    )


    # -----------------------------------------------------
    # LIMIT HOME RAIL
    # -----------------------------------------------------

    new_items = (
        new_items_pool[:8]
    )


    # =====================================================
    # CATEGORY LOOKUP
    # =====================================================
    #
    # Allows access.html to display the friendly category
    # name/icon from a listing's category slug.
    #
    # =====================================================

    category_lookup = {

        category.slug:
            category

        for category
        in categories
    }


    # =====================================================
    # FEATURED LOCAL CONTENT
    # =====================================================

    featured_items_pool = []


    for category in categories:

        try:

            category_items = (
                get_active_content(
                    zone_id=zone.id,
                    category_slug=category.slug,
                )
            )

        except Exception as exc:

            app.logger.exception(
                "Unable to load featured items. "
                "zone=%s category=%s error=%s",
                zone.id,
                category.slug,
                exc,
            )

            continue


        for item in category_items:

            if item.featured:

                featured_items_pool.append(
                    item
                )


    # -----------------------------------------------------
    # NEWEST FEATURED CONTENT FIRST
    # -----------------------------------------------------

    featured_items_pool.sort(

        key=lambda item: (
            item.created_at
            or datetime.min
        ),

        reverse=True,
    )


    # -----------------------------------------------------
    # LIMIT FEATURED CAROUSEL
    # -----------------------------------------------------

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

        categories=categories,

        category_stats=category_stats,

        new_items=new_items,

        category_lookup=category_lookup,

        featured_items=featured_items,

        # -----------------------------------------------
        # ZONE-SPECIFIC CATEGORY BACKGROUND IMAGES
        # -----------------------------------------------

        category_background_images=(
            category_background_images
        ),
    )
# =========================================================
# CATEGORY PAGE
# =========================================================
# =========================================================
# PUSH NOTIFICATION SUBSCRIBE
# =========================================================

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
    # NORMALIZE CATEGORY SLUG
    # =====================================================

    category_slug = (
        category
        .strip()
        .lower()
    )


    # =====================================================
    # VALIDATE CATEGORY
    # =====================================================

    category_record = (
        get_active_category_by_slug(
            category_slug
        )
    )


    if not category_record:

        abort(404)


    # =====================================================
    # GET ACTIVE CONTENT
    # =====================================================

    items = get_active_content(
        zone_id=access_point.zone_id,
        category_slug=category_slug,
    )


    


    # =====================================================
    # RECORD CATEGORY VIEW
    # =====================================================
    #
    # Analytics must NEVER prevent residents from
    # accessing local content.
    #
    # If analytics recording fails, roll back the
    # transaction and continue rendering the page.
    #
    # =====================================================

    try:

        category_event = QRScan(
            access_point_id=access_point.id,

            event_type="category_view",

            category_selected=category_slug,

            user_agent=request.headers.get(
                "User-Agent",
                "",
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
            "access_point=%s category=%s error=%s",
            access_point.code,
            category_slug,
            exc,
        )


    # =====================================================
    # DISPLAY CATEGORY PAGE
    # =====================================================

    return render_template(
        "category.html",

        zone=zone,

        category=category_record,

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

    category_slug = (
        category_slug
        or ""
    ).strip().lower()

    content_type = (
        content_type
        or ""
    ).strip().lower()

    category_workflows = (
        CONTENT_WORKFLOWS.get(
            category_slug,
            {},
        )
    )

    workflow = (
        category_workflows.get(
            content_type
        )
    )

    # -----------------------------------------------------
    # Backward-compatible fallback.
    #
    # Unknown content types are allowed for now because
    # categories are still dynamic in the database.
    # -----------------------------------------------------

    if workflow:
        return workflow

    # Existing Events behaviour should remain safe.
    if category_slug == "events":

        return {
            "lifetime_type":
                "time_specific",
            "notification_eligible":
                True,
        }

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

    categories = (
        get_active_categories()
    )

    # =====================================================
    # POST
    # =====================================================

    if request.method == "POST":

        # =================================================
        # BASIC FORM DATA
        # =================================================

        zone_id = request.form.get(
            "zone_id",
            type=int,
        )

        category_slug = (
            request.form.get(
                "category",
                "",
            )
            .strip()
            .lower()
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

        price = (
            request.form.get(
                "price",
                "",
            )
            .strip()
            or None
        )

        contact = (
            request.form.get(
                "contact",
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
            or not category_slug
            or not title
            or not submitter_name
        ):

            flash(
                "Please complete all required fields.",
                "error",
            )

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

        # =================================================
        # VALIDATE HOME ZONE
        # =================================================

        zone = db.session.get(
            Zone,
            zone_id,
        )

        if (
            not zone
            or not zone.active
        ):

            flash(
                "Please select a valid area.",
                "error",
            )

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

        # =================================================
        # VALIDATE REAL PUBLIC CATEGORY
        # =================================================

        category_record = (
            get_active_category_by_slug(
                category_slug
            )
        )

        if not category_record:

            flash(
                "Please select a valid category.",
                "error",
            )

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

        # =================================================
        # NORMALIZE CATEGORY FOR INTERNAL WORKFLOW/PRICING
        #
        # IMPORTANT:
        #
        # category_slug remains the REAL public category.
        #
        # Example:
        #
        # Public/database:
        # upcoming-event-🥹🔥
        #
        # Internal workflow:
        # events
        # =================================================

        category_aliases = {

            # EVENTS
            "event":
                "events",

            "local-events":
                "events",

            "local_events":
                "events",

            "upcoming-event-🥹🔥":
                "events",

            # FOOD / RESTAURANTS
            "restaurant":
                "local-restaurants",

            "restaurants":
                "local-restaurants",

            "foods":
                "local-restaurants",

            "check-out-our-specials":
                "local-restaurants",

            # BEAUTY
            "beauty":
                "beauty-salon",

            "salon":
                "beauty-salon",

            # OPPORTUNITIES
            "opportunity":
                "opportunities",
        }

        workflow_category = (
            category_aliases.get(
                category_slug,
                category_slug,
            )
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
        #
        # Every newly approved public submission is allowed
        # to trigger the notification engine.
        #
        # This DOES NOT notify everybody.
        #
        # send_zone_push_notification() still requires:
        #
        #   active subscriber
        #       +
        #   matching zone
        #       +
        #   matching REAL public category
        #
        # Therefore:
        #
        # Restaurant subscriber -> restaurant notifications
        # Event subscriber      -> event notifications
        # Salon subscriber      -> salon notifications
        #
        # Commercial content is additionally blocked from
        # push until it is actually paid/waived and live.
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

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

        availability_status = (
            "available"
        )

        # =================================================
        # DETERMINE COMMERCIAL PRICING MODEL
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

                return render_template(
                    "submit.html",
                    zones=zones,
                    categories=categories,
                )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

                # -----------------------------------------
                # AT LEAST ONE ZONE
                # -----------------------------------------

                if not distribution_zone_ids:

                    flash(
                        "Please select at least one "
                        "campaign area.",
                        "error",
                    )

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

                zone_count = len(
                    distribution_zone_ids
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

                return render_template(
                    "submit.html",
                    zones=zones,
                    categories=categories,
                )

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

                return render_template(
                    "submit.html",
                    zones=zones,
                    categories=categories,
                )

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

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

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

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

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

                return render_template(
                    "submit.html",
                    zones=zones,
                    categories=categories,
                )

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

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

        # =================================================
        # CREATE PENDING SUBMISSION
        # =================================================

        submission = PendingSubmission(

            # ---------------------------------------------
            # LOCATION
            # ---------------------------------------------

            zone_id=
                zone.id,

            # ---------------------------------------------
            # REAL PUBLIC CATEGORY
            # ---------------------------------------------

            category=
                category_slug,

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

            start_date=
                start_date,

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

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

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

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

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

    return render_template(
        "submit.html",
        zones=zones,
        categories=categories,
    )
           

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
