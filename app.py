import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time as time_module
import uuid
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from html import unescape
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from urllib.parse import (
    quote,
    urljoin,
)
import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_migrate import Migrate
from itsdangerous import URLSafeTimedSerializer

from admin import admin_bp, get_category_by_slug
from categories import (
    BUSINESS_CATEGORIES,
    get_category_aliases,
    get_consumer_category,
    normalize_category,
)
from image_utils import allowed_image_file, upload_lac_image
from models import (
    AccessPoint,
    Category,
    ContentDistributionZone,
    ContentItem,
    ContentReminder,
    EngagementEvent,
    ListingClaim,
    Organizer,
    PendingSubmission,
    PendingSubmissionImage,
    PushSubscriber,
    PushSubscriberPreference,
    QRScan,
    Zone,
    ZoneCategoryAppearance,
    db,
)
from pricing import (
    KalxaPricingError,
    PRICING_MODEL_CAMPAIGN,
    PRICING_MODEL_PRESENCE,
    calculate_kalxa_price,
    get_pricing_model,
)
from push_service import send_push_notification


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

secret_key = os.environ.get("SECRET_KEY", "").strip()
if not secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required.")

app.secret_key = secret_key

database_url = os.environ.get("DATABASE_URL", "").strip()
if not database_url:
    raise RuntimeError("DATABASE_URL environment variable is required.")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url

db.init_app(app)
migrate = Migrate(app, db)
app.register_blueprint(admin_bp)


# ============================================================
# GLOBAL CONFIGURATION
# ============================================================

KALXA_TIMEZONE = ZoneInfo("Africa/Johannesburg")
UTC_TIMEZONE = ZoneInfo("UTC")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get(
    "VAPID_SUBJECT",
    "https://lac-acess-delivered.onrender.com/",
)

YOCO_WEBHOOK_SECRET = os.environ.get("YOCO_WEBHOOK_SECRET", "").strip()
YOCO_CHECKOUT_URL = "https://payments.yoco.com/api/checkouts"

ENGAGEMENT_EVENT_TYPES = {
    "category_view",
    "listing_view",
    "whatsapp_click",
    "call_click",
    "share_click",
    "directions_click",
    "job_apply_click",
}

VALID_LIFETIME_TYPES = {
    "time_specific",
    "until_unavailable",
    "ongoing",
    "recurring",
}

KALXA_REMINDER_MAX_RETRIES = 3
KALXA_REMINDER_RETRY_DELAYS = {
    1: 5,
    2: 15,
    3: 60,
}

KALXA_JOB_TYPES = {
    "job": "Job",
    "internship": "Internship",
    "learnership": "Learnership",
    "training": "Training Opportunity",
    "tender": "Tender",
    "business_opportunity": "Business Opportunity",
}


# ============================================================
# SERVICE WORKER
# ============================================================

@app.route("/service-worker.js")
def service_worker():
    response = send_from_directory(
        app.static_folder,
        "service-worker.js",
        mimetype="application/javascript",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


# ============================================================
# PHONE / WHATSAPP HELPERS
# ============================================================

def normalize_phone_number(value):
    if not value:
        return ""

    digits = re.sub(r"\D", "", str(value))

    if digits.startswith("0"):
        digits = "27" + digits[1:]

    return digits


@app.template_filter("whatsapp_link")
def whatsapp_link(value):
    number = normalize_phone_number(value)
    if not number:
        return "#"

    message = quote(
        "Hi, I saw your listing on Kalxa and I would like more information."
    )
    return f"https://wa.me/{number}?text={message}"


@app.template_filter("phone_link")
def phone_link(value):
    number = normalize_phone_number(value)
    if not number:
        return "#"
    return f"tel:+{number}"


# ============================================================
# GENERIC HELPERS
# ============================================================

def parse_optional_time(value):
    value = str(value or "").strip()
    if not value:
        return None

    for time_format in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, time_format).time()
        except ValueError:
            continue

    raise ValueError("Invalid time value.")


def parse_form_date(field_name):
    value = (request.form.get(field_name, "") or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def get_public_base_url():
    return os.environ.get(
        "PUBLIC_BASE_URL",
        "https://lac-local-access.onrender.com",
    ).rstrip("/")


# ============================================================
# KALXA -> TICKETING SECURE BRIDGE
# ============================================================

def get_ticketing_bridge_serializer():
    bridge_secret = os.environ.get("KALXA_TICKETING_BRIDGE_SECRET", "").strip()
    if not bridge_secret:
        raise RuntimeError("KALXA_TICKETING_BRIDGE_SECRET is not configured.")

    return URLSafeTimedSerializer(
        secret_key=bridge_secret,
        salt="kalxa-ticketing-bridge-v1",
    )


# ============================================================
# HOME
# ============================================================

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
            .filter_by(zone_id=zone.id, active=True)
            .order_by(AccessPoint.id.asc())
            .first()
        )

        if access_point:
            zone_access_points.append(
                {
                    "zone": zone,
                    "access_point": access_point,
                }
            )

    return render_template(
        "qr_entry.html",
        zone_access_points=zone_access_points,
    )


@app.route("/app")
def pwa_app():
    return render_template("pwa_launcher.html")


# ============================================================
# SPONSORSHIP STATE
# ============================================================

def is_sponsorship_active(item, now_utc=None):
    if not item:
        return False

    if not getattr(item, "is_sponsored", False):
        return False

    sponsorship_status = str(
        getattr(item, "sponsorship_status", "inactive") or "inactive"
    ).strip().lower()

    if sponsorship_status != "active":
        return False

    now_utc = now_utc or datetime.utcnow()

    sponsored_starts_at = getattr(item, "sponsored_starts_at", None)
    sponsored_expires_at = getattr(item, "sponsored_expires_at", None)

    if sponsored_starts_at and sponsored_starts_at > now_utc:
        return False

    if sponsored_expires_at and sponsored_expires_at <= now_utc:
        return False

    return True


def attach_sponsorship_state(item, now_utc=None):
    if not item:
        return item

    sponsorship_active = is_sponsorship_active(item, now_utc=now_utc)
    item.sponsorship_active = sponsorship_active

    if sponsorship_active:
        try:
            item.effective_sponsored_priority = max(
                0,
                int(getattr(item, "sponsored_priority", 0) or 0),
            )
        except (TypeError, ValueError):
            item.effective_sponsored_priority = 0
    else:
        item.effective_sponsored_priority = 0

    return item


def attach_sponsorship_states(items):
    if not items:
        return []

    now_utc = datetime.utcnow()
    for item in items:
        attach_sponsorship_state(item, now_utc=now_utc)

    return items


# ============================================================
# CAMPAIGN STATE
# ============================================================

def get_campaign_state(item, now=None):
    if now is None:
        now = datetime.now(KALXA_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KALXA_TIMEZONE)
    else:
        now = now.astimezone(KALXA_TIMEZONE)

    today = now.date()
    canonical_category = normalize_category(item.category)
    is_event = canonical_category == "events"

    start_time_value = getattr(item, "start_time", None)
    end_time_value = getattr(item, "end_time", None)

    if is_event:
        start_date_value = (
            getattr(item, "event_date", None)
            or getattr(item, "start_date", None)
        )
        end_date_value = (
            getattr(item, "event_end_date", None)
            or start_date_value
        )
    else:
        start_date_value = getattr(item, "start_date", None)
        end_date_value = getattr(item, "end_date", None)

    if not start_date_value and not end_date_value:
        return {
            "state": "undated",
            "label": None,
            "css_class": "campaign-undated",
            "days_to_go": None,
            "is_live": False,
            "is_ended": False,
            "starts_at": None,
            "ends_at": None,
        }

    start_datetime = None
    if start_date_value:
        start_datetime = datetime.combine(
            start_date_value,
            start_time_value or dt_time.min,
        ).replace(tzinfo=KALXA_TIMEZONE)

    end_datetime = None
    if end_date_value:
        end_datetime = datetime.combine(
            end_date_value,
            end_time_value or dt_time.max,
        ).replace(tzinfo=KALXA_TIMEZONE)

    if start_datetime and end_datetime and end_datetime < start_datetime:
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

    if end_datetime and now > end_datetime:
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

    days_to_go = None
    if start_date_value:
        days_to_go = (start_date_value - today).days

    if days_to_go is not None and days_to_go > 1:
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

    if days_to_go == 1:
        return {
            "state": "tomorrow",
            "label": "🔥 TOMORROW",
            "css_class": "campaign-tomorrow",
            "days_to_go": 1,
            "is_live": False,
            "is_ended": False,
            "starts_at": start_datetime,
            "ends_at": end_datetime,
        }

    if is_event:
        if (
            days_to_go == 0
            and start_datetime
            and now < start_datetime
        ):
            if start_time_value and start_time_value >= dt_time(17, 0):
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

        if days_to_go == 0 and not start_time_value:
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

        if (
            start_datetime
            and end_datetime
            and start_time_value
            and start_datetime <= now <= end_datetime
        ):
            remaining_seconds = (end_datetime - now).total_seconds()

            if 0 <= remaining_seconds <= 3 * 60 * 60:
                return {
                    "state": "ending_soon",
                    "label": "⏳ ENDING SOON",
                    "css_class": "campaign-ending-soon",
                    "days_to_go": 0,
                    "is_live": True,
                    "is_ended": False,
                    "starts_at": start_datetime,
                    "ends_at": end_datetime,
                }

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

        if (
            start_date_value
            and end_date_value
            and today > end_date_value
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

        if start_date_value and today == start_date_value:
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

    else:
        if (
            days_to_go == 0
            and start_datetime
            and start_time_value
            and now < start_datetime
        ):
            return {
                "state": "today",
                "label": "STARTS TODAY",
                "css_class": "campaign-today",
                "days_to_go": 0,
                "is_live": False,
                "is_ended": False,
                "starts_at": start_datetime,
                "ends_at": end_datetime,
            }

        campaign_has_started = start_datetime is None or now >= start_datetime
        campaign_has_not_ended = end_datetime is None or now <= end_datetime

        if campaign_has_started and campaign_has_not_ended:
            if end_datetime and end_time_value:
                remaining_seconds = (end_datetime - now).total_seconds()
                if 0 <= remaining_seconds <= 3 * 60 * 60:
                    return {
                        "state": "ending_soon",
                        "label": "⏳ ENDING SOON",
                        "css_class": "campaign-ending-soon",
                        "days_to_go": 0,
                        "is_live": True,
                        "is_ended": False,
                        "starts_at": start_datetime,
                        "ends_at": end_datetime,
                    }

            if end_datetime and end_datetime.date() == today:
                return {
                    "state": "ends_today",
                    "label": "🔥 ENDS TODAY",
                    "css_class": "campaign-ending",
                    "days_to_go": 0,
                    "is_live": True,
                    "is_ended": False,
                    "starts_at": start_datetime,
                    "ends_at": end_datetime,
                }

            return {
                "state": "active",
                "label": "🔥 AVAILABLE NOW",
                "css_class": "campaign-active",
                "days_to_go": days_to_go if days_to_go is not None else 0,
                "is_live": True,
                "is_ended": False,
                "starts_at": start_datetime,
                "ends_at": end_datetime,
            }

    if end_datetime and now > end_datetime:
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

    return {
        "state": "active",
        "label": "🔥 AVAILABLE NOW",
        "css_class": "campaign-active",
        "days_to_go": days_to_go if days_to_go is not None else 0,
        "is_live": True,
        "is_ended": False,
        "starts_at": start_datetime,
        "ends_at": end_datetime,
    }


def get_campaign_state_label(item, campaign_state):
    if not campaign_state:
        return campaign_state

    state = campaign_state.get("state")
    if not state:
        return campaign_state

    canonical_category = normalize_category(item.category)
    days_to_go = campaign_state.get("days_to_go")

    label_maps = {
        "events": {
            "tomorrow": "🔥 TOMORROW",
            "today": "🔥 TODAY",
            "tonight": "🔥 TONIGHT",
            "live": "🔴 HAPPENING NOW",
            "ending_soon": "⏳ ENDING SOON",
            "ended": "ENDED",
        },
        "retail_specials": {
            "tomorrow": "🔥 STARTS TOMORROW",
            "today": "STARTS TODAY",
            "active": "🔥 AVAILABLE NOW",
            "ends_today": "🔥 ENDS TODAY",
            "ending_soon": "⏳ LAST 3 HOURS",
            "ended": "ENDED",
        },
        "restaurants": {
            "tomorrow": "🔥 STARTS TOMORROW",
            "today": "STARTS TODAY",
            "active": "🔥 AVAILABLE NOW",
            "ends_today": "🔥 ENDS TODAY",
            "ending_soon": "⏳ LAST FEW HOURS",
            "ended": "ENDED",
        },
        "beauty": {
            "tomorrow": "🔥 STARTS TOMORROW",
            "today": "STARTS TODAY",
            "active": "🔥 AVAILABLE NOW",
            "ends_today": "🔥 ENDS TODAY",
            "ending_soon": "⏳ LAST FEW HOURS",
            "ended": "ENDED",
        },
        "accommodation": {
            "tomorrow": "🔥 STARTS TOMORROW",
            "today": "STARTS TODAY",
            "active": "🔥 AVAILABLE NOW",
            "ends_today": "🔥 ENDS TODAY",
            "ending_soon": "⏳ ENDING SOON",
            "ended": "ENDED",
        },
        "jobs": {
            "tomorrow": "OPENS TOMORROW",
            "today": "OPENS TODAY",
            "active": "APPLICATIONS OPEN",
            "ends_today": "🔥 CLOSES TODAY",
            "ending_soon": "⏳ CLOSING SOON",
            "ended": "CLOSED",
        },
        "rentals": {
            "tomorrow": "AVAILABLE TOMORROW",
            "today": "AVAILABLE TODAY",
            "active": "🏠 AVAILABLE NOW",
            "ends_today": "ENDS TODAY",
            "ending_soon": "⏳ ENDING SOON",
            "ended": "NO LONGER AVAILABLE",
        },
        "building": {
            "tomorrow": "🔥 STARTS TOMORROW",
            "today": "STARTS TODAY",
            "active": "🔥 AVAILABLE NOW",
            "ends_today": "🔥 ENDS TODAY",
            "ending_soon": "⏳ LAST FEW HOURS",
            "ended": "ENDED",
        },
        "announcements": {
            "tomorrow": "STARTS TOMORROW",
            "today": "STARTS TODAY",
            "active": "📢 ACTIVE NOW",
            "ends_today": "ENDS TODAY",
            "ending_soon": "⏳ ENDING SOON",
            "ended": "ENDED",
        },
    }

    labels = label_maps.get(canonical_category)
    if labels:
        if state == "upcoming" and days_to_go is not None:
            if canonical_category == "events":
                campaign_state["label"] = f"{days_to_go} DAYS TO GO"
            elif canonical_category == "jobs":
                campaign_state["label"] = f"OPENS IN {days_to_go} DAYS"
            elif canonical_category == "rentals":
                campaign_state["label"] = f"AVAILABLE IN {days_to_go} DAYS"
            else:
                campaign_state["label"] = f"STARTS IN {days_to_go} DAYS"
        elif state in labels:
            campaign_state["label"] = labels[state]

    return campaign_state


def should_show_campaign_state(item):
    if not item:
        return False

    canonical_category = normalize_category(item.category)

    if canonical_category == "events":
        return bool(item.event_date or item.start_date)

    # Free Jobs still need deadline/closing-state badges even though
    # pricing_model is intentionally None.
    if canonical_category == "jobs":
        return bool(item.start_date or item.end_date)

    if item.pricing_model == PRICING_MODEL_CAMPAIGN:
        return bool(item.start_date or item.end_date)

    return False


def attach_campaign_state(item):
    if not item:
        return item

    if should_show_campaign_state(item):
        state = get_campaign_state(item)
        item.campaign_state = get_campaign_state_label(item, state)
    else:
        item.campaign_state = None

    return item


def attach_campaign_states(items):
    if not items:
        return []

    for item in items:
        attach_campaign_state(item)

    return items


# ============================================================
# JOBS - PUBLIC FREE SUBMISSION
# ============================================================

def build_job_application_url(application_url, application_email, job_title):
    application_url = str(application_url or "").strip()
    application_email = str(application_email or "").strip()

    if application_url:
        if not application_url.lower().startswith(("http://", "https://")):
            application_url = "https://" + application_url
        return application_url

    if application_email:
        subject = quote(f"Application: {job_title}")
        return f"mailto:{application_email}?subject={subject}"

    return None


def find_duplicate_job_submission(
    *,
    zone_id,
    title,
    business_name,
    venue,
    end_date,
):
    normalized_title = str(title or "").strip().lower()
    normalized_business = str(business_name or "").strip().lower()
    normalized_venue = str(venue or "").strip().lower()

    job_aliases = set(get_category_aliases("jobs") or [])
    job_aliases.add("jobs")

    pending_query = PendingSubmission.query.filter(
        PendingSubmission.zone_id == zone_id,
        PendingSubmission.category.in_(job_aliases),
        PendingSubmission.status == "pending",
        db.func.lower(PendingSubmission.title) == normalized_title,
    )

    if normalized_business:
        pending_query = pending_query.filter(
            db.func.lower(
                db.func.coalesce(PendingSubmission.business_name, "")
            ) == normalized_business
        )

    if normalized_venue:
        pending_query = pending_query.filter(
            db.func.lower(
                db.func.coalesce(PendingSubmission.venue, "")
            ) == normalized_venue
        )

    if end_date:
        pending_query = pending_query.filter(
            PendingSubmission.end_date == end_date
        )

    pending_duplicate = pending_query.first()
    if pending_duplicate:
        return {"type": "pending", "record": pending_duplicate}

    published_query = ContentItem.query.filter(
        ContentItem.zone_id == zone_id,
        ContentItem.category.in_(job_aliases),
        ContentItem.active.is_(True),
        ContentItem.archived.is_(False),
        db.func.lower(ContentItem.title) == normalized_title,
    )

    if normalized_business:
        published_query = published_query.filter(
            db.func.lower(db.func.coalesce(ContentItem.business_name, ""))
            == normalized_business
        )

    if normalized_venue:
        published_query = published_query.filter(
            db.func.lower(db.func.coalesce(ContentItem.venue, ""))
            == normalized_venue
        )

    if end_date:
        published_query = published_query.filter(ContentItem.end_date == end_date)

    published_duplicate = published_query.first()
    if published_duplicate:
        return {"type": "published", "record": published_duplicate}

    return None


# ============================================================
# KALXA JOBS - EXTERNAL INGESTION
# ============================================================

EXTERNAL_JOB_TYPE_MAP = {
    "job": "job",
    "full-time": "job",
    "full_time": "job",
    "part-time": "job",
    "part_time": "job",
    "permanent": "job",
    "contract": "job",

    "internship": "internship",
    "intern": "internship",

    "learnership": "learnership",

    "training": "training",
    "training opportunity": "training",

    "tender": "tender",

    "business opportunity": "business_opportunity",
    "business_opportunity": "business_opportunity",
}


# ============================================================
# KALXA JOBS - RSS / ATOM IMPORTER
# ============================================================

RSS_JOB_TYPE_KEYWORDS = {

    "learnership":
        "learnership",

    "internship":
        "internship",

    "intern":
        "internship",

    "training":
        "training",

    "tender":
        "tender",

    "business opportunity":
        "business_opportunity",

    "vacancy":
        "job",

    "job":
        "job",
}


# ============================================================
# XML TAG HELPER
# ============================================================

def rss_local_name(
    tag,
):
    """
    Remove an XML namespace from a tag.

    Example:

        {http://www.w3.org/2005/Atom}title

    becomes:

        title
    """

    return (
        str(
            tag
            or ""
        )
        .split(
            "}"
        )[-1]
        .strip()
        .lower()
    )


# ============================================================
# CLEAN HTML DESCRIPTION
# ============================================================

def clean_rss_html(
    value,
):
    """
    Convert common RSS/Atom HTML descriptions into
    readable plain text for Kalxa moderation.
    """

    value = (
        str(
            value
            or ""
        )
    )


    if not value:
        return ""


    # --------------------------------------------------------
    # LINE BREAK TAGS
    # --------------------------------------------------------

    value = re.sub(
        r"(?i)<br\s*/?>",
        "\n",
        value,
    )


    value = re.sub(
        r"(?i)</p\s*>",
        "\n",
        value,
    )


    # --------------------------------------------------------
    # REMOVE HTML TAGS
    # --------------------------------------------------------

    value = re.sub(
        r"<[^>]+>",
        " ",
        value,
    )


    # --------------------------------------------------------
    # HTML ENTITIES
    # --------------------------------------------------------

    value = unescape(
        value
    )


    # --------------------------------------------------------
    # NORMALIZE WHITESPACE
    # --------------------------------------------------------

    value = re.sub(
        r"[ \t]+",
        " ",
        value,
    )


    value = re.sub(
        r"\n\s*\n+",
        "\n\n",
        value,
    )


    return (
        value
        .strip()
    )


# ============================================================
# GET CHILD TEXT
# ============================================================

def get_xml_child_text(
    element,
    *wanted_names,
):
    """
    Find the first matching direct XML child regardless
    of RSS/Atom namespaces.
    """

    wanted_names = {
        str(
            name
        )
        .strip()
        .lower()

        for name
        in wanted_names
    }


    for child in list(
        element
    ):

        child_name = (
            rss_local_name(
                child.tag
            )
        )


        if (
            child_name
            not in wanted_names
        ):

            continue


        text_value = (
            child.text
            or ""
        )


        text_value = (
            text_value
            .strip()
        )


        if text_value:

            return text_value


    return None


# ============================================================
# ATOM / RSS LINK
# ============================================================

def get_feed_entry_link(
    entry,
    feed_url,
):
    """
    Resolve an application/source URL from either RSS
    or Atom.
    """

    # --------------------------------------------------------
    # RSS:
    #
    # <link>https://...</link>
    # --------------------------------------------------------

    direct_link = (
        get_xml_child_text(
            entry,
            "link",
        )
    )


    if direct_link:

        return urljoin(
            feed_url,
            direct_link,
        )


    # --------------------------------------------------------
    # ATOM:
    #
    # <link href="..." rel="alternate" />
    # --------------------------------------------------------

    fallback_link = None


    for child in list(
        entry
    ):

        if (
            rss_local_name(
                child.tag
            )
            != "link"
        ):

            continue


        href = (
            child.attrib.get(
                "href",
                ""
            )
            .strip()
        )


        if not href:

            continue


        rel = (
            child.attrib.get(
                "rel",
                "alternate",
            )
            .strip()
            .lower()
        )


        resolved = (
            urljoin(
                feed_url,
                href,
            )
        )


        if rel == "alternate":

            return resolved


        if fallback_link is None:

            fallback_link = (
                resolved
            )


    return fallback_link


# ============================================================
# ENTRY IDENTIFIER
# ============================================================

def get_feed_entry_identifier(
    entry,
):
    """
    Return the strongest available external RSS/Atom
    identifier.
    """

    return (
        get_xml_child_text(
            entry,
            "guid",
            "id",
        )
    )


# ============================================================
# FEED ENTRY DESCRIPTION
# ============================================================

def get_feed_entry_description(
    entry,
):
    """
    RSS commonly uses description/content.
    Atom commonly uses summary/content.
    """

    raw_description = (
        get_xml_child_text(
            entry,
            "description",
            "summary",
            "content",
            "encoded",
        )
    )


    return clean_rss_html(
        raw_description
    )


# ============================================================
# INFER JOB TYPE
# ============================================================

def infer_rss_job_type(
    title,
    description,
):
    """
    Infer a Kalxa Jobs content_type from feed text.
    """

    searchable_text = (
        f"{title or ''} "
        f"{description or ''}"
    ).lower()


    # --------------------------------------------------------
    # More specific types first.
    # --------------------------------------------------------

    ordered_keywords = (
        "business opportunity",
        "learnership",
        "internship",
        "intern",
        "training",
        "tender",
        "vacancy",
        "job",
    )


    for keyword in ordered_keywords:

        if keyword in searchable_text:

            return (
                RSS_JOB_TYPE_KEYWORDS[
                    keyword
                ]
            )


    return "job"


# ============================================================
# EXTRACT CLOSING DATE
# ============================================================

def parse_job_closing_date_text(
    value,
):
    """
    Try to discover a closing date from structured feed text.

    Supported examples:

        2026-10-15
        15/10/2026
        15-10-2026
        15 October 2026
        October 15 2026
    """

    value = (
        clean_rss_html(
            value
        )
    )


    if not value:

        return None


    # ========================================================
    # ISO DATE
    # ========================================================

    iso_match = re.search(
        r"\b"
        r"(20\d{2})"
        r"[-/]"
        r"(0?[1-9]|1[0-2])"
        r"[-/]"
        r"(0?[1-9]|[12]\d|3[01])"
        r"\b",
        value,
    )


    if iso_match:

        try:

            return date(
                int(
                    iso_match.group(1)
                ),
                int(
                    iso_match.group(2)
                ),
                int(
                    iso_match.group(3)
                ),
            )

        except ValueError:

            pass


    # ========================================================
    # SOUTH AFRICAN / EUROPEAN STYLE
    #
    # 15/10/2026
    # 15-10-2026
    # ========================================================

    numeric_match = re.search(
        r"\b"
        r"(0?[1-9]|[12]\d|3[01])"
        r"[-/]"
        r"(0?[1-9]|1[0-2])"
        r"[-/]"
        r"(20\d{2})"
        r"\b",
        value,
    )


    if numeric_match:

        try:

            return date(
                int(
                    numeric_match.group(3)
                ),
                int(
                    numeric_match.group(2)
                ),
                int(
                    numeric_match.group(1)
                ),
            )

        except ValueError:

            pass


    # ========================================================
    # MONTH NAME
    # ========================================================

    month_formats = (
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%d %B, %Y",
        "%d %b, %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    )


    month_patterns = (

        r"\b\d{1,2}\s+"
        r"[A-Za-z]{3,9},?\s+"
        r"20\d{2}\b",

        r"\b[A-Za-z]{3,9}\s+"
        r"\d{1,2},?\s+"
        r"20\d{2}\b",

    )


    for pattern in month_patterns:

        matches = re.findall(
            pattern,
            value,
        )


        for candidate in matches:

            for format_value in (
                month_formats
            ):

                try:

                    return (
                        datetime.strptime(
                            candidate,
                            format_value,
                        )
                        .date()
                    )

                except ValueError:

                    continue


    return None


# ============================================================
# EXPLICIT CLOSING DATE FIELDS
# ============================================================

def get_feed_entry_closing_date(
    entry,
    description,
):
    """
    First use known XML closing-date fields.

    If none exist, look for a closing/deadline date
    inside the description.
    """

    explicit_value = (
        get_xml_child_text(
            entry,
            "closingdate",
            "closing_date",
            "deadline",
            "applicationdeadline",
            "application_deadline",
            "expirationdate",
            "expirydate",
            "enddate",
        )
    )


    if explicit_value:

        result = (
            parse_job_closing_date_text(
                explicit_value
            )
        )


        if result:

            return result


    # --------------------------------------------------------
    # Search only likely closing/deadline fragments first.
    # --------------------------------------------------------

    if description:

        closing_match = re.search(
            (
                r"(?i)"
                r"(?:closing\s*date|"
                r"applications?\s*close|"
                r"deadline|"
                r"closing)"
                r"[^.\n]{0,80}"
            ),
            description,
        )


        if closing_match:

            result = (
                parse_job_closing_date_text(
                    closing_match.group(0)
                )
            )


            if result:

                return result


    return None


# ============================================================
# COMPANY / EMPLOYER
# ============================================================

def get_feed_entry_employer(
    entry,
    default_employer=None,
):
    """
    Attempt common RSS/Atom employer fields.
    """

    employer = (
        get_xml_child_text(
            entry,
            "company",
            "companyname",
            "employer",
            "organisation",
            "organization",
            "author",
        )
    )


    if employer:

        return clean_rss_html(
            employer
        )


    return (
        str(
            default_employer
            or ""
        )
        .strip()
    )


# ============================================================
# LOCATION
# ============================================================

def get_feed_entry_location(
    entry,
    default_location=None,
):
    """
    Attempt common location fields.

    The configured Kalxa zone is used as the fallback.
    """

    location = (
        get_xml_child_text(
            entry,
            "location",
            "joblocation",
            "job_location",
            "city",
            "area",
            "region",
        )
    )


    if location:

        return clean_rss_html(
            location
        )


    return (
        str(
            default_location
            or ""
        )
        .strip()
    )


# ============================================================
# SALARY
# ============================================================

def get_feed_entry_salary(
    entry,
):
    """
    Read salary when supplied by the feed.
    """

    salary = (
        get_xml_child_text(
            entry,
            "salary",
            "salarytext",
            "compensation",
            "remuneration",
        )
    )


    if salary:

        return clean_rss_html(
            salary
        )


    return None


# ============================================================
# PARSE RSS / ATOM DOCUMENT
# ============================================================

def parse_job_feed_xml(
    xml_content,
    feed_url,
    default_employer=None,
    default_location=None,
):
    """
    Parse RSS 2.x or Atom into normalized Kalxa job
    dictionaries.
    """

    try:

        root = (
            ET.fromstring(
                xml_content
            )
        )


    except ET.ParseError as exc:

        raise ValueError(
            f"Invalid RSS/Atom XML: {exc}"
        )


    entries = []


    # ========================================================
    # FIND RSS <item> OR ATOM <entry>
    # ========================================================

    for element in root.iter():

        local_name = (
            rss_local_name(
                element.tag
            )
        )


        if (
            local_name
            not in {
                "item",
                "entry",
            }
        ):

            continue


        title = clean_rss_html(
            get_xml_child_text(
                element,
                "title",
            )
        )


        description = (
            get_feed_entry_description(
                element
            )
        )


        link = (
            get_feed_entry_link(
                element,
                feed_url,
            )
        )


        external_id = (
            get_feed_entry_identifier(
                element
            )
        )


        business_name = (
            get_feed_entry_employer(
                element,
                default_employer=
                    default_employer,
            )
        )


        venue = (
            get_feed_entry_location(
                element,
                default_location=
                    default_location,
            )
        )


        salary_text = (
            get_feed_entry_salary(
                element
            )
        )


        closing_date = (
            get_feed_entry_closing_date(
                element,
                description,
            )
        )


        content_type = (
            infer_rss_job_type(
                title,
                description,
            )
        )


        entries.append({

            "title":
                title,

            "business_name":
                business_name,

            "venue":
                venue,

            "description":
                description,

            "application_url":
                link,

            "salary_text":
                salary_text,

            "closing_date":
                closing_date,

            "content_type":
                content_type,

            "external_id":
                external_id,

        })


    return entries


# ============================================================
# LOAD FEED CONFIGURATION
# ============================================================

def get_job_feed_configs():
    """
    Recommended Render environment variable:

    KALXA_JOB_FEEDS_JSON

    Example:

    [
      {
        "name": "Company Careers",
        "url": "https://example.com/jobs.xml",
        "zone_id": 1,
        "employer": "Example Company"
      }
    ]

    Multiple feeds can be configured without changing code.
    """

    raw_config = (
        os.environ.get(
            "KALXA_JOB_FEEDS_JSON",
            "",
        )
        .strip()
    )


    if not raw_config:

        return []


    try:

        configs = (
            json.loads(
                raw_config
            )
        )


    except json.JSONDecodeError as exc:

        raise RuntimeError(
            (
                "KALXA_JOB_FEEDS_JSON contains "
                f"invalid JSON: {exc}"
            )
        )


    if not isinstance(
        configs,
        list,
    ):

        raise RuntimeError(
            "KALXA_JOB_FEEDS_JSON must be a JSON array."
        )


    cleaned_configs = []


    for config in configs:

        if not isinstance(
            config,
            dict,
        ):

            continue


        feed_url = (
            str(
                config.get(
                    "url"
                )
                or ""
            )
            .strip()
        )


        if not feed_url:

            continue


        try:

            zone_id = int(
                config.get(
                    "zone_id"
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue


        cleaned_configs.append({

            "name":
                str(
                    config.get(
                        "name"
                    )
                    or feed_url
                )
                .strip(),

            "url":
                feed_url,

            "zone_id":
                zone_id,

            "employer":
                (
                    str(
                        config.get(
                            "employer"
                        )
                        or ""
                    )
                    .strip()
                    or None
                ),

        })


    return cleaned_configs


# ============================================================
# FETCH ONE FEED
# ============================================================
# FETCH ONE RSS / ATOM FEED
# ============================================================

def fetch_job_feed(feed_config):
    """
    Download and parse one configured RSS/Atom feed.

    Kalxa's own test feed is generated internally, so the Render web
    service never makes an HTTP request back to itself.

    Real external feeds continue to use requests.get().
    """

    # ========================================================
    # FEED URL
    # ========================================================

    feed_url = str(
        feed_config.get("url", "") or ""
    ).strip()

    if not feed_url:
        raise ValueError("RSS/Atom feed URL is missing.")

    # ========================================================
    # ZONE
    # ========================================================

    try:
        zone_id = int(feed_config.get("zone_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "RSS/Atom feed zone_id is invalid."
        ) from exc

    zone = db.session.get(Zone, zone_id)

    if not zone or not zone.active:
        raise ValueError(
            "Configured Kalxa zone does not exist or is inactive: "
            f"{zone_id}"
        )

    # ========================================================
    # CLEAN FEED URL
    #
    # Remove query parameters and trailing slashes so Kalxa's
    # test-feed detection remains reliable.
    # ========================================================

    clean_feed_url = feed_url.split("?", 1)[0].rstrip("/")

    # ========================================================
    # DEBUG
    #
    # These logs help confirm exactly what Render receives from
    # KALXA_JOB_FEEDS_JSON.
    # ========================================================

    current_app.logger.info(
        "[Kalxa RSS DEBUG] raw feed_url=%r",
        feed_url,
    )

    current_app.logger.info(
        "[Kalxa RSS DEBUG] clean_feed_url=%r",
        clean_feed_url,
    )

    test_feed_path = "/test/jobs-feed.xml"

    is_internal_test_feed = clean_feed_url.endswith(test_feed_path)

    current_app.logger.info(
        "[Kalxa RSS DEBUG] test_match=%s",
        is_internal_test_feed,
    )

    # ========================================================
    # KALXA INTERNAL TEST FEED
    #
    # Do not call the Render service through requests.get().
    # On a single-worker deployment, the current POST request can
    # occupy the only worker and cause the internal GET request to
    # wait until the POST request times out.
    # ========================================================

    if is_internal_test_feed:
        current_app.logger.info(
            "[Kalxa RSS Jobs] "
            "Using internally generated test feed. "
            "No HTTP self-request will be made."
        )

        xml_content = build_test_jobs_rss_xml().encode("utf-8")

        entries = parse_job_feed_xml(
            xml_content=xml_content,
            feed_url=feed_url,
            default_employer=feed_config.get("employer"),
            default_location=zone.name,
        )

        current_app.logger.info(
            "[Kalxa RSS Jobs] Internal test feed parsed. entries=%s",
            len(entries),
        )

        return zone, entries

    # ========================================================
    # REAL EXTERNAL RSS / ATOM FEED
    # ========================================================

    current_app.logger.info(
        "[Kalxa RSS Jobs] Fetching external feed: %s",
        feed_url,
    )

    headers = {
        "Accept": (
            "application/rss+xml, "
            "application/atom+xml, "
            "application/xml, "
            "text/xml, "
            "*/*"
        ),
        "User-Agent": "Kalxa-Jobs-RSS-Importer/1.0",
    }

    try:
        response = requests.get(
            feed_url,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

    except requests.RequestException as exc:
        current_app.logger.exception(
            "[Kalxa RSS Jobs] "
            "Unable to fetch external feed. url=%s error=%s",
            feed_url,
            exc,
        )

        raise ValueError(
            f"Unable to fetch RSS/Atom feed {feed_url}: {exc}"
        ) from exc

    # ========================================================
    # PARSE EXTERNAL FEED
    # ========================================================

    entries = parse_job_feed_xml(
        xml_content=response.content,
        feed_url=feed_url,
        default_employer=feed_config.get("employer"),
        default_location=zone.name,
    )

    current_app.logger.info(
        "[Kalxa RSS Jobs] External feed parsed. url=%s entries=%s",
        feed_url,
        len(entries),
    )

    return zone, entries

# ============================================================
# APPLICATION URL DUPLICATE CHECK
# ============================================================

def find_duplicate_job_url(
    application_url,
):
    """
    External feeds commonly provide stable job URLs.

    Checking the URL catches duplicates even if the feed
    later changes the title slightly.
    """

    application_url = (
        str(
            application_url
            or ""
        )
        .strip()
    )


    if not application_url:

        return None


    pending = (
        PendingSubmission.query
        .filter(
            PendingSubmission.category
            == "jobs",

            PendingSubmission.status
            == "pending",

            PendingSubmission.ticket_url
            == application_url,
        )
        .first()
    )


    if pending:

        return {
            "type":
                "pending",

            "record":
                pending,
        }


    published = (
        ContentItem.query
        .filter(
            ContentItem.category
            == "jobs",

            ContentItem.ticket_url
            == application_url,

            ContentItem.active.is_(
                True
            ),

            ContentItem.archived.is_(
                False
            ),
        )
        .first()
    )


    if published:

        return {
            "type":
                "published",

            "record":
                published,
        }


    return None


# ============================================================
# CREATE RSS JOB PENDING SUBMISSION
# ============================================================

def create_pending_rss_job(
    *,
    job,
    zone,
    feed_name,
    feed_url,
):
    """
    Convert one normalized feed entry into a free
    Kalxa PendingSubmission.

    Returns:

        "created"
        "duplicate"
        "expired"
        "invalid"
    """

    title = (
        str(
            job.get(
                "title"
            )
            or ""
        )
        .strip()
    )


    business_name = (
        str(
            job.get(
                "business_name"
            )
            or ""
        )
        .strip()
    )


    venue = (
        str(
            job.get(
                "venue"
            )
            or zone.name
        )
        .strip()
    )


    description = (
        str(
            job.get(
                "description"
            )
            or ""
        )
        .strip()
    )


    application_url = (
        str(
            job.get(
                "application_url"
            )
            or ""
        )
        .strip()
    )


    salary_text = (
        str(
            job.get(
                "salary_text"
            )
            or ""
        )
        .strip()
        or None
    )


    closing_date = (
        job.get(
            "closing_date"
        )
    )


    content_type = (
        str(
            job.get(
                "content_type"
            )
            or "job"
        )
        .strip()
        .lower()
    )


    external_id = (
        str(
            job.get(
                "external_id"
            )
            or ""
        )
        .strip()
        or None
    )


    # ========================================================
    # REQUIRED DATA
    # ========================================================

    if (
        not title
        or
        not application_url
    ):

        return "invalid"


    # --------------------------------------------------------
    # Feeds do not always provide company separately.
    #
    # Use the configured feed name instead of losing the
    # opportunity completely.
    # --------------------------------------------------------

    if not business_name:

        business_name = (
            feed_name
            or "External Employer"
        )


    if not description:

        description = (
            f"{title} opportunity from "
            f"{business_name}. "
            "Open the application link for full details."
        )


    # ========================================================
    # VALID CONTENT TYPE
    # ========================================================

    if (
        content_type
        not in KALXA_JOB_TYPES
    ):

        content_type = (
            "job"
        )


    # ========================================================
    # EXPIRED
    # ========================================================

    today = (
        datetime.now(
            KALXA_TIMEZONE
        )
        .date()
    )


    if (
        closing_date
        and
        closing_date < today
    ):

        return "expired"


    # ========================================================
    # URL DUPLICATE
    # ========================================================

    if find_duplicate_job_url(
        application_url
    ):

        return "duplicate"


    # ========================================================
    # CONTENT DUPLICATE
    # ========================================================

    existing_duplicate = (
        find_duplicate_job_submission(

            zone_id=
                zone.id,

            title=
                title,

            business_name=
                business_name,

            venue=
                venue,

            end_date=
                closing_date,
        )
    )


    if existing_duplicate:

        return "duplicate"


    # ========================================================
    # LIFETIME
    # ========================================================

    lifetime_type = (
        "time_specific"
        if closing_date
        else "until_unavailable"
    )


    # ========================================================
    # IMPORT AUDIT INFORMATION
    # ========================================================

    source_notes = [

        "Imported automatically from an RSS/Atom jobs feed.",

        f"Source: {feed_name}",

        f"Feed URL: {feed_url}",
    ]


    if external_id:

        source_notes.append(
            f"External reference: {external_id}"
        )


    admin_notes = (
        "\n".join(
            source_notes
        )
    )


    # ========================================================
    # CREATE PENDING SUBMISSION
    # ========================================================

    submission = (
        PendingSubmission(

            organizer_id=
                None,

            zone_id=
                zone.id,

            category=
                "jobs",

            content_type=
                content_type,

            lifetime_type=
                lifetime_type,

            availability_status=
                "available",

            notification_eligible=
                True,


            # ------------------------------------------------
            # JOB
            # ------------------------------------------------

            title=
                title,

            description=
                description,

            business_name=
                business_name,

            venue=
                venue,

            price=
                salary_text,


            # ------------------------------------------------
            # APPLICATION
            # ------------------------------------------------

            ticket_url=
                application_url,


            # ------------------------------------------------
            # DATES
            # ------------------------------------------------

            start_date=
                None,

            end_date=
                closing_date,

            start_time=
                None,

            end_time=
                None,

            publish_from=
                None,

            event_date=
                None,

            event_end_date=
                None,


            # ------------------------------------------------
            # FREE KALXA JOB
            # ------------------------------------------------

            pricing_model=
                None,

            commercial_duration_days=
                None,

            amount_due=
                None,

            payment_status=
                "waived",

            distribution_zone_ids=
                [],


            # ------------------------------------------------
            # SOURCE
            # ------------------------------------------------

            submitter_name=
                (
                    f"Kalxa RSS Import · "
                    f"{feed_name}"
                ),

            submitter_email=
                None,

            submitter_phone=
                None,

            admin_notes=
                admin_notes,


            # ------------------------------------------------
            # MODERATION
            # ------------------------------------------------

            status=
                "pending",
        )
    )


    if not submission.tracking_code:

        submission.tracking_code = (
            uuid.uuid4()
            .hex[:12]
            .upper()
        )


    db.session.add(
        submission
    )


    return "created"

# ============================================================
# KALXA JOBS - TEST RSS FEED
# ============================================================

def build_test_jobs_rss_xml():
    """
    Build the Kalxa development RSS feed.

    This function can be used both by:

        /test/jobs-feed.xml

    and internally by the RSS importer.

    This avoids making the Render web service call itself.
    """

    public_base_url = (
        get_public_base_url()
        .rstrip("/")
    )


    today = (
        datetime.now(
            KALXA_TIMEZONE
        )
        .date()
    )


    shop_assistant_closing_date = (
        today
        + timedelta(
            days=30
        )
    )


    internship_closing_date = (
        today
        + timedelta(
            days=45
        )
    )


    shop_assistant_url = (
        f"{public_base_url}/"
        "?kalxa_test_job=shop-assistant-001"
    )


    driver_url = (
        f"{public_base_url}/"
        "?kalxa_test_job=delivery-driver-002"
    )


    internship_url = (
        f"{public_base_url}/"
        "?kalxa_test_job=internship-003"
    )


    generated_at = (
        datetime.now(
            KALXA_TIMEZONE
        )
    )


    rss_pub_date = (
        generated_at.strftime(
            "%a, %d %b %Y %H:%M:%S %z"
        )
    )


    test_jobs = [

        {
            "guid":
                "kalxa-test-job-shop-assistant-001",

            "title":
                "Shop Assistant",

            "company":
                "KwaMhlanga Supermarket",

            "location":
                "KwaMhlanga",

            "salary":
                "R6,500 per month",

            "closing_date":
                shop_assistant_closing_date,

            "job_type":
                "job",

            "application_url":
                shop_assistant_url,

            "description":
                (
                    "KwaMhlanga Supermarket is looking for "
                    "a reliable Shop Assistant. Duties include "
                    "helping customers, packing shelves, keeping "
                    "the store clean and assisting at busy times. "
                    "Applicants should be punctual, friendly and "
                    "comfortable working with customers."
                ),
        },


        {
            "guid":
                "kalxa-test-job-delivery-driver-002",

            "title":
                "Local Delivery Driver",

            "company":
                "Kalxa Test Foods",

            "location":
                "KwaMhlanga",

            "salary":
                "Negotiable",

            "closing_date":
                None,

            "job_type":
                "job",

            "application_url":
                driver_url,

            "description":
                (
                    "Kalxa Test Foods is looking for a local "
                    "delivery driver. Applicants should know the "
                    "KwaMhlanga area well and have reliable access "
                    "to transport. This opportunity remains open "
                    "until the position is filled."
                ),
        },


        {
            "guid":
                "kalxa-test-job-internship-003",

            "title":
                "Digital Marketing Internship",

            "company":
                "Kalxa Test Media",

            "location":
                "KwaMhlanga",

            "salary":
                "Monthly stipend",

            "closing_date":
                internship_closing_date,

            "job_type":
                "internship",

            "application_url":
                internship_url,

            "description":
                (
                    "Kalxa Test Media is offering a Digital "
                    "Marketing Internship for a young person "
                    "interested in social media, content creation "
                    "and local business marketing. Basic computer "
                    "and smartphone skills are required."
                ),
        },

    ]


    rss_items = []


    for job in test_jobs:

        closing_date_xml = ""


        if job["closing_date"]:

            closing_date_xml = (
                "\n"
                "            <closing_date>"
                f"{escape(job['closing_date'].isoformat())}"
                "</closing_date>"
            )


        item_xml = f"""
        <item>

            <guid isPermaLink="false">
                {escape(job["guid"])}
            </guid>

            <title>
                {escape(job["title"])}
            </title>

            <company>
                {escape(job["company"])}
            </company>

            <location>
                {escape(job["location"])}
            </location>

            <salary>
                {escape(job["salary"])}
            </salary>

            <job_type>
                {escape(job["job_type"])}
            </job_type>
{closing_date_xml}

            <description>
                {escape(job["description"])}
            </description>

            <link>
                {escape(job["application_url"])}
            </link>

            <pubDate>
                {escape(rss_pub_date)}
            </pubDate>

        </item>
        """


        rss_items.append(
            item_xml
        )


    return f"""<?xml version="1.0" encoding="UTF-8"?>

<rss version="2.0">

    <channel>

        <title>
            Kalxa Test Jobs
        </title>

        <link>
            {escape(public_base_url)}
        </link>

        <description>
            Kalxa development RSS feed for testing the Jobs importer.
        </description>

        <language>
            en-ZA
        </language>

        <lastBuildDate>
            {escape(rss_pub_date)}
        </lastBuildDate>

        {''.join(rss_items)}

    </channel>

</rss>
"""



# ============================================================
# RSS / ATOM IMPORT ROUTE
# ============================================================

@app.route(
    "/internal/jobs/import/rss",
    methods=[
        "POST",
    ],
)
def import_rss_jobs():
    """
    Import configured RSS/Atom job feeds.

    Security:

        Authorization:
        Bearer <KALXA_JOB_FEED_IMPORT_TOKEN>

    Imported jobs DO NOT publish immediately.

    They enter:

        PendingSubmission
            ↓
        Admin moderation
            ↓
        ContentItem
    """

    # =====================================================
    # AUTH TOKEN
    # =====================================================

    expected_token = (
        os.environ.get(
            "KALXA_JOB_FEED_IMPORT_TOKEN",
            "",
        )
        .strip()
    )


    # --------------------------------------------------------
    # Optional fallback so you can reuse the external Jobs
    # importer token if preferred.
    # --------------------------------------------------------

    if not expected_token:

        expected_token = (
            os.environ.get(
                "KALXA_EXTERNAL_JOBS_IMPORT_TOKEN",
                "",
            )
            .strip()
        )


    if not expected_token:

        current_app.logger.error(
            "[Kalxa RSS Jobs] "
            "Importer token is not configured."
        )


        return jsonify({

            "success":
                False,

            "message":
                "RSS Jobs importer is not configured.",

        }), 503


    authorization = (
        request.headers.get(
            "Authorization",
            "",
        )
        .strip()
    )


    if not authorization.startswith(
        "Bearer "
    ):

        return jsonify({

            "success":
                False,

            "message":
                "Unauthorized.",

        }), 401


    supplied_token = (
        authorization[
            len("Bearer "):
        ]
        .strip()
    )


    if (
        not supplied_token
        or not secrets.compare_digest(
            supplied_token,
            expected_token,
        )
    ):

        return jsonify({

            "success":
                False,

            "message":
                "Unauthorized.",

        }), 401


    # =====================================================
    # CONFIGURED FEEDS
    # =====================================================

    try:

        feed_configs = (
            get_job_feed_configs()
        )


    except Exception as exc:

        current_app.logger.exception(
            "[Kalxa RSS Jobs] "
            "Unable to load feed configuration: %s",
            exc,
        )


        return jsonify({

            "success":
                False,

            "message":
                str(
                    exc
                ),

        }), 503

        # =====================================================
        if not feed_configs:

         current_app.logger.info(
          (
            "[Kalxa RSS Jobs] "
            "Scheduled import skipped because "
            "no RSS/Atom feeds are configured."
          )
         )
         return jsonify({

          "success":
            True,

          "skipped":
            True,

          "feeds":
            0,

          "feed_errors":
            0,

          "fetched":
            0,

          "created":
            0,

          "duplicates":
            0,

          "expired":
            0,

          "invalid":
            0,

          "results":
            [],

          "message":
            (
                "No RSS/Atom Jobs feeds are "
                "currently configured."
            ),

         }), 200
   

    # =====================================================
    # TOTAL COUNTERS
    # =====================================================

    total_fetched = 0
    total_created = 0
    total_duplicates = 0
    total_invalid = 0
    total_expired = 0
    total_feed_errors = 0


    feed_results = []


    # =====================================================
    # PROCESS EACH FEED
    # =====================================================

    for feed_config in (
        feed_configs
    ):

        feed_name = (
            feed_config[
                "name"
            ]
        )


        feed_url = (
            feed_config[
                "url"
            ]
        )


        feed_result = {

            "name":
                feed_name,

            "url":
                feed_url,

            "fetched":
                0,

            "created":
                0,

            "duplicates":
                0,

            "invalid":
                0,

            "expired":
                0,

            "error":
                None,
        }


        try:

            zone, jobs = (
                fetch_job_feed(
                    feed_config
                )
            )


            feed_result[
                "zone_id"
            ] = (
                zone.id
            )


            feed_result[
                "zone_name"
            ] = (
                zone.name
            )


            feed_result[
                "fetched"
            ] = (
                len(
                    jobs
                )
            )


            total_fetched += (
                len(
                    jobs
                )
            )


            # =================================================
            # PROCESS ENTRIES
            # =================================================

            for job in jobs:

                result = (
                    create_pending_rss_job(

                        job=
                            job,

                        zone=
                            zone,

                        feed_name=
                            feed_name,

                        feed_url=
                            feed_url,
                    )
                )


                if result == "created":

                    feed_result[
                        "created"
                    ] += 1

                    total_created += 1


                elif result == "duplicate":

                    feed_result[
                        "duplicates"
                    ] += 1

                    total_duplicates += 1


                elif result == "expired":

                    feed_result[
                        "expired"
                    ] += 1

                    total_expired += 1


                else:

                    feed_result[
                        "invalid"
                    ] += 1

                    total_invalid += 1


            # =================================================
            # COMMIT THIS FEED
            # =================================================

            db.session.commit()


            current_app.logger.info(
                (
                    "[Kalxa RSS Jobs] "
                    "feed=%s "
                    "zone_id=%s "
                    "fetched=%s "
                    "created=%s "
                    "duplicates=%s "
                    "expired=%s "
                    "invalid=%s"
                ),
                feed_name,
                zone.id,
                feed_result[
                    "fetched"
                ],
                feed_result[
                    "created"
                ],
                feed_result[
                    "duplicates"
                ],
                feed_result[
                    "expired"
                ],
                feed_result[
                    "invalid"
                ],
            )


        except (
            requests.RequestException,
            ValueError,
        ) as exc:

            db.session.rollback()


            feed_result[
                "error"
            ] = str(
                exc
            )


            total_feed_errors += 1


            current_app.logger.exception(
                (
                    "[Kalxa RSS Jobs] "
                    "Feed import failed "
                    "feed=%s "
                    "url=%s "
                    "error=%s"
                ),
                feed_name,
                feed_url,
                exc,
            )


        except Exception as exc:

            db.session.rollback()


            feed_result[
                "error"
            ] = (
                "Unexpected feed import error."
            )


            total_feed_errors += 1


            current_app.logger.exception(
                (
                    "[Kalxa RSS Jobs] "
                    "Unexpected feed error "
                    "feed=%s "
                    "url=%s "
                    "error=%s"
                ),
                feed_name,
                feed_url,
                exc,
            )


        feed_results.append(
            feed_result
        )


    # =====================================================
    # RESPONSE
    # =====================================================

    return jsonify({

        "success":
            total_feed_errors
            < len(
                feed_configs
            ),

        "feeds":
            len(
                feed_configs
            ),

        "feed_errors":
            total_feed_errors,

        "fetched":
            total_fetched,

        "created":
            total_created,

        "duplicates":
            total_duplicates,

        "expired":
            total_expired,

        "invalid":
            total_invalid,

        "results":
            feed_results,

        "message":
            (
                f"{total_created} RSS/Atom job(s) "
                "were added to Kalxa moderation."
            ),

    }), 200

def normalize_external_job_type(
    value,
):
    """
    Convert external source job types into Kalxa's
    supported Jobs content types.
    """

    value = (
        str(
            value
            or ""
        )
        .strip()
        .lower()
    )


    return (
        EXTERNAL_JOB_TYPE_MAP.get(
            value,
            "job",
        )
    )


def parse_external_job_date(
    value,
):
    """
    Accept common external date formats.

    Examples:

        2026-09-30
        2026-09-30T23:59:59
        2026-09-30T23:59:59Z

    Returns:
        datetime.date | None
    """

    value = (
        str(
            value
            or ""
        )
        .strip()
    )


    if not value:
        return None


    # --------------------------------------------------------
    # YYYY-MM-DD
    # --------------------------------------------------------

    try:

        return datetime.strptime(
            value[:10],
            "%Y-%m-%d",
        ).date()

    except ValueError:

        return None


def get_external_job_value(
    payload,
    *keys,
):
    """
    Return the first useful value found among several
    possible external API field names.
    """

    for key in keys:

        value = payload.get(
            key
        )


        if value not in (
            None,
            "",
            [],
            {},
        ):

            return value


    return None


def normalize_external_job(
    payload,
):
    """
    Convert different external job JSON structures into
    one Kalxa Jobs structure.

    External providers often use different names:

        company
        company_name
        employer

    This helper gives Kalxa one common format.
    """

    title = (
        get_external_job_value(
            payload,
            "title",
            "job_title",
            "position",
            "name",
        )
    )


    business_name = (
        get_external_job_value(
            payload,
            "company",
            "company_name",
            "employer",
            "organisation",
            "organization",
        )
    )


    venue = (
        get_external_job_value(
            payload,
            "location",
            "venue",
            "city",
            "area",
        )
    )


    description = (
        get_external_job_value(
            payload,
            "description",
            "summary",
            "job_description",
            "details",
        )
    )


    application_url = (
        get_external_job_value(
            payload,
            "application_url",
            "apply_url",
            "apply_link",
            "url",
            "job_url",
        )
    )


    application_email = (
        get_external_job_value(
            payload,
            "application_email",
            "apply_email",
            "email",
        )
    )


    salary_text = (
        get_external_job_value(
            payload,
            "salary",
            "salary_text",
            "compensation",
        )
    )


    closing_date = (
        parse_external_job_date(
            get_external_job_value(
                payload,
                "closing_date",
                "deadline",
                "application_deadline",
                "end_date",
            )
        )
    )


    content_type = (
        normalize_external_job_type(
            get_external_job_value(
                payload,
                "job_type",
                "type",
                "employment_type",
                "opportunity_type",
            )
        )
    )


    external_id = (
        get_external_job_value(
            payload,
            "id",
            "job_id",
            "external_id",
            "reference",
        )
    )


    return {
        "title":
            str(title or "").strip(),

        "business_name":
            str(
                business_name
                or ""
            ).strip(),

        "venue":
            str(
                venue
                or ""
            ).strip(),

        "description":
            str(
                description
                or ""
            ).strip(),

        "application_url":
            str(
                application_url
                or ""
            ).strip(),

        "application_email":
            str(
                application_email
                or ""
            ).strip(),

        "salary_text":
            (
                str(
                    salary_text
                ).strip()
                if salary_text
                else None
            ),

        "closing_date":
            closing_date,

        "content_type":
            content_type,

        "external_id":
            (
                str(
                    external_id
                ).strip()
                if external_id
                else None
            ),
    }


def extract_external_jobs(
    payload,
):
    """
    Support common JSON feed shapes.

    Examples:

        [
            {...},
            {...}
        ]

    or

        {
            "jobs": [...]
        }

    or

        {
            "results": [...]
        }

    or

        {
            "data": [...]
        }
    """

    if isinstance(
        payload,
        list,
    ):

        return payload


    if not isinstance(
        payload,
        dict,
    ):

        return []


    for key in (
        "jobs",
        "results",
        "data",
        "items",
    ):

        jobs = payload.get(
            key
        )


        if isinstance(
            jobs,
            list,
        ):

            return jobs


    return []


def fetch_external_jobs():
    """
    Fetch jobs from the configured external JSON feed.
    """

    feed_url = (
        os.environ.get(
            "KALXA_EXTERNAL_JOBS_URL",
            "",
        )
        .strip()
    )


    if not feed_url:

        raise RuntimeError(
            "KALXA_EXTERNAL_JOBS_URL is not configured."
        )


    headers = {
        "Accept":
            "application/json",

        "User-Agent":
            "Kalxa-Jobs-Importer/1.0",
    }


    # --------------------------------------------------------
    # OPTIONAL EXTERNAL API KEY
    # --------------------------------------------------------

    api_key = (
        os.environ.get(
            "KALXA_EXTERNAL_JOBS_API_KEY",
            "",
        )
        .strip()
    )


    if api_key:

        headers[
            "Authorization"
        ] = (
            f"Bearer {api_key}"
        )


    response = requests.get(
        feed_url,
        headers=headers,
        timeout=20,
    )


    response.raise_for_status()


    payload = response.json()


    return extract_external_jobs(
        payload
    )


# ============================================================
# KALXA JOBS - TEST RSS FEED
# ============================================================

# ============================================================
# KALXA JOBS - TEST RSS FEED
# ============================================================

def build_test_jobs_rss_xml():

    public_base_url = (
        get_public_base_url()
        .rstrip("/")
    )


    today = (
        datetime.now(
            KALXA_TIMEZONE
        )
        .date()
    )


    shop_assistant_closing_date = (
        today
        + timedelta(
            days=30
        )
    )


    internship_closing_date = (
        today
        + timedelta(
            days=45
        )
    )


    shop_assistant_url = (
        f"{public_base_url}/"
        "?kalxa_test_job=shop-assistant-001"
    )


    driver_url = (
        f"{public_base_url}/"
        "?kalxa_test_job=delivery-driver-002"
    )


    internship_url = (
        f"{public_base_url}/"
        "?kalxa_test_job=internship-003"
    )


    generated_at = (
        datetime.now(
            KALXA_TIMEZONE
        )
    )


    rss_pub_date = (
        generated_at.strftime(
            "%a, %d %b %Y %H:%M:%S %z"
        )
    )


    test_jobs = [

        {
            "guid":
                "kalxa-test-job-shop-assistant-001",

            "title":
                "Shop Assistant",

            "company":
                "KwaMhlanga Supermarket",

            "location":
                "KwaMhlanga",

            "salary":
                "R6,500 per month",

            "closing_date":
                shop_assistant_closing_date,

            "job_type":
                "job",

            "application_url":
                shop_assistant_url,

            "description":
                (
                    "KwaMhlanga Supermarket is looking for "
                    "a reliable Shop Assistant. Duties include "
                    "helping customers, packing shelves, keeping "
                    "the store clean and assisting at busy times. "
                    "Applicants should be punctual, friendly and "
                    "comfortable working with customers."
                ),
        },


        {
            "guid":
                "kalxa-test-job-delivery-driver-002",

            "title":
                "Local Delivery Driver",

            "company":
                "Kalxa Test Foods",

            "location":
                "KwaMhlanga",

            "salary":
                "Negotiable",

            "closing_date":
                None,

            "job_type":
                "job",

            "application_url":
                driver_url,

            "description":
                (
                    "Kalxa Test Foods is looking for a local "
                    "delivery driver. Applicants should know the "
                    "KwaMhlanga area well and have reliable access "
                    "to transport. This opportunity remains open "
                    "until the position is filled."
                ),
        },


        {
            "guid":
                "kalxa-test-job-internship-003",

            "title":
                "Digital Marketing Internship",

            "company":
                "Kalxa Test Media",

            "location":
                "KwaMhlanga",

            "salary":
                "Monthly stipend",

            "closing_date":
                internship_closing_date,

            "job_type":
                "internship",

            "application_url":
                internship_url,

            "description":
                (
                    "Kalxa Test Media is offering a Digital "
                    "Marketing Internship for a young person "
                    "interested in social media, content creation "
                    "and local business marketing. Basic computer "
                    "and smartphone skills are required."
                ),
        },

    ]


    rss_items = []


    for job in test_jobs:

        closing_date_xml = ""


        if job["closing_date"]:

            closing_date_xml = (
                "\n"
                "            <closing_date>"
                f"{escape(job['closing_date'].isoformat())}"
                "</closing_date>"
            )


        item_xml = f"""
        <item>

            <guid isPermaLink="false">
                {escape(job["guid"])}
            </guid>

            <title>
                {escape(job["title"])}
            </title>

            <company>
                {escape(job["company"])}
            </company>

            <location>
                {escape(job["location"])}
            </location>

            <salary>
                {escape(job["salary"])}
            </salary>

            <job_type>
                {escape(job["job_type"])}
            </job_type>
{closing_date_xml}

            <description>
                {escape(job["description"])}
            </description>

            <link>
                {escape(job["application_url"])}
            </link>

            <pubDate>
                {escape(rss_pub_date)}
            </pubDate>

        </item>
        """


        rss_items.append(
            item_xml
        )


    return f"""<?xml version="1.0" encoding="UTF-8"?>

<rss version="2.0">

    <channel>

        <title>
            Kalxa Test Jobs
        </title>

        <link>
            {escape(public_base_url)}
        </link>

        <description>
            Kalxa development RSS feed for testing the Jobs importer.
        </description>

        <language>
            en-ZA
        </language>

        <lastBuildDate>
            {escape(rss_pub_date)}
        </lastBuildDate>

        {''.join(rss_items)}

    </channel>

</rss>
"""


@app.route(
    "/test/jobs-feed.xml",
    methods=["GET"],
)
def test_jobs_rss_feed():

    rss_xml = (
        build_test_jobs_rss_xml()
    )


    response = (
        current_app.make_response(
            rss_xml
        )
    )


    response.headers[
        "Content-Type"
    ] = (
        "application/rss+xml; "
        "charset=utf-8"
    )


    response.headers[
        "Cache-Control"
    ] = (
        "no-store, max-age=0"
    )


    return response
# ============================================================
# INTERNAL EXTERNAL JOB IMPORT ROUTE
# ============================================================

@app.route(
    "/internal/jobs/import",
    methods=[
        "POST",
    ],
)
def import_external_jobs():

    # =====================================================
    # AUTHENTICATION
    # =====================================================

    expected_token = (
        os.environ.get(
            "KALXA_EXTERNAL_JOBS_IMPORT_TOKEN",
            "",
        )
        .strip()
    )


    if not expected_token:

        current_app.logger.error(
            "[Kalxa Jobs Import] "
            "Import token is not configured."
        )


        return jsonify({
            "success":
                False,

            "message":
                "External Jobs importer is not configured.",
        }), 503


    authorization = (
        request.headers.get(
            "Authorization",
            "",
        )
        .strip()
    )


    if not authorization.startswith(
        "Bearer "
    ):

        return jsonify({
            "success":
                False,

            "message":
                "Unauthorized.",
        }), 401


    supplied_token = (
        authorization[
            len("Bearer "):
        ]
        .strip()
    )


    if (
        not supplied_token
        or not secrets.compare_digest(
            supplied_token,
            expected_token,
        )
    ):

        return jsonify({
            "success":
                False,

            "message":
                "Unauthorized.",
        }), 401


    # =====================================================
    # DEFAULT KALXA ZONE
    # =====================================================

    raw_zone_id = (
        os.environ.get(
            "KALXA_EXTERNAL_JOBS_ZONE_ID",
            "",
        )
        .strip()
    )


    try:

        zone_id = int(
            raw_zone_id
        )

    except (
        TypeError,
        ValueError,
    ):

        return jsonify({
            "success":
                False,

            "message":
                "KALXA_EXTERNAL_JOBS_ZONE_ID is invalid.",
        }), 503


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

        return jsonify({
            "success":
                False,

            "message":
                "Configured Kalxa Jobs zone is unavailable.",
        }), 503


    # =====================================================
    # FETCH EXTERNAL JOBS
    # =====================================================

    try:

        external_jobs = (
            fetch_external_jobs()
        )


    except requests.RequestException as exc:

        current_app.logger.exception(
            "[Kalxa Jobs Import] "
            "External request failed: %s",
            exc,
        )


        return jsonify({
            "success":
                False,

            "message":
                "Unable to fetch external jobs.",
        }), 502


    except ValueError as exc:

        current_app.logger.exception(
            "[Kalxa Jobs Import] "
            "External source returned invalid JSON: %s",
            exc,
        )


        return jsonify({
            "success":
                False,

            "message":
                "External job source returned invalid JSON.",
        }), 502


    except Exception as exc:

        current_app.logger.exception(
            "[Kalxa Jobs Import] "
            "Import failed before processing: %s",
            exc,
        )


        return jsonify({
            "success":
                False,

            "message":
                "External job import failed.",
        }), 500


    # =====================================================
    # COUNTERS
    # =====================================================

    fetched_count = len(
        external_jobs
    )

    created_count = 0

    duplicate_count = 0

    invalid_count = 0

    expired_count = 0


    today = (
        datetime.now(
            KALXA_TIMEZONE
        )
        .date()
    )


    # =====================================================
    # PROCESS JOBS
    # =====================================================

    for raw_job in external_jobs:

        if not isinstance(
            raw_job,
            dict,
        ):

            invalid_count += 1

            continue


        job = (
            normalize_external_job(
                raw_job
            )
        )


        # =================================================
        # REQUIRED DATA
        # =================================================

        if (
            not job["title"]
            or
            not job["business_name"]
            or
            not job["venue"]
            or
            not job["description"]
        ):

            invalid_count += 1

            continue


        # =================================================
        # APPLICATION METHOD
        # =================================================

        application_url = (
            build_job_application_url(

                application_url=
                    job[
                        "application_url"
                    ],

                application_email=
                    job[
                        "application_email"
                    ],

                job_title=
                    job[
                        "title"
                    ],
            )
        )


        if not application_url:

            invalid_count += 1

            continue


        # =================================================
        # EXPIRED JOB
        # =================================================

        closing_date = (
            job[
                "closing_date"
            ]
        )


        if (
            closing_date
            and
            closing_date < today
        ):

            expired_count += 1

            continue


        # =================================================
        # DUPLICATE CHECK
        # =================================================

        duplicate = (
            find_duplicate_job_submission(

                zone_id=
                    zone.id,

                title=
                    job[
                        "title"
                    ],

                business_name=
                    job[
                        "business_name"
                    ],

                venue=
                    job[
                        "venue"
                    ],

                end_date=
                    closing_date,
            )
        )


        if duplicate:

            duplicate_count += 1

            continue


        # =================================================
        # LIFETIME
        # =================================================

        if closing_date:

            lifetime_type = (
                "time_specific"
            )

        else:

            lifetime_type = (
                "until_unavailable"
            )


        # =================================================
        # SOURCE INFORMATION
        # =================================================

        external_reference = (
            job[
                "external_id"
            ]
        )


        source_note = (
            "Imported from an external jobs feed."
        )


        if external_reference:

            source_note += (
                " External reference: "
                f"{external_reference}."
            )


        description = (
            job[
                "description"
            ]
        )


        # =================================================
        # CREATE PENDING SUBMISSION
        # =================================================

        submission = (
            PendingSubmission(

                organizer_id=
                    None,

                zone_id=
                    zone.id,

                category=
                    "jobs",

                content_type=
                    job[
                        "content_type"
                    ],

                lifetime_type=
                    lifetime_type,

                availability_status=
                    "available",

                notification_eligible=
                    True,


                # -----------------------------------------
                # LISTING
                # -----------------------------------------

                title=
                    job[
                        "title"
                    ],

                description=
                    description,

                business_name=
                    job[
                        "business_name"
                    ],

                venue=
                    job[
                        "venue"
                    ],

                price=
                    job[
                        "salary_text"
                    ],


                # -----------------------------------------
                # APPLICATION
                # -----------------------------------------

                ticket_url=
                    application_url,


                # -----------------------------------------
                # DATE
                # -----------------------------------------

                start_date=
                    None,

                end_date=
                    closing_date,

                start_time=
                    None,

                end_time=
                    None,

                publish_from=
                    None,

                event_date=
                    None,

                event_end_date=
                    None,


                # -----------------------------------------
                # FREE JOBS
                # -----------------------------------------

                pricing_model=
                    None,

                commercial_duration_days=
                    None,

                amount_due=
                    None,

                payment_status=
                    "waived",

                distribution_zone_ids=
                    [],


                # -----------------------------------------
                # IMPORT IDENTITY
                # -----------------------------------------

                submitter_name=
                    "Kalxa External Jobs Import",

                submitter_email=
                    None,

                submitter_phone=
                    None,


                # -----------------------------------------
                # MODERATION
                # -----------------------------------------

                status=
                    "pending",

                admin_notes=
                    source_note,
            )
        )


        if not submission.tracking_code:

            submission.tracking_code = (
                uuid.uuid4()
                .hex[:12]
                .upper()
            )


        db.session.add(
            submission
        )


        created_count += 1


    # =====================================================
    # COMMIT BATCH
    # =====================================================

    try:

        db.session.commit()


    except Exception as exc:

        db.session.rollback()


        current_app.logger.exception(
            "[Kalxa Jobs Import] "
            "Unable to save imported jobs: %s",
            exc,
        )


        return jsonify({
            "success":
                False,

            "message":
                "Imported jobs could not be saved.",
        }), 500


    # =====================================================
    # RESULT
    # =====================================================

    current_app.logger.info(
        "[Kalxa Jobs Import] "
        "fetched=%s created=%s duplicates=%s "
        "invalid=%s expired=%s zone_id=%s",
        fetched_count,
        created_count,
        duplicate_count,
        invalid_count,
        expired_count,
        zone.id,
    )


    return jsonify({

        "success":
            True,

        "zone_id":
            zone.id,

        "zone_name":
            zone.name,

        "fetched":
            fetched_count,

        "created":
            created_count,

        "duplicates":
            duplicate_count,

        "invalid":
            invalid_count,

        "expired":
            expired_count,

        "message":
            (
                f"{created_count} external job(s) "
                "were added to Kalxa moderation."
            ),

    }), 200


@app.route("/jobs/submit", methods=["GET", "POST"])
def submit_job():
    zones = (
        Zone.query
        .filter_by(active=True)
        .order_by(Zone.name.asc())
        .all()
    )

    selected_zone_id = request.args.get("zone_id", type=int)
    selected_zone = None

    if selected_zone_id:
        selected_zone = db.session.get(Zone, selected_zone_id)
        if not selected_zone or not selected_zone.active:
            selected_zone_id = None
            selected_zone = None

    def render_job_form(status_code=200):
        return (
            render_template(
                "jobs_submit.html",
                zones=zones,
                job_types=KALXA_JOB_TYPES,
                selected_zone_id=selected_zone_id,
                selected_zone=selected_zone,
            ),
            status_code,
        )

    if request.method == "GET":
        return render_job_form()

    zone_id = request.form.get("zone_id", type=int)
    content_type = (request.form.get("job_type", "job") or "job").strip().lower()
    title = (request.form.get("title", "") or "").strip()
    business_name = (request.form.get("business_name", "") or "").strip()
    venue = (request.form.get("venue", "") or "").strip()
    description = (request.form.get("description", "") or "").strip()
    salary_text = (request.form.get("salary_text", "") or "").strip() or None
    closing_date_raw = (request.form.get("closing_date", "") or "").strip()
    application_url = (request.form.get("application_url", "") or "").strip()
    application_email = (request.form.get("application_email", "") or "").strip()
    contact = (request.form.get("contact", "") or "").strip() or None
    whatsapp_number = (request.form.get("whatsapp_number", "") or "").strip() or None
    submitter_name = (request.form.get("submitter_name", "") or "").strip()
    submitter_email = (request.form.get("submitter_email", "") or "").strip() or None
    submitter_phone = (request.form.get("submitter_phone", "") or "").strip() or None

    zone = db.session.get(Zone, zone_id) if zone_id else None
    if not zone or not zone.active:
        flash("Please select a valid area.", "error")
        return render_job_form(400)

    selected_zone_id = zone.id
    selected_zone = zone

    required_checks = (
        (title, "Please enter the job title."),
        (business_name, "Please enter the employer or organisation name."),
        (venue, "Please enter the job location."),
        (description, "Please describe the opportunity."),
        (submitter_name, "Please enter your name."),
    )

    for value, message in required_checks:
        if not value:
            flash(message, "error")
            return render_job_form(400)

    if content_type not in KALXA_JOB_TYPES:
        flash("Please select a valid opportunity type.", "error")
        return render_job_form(400)

    closing_date = None
    if closing_date_raw:
        try:
            closing_date = datetime.strptime(closing_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Please enter a valid closing date.", "error")
            return render_job_form(400)

        if closing_date < date.today():
            flash("The closing date cannot be in the past.", "error")
            return render_job_form(400)

    public_application_url = build_job_application_url(
        application_url=application_url,
        application_email=application_email,
        job_title=title,
    )

    if not public_application_url and not contact and not whatsapp_number:
        flash(
            "Please provide at least one way for people to apply: "
            "application link, email, phone or WhatsApp.",
            "error",
        )
        return render_job_form(400)

    duplicate = find_duplicate_job_submission(
        zone_id=zone.id,
        title=title,
        business_name=business_name,
        venue=venue,
        end_date=closing_date,
    )

    if duplicate:
        if duplicate["type"] == "pending":
            flash(
                "A very similar job opportunity is already waiting for Kalxa review.",
                "error",
            )
        else:
            flash(
                "A very similar job opportunity is already published on Kalxa.",
                "error",
            )
        return render_job_form(409)

    uploaded_image = request.files.get("image")
    if (
        uploaded_image
        and uploaded_image.filename
        and not allowed_image_file(uploaded_image.filename)
    ):
        flash("Job poster must be JPG, JPEG, PNG or WebP.", "error")
        return render_job_form(400)

    lifetime_type = "time_specific" if closing_date else "until_unavailable"

    submission = PendingSubmission(
        organizer_id=None,
        zone_id=zone.id,
        category="jobs",
        content_type=content_type,
        lifetime_type=lifetime_type,
        availability_status="available",
        notification_eligible=True,
        title=title,
        description=description,
        business_name=business_name,
        venue=venue,
        price=salary_text,
        contact=contact,
        whatsapp_number=whatsapp_number,
        ticket_url=public_application_url,
        start_date=None,
        end_date=closing_date,
        start_time=None,
        end_time=None,
        publish_from=None,
        event_date=None,
        event_end_date=None,
        pricing_model=None,
        commercial_duration_days=None,
        amount_due=None,
        payment_status="waived",
        distribution_zone_ids=[],
        submitter_name=submitter_name,
        submitter_email=submitter_email,
        submitter_phone=submitter_phone,
        status="pending",
    )

    if not submission.tracking_code:
        submission.tracking_code = uuid.uuid4().hex[:12].upper()

    try:
        db.session.add(submission)
        db.session.flush()

        if uploaded_image and uploaded_image.filename:
            image_url = upload_lac_image(uploaded_image, folder="lac/jobs")
            if image_url:
                submission.image_url = image_url
                db.session.add(
                    PendingSubmissionImage(
                        submission_id=submission.id,
                        image_url=image_url,
                        display_order=1,
                    )
                )

        db.session.commit()

    except Exception as error:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Jobs] Unable to create free job submission error=%s",
            error,
        )
        flash(
            "Kalxa could not submit this opportunity right now. Please try again.",
            "error",
        )
        return render_job_form(500)

    current_app.logger.info(
        "[Kalxa Jobs] Free job submitted submission_id=%s zone_id=%s type=%s",
        submission.id,
        submission.zone_id,
        submission.content_type,
    )

    return redirect(
        url_for(
            "job_submission_success",
            code=submission.tracking_code,
        )
    )


@app.route("/jobs/submit/success/<code>")
def job_submission_success(code):
    job_aliases = set(get_category_aliases("jobs") or [])
    job_aliases.add("jobs")

    submission = (
        PendingSubmission.query
        .filter(
            PendingSubmission.tracking_code == code,
            PendingSubmission.category.in_(job_aliases),
        )
        .first_or_404()
    )

    return render_template(
        "jobs_submit_success.html",
        submission=submission,
    )


# ============================================================
# ORGANIZER SESSION HELPERS
# ============================================================

def get_current_organizer():
    organizer_id = session.get("organizer_id")
    if not organizer_id:
        return None

    organizer = db.session.get(Organizer, organizer_id)

    if not organizer or not organizer.active:
        session.pop("organizer_id", None)
        return None

    return organizer


def require_organizer():
    organizer = get_current_organizer()
    if organizer:
        return None

    flash("Please sign in to your organizer account.", "error")
    return redirect(url_for("organizer_login"))


# ============================================================
# REMINDER HELPERS
# ============================================================

def recover_stale_content_reminders(stale_after_minutes=10):
    now_utc = datetime.utcnow()
    stale_before = now_utc - timedelta(minutes=stale_after_minutes)

    try:
        recovered_count = (
            ContentReminder.query
            .filter(
                ContentReminder.status == "processing",
                ContentReminder.processing_started_at.isnot(None),
                ContentReminder.processing_started_at <= stale_before,
            )
            .update(
                {
                    ContentReminder.status: "pending",
                    ContentReminder.processing_started_at: None,
                },
                synchronize_session=False,
            )
        )
        db.session.commit()

        if recovered_count:
            current_app.logger.warning(
                "[Kalxa Reminder] Recovered %s stale processing reminder(s).",
                recovered_count,
            )

        return recovered_count

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Reminder] Failed to recover stale reminders: %s",
            exc,
        )
        return 0


def get_content_reminder_retry_datetime(retry_count):
    delay_minutes = KALXA_REMINDER_RETRY_DELAYS.get(retry_count)
    if delay_minutes is None:
        return None

    return datetime.utcnow() + timedelta(minutes=delay_minutes)


def get_reminder_datetime(item, minutes_before=60):
    canonical_category = normalize_category(item.category)
    start_time_value = getattr(item, "start_time", None)

    if canonical_category == "events":
        reminder_date = (
            getattr(item, "event_date", None)
            or getattr(item, "start_date", None)
        )
        effective_time = start_time_value or dt_time(9, 0)

    elif canonical_category == "jobs":
        # Free Jobs normally have a closing date rather than a campaign start.
        # Treat that deadline as the reminder target.
        reminder_date = (
            getattr(item, "end_date", None)
            or getattr(item, "start_date", None)
        )
        effective_time = (
            getattr(item, "end_time", None)
            or dt_time(17, 0)
        )

    else:
        reminder_date = getattr(item, "start_date", None)
        effective_time = start_time_value or dt_time(9, 0)

    if not reminder_date:
        return None

    target_datetime = datetime.combine(
        reminder_date,
        effective_time,
    ).replace(tzinfo=KALXA_TIMEZONE)

    return target_datetime - timedelta(minutes=minutes_before)


def send_due_content_reminders(limit=100):
    recovered_count = recover_stale_content_reminders(stale_after_minutes=10)
    now_utc = datetime.utcnow()

    processed_count = 0
    sent_count = 0
    failed_count = 0
    cancelled_count = 0
    retried_count = 0
    skipped_claimed_count = 0

    candidate_rows = (
        db.session.query(ContentReminder.id)
        .filter(
            ContentReminder.status == "pending",
            ContentReminder.scheduled_for.isnot(None),
            ContentReminder.scheduled_for <= now_utc,
            db.or_(
                ContentReminder.next_retry_at.is_(None),
                ContentReminder.next_retry_at <= now_utc,
            ),
        )
        .order_by(
            ContentReminder.scheduled_for.asc(),
            ContentReminder.id.asc(),
        )
        .limit(limit)
        .all()
    )

    candidate_ids = [row[0] for row in candidate_rows]

    if not candidate_ids:
        return {
            "processed": 0,
            "sent": 0,
            "failed": 0,
            "cancelled": 0,
            "retried": 0,
            "skipped_claimed": 0,
            "recovered_stale": recovered_count,
        }

    for reminder_id in candidate_ids:
        claim_time = datetime.utcnow()

        try:
            claimed_rows = (
                ContentReminder.query
                .filter(
                    ContentReminder.id == reminder_id,
                    ContentReminder.status == "pending",
                    ContentReminder.scheduled_for.isnot(None),
                    ContentReminder.scheduled_for <= now_utc,
                    db.or_(
                        ContentReminder.next_retry_at.is_(None),
                        ContentReminder.next_retry_at <= now_utc,
                    ),
                )
                .update(
                    {
                        ContentReminder.status: "processing",
                        ContentReminder.processing_started_at: claim_time,
                        ContentReminder.last_attempt_at: claim_time,
                    },
                    synchronize_session=False,
                )
            )
            db.session.commit()

        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception(
                "[Kalxa Reminder] Unable to claim reminder reminder_id=%s error=%s",
                reminder_id,
                exc,
            )
            failed_count += 1
            continue

        if claimed_rows != 1:
            skipped_claimed_count += 1
            continue

        processed_count += 1
        reminder = db.session.get(ContentReminder, reminder_id)

        if not reminder:
            cancelled_count += 1
            continue

        item = reminder.content_item
        if not item or not item.active:
            reminder.status = "cancelled"
            reminder.processing_started_at = None
            reminder.next_retry_at = None
            reminder.last_error = "Content item is unavailable."
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            cancelled_count += 1
            continue

        subscriber = reminder.push_subscriber
        if not subscriber or not subscriber.active:
            reminder.status = "cancelled"
            reminder.processing_started_at = None
            reminder.next_retry_at = None
            reminder.last_error = "Push subscriber is unavailable."
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            cancelled_count += 1
            continue

        canonical_category = normalize_category(item.category)
        notification_title = "🔔 Kalxa Reminder"

        if canonical_category == "jobs":
            notification_body = f"{item.title} is closing soon."
        elif canonical_category == "events":
            notification_body = f"{item.title} is coming up soon."
        elif canonical_category == "retail_specials":
            notification_body = f"{item.title} is starting soon."
        else:
            notification_body = f"{item.title} is coming up soon."

        target_url = f"{get_public_base_url()}/listing/{item.id}"
        send_error = None

        try:
            sent = send_push_notification(
                subscriber=subscriber,
                title=notification_title,
                body=notification_body,
                url=target_url,
                tag=f"kalxa-reminder-{reminder.id}",
            )
            if not sent:
                send_error = "Push delivery returned False."
        except Exception as exc:
            sent = False
            send_error = str(exc)
            current_app.logger.exception(
                "[Kalxa Reminder] Unexpected push exception reminder_id=%s error=%s",
                reminder.id,
                exc,
            )

        if sent:
            reminder.status = "sent"
            reminder.sent_at = datetime.utcnow()
            reminder.processing_started_at = None
            reminder.next_retry_at = None
            reminder.last_error = None

            try:
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception(
                    "[Kalxa Reminder] Push sent but DB update failed reminder_id=%s error=%s",
                    reminder.id,
                    exc,
                )
                failed_count += 1
                continue

            sent_count += 1
            continue

        reminder.retry_count = (reminder.retry_count or 0) + 1
        reminder.processing_started_at = None
        reminder.last_error = send_error or "Push delivery failed."

        retry_datetime = get_content_reminder_retry_datetime(reminder.retry_count)

        if (
            reminder.retry_count <= KALXA_REMINDER_MAX_RETRIES
            and retry_datetime
        ):
            reminder.status = "pending"
            reminder.next_retry_at = retry_datetime

            try:
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                current_app.logger.exception(
                    "[Kalxa Reminder] Unable to schedule retry reminder_id=%s error=%s",
                    reminder.id,
                    exc,
                )
                failed_count += 1
                continue

            retried_count += 1
            continue

        reminder.status = "failed"
        reminder.next_retry_at = None

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception(
                "[Kalxa Reminder] Unable to mark reminder failed reminder_id=%s error=%s",
                reminder.id,
                exc,
            )

        failed_count += 1

    return {
        "processed": processed_count,
        "sent": sent_count,
        "failed": failed_count,
        "cancelled": cancelled_count,
        "retried": retried_count,
        "skipped_claimed": skipped_claimed_count,
        "recovered_stale": recovered_count,
    }


@app.route("/internal/run-content-reminders", methods=["POST"])
def run_content_reminders():
    expected_token = os.environ.get("REMINDER_CRON_TOKEN", "").strip()

    if not expected_token:
        current_app.logger.error("REMINDER_CRON_TOKEN is not configured.")
        return {
            "success": False,
            "message": "Reminder scheduler is not configured.",
        }, 503

    authorization = request.headers.get("Authorization", "").strip()
    if not authorization.startswith("Bearer "):
        return {"success": False, "message": "Unauthorized."}, 401

    supplied_token = authorization[len("Bearer "):].strip()

    if (
        not supplied_token
        or not secrets.compare_digest(supplied_token, expected_token)
    ):
        return {"success": False, "message": "Unauthorized."}, 401

    try:
        result = send_due_content_reminders(limit=100)
        return {
            "success": True,
            **result,
        }, 200

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Reminder Scheduler] Scheduler execution failed: %s",
            exc,
        )
        return {
            "success": False,
            "message": "Reminder processing failed.",
        }, 500


@app.route("/listing/<int:item_id>/remind", methods=["POST"])
def create_content_reminder(item_id):
    item = (
        ContentItem.query
        .filter_by(id=item_id, active=True, archived=False)
        .first_or_404()
    )

    try:
        minutes_before = int(request.form.get("minutes_before", 60))
    except (TypeError, ValueError):
        minutes_before = 60

    if minutes_before not in {60, 180, 1440}:
        return jsonify({
            "success": False,
            "message": "Invalid reminder time.",
        }), 400

    scheduled_for = get_reminder_datetime(
        item,
        minutes_before=minutes_before,
    )

    if not scheduled_for:
        return jsonify({
            "success": False,
            "message": "This listing does not have enough timing information for a reminder.",
        }), 400

    now_local = datetime.now(KALXA_TIMEZONE)
    if scheduled_for <= now_local:
        return jsonify({
            "success": False,
            "message": "That reminder time has already passed. Choose a shorter reminder.",
        }), 400

    push_endpoint = (request.form.get("push_endpoint", "") or "").strip()
    if not push_endpoint:
        return jsonify({
            "success": False,
            "requires_push": True,
            "message": "Please enable Kalxa notifications first.",
        }), 400

    push_subscriber = (
        PushSubscriber.query
        .filter_by(endpoint=push_endpoint, active=True)
        .first()
    )

    if not push_subscriber:
        return jsonify({
            "success": False,
            "requires_push": True,
            "message": "Your Kalxa notification subscription could not be found. Please enable notifications again.",
        }), 400

    zone_id = request.form.get("zone_id", type=int)
    access_point_id = request.form.get("access_point_id", type=int)

    existing = (
        ContentReminder.query
        .filter_by(
            content_item_id=item.id,
            push_subscriber_id=push_subscriber.id,
            reminder_minutes_before=minutes_before,
            status="pending",
        )
        .first()
    )

    if existing:
        return jsonify({
            "success": True,
            "already_exists": True,
            "reminder_id": existing.id,
            "minutes_before": existing.reminder_minutes_before,
            "scheduled_for": (
                existing.scheduled_for.isoformat()
                if existing.scheduled_for
                else None
            ),
            "message": "Reminder already saved.",
        }), 200

    existing_reminders = (
        ContentReminder.query
        .filter_by(
            content_item_id=item.id,
            push_subscriber_id=push_subscriber.id,
            status="pending",
        )
        .all()
    )

    for old_reminder in existing_reminders:
        old_reminder.status = "cancelled"

    scheduled_for_utc = (
        scheduled_for
        .astimezone(UTC_TIMEZONE)
        .replace(tzinfo=None)
    )

    canonical_category = normalize_category(item.category)
    reminder_type = (
        "before_deadline"
        if canonical_category == "jobs"
        else "before_start"
    )

    reminder = ContentReminder(
        content_item_id=item.id,
        zone_id=zone_id or push_subscriber.zone_id,
        access_point_id=access_point_id,
        push_subscriber_id=push_subscriber.id,
        reminder_type=reminder_type,
        reminder_minutes_before=minutes_before,
        scheduled_for=scheduled_for_utc,
        status="pending",
    )

    try:
        db.session.add(reminder)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Reminder] Unable to create reminder content_item_id=%s subscriber_id=%s error=%s",
            item.id,
            push_subscriber.id,
            exc,
        )
        return jsonify({
            "success": False,
            "message": "Could not save reminder.",
        }), 500

    try:
        db.session.add(
            EngagementEvent(
                event_type="reminder_created",
                zone_id=zone_id or push_subscriber.zone_id,
                access_point_id=access_point_id,
                content_item_id=item.id,
                category=canonical_category,
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning(
            "[Kalxa Reminder] Reminder saved but analytics failed reminder_id=%s error=%s",
            reminder.id,
            exc,
        )

    return jsonify({
        "success": True,
        "already_exists": False,
        "reminder_id": reminder.id,
        "minutes_before": reminder.reminder_minutes_before,
        "scheduled_for": reminder.scheduled_for.isoformat(),
        "message": "Reminder saved.",
    }), 201


@app.route("/listing/<int:item_id>/reminder-status", methods=["POST"])
def content_reminder_status(item_id):
    data = request.get_json(silent=True) or {}
    push_endpoint = str(data.get("push_endpoint", "") or "").strip()

    if not push_endpoint:
        return jsonify({"success": True, "has_reminder": False}), 200

    subscriber = (
        PushSubscriber.query
        .filter_by(endpoint=push_endpoint, active=True)
        .first()
    )

    if not subscriber:
        return jsonify({"success": True, "has_reminder": False}), 200

    reminder = (
        ContentReminder.query
        .filter_by(
            content_item_id=item_id,
            push_subscriber_id=subscriber.id,
            status="pending",
        )
        .order_by(ContentReminder.created_at.desc())
        .first()
    )

    if not reminder:
        return jsonify({"success": True, "has_reminder": False}), 200

    return jsonify({
        "success": True,
        "has_reminder": True,
        "reminder_id": reminder.id,
        "minutes_before": reminder.reminder_minutes_before,
        "scheduled_for": (
            reminder.scheduled_for.isoformat()
            if reminder.scheduled_for
            else None
        ),
    }), 200


@app.route("/reminders/<int:reminder_id>/cancel", methods=["POST"])
def cancel_content_reminder(reminder_id):
    data = request.get_json(silent=True) or {}
    push_endpoint = str(data.get("push_endpoint", "") or "").strip()

    if not push_endpoint:
        return jsonify({
            "success": False,
            "message": "Notification subscription missing.",
        }), 400

    subscriber = (
        PushSubscriber.query
        .filter_by(endpoint=push_endpoint, active=True)
        .first()
    )

    if not subscriber:
        return jsonify({
            "success": False,
            "message": "Notification subscription not found.",
        }), 404

    reminder = (
        ContentReminder.query
        .filter_by(
            id=reminder_id,
            push_subscriber_id=subscriber.id,
            status="pending",
        )
        .first()
    )

    if not reminder:
        return jsonify({
            "success": False,
            "message": "Active reminder not found.",
        }), 404

    reminder.status = "cancelled"

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Reminder] Unable to cancel reminder reminder_id=%s error=%s",
            reminder_id,
            exc,
        )
        return jsonify({
            "success": False,
            "message": "Could not cancel reminder.",
        }), 500

    return jsonify({
        "success": True,
        "message": "Reminder cancelled.",
    }), 200


# ============================================================
# CATEGORY HELPERS
# ============================================================

def get_active_categories():
    return (
        Category.query
        .filter_by(active=True)
        .order_by(Category.display_order, Category.name)
        .all()
    )


def get_active_category_by_slug(slug):
    return (
        Category.query
        .filter_by(slug=slug, active=True)
        .first()
    )


# ============================================================
# CONTENT WORKFLOW CONFIGURATION
# ============================================================

CONTENT_WORKFLOWS = {
    "property": {
        "room": {"lifetime_type": "until_unavailable", "notification_eligible": True},
        "rental": {"lifetime_type": "until_unavailable", "notification_eligible": True},
        "property_sale": {"lifetime_type": "until_unavailable", "notification_eligible": True},
        "hotel_lodge": {"lifetime_type": "ongoing", "notification_eligible": False},
        "accommodation_special": {"lifetime_type": "time_specific", "notification_eligible": True},
    },
    "events": {
        "event": {"lifetime_type": "time_specific", "notification_eligible": True},
        "entertainment": {"lifetime_type": "time_specific", "notification_eligible": True},
        "church_event": {"lifetime_type": "time_specific", "notification_eligible": True},
        "sports_event": {"lifetime_type": "time_specific", "notification_eligible": True},
        "community_event": {"lifetime_type": "time_specific", "notification_eligible": True},
        "business_event": {"lifetime_type": "time_specific", "notification_eligible": True},
    },
    "discount-deals": {
        "grocery_special": {"lifetime_type": "time_specific", "notification_eligible": True},
        "product_discount": {"lifetime_type": "time_specific", "notification_eligible": True},
        "weekend_special": {"lifetime_type": "time_specific", "notification_eligible": True},
        "clearance": {"lifetime_type": "time_specific", "notification_eligible": True},
    },
    "local-restaurants": {
        "restaurant": {"lifetime_type": "ongoing", "notification_eligible": False},
        "takeaway": {"lifetime_type": "ongoing", "notification_eligible": False},
        "daily_special": {"lifetime_type": "time_specific", "notification_eligible": True},
        "weekend_special": {"lifetime_type": "time_specific", "notification_eligible": True},
        "food_deal": {"lifetime_type": "time_specific", "notification_eligible": True},
    },
    "jobs": {
        "job": {"lifetime_type": "until_unavailable", "notification_eligible": True},
        "learnership": {"lifetime_type": "time_specific", "notification_eligible": True},
        "internship": {"lifetime_type": "time_specific", "notification_eligible": True},
        "training": {"lifetime_type": "time_specific", "notification_eligible": True},
        "tender": {"lifetime_type": "time_specific", "notification_eligible": True},
        "business_opportunity": {"lifetime_type": "time_specific", "notification_eligible": True},
    },
    "opportunities": {
        "job": {"lifetime_type": "until_unavailable", "notification_eligible": True},
        "learnership": {"lifetime_type": "time_specific", "notification_eligible": True},
        "internship": {"lifetime_type": "time_specific", "notification_eligible": True},
        "training": {"lifetime_type": "time_specific", "notification_eligible": True},
        "tender": {"lifetime_type": "time_specific", "notification_eligible": True},
        "business_opportunity": {"lifetime_type": "time_specific", "notification_eligible": True},
    },
    "services": {
        "service_provider": {"lifetime_type": "ongoing", "notification_eligible": False},
        "plumber": {"lifetime_type": "ongoing", "notification_eligible": False},
        "mechanic": {"lifetime_type": "ongoing", "notification_eligible": False},
        "electrician": {"lifetime_type": "ongoing", "notification_eligible": False},
        "builder": {"lifetime_type": "ongoing", "notification_eligible": False},
        "cleaning_service": {"lifetime_type": "ongoing", "notification_eligible": False},
    },
    "beauty-salon": {
        "salon": {"lifetime_type": "ongoing", "notification_eligible": False},
        "barber": {"lifetime_type": "ongoing", "notification_eligible": False},
        "beauty_service": {"lifetime_type": "ongoing", "notification_eligible": False},
        "beauty_special": {"lifetime_type": "time_specific", "notification_eligible": True},
    },
}


def get_content_workflow(category, content_type):
    raw_category = str(category or "").strip().lower()
    content_type = str(content_type or "").strip().lower()
    canonical_category = normalize_category(raw_category)

    lookup_candidates = [raw_category]
    if canonical_category and canonical_category not in lookup_candidates:
        lookup_candidates.append(canonical_category)

    workflow = None
    for key in lookup_candidates:
        category_workflows = CONTENT_WORKFLOWS.get(key, {})
        workflow = category_workflows.get(content_type)
        if workflow:
            workflow = dict(workflow)
            break

    if workflow is None:
        if canonical_category == "jobs":
            workflow = {
                "lifetime_type": (
                    "until_unavailable"
                    if content_type == "job"
                    else "time_specific"
                ),
                "notification_eligible": True,
            }
        elif canonical_category == "events":
            workflow = {
                "lifetime_type": "time_specific",
                "notification_eligible": True,
            }
        elif canonical_category in {
            "restaurants",
            "beauty",
            "accommodation",
            "delivery",
            "services",
            "building",
            "transport",
        }:
            workflow = {
                "lifetime_type": "ongoing",
                "notification_eligible": True,
            }
        elif canonical_category in {"rentals", "property"}:
            workflow = {
                "lifetime_type": "until_unavailable",
                "notification_eligible": True,
            }
        else:
            workflow = {
                "lifetime_type": "time_specific",
                "notification_eligible": True,
            }

    # Jobs are a free community utility and never enter Yoco/commercial pricing.
    if canonical_category == "jobs":
        workflow["pricing_model"] = None
    else:
        workflow["pricing_model"] = get_pricing_model(
            raw_category,
            content_type,
        )

    return workflow


def get_legacy_lifetime_type(item):
    lifetime_type = getattr(item, "lifetime_type", None)
    if lifetime_type:
        return lifetime_type

    if normalize_category(getattr(item, "category", None)) == "events":
        return "time_specific"

    if getattr(item, "end_date", None):
        return "time_specific"

    return "ongoing"


# ============================================================
# ACTIVE CONTENT
# ============================================================

def get_active_content(zone_id, category_slug):
    today = date.today()
    now = datetime.utcnow()

    requested_category = str(category_slug or "").strip().lower()
    if not requested_category:
        return []

    canonical_category = normalize_category(requested_category)
    if not canonical_category:
        return []

    category_aliases = set(get_category_aliases(canonical_category) or [])
    category_aliases.update({requested_category, canonical_category})
    category_aliases.discard(None)
    category_aliases.discard("")

    if not category_aliases:
        return []

    featured_priority = db.case(
        (ContentItem.featured.is_(True), 1),
        else_=0,
    )

    zone_visibility = db.or_(
        ContentItem.zone_id == zone_id,
        db.and_(
            ContentItem.pricing_model == PRICING_MODEL_CAMPAIGN,
            db.exists().where(
                db.and_(
                    ContentDistributionZone.content_item_id == ContentItem.id,
                    ContentDistributionZone.zone_id == zone_id,
                )
            ),
        ),
    )

    legacy_admin_commercial_content = db.and_(
        ContentItem.pricing_model.in_(
            (PRICING_MODEL_PRESENCE, PRICING_MODEL_CAMPAIGN)
        ),
        ContentItem.amount_due.is_(None),
        ContentItem.amount_paid.is_(None),
        ContentItem.paid_at.is_(None),
        ContentItem.commercial_starts_at.is_(None),
        ContentItem.commercial_expires_at.is_(None),
    )

    paid_commercial_content = db.and_(
        ContentItem.pricing_model.in_(
            (PRICING_MODEL_PRESENCE, PRICING_MODEL_CAMPAIGN)
        ),
        ContentItem.payment_status == "paid",
        ContentItem.commercial_starts_at.is_not(None),
        ContentItem.commercial_expires_at.is_not(None),
        ContentItem.commercial_starts_at <= now,
        ContentItem.commercial_expires_at >= now,
    )

    commercial_visibility = db.or_(
        ContentItem.pricing_model.is_(None),
        ContentItem.payment_status == "waived",
        legacy_admin_commercial_content,
        paid_commercial_content,
    )

    query = ContentItem.query.filter(
        ContentItem.active.is_(True),
        ContentItem.archived.is_(False),
        ContentItem.category.in_(category_aliases),
        zone_visibility,
        commercial_visibility,
    )

    urgency_priority = {
        "live": 0,
        "ending_soon": 1,
        "tonight": 2,
        "ends_today": 3,
        "today": 4,
        "tomorrow": 5,
        "upcoming": 6,
        "active": 7,
        "undated": 8,
        "ended": 9,
    }

    def campaign_sort_key(item):
        campaign_state = getattr(item, "campaign_state", None) or {}
        state = campaign_state.get("state")

        live_rank = 0 if state == "live" else 1
        sponsorship_active = bool(getattr(item, "sponsorship_active", False))
        sponsored_rank = 0 if sponsorship_active else 1

        if sponsorship_active:
            try:
                sponsored_priority_rank = -int(
                    getattr(
                        item,
                        "effective_sponsored_priority",
                        getattr(item, "sponsored_priority", 0),
                    )
                    or 0
                )
            except (TypeError, ValueError):
                sponsored_priority_rank = 0
        else:
            sponsored_priority_rank = 0

        featured_rank = 0 if getattr(item, "featured", False) else 1
        urgency_rank = urgency_priority.get(state, 8)

        days_to_go = campaign_state.get("days_to_go")
        try:
            days_rank = int(days_to_go) if days_to_go is not None else 999999
        except (TypeError, ValueError):
            days_rank = 999999

        if normalize_category(item.category) == "events":
            relevant_date = (
                getattr(item, "event_date", None)
                or getattr(item, "start_date", None)
            )
        elif normalize_category(item.category) == "jobs":
            relevant_date = (
                getattr(item, "end_date", None)
                or getattr(item, "start_date", None)
            )
        else:
            relevant_date = getattr(item, "start_date", None)

        date_rank = relevant_date.toordinal() if relevant_date else 999999999

        created_at = getattr(item, "created_at", None)
        if created_at:
            try:
                created_rank = -created_at.timestamp()
            except (ValueError, OSError):
                created_rank = 0
        else:
            created_rank = 0

        return (
            live_rank,
            sponsored_rank,
            sponsored_priority_rank,
            featured_rank,
            urgency_rank,
            days_rank,
            date_rank,
            created_rank,
        )

    if canonical_category == "events":
        event_natural_visibility = db.or_(
            db.and_(
                ContentItem.event_end_date.is_not(None),
                ContentItem.event_end_date >= today,
            ),
            db.and_(
                ContentItem.event_end_date.is_(None),
                ContentItem.end_date.is_not(None),
                ContentItem.end_date >= today,
            ),
            db.and_(
                ContentItem.event_end_date.is_(None),
                ContentItem.end_date.is_(None),
                ContentItem.event_date.is_not(None),
                ContentItem.event_date >= today,
            ),
            db.and_(
                ContentItem.event_end_date.is_(None),
                ContentItem.end_date.is_(None),
                ContentItem.event_date.is_(None),
            ),
        )

        query = query.filter(event_natural_visibility)

        items = (
            query
            .order_by(
                featured_priority.desc(),
                ContentItem.event_date.asc().nullslast(),
                ContentItem.created_at.desc(),
            )
            .all()
        )

    else:
        natural_content_visibility = db.or_(
            db.and_(
                ContentItem.pricing_model == PRICING_MODEL_CAMPAIGN,
                db.or_(
                    ContentItem.end_date.is_(None),
                    ContentItem.end_date >= today,
                ),
            ),
            db.and_(
                db.or_(
                    ContentItem.pricing_model.is_(None),
                    ContentItem.pricing_model != PRICING_MODEL_CAMPAIGN,
                ),
                db.or_(
                    ContentItem.start_date.is_(None),
                    ContentItem.start_date <= today,
                ),
                db.or_(
                    ContentItem.end_date.is_(None),
                    ContentItem.end_date >= today,
                ),
            ),
        )

        query = query.filter(natural_content_visibility)

        items = (
            query
            .order_by(
                featured_priority.desc(),
                ContentItem.start_date.asc().nullslast(),
                ContentItem.created_at.desc(),
            )
            .all()
        )

    attach_campaign_states(items)
    attach_sponsorship_states(items)
    items.sort(key=campaign_sort_key)
    return items


# ============================================================
# QR ACCESS
# ============================================================

@app.route("/q/<access_code>")
def qr_access(access_code):
    access_point = (
        AccessPoint.query
        .filter_by(code=access_code, active=True)
        .first_or_404()
    )

    zone = access_point.zone

    try:
        db.session.add(
            QRScan(
                access_point_id=access_point.id,
                event_type="scan",
                user_agent=request.headers.get("User-Agent", ""),
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.exception(
            "Failed to record QR scan for %s: %s",
            access_point.code,
            exc,
        )

    if access_point.default_category:
        category_slug = str(access_point.default_category or "").strip().lower()
        category_record = get_category_by_slug(category_slug, active_only=True)
        if not category_record:
            abort(404)

        return redirect(
            url_for(
                "qr_category",
                code=access_point.code,
                category=category_slug,
            )
        )

    categories = get_active_categories()
    today = date.today()

    zone_category_appearances = (
        ZoneCategoryAppearance.query
        .filter_by(zone_id=zone.id)
        .all()
    )

    appearance_lookup = {
        appearance.category_id: appearance
        for appearance in zone_category_appearances
    }

    category_background_images = {}

    for category in categories:
        appearance = appearance_lookup.get(category.id)

        image_1 = (
            appearance.image_url
            if appearance and appearance.image_url
            else category.image_url
        )
        image_2 = (
            appearance.image_url_2
            if appearance and appearance.image_url_2
            else category.image_url_2
        )
        image_3 = (
            appearance.image_url_3
            if appearance and appearance.image_url_3
            else category.image_url_3
        )

        category_background_images[category.id] = [
            image
            for image in (image_1, image_2, image_3)
            if image
        ]

    category_lookup = {}
    consumer_category_lookup = {}
    consumer_categories = []
    seen_consumer_category_keys = set()

    for category in categories:
        category_lookup[category.slug] = category
        canonical_key = normalize_category(category.slug)

        if canonical_key not in category_lookup:
            category_lookup[canonical_key] = category

        presentation = get_consumer_category(canonical_key)
        presentation_data = {
            "key": canonical_key,
            "title": presentation["title"],
            "subtitle": presentation["subtitle"],
            "icon": presentation["icon"],
        }

        consumer_category_lookup[category.slug] = presentation_data
        consumer_category_lookup.setdefault(canonical_key, presentation_data)

        if canonical_key in seen_consumer_category_keys:
            continue

        seen_consumer_category_keys.add(canonical_key)
        consumer_categories.append(
            {
                "key": canonical_key,
                "category": category,
                "title": presentation["title"],
                "subtitle": presentation["subtitle"],
                "icon": presentation["icon"],
                "url": url_for(
                    "qr_category",
                    code=access_point.code,
                    category=canonical_key,
                ),
            }
        )

    category_stats = {}
    new_cutoff = datetime.utcnow() - timedelta(days=7)

    for consumer_category in consumer_categories:
        canonical_key = consumer_category["key"]
        representative_category = consumer_category["category"]

        try:
            active_items = get_active_content(
                zone_id=zone.id,
                category_slug=canonical_key,
            )
        except Exception as exc:
            app.logger.exception(
                "Unable to calculate category stats zone=%s category=%s error=%s",
                zone.id,
                canonical_key,
                exc,
            )
            active_items = []

        unique_active_items = []
        seen_active_item_ids = set()

        for item in active_items:
            if item.id in seen_active_item_ids:
                continue
            seen_active_item_ids.add(item.id)
            unique_active_items.append(item)

        item_count = len(unique_active_items)
        new_count = sum(
            1
            for item in unique_active_items
            if getattr(item, "created_at", None)
            and item.created_at >= new_cutoff
        )

        badge_map = {
            "events": ("📅", "UPCOMING"),
            "rentals": ("🏠", "AVAILABLE"),
            "accommodation": ("🛏️", "AVAILABLE"),
            "jobs": ("💼", "OPEN"),
            "emergency": ("🚨", "INFO"),
            "announcements": ("📢", "UPDATE"),
        }
        badge_icon, badge_label = badge_map.get(
            canonical_key,
            ("🔥", "LIVE"),
        )

        stats_data = {
            "count": item_count,
            "new_count": new_count,
            "label": badge_label,
            "icon": badge_icon,
            "canonical_key": canonical_key,
        }

        category_stats[canonical_key] = stats_data
        category_stats[representative_category.slug] = stats_data

    new_items_pool = []
    seen_new_item_ids = set()

    for consumer_category in consumer_categories:
        try:
            category_items = get_active_content(
                zone_id=zone.id,
                category_slug=consumer_category["key"],
            )
        except Exception as exc:
            app.logger.exception(
                "Unable to load New Near You items zone=%s category=%s error=%s",
                zone.id,
                consumer_category["key"],
                exc,
            )
            continue

        for item in category_items:
            if item.id in seen_new_item_ids:
                continue
            seen_new_item_ids.add(item.id)
            new_items_pool.append(item)

    new_items_pool.sort(
        key=lambda item: item.created_at or datetime.min,
        reverse=True,
    )
    new_items = new_items_pool[:8]

    featured_items_pool = []
    seen_featured_item_ids = set()

    for consumer_category in consumer_categories:
        try:
            category_items = get_active_content(
                zone_id=zone.id,
                category_slug=consumer_category["key"],
            )
        except Exception as exc:
            app.logger.exception(
                "Unable to load featured items zone=%s category=%s error=%s",
                zone.id,
                consumer_category["key"],
                exc,
            )
            continue

        for item in category_items:
            if not item.featured or item.id in seen_featured_item_ids:
                continue
            seen_featured_item_ids.add(item.id)
            featured_items_pool.append(item)

    featured_items_pool.sort(
        key=lambda item: item.created_at or datetime.min,
        reverse=True,
    )
    featured_items = featured_items_pool[:6]

    return render_template(
        "access.html",
        zone=zone,
        access_point=access_point,
        categories=categories,
        consumer_categories=consumer_categories,
        consumer_category_lookup=consumer_category_lookup,
        category_stats=category_stats,
        new_items=new_items,
        category_lookup=category_lookup,
        featured_items=featured_items,
        category_background_images=category_background_images,
        today=today,
    )


@app.route("/q/<code>/<category>")
def qr_category(code, category):
    access_point = (
        AccessPoint.query
        .filter_by(code=code, active=True)
        .first_or_404()
    )
    zone = access_point.zone

    category_slug = str(category or "").strip().lower()
    if not category_slug:
        abort(404)

    canonical_category = normalize_category(category_slug)
    if not canonical_category:
        abort(404)

    category_record = get_category_by_slug(
        category_slug,
        active_only=True,
    )
    if not category_record:
        abort(404)

    consumer_category = get_consumer_category(canonical_category)
    items = get_active_content(
        zone_id=access_point.zone_id,
        category_slug=category_slug,
    )

    # get_active_content() already attaches both states, but these calls are
    # intentionally idempotent and protect template callers if that changes.
    attach_campaign_states(items)
    attach_sponsorship_states(items)

    try:
        db.session.add(
            QRScan(
                access_point_id=access_point.id,
                event_type="category_view",
                category_selected=category_slug,
                user_agent=request.headers.get("User-Agent", ""),
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.exception(
            "Failed to record category view access_point=%s category=%s canonical=%s error=%s",
            access_point.code,
            category_slug,
            canonical_category,
            exc,
        )

    return render_template(
        "category.html",
        zone=zone,
        category=category_record,
        canonical_category=canonical_category,
        consumer_category=consumer_category,
        items=items,
        access_point=access_point,
        today=date.today(),
    )


# ============================================================
# ENGAGEMENT ANALYTICS
# ============================================================

@app.route("/api/engagement", methods=["POST"])
def record_engagement():
    data = request.get_json(silent=True) or {}
    event_type = (data.get("event_type") or "").strip()

    if event_type not in ENGAGEMENT_EVENT_TYPES:
        return jsonify({
            "ok": False,
            "error": "Invalid event type.",
        }), 400

    zone_id = data.get("zone_id")
    access_point_id = data.get("access_point_id")
    content_item_id = data.get("content_item_id")
    category = data.get("category") or None

    try:
        db.session.add(
            EngagementEvent(
                event_type=event_type,
                zone_id=zone_id,
                access_point_id=access_point_id,
                content_item_id=content_item_id,
                category=category,
            )
        )
        db.session.commit()
        return jsonify({"ok": True}), 201

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Engagement] Unable to record event type=%s error=%s",
            event_type,
            exc,
        )
        return jsonify({"ok": False}), 500


# ============================================================
# PUSH SUBSCRIPTIONS
# ============================================================

@app.route("/push/public-key", methods=["GET"])
def push_public_key():
    if not VAPID_PUBLIC_KEY:
        return jsonify({
            "success": False,
            "error": "VAPID public key is not configured.",
        }), 500

    return jsonify({
        "success": True,
        "public_key": VAPID_PUBLIC_KEY,
    }), 200


@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json(silent=True) or {}
    zone_id = data.get("zone_id")
    subscription = data.get("subscription") or {}
    categories = data.get("categories") or []

    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth_key = keys.get("auth")

    try:
        zone_id = int(zone_id)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "A valid zone is required.",
        }), 400

    zone = db.session.get(Zone, zone_id)
    if not zone or not zone.active:
        return jsonify({
            "ok": False,
            "error": "The selected zone is unavailable.",
        }), 400

    if not endpoint:
        return jsonify({"ok": False, "error": "Push endpoint is required."}), 400

    if not p256dh or not auth_key:
        return jsonify({
            "ok": False,
            "error": "Push subscription keys are missing.",
        }), 400

    if not isinstance(categories, list):
        return jsonify({"ok": False, "error": "Categories must be a list."}), 400

    requested_categories = []
    for category_slug in categories:
        if not isinstance(category_slug, str):
            continue
        category_slug = category_slug.strip().lower()
        if category_slug and category_slug not in requested_categories:
            requested_categories.append(category_slug)

    if not requested_categories:
        return jsonify({
            "ok": False,
            "error": "Choose at least one notification category.",
        }), 400

    active_categories = Category.query.filter(Category.active.is_(True)).all()
    category_by_slug = {record.slug: record for record in active_categories}

    cleaned_categories = []

    for requested in requested_categories:
        # Exact legacy/public slug.
        if requested in category_by_slug:
            resolved = requested
        else:
            canonical = normalize_category(requested)
            record = get_category_by_slug(canonical, active_only=True)
            resolved = record.slug if record else None

        if resolved and resolved not in cleaned_categories:
            cleaned_categories.append(resolved)

    if not cleaned_categories:
        return jsonify({
            "ok": False,
            "error": "No valid notification categories were selected.",
        }), 400

    try:
        subscriber = PushSubscriber.query.filter_by(endpoint=endpoint).first()

        if subscriber is None:
            subscriber = PushSubscriber(
                zone_id=zone.id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth_key=auth_key,
                active=True,
            )
            db.session.add(subscriber)
            db.session.flush()
        else:
            subscriber.zone_id = zone.id
            subscriber.p256dh = p256dh
            subscriber.auth_key = auth_key
            subscriber.active = True

        PushSubscriberPreference.query.filter_by(
            subscriber_id=subscriber.id
        ).delete(synchronize_session=False)

        for category_slug in cleaned_categories:
            db.session.add(
                PushSubscriberPreference(
                    subscriber_id=subscriber.id,
                    category=category_slug,
                )
            )

        db.session.commit()

        return jsonify({
            "ok": True,
            "subscriber_id": subscriber.id,
            "zone_id": subscriber.zone_id,
            "categories": cleaned_categories,
        }), 200

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Push] Unable to save subscription preferences zone_id=%s error=%s",
            zone_id,
            exc,
        )
        return jsonify({
            "ok": False,
            "error": "Unable to save notification preferences.",
        }), 500


@app.route("/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    data = request.get_json(silent=True)

    if data is None:
        try:
            data = json.loads(request.get_data(as_text=True))
        except Exception:
            data = {}

    endpoint = data.get("endpoint")
    if not endpoint:
        return jsonify({
            "success": False,
            "error": "Endpoint is required",
        }), 400

    subscriber = PushSubscriber.query.filter_by(endpoint=endpoint).first()
    if subscriber:
        subscriber.active = False
        db.session.commit()

    return jsonify({"success": True}), 200


# ============================================================
# PUBLIC LISTING DETAIL
# ============================================================

@app.route("/listing/<int:item_id>")
def listing_detail(item_id):
    item = (
        ContentItem.query
        .filter_by(id=item_id, active=True, archived=False)
        .first_or_404()
    )

    zone = item.zone
    category = get_category_by_slug(item.category, active_only=False)

    if category is None:
        current_app.logger.warning(
            "[Kalxa Listing] Category not found listing_id=%s category=%s",
            item.id,
            item.category,
        )

    access_point = None
    access_point_id = request.args.get("ap", type=int)

    if access_point_id:
        access_point = (
            AccessPoint.query
            .filter_by(
                id=access_point_id,
                zone_id=item.zone_id,
            )
            .first()
        )

    attach_campaign_state(item)
    attach_sponsorship_state(item)

    return render_template(
        "listing_detail.html",
        item=item,
        zone=zone,
        category=category,
        access_point=access_point,
    )


# ============================================================
# FIND LIVE ACCESS POINT
# ============================================================

def find_live_access_point(published_content):
    if not published_content:
        return None

    category_aliases = set(
        get_category_aliases(normalize_category(published_content.category)) or []
    )
    category_aliases.add(published_content.category)

    access_point = (
        AccessPoint.query
        .filter(
            AccessPoint.zone_id == published_content.zone_id,
            AccessPoint.active.is_(True),
            AccessPoint.qr_type == "category",
            AccessPoint.default_category.in_(category_aliases),
        )
        .order_by(AccessPoint.id.asc())
        .first()
    )

    if access_point:
        return access_point

    access_point = (
        AccessPoint.query
        .filter(
            AccessPoint.zone_id == published_content.zone_id,
            AccessPoint.active.is_(True),
            AccessPoint.qr_type == "general",
        )
        .order_by(AccessPoint.id.asc())
        .first()
    )

    if access_point:
        return access_point

    return (
        AccessPoint.query
        .filter(
            AccessPoint.zone_id == published_content.zone_id,
            AccessPoint.active.is_(True),
        )
        .order_by(AccessPoint.id.asc())
        .first()
    )


# ============================================================
# LISTING CLAIMS
# ============================================================

@app.route("/claim/<int:item_id>", methods=["GET", "POST"])
def claim_listing(item_id):
    item = (
        ContentItem.query
        .filter_by(id=item_id, active=True, archived=False)
        .first_or_404()
    )

    if not item.can_be_claimed():
        return render_template(
            "claim_listing_unavailable.html",
            item=item,
        ), 409

    if item.has_pending_claim():
        return render_template(
            "claim_listing_pending.html",
            item=item,
        ), 409

    if request.method == "POST":
        claimant_name = (request.form.get("claimant_name", "") or "").strip()
        business_name = (request.form.get("business_name", "") or "").strip()
        phone = (request.form.get("phone", "") or "").strip()
        email = (request.form.get("email", "") or "").strip()
        proof_notes = (request.form.get("proof_notes", "") or "").strip()

        errors = []
        if not claimant_name:
            errors.append("Please enter your name.")
        if not business_name:
            errors.append("Please enter the business name.")
        if not phone:
            errors.append("Please enter a contact number.")

        if errors:
            return render_template(
                "claim_listing.html",
                item=item,
                errors=errors,
                form_data=request.form,
            ), 400

        claim = ListingClaim(
            content_item_id=item.id,
            claimant_name=claimant_name,
            business_name=business_name,
            phone=phone,
            email=email or None,
            proof_notes=proof_notes or None,
            status="pending",
        )

        try:
            db.session.add(claim)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.exception(
                "Failed to create listing claim content_item_id=%s error=%s",
                item.id,
                exc,
            )
            return render_template(
                "claim_listing.html",
                item=item,
                errors=["We could not submit your claim right now. Please try again."],
                form_data=request.form,
            ), 500

        return redirect(
            url_for(
                "claim_listing_success",
                claim_id=claim.id,
            )
        )

    return render_template(
        "claim_listing.html",
        item=item,
        errors=[],
        form_data={},
    )


@app.route("/claim/success/<int:claim_id>")
def claim_listing_success(claim_id):
    claim = ListingClaim.query.filter_by(id=claim_id).first_or_404()
    return render_template(
        "claim_listing_success.html",
        claim=claim,
        item=claim.content_item,
    )


# ============================================================
# PUBLIC CONTENT SUBMISSION
# ============================================================

@app.route("/submit", methods=["GET", "POST"])
def submit_content():
    zones = (
        Zone.query
        .filter_by(active=True)
        .order_by(Zone.name.asc())
        .all()
    )

    business_categories = BUSINESS_CATEGORIES

    selected_zone_id = request.args.get("zone_id", type=int)
    selected_zone = None

    if selected_zone_id:
        selected_zone = db.session.get(Zone, selected_zone_id)
        if not selected_zone or not selected_zone.active:
            selected_zone_id = None
            selected_zone = None

    def render_submit_form():
        return render_template(
            "submit.html",
            zones=zones,
            business_categories=business_categories,
            categories=business_categories,
            selected_zone_id=selected_zone_id,
            selected_zone=selected_zone,
        )

    if request.method == "POST":
        zone_id = request.form.get("zone_id", type=int)
        raw_category = (request.form.get("category", "") or "").strip()
        category_key = normalize_category(raw_category)

        # Jobs have their own dedicated free workflow. Never allow a Jobs
        # submission to accidentally fall through to Yoco/commercial pricing.
        if category_key == "jobs":
            return redirect(url_for("submit_job", zone_id=zone_id))

        content_type = (request.form.get("content_type", "") or "").strip().lower() or None
        title = (request.form.get("title", "") or "").strip()
        description = (request.form.get("description", "") or "").strip() or None
        business_name = (request.form.get("business_name", "") or "").strip() or None
        venue = (request.form.get("venue", "") or "").strip() or None

        try:
            start_time = parse_optional_time(request.form.get("start_time"))
            end_time = parse_optional_time(request.form.get("end_time"))
        except ValueError:
            flash("Please enter valid campaign times.", "error")
            return render_submit_form()

        price = (request.form.get("price", "") or "").strip() or None
        contact = (request.form.get("contact", "") or "").strip() or None
        whatsapp_number = (request.form.get("whatsapp_number", "") or "").strip() or None
        directions_url = (request.form.get("directions_url", "") or "").strip() or None
        ticket_url = (request.form.get("ticket_url", "") or "").strip() or None

        submitter_name = (request.form.get("submitter_name", "") or "").strip()
        submitter_email = (request.form.get("submitter_email", "") or "").strip() or None
        submitter_phone = (request.form.get("submitter_phone", "") or "").strip() or None

        if not zone_id or not category_key or not title or not submitter_name:
            flash("Please complete all required fields.", "error")
            return render_submit_form()

        zone = db.session.get(Zone, zone_id)
        if not zone or not zone.active:
            flash("Please select a valid area.", "error")
            return render_submit_form()

        selected_zone_id = zone.id
        selected_zone = zone

        if category_key not in BUSINESS_CATEGORIES:
            flash("Please select a valid business/content category.", "error")
            return render_submit_form()

        workflow = get_content_workflow(category_key, content_type)
        lifetime_type = workflow.get("lifetime_type")
        notification_eligible = bool(
            workflow.get("notification_eligible", True)
        )

        if lifetime_type not in VALID_LIFETIME_TYPES:
            flash(
                "The selected listing type has an invalid lifecycle configuration.",
                "error",
            )
            return render_submit_form()

        availability_status = "available"
        pricing_model = workflow.get("pricing_model")
        commercial_duration_days = None
        amount_due = None
        distribution_zone_ids = []

        if pricing_model:
            raw_duration = (
                request.form.get("commercial_duration_days", "") or ""
            ).strip()

            try:
                commercial_duration_days = int(raw_duration)
            except (TypeError, ValueError):
                flash("Please select a valid Kalxa package duration.", "error")
                return render_submit_form()

            if pricing_model == PRICING_MODEL_CAMPAIGN:
                for raw_zone_id in request.form.getlist("distribution_zone_ids"):
                    try:
                        distribution_zone_id = int(raw_zone_id)
                    except (TypeError, ValueError):
                        continue

                    if distribution_zone_id not in distribution_zone_ids:
                        distribution_zone_ids.append(distribution_zone_id)

                if zone.id not in distribution_zone_ids:
                    flash("Your campaign must include its home area.", "error")
                    return render_submit_form()

                if not distribution_zone_ids:
                    flash("Please select at least one campaign area.", "error")
                    return render_submit_form()

                if len(distribution_zone_ids) > 3:
                    flash(
                        "Kalxa Campaign currently supports a maximum of 3 areas.",
                        "error",
                    )
                    return render_submit_form()

                valid_distribution_zones = (
                    Zone.query
                    .filter(
                        Zone.id.in_(distribution_zone_ids),
                        Zone.active.is_(True),
                    )
                    .all()
                )

                valid_ids = {record.id for record in valid_distribution_zones}
                if len(valid_ids) != len(distribution_zone_ids):
                    flash("One or more selected campaign areas are invalid.", "error")
                    return render_submit_form()

                zone_count = len(distribution_zone_ids)

            elif pricing_model == PRICING_MODEL_PRESENCE:
                distribution_zone_ids = []
                zone_count = 1
            else:
                flash("The selected listing has an invalid Kalxa pricing model.", "error")
                return render_submit_form()

            try:
                amount_due = calculate_kalxa_price(
                    pricing_model=pricing_model,
                    duration_days=commercial_duration_days,
                    zone_count=zone_count,
                )
            except KalxaPricingError as error:
                flash(str(error), "error")
                return render_submit_form()

        try:
            publish_from = parse_form_date("publish_from")
            event_date = parse_form_date("event_date")
            event_end_date = parse_form_date("event_end_date")
            start_date = parse_form_date("start_date")
            end_date = parse_form_date("end_date")
        except ValueError:
            flash("One or more dates are invalid.", "error")
            return render_submit_form()

        if lifetime_type == "time_specific":
            if category_key == "events":
                if not event_date:
                    flash("Event Date is required for events.", "error")
                    return render_submit_form()

                if publish_from and publish_from > event_date:
                    flash("Publish From cannot be after Event Date.", "error")
                    return render_submit_form()

                if event_end_date and event_end_date < event_date:
                    flash("Event End Date cannot be before Event Date.", "error")
                    return render_submit_form()

                if (
                    event_end_date
                    and event_end_date == event_date
                    and start_time
                    and end_time
                    and end_time <= start_time
                ):
                    flash(
                        "End time must be after start time for a same-day event.",
                        "error",
                    )
                    return render_submit_form()

                start_date = None
                end_date = None
            else:
                if not end_date:
                    flash("An end date is required for time-specific listings.", "error")
                    return render_submit_form()

                if start_date and end_date < start_date:
                    flash("End date cannot be before start date.", "error")
                    return render_submit_form()

                if (
                    start_date
                    and start_date == end_date
                    and start_time
                    and end_time
                    and end_time <= start_time
                ):
                    flash(
                        "End time must be after start time when the listing starts and ends on the same day.",
                        "error",
                    )
                    return render_submit_form()

                publish_from = None
                event_date = None
                event_end_date = None

        elif lifetime_type in {"until_unavailable", "ongoing"}:
            publish_from = None
            event_date = None
            event_end_date = None
            start_date = None
            end_date = None
            start_time = None
            end_time = None

        elif lifetime_type == "recurring":
            publish_from = None
            event_date = None
            event_end_date = None
            if start_date and end_date and end_date < start_date:
                flash("End date cannot be before start date.", "error")
                return render_submit_form()

        uploaded_images = [
            image
            for image in request.files.getlist("images")
            if image and image.filename
        ]

        if len(uploaded_images) > 3:
            flash("You can upload a maximum of 3 images.", "error")
            return render_submit_form()

        for image in uploaded_images:
            if not allowed_image_file(image.filename):
                flash("Images must be JPG, JPEG, PNG or WebP.", "error")
                return render_submit_form()

        submission = PendingSubmission(
            organizer_id=None,
            zone_id=zone.id,
            category=category_key,
            content_type=content_type,
            lifetime_type=lifetime_type,
            availability_status=availability_status,
            notification_eligible=notification_eligible,
            title=title,
            description=description,
            business_name=business_name,
            venue=venue,
            price=price,
            contact=contact,
            whatsapp_number=whatsapp_number,
            directions_url=directions_url,
            ticket_url=ticket_url,
            submitter_name=submitter_name,
            submitter_email=submitter_email,
            submitter_phone=submitter_phone,
            publish_from=publish_from,
            event_date=event_date,
            event_end_date=event_end_date,
            start_time=start_time,
            start_date=start_date,
            end_time=end_time,
            end_date=end_date,
            pricing_model=pricing_model,
            commercial_duration_days=commercial_duration_days,
            amount_due=amount_due,
            payment_status="unpaid" if pricing_model else "waived",
            distribution_zone_ids=distribution_zone_ids,
            status="pending",
        )

        if not submission.tracking_code:
            submission.tracking_code = uuid.uuid4().hex[:12].upper()

        try:
            db.session.add(submission)
            db.session.flush()

            first_image_url = None

            for index, uploaded_image in enumerate(uploaded_images, start=1):
                image_url = upload_lac_image(
                    uploaded_image,
                    folder="lac/submissions",
                )

                if not image_url:
                    continue

                if not first_image_url:
                    first_image_url = image_url

                db.session.add(
                    PendingSubmissionImage(
                        submission_id=submission.id,
                        image_url=image_url,
                        display_order=index,
                    )
                )

            if first_image_url:
                submission.image_url = first_image_url

            db.session.commit()

        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return render_submit_form()

        except Exception as error:
            db.session.rollback()
            current_app.logger.exception(
                "[Kalxa Submission] Unable to create submission: %s",
                error,
            )
            flash("Unable to submit your listing. Please try again.", "error")
            return render_submit_form()

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

    return render_submit_form()


# ============================================================
# SUBMISSION SUCCESS / STATUS
# ============================================================

@app.route("/submit/success/<code>")
def submission_success(code):
    submission = (
        PendingSubmission.query
        .filter_by(tracking_code=code)
        .first_or_404()
    )

    is_commercial = (
        submission.pricing_model in {
            PRICING_MODEL_PRESENCE,
            PRICING_MODEL_CAMPAIGN,
        }
        and submission.amount_due is not None
    )

    payment_confirmed = submission.payment_status == "paid"
    payment_processing = (
        is_commercial
        and submission.payment_status == "unpaid"
        and bool(submission.yoco_checkout_id)
    )
    payment_required = (
        is_commercial
        and submission.payment_status == "unpaid"
        and not submission.yoco_checkout_id
    )

    return render_template(
        "submission_success.html",
        submission=submission,
        is_commercial=is_commercial,
        payment_required=payment_required,
        payment_processing=payment_processing,
        payment_confirmed=payment_confirmed,
        amount_due=submission.amount_due,
    )


@app.route("/submission/status/<code>")
def submission_status(code):
    submission = (
        PendingSubmission.query
        .filter_by(tracking_code=code)
        .first_or_404()
    )

    published_content = None
    live_access_point = None

    if submission.published_content_id:
        published_content = db.session.get(
            ContentItem,
            submission.published_content_id,
        )

    if published_content:
        live_access_point = find_live_access_point(published_content)

    return render_template(
        "submission_status.html",
        submission=submission,
        published_content=published_content,
        live_access_point=live_access_point,
    )


# ============================================================
# YOCO CHECKOUT
# ============================================================

@app.route("/submit/<code>/pay", methods=["POST"])
def create_yoco_checkout(code):
    yoco_secret_key = os.environ.get("YOCO_SECRET_KEY", "").strip()

    if not yoco_secret_key:
        current_app.logger.error("[Kalxa Yoco] YOCO_SECRET_KEY is not configured.")
        flash(
            "Online payment is temporarily unavailable. Please try again later.",
            "error",
        )
        return redirect(url_for("submission_success", code=code))

    submission = (
        PendingSubmission.query
        .filter_by(tracking_code=code)
        .first_or_404()
    )

    if normalize_category(submission.category) == "jobs":
        flash("Kalxa Jobs are free and do not require payment.", "info")
        return redirect(
            url_for(
                "job_submission_success",
                code=submission.tracking_code,
            )
        )

    if submission.pricing_model not in {
        PRICING_MODEL_PRESENCE,
        PRICING_MODEL_CAMPAIGN,
    }:
        flash("This submission does not require a Kalxa listing payment.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    if submission.payment_status == "paid":
        flash("Payment has already been confirmed for this listing.", "success")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    if submission.payment_status != "unpaid":
        flash("This submission cannot currently accept payment.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    if submission.amount_due is None:
        flash("Unable to calculate the payment amount for this listing.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    try:
        amount_cents = int(submission.amount_due * 100)
    except (TypeError, ValueError, ArithmeticError):
        current_app.logger.exception(
            "[Kalxa Yoco] Invalid amount_due submission_id=%s amount_due=%s",
            submission.id,
            submission.amount_due,
        )
        flash("Unable to calculate the payment amount for this listing.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    if amount_cents <= 0:
        flash("The listing payment amount is invalid.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    success_url = url_for(
        "yoco_payment_return",
        code=submission.tracking_code,
        _external=True,
    )
    cancel_url = url_for(
        "yoco_payment_cancelled",
        code=submission.tracking_code,
        _external=True,
    )
    failure_url = url_for(
        "yoco_payment_failed",
        code=submission.tracking_code,
        _external=True,
    )

    payload = {
        "amount": amount_cents,
        "currency": "ZAR",
        "successUrl": success_url,
        "cancelUrl": cancel_url,
        "failureUrl": failure_url,
        "clientReferenceId": submission.tracking_code,
        "externalId": str(submission.id),
        "metadata": {
            "kalxaSubmissionId": str(submission.id),
            "trackingCode": submission.tracking_code,
        },
        "lineItems": [
            {
                "displayName": submission.title or "Kalxa Listing",
                "quantity": 1,
                "pricingDetails": {"price": amount_cents},
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {yoco_secret_key}",
        "Content-Type": "application/json",
        "Idempotency-Key": (
            f"kalxa-submission-{submission.id}-{submission.tracking_code}"
        ),
    }

    try:
        response = requests.post(
            YOCO_CHECKOUT_URL,
            json=payload,
            headers=headers,
            timeout=20,
        )
    except requests.RequestException as error:
        current_app.logger.exception(
            "[Kalxa Yoco] Checkout request failed submission_id=%s error=%s",
            submission.id,
            error,
        )
        flash("Unable to connect to Yoco. Please try again.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    if not 200 <= response.status_code < 300:
        current_app.logger.error(
            "[Kalxa Yoco] Checkout creation rejected submission_id=%s status_code=%s response=%s",
            submission.id,
            response.status_code,
            response.text[:1000],
        )
        flash("Yoco could not create the payment checkout. Please try again.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    try:
        checkout = response.json()
    except ValueError:
        current_app.logger.error(
            "[Kalxa Yoco] Yoco returned invalid JSON submission_id=%s response=%s",
            submission.id,
            response.text[:1000],
        )
        flash("Yoco returned an invalid payment response. Please try again.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    checkout_id = checkout.get("id")
    redirect_url = checkout.get("redirectUrl")

    if not checkout_id or not redirect_url:
        flash("Unable to start the Yoco payment. Please try again.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    try:
        submission.yoco_checkout_id = checkout_id
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Yoco] Unable to save checkout ID submission_id=%s checkout_id=%s error=%s",
            submission.id,
            checkout_id,
            error,
        )
        flash("Unable to prepare the payment. Please try again.", "error")
        return redirect(url_for("submission_success", code=submission.tracking_code))

    return redirect(redirect_url)


@app.route("/submit/payment/success/<code>")
def yoco_payment_return(code):
    submission = (
        PendingSubmission.query
        .filter_by(tracking_code=code)
        .first_or_404()
    )

    if submission.payment_status == "paid":
        flash(
            "Payment received successfully. Your listing is now waiting for Kalxa review.",
            "success",
        )
    else:
        flash(
            "Payment completed. Kalxa is confirming your payment securely. "
            "Your listing will remain pending review.",
            "success",
        )

    return redirect(url_for("submission_success", code=submission.tracking_code))


@app.route("/submit/payment/cancelled/<code>")
def yoco_payment_cancelled(code):
    submission = (
        PendingSubmission.query
        .filter_by(tracking_code=code)
        .first_or_404()
    )
    flash(
        "Payment was cancelled. Your listing is still saved and remains pending. "
        "You can try again.",
        "error",
    )
    return redirect(url_for("submission_success", code=submission.tracking_code))


@app.route("/submit/payment/failed/<code>")
def yoco_payment_failed(code):
    submission = (
        PendingSubmission.query
        .filter_by(tracking_code=code)
        .first_or_404()
    )
    flash(
        "The payment could not be completed. Your listing remains pending and you can try again.",
        "error",
    )
    return redirect(url_for("submission_success", code=submission.tracking_code))


# ============================================================
# YOCO WEBHOOK
# ============================================================

@app.route("/webhooks/yoco", methods=["POST"])
def yoco_webhook():
    webhook_secret = os.environ.get("YOCO_WEBHOOK_SECRET", "").strip()

    if not webhook_secret:
        current_app.logger.error(
            "[Kalxa Yoco Webhook] YOCO_WEBHOOK_SECRET is not configured."
        )
        return "", 500

    raw_body = request.get_data()
    webhook_id = request.headers.get("webhook-id")
    webhook_timestamp = request.headers.get("webhook-timestamp")
    webhook_signature = request.headers.get("webhook-signature")

    if not all((webhook_id, webhook_timestamp, webhook_signature)):
        return "", 400

    try:
        timestamp_int = int(webhook_timestamp)
    except (TypeError, ValueError):
        return "", 400

    current_timestamp = int(time_module.time())
    if abs(current_timestamp - timestamp_int) > 180:
        current_app.logger.warning(
            "[Kalxa Yoco Webhook] Timestamp outside allowed window webhook_id=%s",
            webhook_id,
        )
        return "", 400

    try:
        raw_body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return "", 400

    signed_content = f"{webhook_id}.{webhook_timestamp}.{raw_body_text}"

    try:
        if not webhook_secret.startswith("whsec_"):
            raise ValueError("Invalid webhook secret prefix.")
        encoded_secret = webhook_secret.split("_", 1)[1]
        secret_bytes = base64.b64decode(encoded_secret)
    except Exception:
        current_app.logger.exception(
            "[Kalxa Yoco Webhook] Unable to decode webhook secret."
        )
        return "", 500

    expected_signature = base64.b64encode(
        hmac.new(
            secret_bytes,
            signed_content.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    signature_valid = False

    # Yoco/Svix-style signatures can be space separated.
    for signature in webhook_signature.split():
        if not signature.startswith("v1,"):
            continue
        supplied_signature = signature[len("v1,"):]
        if hmac.compare_digest(expected_signature, supplied_signature):
            signature_valid = True
            break

    if not signature_valid:
        current_app.logger.warning(
            "[Kalxa Yoco Webhook] Invalid webhook signature webhook_id=%s",
            webhook_id,
        )
        return "", 401

    try:
        event = json.loads(raw_body_text)
    except json.JSONDecodeError:
        return "", 400

    event_type = event.get("type")
    if event_type != "payment.succeeded":
        return "", 200

    payload = event.get("payload") or {}
    payment_status = payload.get("status")
    payment_id = payload.get("id")
    payment_amount = payload.get("amount")
    payment_currency = payload.get("currency")
    metadata = payload.get("metadata") or {}
    checkout_id = metadata.get("checkoutId")

    if (
        payment_status != "succeeded"
        or not payment_id
        or payment_amount is None
        or payment_currency != "ZAR"
        or not checkout_id
    ):
        return "", 400

    submission = (
        PendingSubmission.query
        .filter_by(yoco_checkout_id=checkout_id)
        .first()
    )

    if submission is None:
        current_app.logger.error(
            "[Kalxa Yoco Webhook] No submission found checkout_id=%s payment_id=%s",
            checkout_id,
            payment_id,
        )
        return "", 404

    # Free Jobs must never be converted into a paid state by a stale/incorrect
    # checkout or webhook.
    if normalize_category(submission.category) == "jobs":
        current_app.logger.warning(
            "[Kalxa Yoco Webhook] Ignoring payment event for free Jobs submission_id=%s",
            submission.id,
        )
        return "", 409

    if (
        submission.payment_status == "paid"
        and submission.yoco_payment_id == payment_id
    ):
        return "", 200

    existing_payment = (
        PendingSubmission.query
        .filter(
            PendingSubmission.yoco_payment_id == payment_id,
            PendingSubmission.id != submission.id,
        )
        .first()
    )

    if existing_payment:
        return "", 409

    if submission.amount_due is None:
        return "", 409

    try:
        expected_amount_cents = int(submission.amount_due * 100)
        received_amount_cents = int(payment_amount)
    except (TypeError, ValueError, ArithmeticError):
        return "", 400

    if received_amount_cents != expected_amount_cents:
        current_app.logger.error(
            "[Kalxa Yoco Webhook] PAYMENT AMOUNT MISMATCH submission_id=%s expected=%s received=%s payment_id=%s",
            submission.id,
            expected_amount_cents,
            received_amount_cents,
            payment_id,
        )
        return "", 409

    if submission.payment_status != "unpaid":
        return "", 409

    try:
        submission.payment_status = "paid"
        submission.yoco_payment_id = payment_id
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.exception(
            "[Kalxa Yoco Webhook] Database error confirming payment submission_id=%s payment_id=%s error=%s",
            submission.id,
            payment_id,
            error,
        )
        return "", 500

    current_app.logger.info(
        "[Kalxa Yoco Webhook] PAYMENT CONFIRMED submission_id=%s reference=%s payment_id=%s checkout_id=%s amount_cents=%s webhook_id=%s",
        submission.id,
        submission.tracking_code,
        payment_id,
        checkout_id,
        received_amount_cents,
        webhook_id,
    )

    return "", 200


# ============================================================
# SUBMISSION DASHBOARD
# ============================================================

@app.route("/submission/dashboard/<code>")
def submission_dashboard(code):
    submission = (
        PendingSubmission.query
        .filter_by(tracking_code=code)
        .first_or_404()
    )

    if submission.organizer_id:
        organizer_id = session.get("organizer_id")

        if not organizer_id or organizer_id != submission.organizer_id:
            flash(
                "Please sign in with the organizer account that owns this listing.",
                "error",
            )
            return redirect(
                url_for(
                    "submission_status",
                    code=submission.tracking_code,
                )
            )

        organizer = db.session.get(Organizer, organizer_id)
        if not organizer or not organizer.active:
            session.pop("organizer_id", None)
            flash("Your organizer session is no longer valid. Please sign in again.", "error")
            return redirect(
                url_for(
                    "submission_status",
                    code=submission.tracking_code,
                )
            )
    else:
        organizer = None

    published_content = None
    live_access_point = None
    listing_expired = False
    listing_closed = False
    expiry_date = None
    days_remaining = None

    lifetime_type = get_legacy_lifetime_type(submission)
    category_record = get_category_by_slug(
        submission.category,
        active_only=False,
    )

    if submission.published_content_id:
        published_content = db.session.get(
            ContentItem,
            submission.published_content_id,
        )

        if (
            submission.organizer_id
            and published_content
            and published_content.organizer_id != submission.organizer_id
        ):
            current_app.logger.warning(
                "[Kalxa Ownership] Organizer mismatch submission_id=%s content_item_id=%s",
                submission.id,
                published_content.id,
            )

    if published_content:
        live_access_point = find_live_access_point(published_content)
        lifetime_type = get_legacy_lifetime_type(published_content)

        closed_statuses = {
            "taken",
            "sold",
            "filled",
            "closed",
            "expired",
        }

        listing_closed = (
            published_content.availability_status in closed_statuses
        )

        if lifetime_type == "time_specific":
            if normalize_category(published_content.category) == "events":
                expiry_date = (
                    published_content.event_end_date
                    or published_content.event_date
                )
            else:
                expiry_date = published_content.end_date

            if expiry_date:
                days_remaining = (expiry_date - date.today()).days
                listing_expired = expiry_date < date.today()

    return render_template(
        "submission_dashboard.html",
        organizer=organizer,
        submission=submission,
        published_content=published_content,
        live_access_point=live_access_point,
        category_record=category_record,
        lifetime_type=lifetime_type,
        expiry_date=expiry_date,
        days_remaining=days_remaining,
        listing_expired=listing_expired,
        listing_closed=listing_closed,
    )


# ============================================================
# ADMIN TEST PUSH
# ============================================================

@app.route("/admin/push/test/<int:subscriber_id>", methods=["POST"])
def admin_test_push(subscriber_id):
    if not session.get("lac_admin"):
        return jsonify({
            "success": False,
            "error": "Admin login required.",
        }), 401

    subscriber = db.session.get(PushSubscriber, subscriber_id)
    if not subscriber:
        return jsonify({
            "success": False,
            "error": "Subscriber not found.",
        }), 404

    success = send_push_notification(
        subscriber=subscriber,
        title="Kalxa Notifications Are Live 🔔",
        body="Your Kalxa local notification system is working.",
        url="/app",
        tag="kalxa-test",
    )

    if not success:
        return jsonify({
            "success": False,
            "error": "Push failed. Check Render logs.",
        }), 500

    return jsonify({
        "success": True,
        "message": "Test notification sent.",
        "subscriber_id": subscriber.id,
        "zone_id": subscriber.zone_id,
    }), 200


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return {"status": "ok"}, 200


# ============================================================
# RUN APP
# ============================================================

if __name__ == "__main__":
    app.run(debug=True)
