import os
import re
import uuid
from datetime import date, datetime
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
    current_app,
    flash,
    jsonify,
    redirect,
    session,
    render_template,
    request,
    url_for,
)
from flask_migrate import Migrate
from sqlalchemy import or_

from models import (
    db,
    Zone,
    AccessPoint,
    ContentItem,
    QRScan,
    PendingSubmission,
    PendingSubmissionImage,
    Category,
    PushSubscriber,
    EngagementEvent,
    PushSubscriberPreferences,
)
import json
from admin import admin_bp


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


def upload_lac_image(
    image_file,
    folder="lac/submissions",
):

    if (
        not image_file
        or not image_file.filename
    ):
        return None

    if not allowed_image_file(
        image_file.filename
    ):
        raise ValueError(
            "Only PNG, JPG, JPEG and WEBP images are allowed."
        )

    result = cloudinary.uploader.upload(
        image_file,
        folder=folder,
        resource_type="image",
    )

    return result[
        "secure_url"
    ]
    

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

    return render_template(
        "qr_entry.html"
    )

@app.route("/app")
def pwa_app():
    return render_template(
        "pwa_launcher.html"
    )

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


# =========================================================
# ACTIVE CONTENT HELPER
# =========================================================

def get_active_content(
    zone_id,
    category_slug,
):

    today = date.today()

    query = (
        ContentItem.query
        .filter(
            ContentItem.zone_id
            == zone_id,

            ContentItem.category
            == category_slug,

            ContentItem.active.is_(
                True
            ),

            ContentItem.archived.is_(
                False
            ),
        )
    )

    # -----------------------------------------------------
    # EVENTS
    # -----------------------------------------------------

    if category_slug == "events":

        query = query.filter(
            or_(
                ContentItem.publish_from.is_(None),
                ContentItem.publish_from <= today,
            ),
            or_(
                ContentItem.event_end_date.is_(None),
                ContentItem.event_end_date >= today,
            ),
        )

        return (
            query
            .order_by(
                ContentItem.featured.desc(),
                ContentItem.event_date.asc(),
                ContentItem.created_at.desc(),
            )
            .all()
        )

    # -----------------------------------------------------
    # OTHER CONTENT
    # Show future listings immediately.
    # Hide only after expiry.
    # -----------------------------------------------------

    query = query.filter(
        or_(
            ContentItem.end_date.is_(None),
            ContentItem.end_date >= today,
        )
    )

    return (
        query
        .order_by(
            ContentItem.featured.desc(),
            ContentItem.start_date.asc(),
            ContentItem.created_at.desc(),
        )
        .all()
    )


# =========================================================
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

        # Do not prevent the public QR page from loading
        # just because analytics recording failed.

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

    categories = (
        get_active_categories()
    )


    return render_template(
        "access.html",

        zone=zone,

        access_point=access_point,

        categories=categories,
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

    try:

        data = request.get_json(
            silent=True
        ) or {}


        zone_id = data.get(
            "zone_id"
        )

        subscription = data.get(
            "subscription"
        ) or {}

        endpoint = subscription.get(
            "endpoint"
        )

        keys = subscription.get(
            "keys"
        ) or {}

        p256dh = keys.get(
            "p256dh"
        )

        auth_key = keys.get(
            "auth"
        )


        # ---------------------------------------------
        # VALIDATION
        # ---------------------------------------------

        if not zone_id:

            return jsonify({
                "success": False,
                "error": "zone_id is required.",
            }), 400


        if not endpoint:

            return jsonify({
                "success": False,
                "error": "Push endpoint is missing.",
            }), 400


        if not p256dh or not auth_key:

            return jsonify({
                "success": False,
                "error": "Push subscription keys are missing.",
            }), 400


        # ---------------------------------------------
        # ZONE
        # ---------------------------------------------

        zone = db.session.get(
            Zone,
            int(zone_id),
        )


        if not zone:

            return jsonify({
                "success": False,
                "error": "Zone not found.",
            }), 404


        # ---------------------------------------------
        # EXISTING SUBSCRIBER
        # ---------------------------------------------

        subscriber = (
            PushSubscriber.query
            .filter_by(
                endpoint=endpoint
            )
            .first()
        )


        if subscriber:

            subscriber.zone_id = (
                zone.id
            )

            subscriber.p256dh = (
                p256dh
            )

            subscriber.auth_key = (
                auth_key
            )

            subscriber.active = True


        else:

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


        db.session.commit()


        app.logger.info(
            "[LaC Push] Subscriber saved. "
            "subscriber_id=%s zone_id=%s",
            subscriber.id,
            subscriber.zone_id,
        )


        return jsonify({

            "success":
                True,

            "subscriber_id":
                subscriber.id,

            "zone_id":
                subscriber.zone_id,

            "zone":
                zone.name,

        }), 200


    except Exception as exc:

        db.session.rollback()


        app.logger.exception(
            "[LaC Push] Subscription failed: %s",
            exc,
        )


        return jsonify({

            "success":
                False,

            "error":
                str(exc),

        }), 500
# =========================================================
# PUBLIC LISTING DETAIL
# =========================================================

@app.route(
    "/listing/<int:item_id>"
)
def listing_detail(item_id):

    today = date.today()

    item = (
        ContentItem.query
        .filter(
            ContentItem.id == item_id,
            ContentItem.active.is_(True),
            ContentItem.archived.is_(False),
        )
        .first_or_404()
    )

    # -----------------------------------------------------
    # EXPIRY
    # -----------------------------------------------------

    if content_is_expired(
        item,
        today,
    ):
        abort(404)

    # -----------------------------------------------------
    # CATEGORY
    # -----------------------------------------------------

    category = (
        Category.query
        .filter_by(
            slug=item.category,
            active=True,
        )
        .first_or_404()
    )

    # -----------------------------------------------------
    # RECORD LISTING VIEW
    # -----------------------------------------------------

    item.view_count = (
        item.view_count or 0
    ) + 1

    db.session.commit()

    # -----------------------------------------------------
    # TEMPLATE
    # -----------------------------------------------------

    return render_template(
        "listing_detail.html",
        item=item,
        category=category,
        zone=item.zone,
        today=today,
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

        # -------------------------------------------------
        # NEW:
        # Type of content INSIDE the selected category.
        #
        # Example:
        #
        # category = property
        # content_type = room
        #
        # category = property
        # content_type = hotel_lodge
        # -------------------------------------------------

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

        # =================================================
        # REQUIRED FIELD VALIDATION
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
        # VALIDATE ZONE
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
                "Please select a valid zone.",
                "error",
            )

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

        # =================================================
        # VALIDATE CATEGORY
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
        # DETERMINE CONTENT WORKFLOW
        # =================================================

        workflow = (
            get_content_workflow(
                category_slug,
                content_type,
            )
        )

        lifetime_type = (
            workflow.get(
                "lifetime_type"
            )
        )

        notification_eligible = bool(
            workflow.get(
                "notification_eligible",
                False,
            )
        )

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

        # Every new listing begins as available.
        availability_status = (
            "available"
        )

        # =================================================
        # DATE HELPER
        # =================================================

        def parse_form_date(
            field_name,
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
        # LIFETIME-SPECIFIC DATE VALIDATION
        # =================================================

        if lifetime_type == "time_specific":

            # ---------------------------------------------
            # EVENTS
            # ---------------------------------------------

            if category_slug == "events":

                if not event_date:

                    flash(
                        "Event Date is required for events.",
                        "error",
                    )

                    return render_template(
                        "submit.html",
                        zones=zones,
                        categories=categories,
                    )

                if (
                    publish_from
                    and publish_from
                    > event_date
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
                    and event_end_date
                    < event_date
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

                # Events use event-specific dates.
                start_date = None
                end_date = None

            # ---------------------------------------------
            # NON-EVENT TIME-SPECIFIC CONTENT
            # ---------------------------------------------

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
                    and end_date
                    and end_date < start_date
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

                # Non-event content does not use
                # event-specific fields.
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

            # Examples:
            # Room → until taken
            # Property → until sold
            # Job → until filled

            publish_from = None
            event_date = None
            event_end_date = None

            # No artificial expiry date.
            start_date = None
            end_date = None

        # =================================================
        # ONGOING
        # =================================================

        elif lifetime_type == "ongoing":

            # Examples:
            # Hotel
            # Restaurant
            # Salon
            # Mechanic
            # Plumber

            publish_from = None
            event_date = None
            event_end_date = None
            start_date = None
            end_date = None

        # =================================================
        # RECURRING
        # =================================================

        elif lifetime_type == "recurring":

            # Recurring schedule fields will be added later.
            #
            # For now start/end dates may optionally describe
            # the overall period during which the recurring
            # listing is valid.

            publish_from = None
            event_date = None
            event_end_date = None

            if (
                start_date
                and end_date
                and end_date < start_date
            ):

                flash(
                    "End date cannot be before start date.",
                    "error",
                )

                return render_template(
                    "submit.html",
                    zones=zones,
                    categories=categories,
                )

        # =================================================
        # IMAGES
        # =================================================

        uploaded_images = (
            request.files.getlist(
                "images"
            )
        )

        uploaded_images = [
            image
            for image in uploaded_images
            if (
                image
                and image.filename
            )
        ]

        if len(
            uploaded_images
        ) > 3:

            flash(
                "You can upload a maximum of 3 images.",
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

            zone_id=
                zone.id,

            category=
                category_slug,

            # NEW
            content_type=
                content_type,

            lifetime_type=
                lifetime_type,

            availability_status=
                availability_status,

            notification_eligible=
                notification_eligible,

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

            submitter_name=
                submitter_name,

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
        # SAVE SUBMISSION + CLOUDINARY IMAGES
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

            print(
                "Submission error:",
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
    # GET REQUEST
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

    return render_template(
        "submission_success.html",
        submission=submission,
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
