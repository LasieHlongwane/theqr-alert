import os
import io

from image_utils import (
    upload_lac_image,
)
from datetime import date, datetime, timedelta
from push_service import (
    send_zone_push_notification,
)
import qrcode
from app import upload_lac_image
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_file,
    current_app,
)

from categories import (
    normalize_category,
    get_category_aliases,
    get_consumer_category,
)

from sqlalchemy import func

from cloud_storage import upload_listing_image

from pricing import (
    PRICING_MODEL_PRESENCE,
    PRICING_MODEL_CAMPAIGN,
    calculate_kalxa_price,
    format_kalxa_price,
    KalxaPricingError,
)
from models import (
    db,
    Zone,
    Category,
    ZoneCategoryAppearance,
    AccessPoint,
    QRScan,
    ContentItem,
    ListingClaim,
    PendingSubmission,
    PendingSubmissionImage,
    ContentImage,
    PushNotification,
    PushSubscriber,
    EngagementEvent,
    ContentDistributionZone,
    ContentReminder,
)
ONGOING_CATEGORIES = {
    "property",
    "transport",
    "services",
}
from qr_generator import generate_access_qr
ARCHIVE_GRACE_DAYS = 7
EXPIRING_SOON_DAYS = 3

# =========================================================
# KALXA PRESENCE CATEGORIES
# =========================================================
#
# These are categories where the GENERAL / fallback listing
# represents an ongoing business or service presence.
#
# More specific content types can override this below.
# =========================================================

# =========================================================
# KALXA COMMERCIAL PRICING CLASSIFICATION
# =========================================================


# =========================================================
# DEFAULT PRESENCE CATEGORIES
#
# These are categories whose GENERAL listing should normally
# be treated as long-term business presence.
#
# Specific content types can override this below.
# =========================================================

KALXA_PRESENCE_CATEGORIES = {

    "local-restaurants",

    "beauty-salon",

    "services",

}


# =========================================================
# DEFAULT CAMPAIGN CATEGORIES
#
# These categories are normally short-term distribution.
# =========================================================

KALXA_CAMPAIGN_CATEGORIES = {

    "events",

    "discount-deals",

    "jobs",

    "opportunities",

}


# =========================================================
# CONTENT-TYPE OVERRIDES
#
# These rules are checked BEFORE the category defaults.
#
# This allows:
#
# Restaurant       -> Presence
# Restaurant Deal  -> Campaign
#
# Salon            -> Presence
# Beauty Special   -> Campaign
#
# Hotel            -> Presence
# Accommodation Special -> Campaign
# =========================================================

KALXA_PRICING_MODEL_OVERRIDES = {


    # =====================================================
    # PROPERTY
    # =====================================================

    ("property", "room"):
        PRICING_MODEL_CAMPAIGN,

    ("property", "rental"):
        PRICING_MODEL_CAMPAIGN,

    ("property", "property_sale"):
        PRICING_MODEL_CAMPAIGN,

    ("property", "hotel_lodge"):
        PRICING_MODEL_PRESENCE,

    ("property", "accommodation_special"):
        PRICING_MODEL_CAMPAIGN,


    # =====================================================
    # EVENTS
    # =====================================================

    ("events", "entertainment"):
        PRICING_MODEL_CAMPAIGN,

    ("events", "church_event"):
        PRICING_MODEL_CAMPAIGN,

    ("events", "sports_event"):
        PRICING_MODEL_CAMPAIGN,

    ("events", "community_event"):
        PRICING_MODEL_CAMPAIGN,

    ("events", "business_event"):
        PRICING_MODEL_CAMPAIGN,

    ("events", "event"):
        PRICING_MODEL_CAMPAIGN,


    # =====================================================
    # DISCOUNT DEALS
    # =====================================================

    ("discount-deals", "grocery_special"):
        PRICING_MODEL_CAMPAIGN,

    ("discount-deals", "product_discount"):
        PRICING_MODEL_CAMPAIGN,

    ("discount-deals", "weekend_special"):
        PRICING_MODEL_CAMPAIGN,

    ("discount-deals", "clearance"):
        PRICING_MODEL_CAMPAIGN,


    # =====================================================
    # LOCAL RESTAURANTS
    # =====================================================

    ("local-restaurants", "restaurant"):
        PRICING_MODEL_PRESENCE,

    ("local-restaurants", "takeaway"):
        PRICING_MODEL_PRESENCE,

    ("local-restaurants", "daily_special"):
        PRICING_MODEL_CAMPAIGN,

    ("local-restaurants", "weekend_special"):
        PRICING_MODEL_CAMPAIGN,

    ("local-restaurants", "food_deal"):
        PRICING_MODEL_CAMPAIGN,


    # =====================================================
    # JOBS
    # =====================================================

    ("jobs", "job"):
        PRICING_MODEL_CAMPAIGN,

    ("jobs", "learnership"):
        PRICING_MODEL_CAMPAIGN,

    ("jobs", "internship"):
        PRICING_MODEL_CAMPAIGN,

    ("jobs", "training"):
        PRICING_MODEL_CAMPAIGN,

    ("jobs", "tender"):
        PRICING_MODEL_CAMPAIGN,

    ("jobs", "business_opportunity"):
        PRICING_MODEL_CAMPAIGN,


    # =====================================================
    # OPPORTUNITIES
    # =====================================================

    ("opportunities", "job"):
        PRICING_MODEL_CAMPAIGN,

    ("opportunities", "learnership"):
        PRICING_MODEL_CAMPAIGN,

    ("opportunities", "internship"):
        PRICING_MODEL_CAMPAIGN,

    ("opportunities", "training"):
        PRICING_MODEL_CAMPAIGN,

    ("opportunities", "tender"):
        PRICING_MODEL_CAMPAIGN,

    ("opportunities", "business_opportunity"):
        PRICING_MODEL_CAMPAIGN,


    # =====================================================
    # SERVICES
    # =====================================================

    ("services", "service_provider"):
        PRICING_MODEL_PRESENCE,

    ("services", "plumber"):
        PRICING_MODEL_PRESENCE,

    ("services", "mechanic"):
        PRICING_MODEL_PRESENCE,

    ("services", "electrician"):
        PRICING_MODEL_PRESENCE,

    ("services", "builder"):
        PRICING_MODEL_PRESENCE,

    ("services", "cleaning_service"):
        PRICING_MODEL_PRESENCE,


    # =====================================================
    # BEAUTY / SALON
    # =====================================================

    ("beauty-salon", "salon"):
        PRICING_MODEL_PRESENCE,

    ("beauty-salon", "barber"):
        PRICING_MODEL_PRESENCE,

    ("beauty-salon", "beauty_service"):
        PRICING_MODEL_PRESENCE,

    ("beauty-salon", "beauty_special"):
        PRICING_MODEL_CAMPAIGN,

}


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_logged_in():
    return session.get("lac_admin") is True


def require_admin():
    if not admin_logged_in():
        return redirect(url_for("admin.login"))
    return None


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def clean_slug(value):
    return (value or "").strip().lower().replace(" ", "-").replace("_", "-")


def get_categories(active_only=True):
    """
    Return the physical/public Category rows stored in the database.

    IMPORTANT:
    Category rows are still used by the existing public navigation,
    QR routes and admin configuration.

    We are NOT migrating those database rows yet.
    """

    query = Category.query

    if active_only:
        query = query.filter(
            Category.active.is_(True)
        )

    return (
        query
        .order_by(
            Category.display_order.asc(),
            Category.name.asc(),
        )
        .all()
    )


def get_category_by_slug(
    slug,
    active_only=True,
):
    """
    Find a Category database row while supporting both:

    1. Existing production/public category slugs.
    2. New canonical business taxonomy keys.

    Examples:

        upcoming-event-🥹🔥
                ↓
              events

        check-out-our-specials
                ↓
           restaurants

        beauty-salon
                ↓
              beauty

    During the taxonomy transition, the Category table may
    still contain legacy slugs while new ContentItem records
    may use canonical category keys.

    This helper allows both systems to coexist.
    """

    # =====================================================
    # CLEAN INPUT
    # =====================================================

    requested_slug = (
        str(
            slug
            or ""
        )
        .strip()
        .lower()
    )

    if not requested_slug:
        return None


    # =====================================================
    # 1. TRY EXACT DATABASE SLUG FIRST
    #
    # This is important for existing public routes.
    #
    # Example:
    #
    #     /q/KWM-TAXI-001/check-out-our-specials
    #
    # If that exact Category row exists, use it.
    #
    # This also preserves the correct:
    #
    # - category image
    # - icon
    # - display order
    # - ZoneCategoryAppearance relationship
    #
    # =====================================================

    exact_query = (
        Category.query
        .filter(
            Category.slug
            == requested_slug
        )
    )

    if active_only:

        exact_query = (
            exact_query
            .filter(
                Category.active.is_(
                    True
                )
            )
        )


    exact_category = (
        exact_query.first()
    )


    if exact_category:
        return exact_category


    # =====================================================
    # 2. NORMALIZE TO CANONICAL TAXONOMY
    #
    # Example:
    #
    # check-out-our-specials
    #       -> restaurants
    #
    # foods
    #       -> restaurants
    #
    # restaurants
    #       -> restaurants
    #
    # =====================================================

    canonical_category = (
        normalize_category(
            requested_slug
        )
    )


    if not canonical_category:
        return None


    # =====================================================
    # 3. GET ALL EQUIVALENT CATEGORY SLUGS
    #
    # Example for restaurants:
    #
    # {
    #     "restaurants",
    #     "check-out-our-specials",
    #     "foods",
    #     "local-restaurants",
    # }
    #
    # get_category_aliases() is now the central source
    # of truth for taxonomy compatibility.
    # =====================================================

    equivalent_slugs = (
        get_category_aliases(
            canonical_category
        )
    )


    # Defensive fallback.
    #
    # This ensures a future canonical category can still
    # resolve even if it has no legacy aliases.

    equivalent_slugs.update(
        {
            requested_slug,
            canonical_category,
        }
    )

    equivalent_slugs.discard(None)
    equivalent_slugs.discard("")


    if not equivalent_slugs:
        return None


    # =====================================================
    # 4. FIND MATCHING CATEGORY DATABASE ROWS
    # =====================================================

    equivalent_query = (
        Category.query
        .filter(
            Category.slug.in_(
                equivalent_slugs
            )
        )
    )


    if active_only:

        equivalent_query = (
            equivalent_query
            .filter(
                Category.active.is_(
                    True
                )
            )
        )


    categories = (
        equivalent_query.all()
    )


    if not categories:
        return None


    # =====================================================
    # 5. PREFER CANONICAL DATABASE ROW
    #
    # Eventually your Category table can contain:
    #
    #     restaurants
    #     events
    #     beauty
    #     retail_specials
    #     rentals
    #     ...
    #
    # Once those rows exist, they automatically become
    # preferred without breaking old URLs.
    # =====================================================

    for category_record in categories:

        if (
            category_record.slug
            == canonical_category
        ):

            return category_record


    # =====================================================
    # 6. LEGACY FALLBACK
    #
    # The database may currently contain several old rows
    # that now belong to one canonical category.
    #
    # Example:
    #
    # check-out-our-specials
    # foods
    #
    # both belong to:
    #
    # restaurants
    #
    # Until the Category table itself is migrated, choose
    # the first active legacy Category according to its
    # existing display order.
    #
    # IMPORTANT:
    #
    # This is only the representative Category DB object.
    # It does NOT determine which ContentItem rows are
    # visible.
    #
    # get_active_content() combines all equivalent aliases.
    # =====================================================

    categories.sort(
        key=lambda category_record: (

            (
                category_record.display_order
                if (
                    category_record.display_order
                    is not None
                )
                else 999999
            ),

            (
                category_record.name
                or ""
            ),

            category_record.id,

        )
    )


    return categories[0]


def get_reminder_analytics(
    top_limit=10,
):

    """
    Build Kalxa reminder analytics for the admin dashboard.

    Metrics:

    - total reminders set
    - pending
    - processing
    - sent
    - failed
    - cancelled
    - retry activity
    - delivery rate
    - top content by reminder intent

    No personally identifiable information is returned.
    """

    # =====================================================
    # IMPORTS
    # =====================================================

    from sqlalchemy import func, case


    # =====================================================
    # SUMMARY COUNTS
    # =====================================================

    summary = (
        db.session.query(

            func.count(
                ContentReminder.id
            )
            .label(
                "total"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.status
                        == "pending",
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "pending"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.status
                        == "processing",
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "processing"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.status
                        == "sent",
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "sent"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.status
                        == "failed",
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "failed"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.status
                        == "cancelled",
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "cancelled"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.retry_count
                        > 0,
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "retried"
            ),

        )
        .one()
    )


    total = int(
        summary.total
        or 0
    )

    pending = int(
        summary.pending
        or 0
    )

    processing = int(
        summary.processing
        or 0
    )

    sent = int(
        summary.sent
        or 0
    )

    failed = int(
        summary.failed
        or 0
    )

    cancelled = int(
        summary.cancelled
        or 0
    )

    retried = int(
        summary.retried
        or 0
    )


    # =====================================================
    # DELIVERY RATE
    #
    # We only calculate delivery rate from completed
    # delivery attempts.
    #
    # sent + failed
    # =====================================================

    completed_delivery_attempts = (
        sent
        + failed
    )


    if completed_delivery_attempts > 0:

        delivery_rate = round(
            (
                sent
                / completed_delivery_attempts
            )
            * 100,
            1,
        )

    else:

        delivery_rate = 0.0


    # =====================================================
    # TOP CONTENT BY REMINDER INTENT
    #
    # A reminder being created is considered an expression
    # of intent from the user.
    # =====================================================

    top_content_rows = (
        db.session.query(

            ContentItem.id.label(
                "content_item_id"
            ),

            ContentItem.title.label(
                "title"
            ),

            ContentItem.category.label(
                "category"
            ),

            ContentItem.event_date.label(
                "event_date"
            ),

            func.count(
                ContentReminder.id
            )
            .label(
                "reminder_count"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.status
                        == "sent",
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "sent_count"
            ),


            func.sum(
                case(
                    (
                        ContentReminder.status
                        == "pending",
                        1,
                    ),
                    else_=0,
                )
            )
            .label(
                "pending_count"
            ),

        )
        .join(
            ContentReminder,
            ContentReminder.content_item_id
            == ContentItem.id,
        )
        .group_by(

            ContentItem.id,

            ContentItem.title,

            ContentItem.category,

            ContentItem.event_date,

        )
        .order_by(

            func.count(
                ContentReminder.id
            )
            .desc(),

            ContentItem.id
            .desc(),

        )
        .limit(
            top_limit
        )
        .all()
    )


    # =====================================================
    # NORMALISE TOP CONTENT
    # =====================================================

    top_content = []


    for row in top_content_rows:

        reminder_count = int(
            row.reminder_count
            or 0
        )

        sent_count = int(
            row.sent_count
            or 0
        )

        pending_count = int(
            row.pending_count
            or 0
        )


        top_content.append({

            "content_item_id":
                row.content_item_id,

            "title":
                row.title,

            "category":
                normalize_category(
                    row.category
                ),

            "event_date":
                row.event_date,

            "reminder_count":
                reminder_count,

            "sent_count":
                sent_count,

            "pending_count":
                pending_count,

        })


    # =====================================================
    # RECENT REMINDER ACTIVITY
    # =====================================================

    recent_rows = (
        db.session.query(
            ContentReminder,
            ContentItem,
        )
        .join(
            ContentItem,
            ContentItem.id
            == ContentReminder.content_item_id,
        )
        .order_by(
            ContentReminder.created_at
            .desc()
        )
        .limit(
            10
        )
        .all()
    )


    recent_activity = []


    for reminder, item in recent_rows:

        recent_activity.append({

            "id":
                reminder.id,

            "content_item_id":
                item.id,

            "title":
                item.title,

            "category":
                normalize_category(
                    item.category
                ),

            "status":
                reminder.status,

            "minutes_before":
                reminder.reminder_minutes_before,

            "retry_count":
                reminder.retry_count
                or 0,

            "created_at":
                reminder.created_at,

            "scheduled_for":
                reminder.scheduled_for,

            "sent_at":
                reminder.sent_at,

        })


    # =====================================================
    # FINAL RESULT
    # =====================================================

    return {

        "total":
            total,

        "pending":
            pending,

        "processing":
            processing,

        "sent":
            sent,

        "failed":
            failed,

        "cancelled":
            cancelled,

        "retried":
            retried,

        "delivery_rate":
            delivery_rate,

        "top_content":
            top_content,

        "recent_activity":
            recent_activity,

    }

def get_content_status(
    item,
    today=None,
):

    today = (
        today
        or date.today()
    )

    # ========================================================
    # ARCHIVED
    # ========================================================

    if item.archived:

        return {
            "key": "archived",
            "label": "ARCHIVED",
            "icon": "📦",
        }

    # ========================================================
    # MANUALLY INACTIVE
    # ========================================================

    if not item.active:

        return {
            "key": "inactive",
            "label": "INACTIVE",
            "icon": "⚪",
        }

    # ========================================================
    # AVAILABILITY STATE
    # ========================================================

    availability_status = (
        getattr(
            item,
            "availability_status",
            None,
        )
        or "available"
    )

    if availability_status in {
        "taken",
        "sold",
        "filled",
        "closed",
    }:

        return {
            # Keep existing admin filter compatibility
            # for now by treating unavailable content
            # as inactive in the content-list status key.
            "key": "inactive",
            "label":
                availability_status.upper(),
            "icon": "⚫",
        }

    if availability_status == "expired":

        return {
            "key": "expired",
            "label": "EXPIRED",
            "icon": "🔴",
        }

    # ========================================================
    # LIFETIME TYPE
    # ========================================================

    lifetime_type = (
        getattr(
            item,
            "lifetime_type",
            None,
        )
    )

    # --------------------------------------------------------
    # LEGACY FALLBACK
    # --------------------------------------------------------

    if not lifetime_type:

        if item.category == "events":

            lifetime_type = (
                "time_specific"
            )

        elif item.end_date:

            lifetime_type = (
                "time_specific"
            )

        else:

            lifetime_type = (
                "ongoing"
            )

    # ========================================================
    # TIME-SPECIFIC
    # ========================================================

    if lifetime_type == "time_specific":

        # ----------------------------------------------------
        # EVENT
        # ----------------------------------------------------

        if item.category == "events":

            if (
                item.publish_from
                and
                item.publish_from > today
            ):

                return {
                    "key": "upcoming",
                    "label": "UPCOMING",
                    "icon": "🟡",
                }

            expiry_date = (
                item.event_end_date
                or
                item.event_date
            )

            if (
                expiry_date
                and
                expiry_date < today
            ):

                return {
                    "key": "expired",
                    "label": "EXPIRED",
                    "icon": "🔴",
                }

            return {
                "key": "live",
                "label": "LIVE",
                "icon": "🟢",
            }

        # ----------------------------------------------------
        # NON-EVENT
        # ----------------------------------------------------

        if (
            item.start_date
            and
            item.start_date > today
        ):

            return {
                "key": "upcoming",
                "label": "UPCOMING",
                "icon": "🟡",
            }

        if (
            item.end_date
            and
            item.end_date < today
        ):

            return {
                "key": "expired",
                "label": "EXPIRED",
                "icon": "🔴",
            }

    # ========================================================
    # ONGOING / UNTIL UNAVAILABLE / RECURRING
    # ========================================================

    return {
        "key": "live",
        "label": "LIVE",
        "icon": "🟢",
    }

def _parse_distribution_zone_ids():

    # =====================================================
    # READ SELECTED ZONES
    # =====================================================

    raw_zone_ids = (
        request.form.getlist(
            "distribution_zone_ids"
        )
    )


    zone_ids = []


    # =====================================================
    # NORMALIZE / VALIDATE IDS
    # =====================================================

    for raw_zone_id in raw_zone_ids:

        try:

            zone_id = int(
                raw_zone_id
            )

        except (
            TypeError,
            ValueError,
        ):

            continue


        if (
            zone_id not in zone_ids
        ):

            zone_ids.append(
                zone_id
            )


    return zone_ids
    
def _configure_commercial_content(
    item,
    category,
    content_type,
):

    # =====================================================
    # GET WORKFLOW
    # =====================================================

    workflow = (
        _get_content_workflow(
            category,
            content_type,
        )
    )


    pricing_model = (
        workflow.get(
            "pricing_model"
        )
    )


    # =====================================================
    # NON-COMMERCIAL / UNMAPPED CONTENT
    # =====================================================

    if not pricing_model:

        item.pricing_model = None

        item.commercial_duration_days = None

        item.commercial_starts_at = None

        item.commercial_expires_at = None

        item.amount_due = None

        return []


    # =====================================================
    # DURATION
    # =====================================================

    raw_duration = (
        request.form.get(
            "commercial_duration_days",
            "",
        )
        .strip()
    )


    try:

        duration_days = int(
            raw_duration
        )

    except (
        TypeError,
        ValueError,
    ):

        raise ValueError(
            "Please select a valid Kalxa package duration."
        )


    # =====================================================
    # PAYMENT STATUS
    # =====================================================

    payment_status = (
        request.form.get(
            "payment_status",
            "unpaid",
        )
        .strip()
        .lower()
    )


    allowed_payment_statuses = {

        "unpaid",

        "paid",

        "waived",

        "refunded",

    }


    if (
        payment_status
        not in allowed_payment_statuses
    ):

        payment_status = (
            "unpaid"
        )


    # =====================================================
    # DISTRIBUTION REACH
    # =====================================================

    distribution_zone_ids = []


    if (
        pricing_model
        == PRICING_MODEL_CAMPAIGN
    ):

        distribution_zone_ids = (
            _parse_distribution_zone_ids()
        )


        if not distribution_zone_ids:

            raise ValueError(
                (
                    "Campaign content must have at least "
                    "one distribution zone."
                )
            )


        if (
            len(
                distribution_zone_ids
            )
            > 3
        ):

            raise ValueError(
                (
                    "The current Kalxa campaign packages "
                    "support a maximum of 3 zones."
                )
            )


        # ---------------------------------------------
        # Confirm all selected zone IDs really exist.
        # ---------------------------------------------

        existing_zone_ids = {

            zone.id

            for zone in (
                Zone.query
                .filter(
                    Zone.id.in_(
                        distribution_zone_ids
                    )
                )
                .all()
            )

        }


        if (
            len(
                existing_zone_ids
            )
            != len(
                distribution_zone_ids
            )
        ):

            raise ValueError(
                "One or more selected campaign zones are invalid."
            )


        zone_count = len(
            distribution_zone_ids
        )


    else:

        # =================================================
        # PRESENCE
        #
        # Presence pricing is based only on duration.
        # No purchased multi-zone reach.
        # =================================================

        distribution_zone_ids = []

        zone_count = 1


    # =====================================================
    # SERVER-SIDE PRICE CALCULATION
    #
    # NEVER trust displayed_amount_due from the browser.
    # =====================================================

    try:

        amount_due = (
            calculate_kalxa_price(
                pricing_model=
                    pricing_model,

                duration_days=
                    duration_days,

                zone_count=
                    zone_count,
            )
        )

    except KalxaPricingError as exc:

        raise ValueError(
            str(
                exc
            )
        )


    # =====================================================
    # COMMERCIAL DATES
    # =====================================================

    now = datetime.utcnow()


    commercial_starts_at = None

    commercial_expires_at = None


    # =====================================================
    # ACTIVATE COMMERCIAL PERIOD ONLY WHEN AUTHORIZED
    #
    # paid:
    #     Customer paid.
    #
    # waived:
    #     Kalxa intentionally grants access.
    #
    # unpaid/refunded:
    #     Do not start purchased visibility.
    # =====================================================

    if (
        payment_status
        in {
            "paid",
            "waived",
        }
    ):

        commercial_starts_at = (
            now
        )


        commercial_expires_at = (
            now
            + timedelta(
                days=duration_days
            )
        )


    # =====================================================
    # SAVE COMMERCIAL VALUES
    # =====================================================

    item.pricing_model = (
        pricing_model
    )


    item.commercial_duration_days = (
        duration_days
    )


    item.commercial_starts_at = (
        commercial_starts_at
    )


    item.commercial_expires_at = (
        commercial_expires_at
    )


    item.payment_status = (
        payment_status
    )


    item.amount_due = (
        amount_due
    )


    item.payment_reference = (
        request.form.get(
            "payment_reference",
            "",
        )
        .strip()
        or None
    )


    # =====================================================
    # AMOUNT PAID / PAID AT
    # =====================================================

    if (
        payment_status
        == "paid"
    ):

        item.amount_paid = (
            amount_due
        )


        item.paid_at = (
            now
        )


    else:

        item.amount_paid = None

        item.paid_at = None


    # =====================================================
    # RETURN DISTRIBUTION IDS
    #
    # The ContentItem must first be flushed so it has an ID.
    # =====================================================

    return distribution_zone_ids

def get_content_expiry_date(
    item,
):

    lifetime_type = (
        getattr(
            item,
            "lifetime_type",
            None,
        )
    )

    # --------------------------------------------------------
    # Legacy records
    # --------------------------------------------------------

    if not lifetime_type:

        if item.category == "events":

            lifetime_type = (
                "time_specific"
            )

        elif item.end_date:

            lifetime_type = (
                "time_specific"
            )

        else:

            lifetime_type = (
                "ongoing"
            )

    # Ongoing and until-unavailable content must NOT
    # enter automatic expiry/archive logic.

    if lifetime_type not in {
        "time_specific",
        "recurring",
    }:

        return None

    if item.category == "events":

        return (
            item.event_end_date
            or
            item.event_date
        )

    return item.end_date


def get_archive_intelligence(item, today=None):
    today = today or date.today()
    if item.archived:
        return None

    expiry_date = get_content_expiry_date(item)
    if not expiry_date or expiry_date >= today:
        return None

    expired_days = (today - expiry_date).days
    archive_date = expiry_date + timedelta(days=ARCHIVE_GRACE_DAYS)
    days_until_archive = (archive_date - today).days

    return {
        "expired_days": expired_days,
        "archive_date": archive_date,
        "days_until_archive": max(days_until_archive, 0),
        "ready_to_archive": today >= archive_date,
    }


# =========================================================
# BUSINESS ANALYTICS CONSTANTS
# =========================================================

BUSINESS_ANALYTICS_EVENTS = {
    "listing_view",
    "whatsapp_click",
    "call_click",
    "directions_click",
    "share_click",
}


ACTION_EVENTS = {
    "whatsapp_click",
    "call_click",
    "directions_click",
    "share_click",
}


# =========================================================
# BUSINESS ANALYTICS DATE RANGE
# =========================================================

def _get_analytics_date_range():

    range_key = (
        request.args.get(
            "range",
            "30d",
        )
        .strip()
        .lower()
    )


    today = date.today()


    if range_key == "7d":

        days = 7

        label = "Last 7 days"


    elif range_key == "90d":

        days = 90

        label = "Last 90 days"


    else:

        range_key = "30d"

        days = 30

        label = "Last 30 days"


    start_date = (
        today
        - timedelta(
            days=days - 1
        )
    )


    return {
        "key": range_key,
        "days": days,
        "label": label,
        "start_date": start_date,
        "end_date": today,
    }


# =========================================================
# BUILD BUSINESS ANALYTICS
# =========================================================

def _build_business_analytics(
    item,
    analytics_range,
):

    start_datetime = datetime.combine(
        analytics_range[
            "start_date"
        ],
        datetime.min.time(),
    )


    end_datetime = datetime.combine(
        analytics_range[
            "end_date"
        ]
        + timedelta(days=1),
        datetime.min.time(),
    )


    # =====================================================
    # BASE QUERY
    # =====================================================

    base_query = (
        EngagementEvent.query
        .filter(
            EngagementEvent.content_item_id
            == item.id,
            EngagementEvent.event_type.in_(
                BUSINESS_ANALYTICS_EVENTS
            ),
            EngagementEvent.created_at
            >= start_datetime,
            EngagementEvent.created_at
            < end_datetime,
        )
    )


    # =====================================================
    # EVENT COUNTS
    # =====================================================

    event_rows = (
        db.session.query(
            EngagementEvent.event_type,
            func.count(
                EngagementEvent.id
            ),
        )
        .filter(
            EngagementEvent.content_item_id
            == item.id,
            EngagementEvent.event_type.in_(
                BUSINESS_ANALYTICS_EVENTS
            ),
            EngagementEvent.created_at
            >= start_datetime,
            EngagementEvent.created_at
            < end_datetime,
        )
        .group_by(
            EngagementEvent.event_type
        )
        .all()
    )


    counts = {
        event_type: count
        for event_type, count
        in event_rows
    }


    listing_views = (
        counts.get(
            "listing_view",
            0,
        )
    )


    whatsapp_clicks = (
        counts.get(
            "whatsapp_click",
            0,
        )
    )


    call_clicks = (
        counts.get(
            "call_click",
            0,
        )
    )


    directions_clicks = (
        counts.get(
            "directions_click",
            0,
        )
    )


    share_clicks = (
        counts.get(
            "share_click",
            0,
        )
    )


    total_actions = (
        whatsapp_clicks
        + call_clicks
        + directions_clicks
        + share_clicks
    )


    # =====================================================
    # ACTION RATE
    #
    # This is NOT conversion-to-sale.
    #
    # It means:
    #
    # tracked actions / listing views
    # =====================================================

    if listing_views:

        action_rate = round(
            (
                total_actions
                / listing_views
            )
            * 100,
            1,
        )

    else:

        action_rate = 0.0


    # =====================================================
    # DAILY LISTING VIEWS
    # =====================================================

    daily_view_rows = (
        db.session.query(
            func.date(
                EngagementEvent.created_at
            ).label(
                "day"
            ),
            func.count(
                EngagementEvent.id
            ).label(
                "views"
            ),
        )
        .filter(
            EngagementEvent.content_item_id
            == item.id,
            EngagementEvent.event_type
            == "listing_view",
            EngagementEvent.created_at
            >= start_datetime,
            EngagementEvent.created_at
            < end_datetime,
        )
        .group_by(
            func.date(
                EngagementEvent.created_at
            )
        )
        .order_by(
            func.date(
                EngagementEvent.created_at
            )
        )
        .all()
    )


    daily_view_map = {
        str(day): count
        for day, count
        in daily_view_rows
    }


    # -----------------------------------------------------
    # Fill missing dates with zero.
    # -----------------------------------------------------

    daily_views = []


    current_day = (
        analytics_range[
            "start_date"
        ]
    )


    while (
        current_day
        <= analytics_range[
            "end_date"
        ]
    ):

        day_key = (
            current_day.isoformat()
        )


        daily_views.append(
            {
                "date":
                    day_key,

                "label":
                    current_day.strftime(
                        "%d %b"
                    ),

                "views":
                    daily_view_map.get(
                        day_key,
                        0,
                    ),
            }
        )


        current_day += timedelta(
            days=1
        )


    # =====================================================
    # BEST DAY
    # =====================================================

    if daily_views:

        best_day = max(
            daily_views,
            key=lambda row:
                row["views"],
        )

    else:

        best_day = {
            "date": None,
            "label": "—",
            "views": 0,
        }


    if (
        best_day["views"]
        == 0
    ):

        best_day = {
            "date": None,
            "label": "—",
            "views": 0,
        }


    # =====================================================
    # ACTION BREAKDOWN
    # =====================================================

    action_breakdown = [
        {
            "event_type":
                "whatsapp_click",

            "label":
                "WhatsApp",

            "icon":
                "💬",

            "count":
                whatsapp_clicks,
        },
        {
            "event_type":
                "call_click",

            "label":
                "Calls",

            "icon":
                "📞",

            "count":
                call_clicks,
        },
        {
            "event_type":
                "directions_click",

            "label":
                "Directions",

            "icon":
                "🧭",

            "count":
                directions_clicks,
        },
        {
            "event_type":
                "share_click",

            "label":
                "Shares",

            "icon":
                "↗",

            "count":
                share_clicks,
        },
    ]


    # =========================================================
# ACCESS POINT ATTRIBUTION
#
# Shows which physical Kalxa access points
# generated attention/actions for this listing.
# =========================================================

    access_point_rows = (

     db.session.query(

        EngagementEvent.access_point_id,

        AccessPoint.name,

        func.sum(
            case(
                (
                    EngagementEvent.event_type
                    == "listing_view",
                    1,
                ),
                else_=0,
            )
        ).label(
            "views"
        ),

        func.sum(
            case(
                (
                    EngagementEvent.event_type.in_(
                        ACTION_EVENTS
                    ),
                    1,
                ),
                else_=0,
            )
        ).label(
            "actions"
        ),

     )

     .join(
        AccessPoint,
        EngagementEvent.access_point_id
        == AccessPoint.id,
     )

     .filter(

        EngagementEvent.content_item_id
        == item.id,

        EngagementEvent.created_at
        >= start_datetime,

        EngagementEvent.created_at
        < end_datetime,

        EngagementEvent.access_point_id
        .isnot(None),

     )

     .group_by(
        EngagementEvent.access_point_id,
        AccessPoint.name,
     )

     .order_by(
        func.sum(
            case(
                (
                    EngagementEvent.event_type
                    == "listing_view",
                    1,
                ),
                else_=0,
            )
        ).desc()
     )

     .all()
    )


    access_points = []


    for (
     access_point_id,
     access_point_name,
     views,
     actions,
    ) in access_point_rows:

     views = int(
        views or 0
     )

     actions = int(
        actions or 0
     )


     action_rate = (

        round(
            (
                actions
                / views
            )
            * 100,
            1,
        )

        if views

        else 0.0

     )


     access_points.append(
        {

            "id":
                access_point_id,

            "name":
                access_point_name,

            "views":
                views,

            "actions":
                actions,

            "action_rate":
                action_rate,

        }
     )


    # =====================================================
    # RETURN ANALYTICS
    # =====================================================

    return {

        "listing_views":
            listing_views,

        "whatsapp_clicks":
            whatsapp_clicks,

        "call_clicks":
            call_clicks,

        "directions_clicks":
            directions_clicks,

        "share_clicks":
            share_clicks,

        "total_actions":
            total_actions,

        "action_rate":
            action_rate,

        "daily_views":
            daily_views,

        "best_day":
            best_day,
        
        "access_points":
            access_points,

        "action_breakdown":
            action_breakdown,

    }


# =========================================================
# BUSINESS ANALYTICS — OVERVIEW
# =========================================================

@admin_bp.route(
    "/business-analytics"
)
def business_analytics():

    auth = require_admin()

    if auth:
        return auth


    # =====================================================
    # ANALYTICS RANGE
    # =====================================================

    analytics_range = (
        _get_analytics_date_range()
    )


    start_datetime = datetime.combine(
        analytics_range[
            "start_date"
        ],
        datetime.min.time(),
    )


    end_datetime = datetime.combine(
        analytics_range[
            "end_date"
        ]
        + timedelta(days=1),
        datetime.min.time(),
    )


    # =====================================================
    # BUSINESS / PROMOTION LISTINGS
    # =====================================================

    listings = (
        ContentItem.query
        .filter(
            ContentItem.listing_level.in_(
                {
                    "business",
                    "promotion",
                }
            )
        )
        .order_by(
            ContentItem.created_at.desc()
        )
        .all()
    )


    listing_ids = [
        item.id
        for item in listings
    ]


    # =====================================================
    # AGGREGATE EVENTS IN ONE QUERY
    # =====================================================

    analytics_by_listing = {}


    if listing_ids:

        rows = (
            db.session.query(
                EngagementEvent.content_item_id,
                EngagementEvent.event_type,
                func.count(
                    EngagementEvent.id
                ).label(
                    "event_count"
                ),
            )
            .filter(
                EngagementEvent.content_item_id.in_(
                    listing_ids
                ),
                EngagementEvent.event_type.in_(
                    BUSINESS_ANALYTICS_EVENTS
                ),
                EngagementEvent.created_at
                >= start_datetime,
                EngagementEvent.created_at
                < end_datetime,
            )
            .group_by(
                EngagementEvent.content_item_id,
                EngagementEvent.event_type,
            )
            .all()
        )


        for (
            content_item_id,
            event_type,
            event_count,
        ) in rows:

            analytics_by_listing.setdefault(
                content_item_id,
                {}
            )


            analytics_by_listing[
                content_item_id
            ][
                event_type
            ] = event_count


    # =====================================================
    # BUILD LISTING REPORT
    # =====================================================

    listing_reports = []


    for item in listings:

        counts = (
            analytics_by_listing.get(
                item.id,
                {},
            )
        )


        views = counts.get(
            "listing_view",
            0,
        )


        whatsapp = counts.get(
            "whatsapp_click",
            0,
        )


        calls = counts.get(
            "call_click",
            0,
        )


        directions = counts.get(
            "directions_click",
            0,
        )


        shares = counts.get(
            "share_click",
            0,
        )


        actions = (
            whatsapp
            + calls
            + directions
            + shares
        )


        action_rate = (
            round(
                (
                    actions
                    / views
                )
                * 100,
                1,
            )
            if views
            else 0.0
        )


        listing_reports.append(
            {
                "item":
                    item,

                "views":
                    views,

                "actions":
                    actions,

                "whatsapp":
                    whatsapp,

                "calls":
                    calls,

                "directions":
                    directions,

                "shares":
                    shares,

                "action_rate":
                    action_rate,
            }
        )


    # =====================================================
    # RANK BY ATTENTION
    # =====================================================

    listing_reports.sort(
        key=lambda row: (
            row["views"],
            row["actions"],
        ),
        reverse=True,
    )


    # =====================================================
    # PLATFORM TOTALS
    # =====================================================

    total_views = sum(
        row["views"]
        for row
        in listing_reports
    )


    total_actions = sum(
        row["actions"]
        for row
        in listing_reports
    )


    total_whatsapp = sum(
        row["whatsapp"]
        for row
        in listing_reports
    )


    total_calls = sum(
        row["calls"]
        for row
        in listing_reports
    )


    total_directions = sum(
        row["directions"]
        for row
        in listing_reports
    )


    total_shares = sum(
        row["shares"]
        for row
        in listing_reports
    )


    overall_action_rate = (
        round(
            (
                total_actions
                / total_views
            )
            * 100,
            1,
        )
        if total_views
        else 0.0
    )


    totals = {

        "views":
            total_views,

        "actions":
            total_actions,

        "whatsapp":
            total_whatsapp,

        "calls":
            total_calls,

        "directions":
            total_directions,

        "shares":
            total_shares,

        "action_rate":
            overall_action_rate,

    }


    return render_template(
        "admin/business_analytics.html",

        analytics_range=
            analytics_range,

        listing_reports=
            listing_reports,

        totals=
            totals,
    )


# =========================================================
# BUSINESS ANALYTICS — LISTING DETAIL
# =========================================================

@admin_bp.route(
    "/business-analytics/<int:item_id>"
)
def business_analytics_detail(
    item_id,
):

    auth = require_admin()

    if auth:
        return auth


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
    # ONLY BUSINESS / PROMOTION
    # =====================================================

    if (
        item.listing_level
        not in {
            "business",
            "promotion",
        }
    ):

        flash(
            (
                "Business analytics are available "
                "for Business and Promotion listings."
            ),
            "error",
        )


        return redirect(
            url_for(
                "admin.business_analytics"
            )
        )


    analytics_range = (
        _get_analytics_date_range()
    )


    analytics = (
        _build_business_analytics(
            item,
            analytics_range,
        )
    )


    return render_template(
        "admin/business_analytics_detail.html",

        item=
            item,

        analytics=
            analytics,

        analytics_range=
            analytics_range,
    )

@admin_bp.route(
    "/categories/<int:category_id>/delete",
    methods=["POST"]
)
def delete_category(category_id):

    auth = require_admin()
    if auth:
        return auth

    category = Category.query.get_or_404(
        category_id
    )

    category_name = category.name
    category_slug = category.slug

    try:

        # -----------------------------------------
        # Find content using this category
        # -----------------------------------------

        items = ContentItem.query.filter_by(
            category=category_slug
        ).all()

        for item in items:

            # Disconnect approved submissions
            submissions = PendingSubmission.query.filter_by(
                published_content_id=item.id
            ).all()

            for submission in submissions:
                submission.published_content_id = None

            # Delete images
            ContentImage.query.filter_by(
                content_item_id=item.id
            ).delete(
                synchronize_session=False
            )

            db.session.delete(item)

        # -----------------------------------------
        # Delete pending submissions in category
        # -----------------------------------------

        pending_submissions = (
            PendingSubmission.query.filter_by(
                category=category_slug
            ).all()
        )

        for submission in pending_submissions:

            PendingSubmissionImage.query.filter_by(
                submission_id=submission.id
            ).delete(
                synchronize_session=False
            )

            db.session.delete(
                submission
            )

        # -----------------------------------------
        # Handle category-specific QR access points
        # -----------------------------------------

        access_points = AccessPoint.query.filter_by(
            default_category=category_slug
        ).all()

        for point in access_points:

            # Turn them back into general QR points
            point.qr_type = "general"
            point.default_category = None

        # -----------------------------------------
        # Delete category itself
        # -----------------------------------------

        db.session.delete(category)
        db.session.commit()

        flash(
            f"{category_name} permanently deleted.",
            "success"
        )

    except Exception as exc:

        db.session.rollback()

        flash(
            f"Unable to delete category: {exc}",
            "error"
        )

    return redirect(
        url_for("admin.categories")
    )

def get_expiry_intelligence(item, today=None):
    today = today or date.today()
    if not item.active or item.archived:
        return None

    expiry_date = get_content_expiry_date(item)
    if not expiry_date:
        return None

    days_remaining = (expiry_date - today).days
    if 0 <= days_remaining <= EXPIRING_SOON_DAYS:
        return {
            "expiring_soon": True,
            "days_remaining": days_remaining,
            "expiry_date": expiry_date,
        }

    return None


def archive_expired_content():
    today = date.today()
    items = ContentItem.query.filter(ContentItem.archived.is_(False)).all()
    archived_count = 0

    for item in items:
        info = get_archive_intelligence(item, today)
        if info and info["ready_to_archive"]:
            item.archived = True
            item.active = False

            item.archived_at = (
               datetime.utcnow()
            )
            archived_count += 1

    if archived_count:
        db.session.commit()

    return archived_count


def get_public_base_url():
    return os.environ.get(
        "PUBLIC_BASE_URL",
        "https://lac-local-access.onrender.com",
    ).rstrip("/")


def get_access_point_qr_url(access_point):
    """
    Every QR points to the access point route.

    General QR:
        /q/KWM-TAXI-001

    Category-specific QR:
        /q/KWM-GROC-001

    The /q/<code> route decides whether to show
    categories or redirect to default_category.
    """
    return (
        f"{get_public_base_url()}"
        f"/q/{access_point.code}"
    )

def get_public_base_url():
    return os.environ.get(
        "PUBLIC_BASE_URL",
        "https://lac-local-access.onrender.com",
    ).rstrip("/")


def get_access_point_qr_url(access_point):
    """
    Every QR points to the access point route.

    General QR:
        /q/KWM-TAXI-001

    Category-specific QR:
        /q/KWM-GROC-001

    The /q/<code> route decides whether to show
    categories or redirect to default_category.
    """
    return (
        f"{get_public_base_url()}"
        f"/q/{access_point.code}"
    )


def _get_pricing_model(
    category,
    content_type=None,
):

    # =====================================================
    # NORMALIZE VALUES
    # =====================================================

    category = (
        str(
            category
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
        or None
    )


    # =====================================================
    # CONTENT TYPE OVERRIDE
    #
    # More specific rule wins first.
    # =====================================================

    override_key = (
        category,
        content_type,
    )


    if (
        override_key
        in KALXA_PRICING_MODEL_OVERRIDES
    ):

        return (
            KALXA_PRICING_MODEL_OVERRIDES[
                override_key
            ]
        )


    # =====================================================
    # PRESENCE CATEGORY
    # =====================================================

    if (
        category
        in KALXA_PRESENCE_CATEGORIES
    ):

        return (
            PRICING_MODEL_PRESENCE
        )


    # =====================================================
    # CAMPAIGN CATEGORY
    # =====================================================

    if (
        category
        in KALXA_CAMPAIGN_CATEGORIES
    ):

        return (
            PRICING_MODEL_CAMPAIGN
        )


    # =====================================================
    # NO COMMERCIAL MODEL YET
    #
    # IMPORTANT:
    #
    # Do NOT guess.
    #
    # Existing/community content can continue working while
    # we decide how a new category should be monetized.
    # =====================================================

    return None

def _get_content_workflow(
    category,
    content_type,
):

    # =====================================================
    # NORMALIZE INPUT
    # =====================================================

    category = (
        category or ""
    ).strip().lower()


    content_type = (
        content_type or ""
    ).strip().lower()


    # =====================================================
    # CHECK EXISTING CONTENT-TYPE WORKFLOW
    # =====================================================

    category_workflows = (
        ADMIN_CONTENT_WORKFLOWS.get(
            category,
            {},
        )
    )


    workflow = (
        category_workflows.get(
            content_type
        )
    )


    # =====================================================
    # USE EXISTING SPECIFIC WORKFLOW
    # =====================================================

    if workflow:

        # ---------------------------------------------
        # Make a copy.
        #
        # This prevents us from modifying the original
        # ADMIN_CONTENT_WORKFLOWS dictionary.
        # ---------------------------------------------

        workflow = dict(
            workflow
        )


    # =====================================================
    # EVENTS ALWAYS EXPIRE
    # =====================================================

    elif category == "events":

        workflow = {

            "lifetime_type":
                "time_specific",

            "notification_eligible":
                True,

        }


    # =====================================================
    # ONLY THESE CATEGORIES ARE ONGOING
    # =====================================================

    elif category in {

        "property",

        "transport",

        "services",

    }:

        workflow = {

            "lifetime_type":
                "ongoing",

            "notification_eligible":
                False,

        }


    # =====================================================
    # EVERYTHING ELSE EXPIRES
    # =====================================================

    else:

        workflow = {

            "lifetime_type":
                "time_specific",

            "notification_eligible":
                True,

        }


    # =====================================================
    # ADD KALXA COMMERCIAL PRICING MODEL
    # =====================================================
    #
    # This does NOT calculate a price.
    #
    # It tells Kalxa which commercial pricing engine
    # applies to this category / content type:
    #
    # presence
    #     Duration-based pricing.
    #
    # campaign
    #     Duration × geographic reach pricing.
    #
    # None
    #     No commercial pricing rule has been assigned.
    # =====================================================

    workflow[
        "pricing_model"
    ] = (
        _get_pricing_model(
            category,
            content_type,
        )
    )


    # =====================================================
    # RETURN COMPLETE WORKFLOW
    # =====================================================

    return workflow

def _calculate_content_price(
    category,
    content_type,
    duration_days,
    zone_count=1,
):

    # =====================================================
    # GET CONTENT WORKFLOW
    # =====================================================

    workflow = (
        _get_content_workflow(
            category,
            content_type,
        )
    )


    pricing_model = (
        workflow.get(
            "pricing_model"
        )
    )


    # =====================================================
    # CONTENT WITHOUT COMMERCIAL PRICING
    # =====================================================

    if not pricing_model:

        return None


    # =====================================================
    # PRESENCE
    #
    # Geographic reach does not affect Presence pricing.
    # =====================================================

    if (
        pricing_model
        == PRICING_MODEL_PRESENCE
    ):

        zone_count = 1


    # =====================================================
    # CALCULATE
    # =====================================================

    return calculate_kalxa_price(
        pricing_model=
            pricing_model,

        duration_days=
            duration_days,

        zone_count=
            zone_count,
    )


def create_access_point_qr(access_point):

    destination_url = (
        get_access_point_qr_url(
            access_point
        )
    )

    qr = qrcode.QRCode(
        version=None,
        error_correction=(
            qrcode.constants.ERROR_CORRECT_H
        ),
        box_size=12,
        border=4,
    )

    qr.add_data(
        destination_url
    )

    qr.make(
        fit=True
    )

    image = qr.make_image(
        fill_color="black",
        back_color="white",
    )

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="PNG",
    )

    buffer.seek(0)

    return buffer


@admin_bp.route(
    "/access-points/<int:access_point_id>/qr"
)
def access_point_qr(access_point_id):

    access_point = (
        AccessPoint.query
        .get_or_404(access_point_id)
    )

    qr_buffer = (
        create_access_point_qr(
            access_point
        )
    )

    return send_file(
        qr_buffer,
        mimetype="image/png",
    )


# ============================================================
# LISTING CLAIMS
# ============================================================

@admin_bp.route(
    "/claims"
)
def claims():

    # ========================================================
    # GET FILTER
    # ========================================================

    status_filter = (
        request.args.get(
            "status",
            "pending",
        )
        .strip()
        .lower()
    )


    allowed_statuses = {
        "pending",
        "approved",
        "rejected",
        "all",
    }


    if status_filter not in allowed_statuses:

        status_filter = "pending"


    # ========================================================
    # BASE QUERY
    # ========================================================

    query = ListingClaim.query


    # ========================================================
    # FILTER BY STATUS
    # ========================================================

    if status_filter != "all":

        query = query.filter(
            ListingClaim.status
            == status_filter
        )


    # ========================================================
    # LOAD CLAIMS
    # ========================================================

    claim_items = (
        query
        .order_by(
            ListingClaim.created_at.desc()
        )
        .all()
    )


    # ========================================================
    # COUNTS
    # ========================================================

    pending_count = (
        ListingClaim.query
        .filter(
            ListingClaim.status
            == "pending"
        )
        .count()
    )


    approved_count = (
        ListingClaim.query
        .filter(
            ListingClaim.status
            == "approved"
        )
        .count()
    )


    rejected_count = (
        ListingClaim.query
        .filter(
            ListingClaim.status
            == "rejected"
        )
        .count()
    )


    total_count = (
        ListingClaim.query
        .count()
    )


    # ========================================================
    # RENDER
    # ========================================================

    return render_template(
        "admin/claims.html",

        claims=claim_items,

        status_filter=status_filter,

        pending_count=pending_count,

        approved_count=approved_count,

        rejected_count=rejected_count,

        total_count=total_count,
    )


# ============================================================
# LISTING CLAIM DETAIL
# ============================================================

@admin_bp.route(
    "/claims/<int:claim_id>"
)
def claim_detail(
    claim_id,
):

    claim = (
        ListingClaim.query
        .filter_by(
            id=claim_id
        )
        .first_or_404()
    )


    item = claim.content_item


    return render_template(
        "admin/claim_detail.html",

        claim=claim,

        item=item,
    )


# ============================================================
# APPROVE LISTING CLAIM
# ============================================================

@admin_bp.route(
    "/claims/<int:claim_id>/approve",
    methods=["POST"],
)
def approve_claim(
    claim_id,
):

    # ========================================================
    # FIND CLAIM
    # ========================================================

    claim = (
        ListingClaim.query
        .filter_by(
            id=claim_id
        )
        .first_or_404()
    )


    item = claim.content_item


    # ========================================================
    # CLAIM MUST STILL BE PENDING
    # ========================================================

    if claim.status != "pending":

        flash(
            "This claim has already been reviewed.",
            "warning",
        )

        return redirect(
            url_for(
                "admin.claim_detail",
                claim_id=claim.id,
            )
        )


    # ========================================================
    # LISTING MUST STILL BE CLAIMABLE
    # ========================================================

    if not item.can_be_claimed():

        flash(
            (
                "This listing can no longer be claimed. "
                "Its ownership or listing level has changed."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "admin.claim_detail",
                claim_id=claim.id,
            )
        )


    # ========================================================
    # ADMIN NOTES
    # ========================================================

    admin_notes = (
        request.form.get(
            "admin_notes",
            ""
        )
        .strip()
    )


    # ========================================================
    # APPROVE CLAIM
    # ========================================================
    #
    # Discovery
    #     ↓
    # Business
    #
    # Unclaimed
    #     ↓
    # Claimed
    #
    # Verification is granted because an administrator
    # has explicitly reviewed and approved the claim.
    #
    # ========================================================

    claim.status = "approved"

    claim.reviewed_at = datetime.utcnow()

    claim.admin_notes = (
        admin_notes
        if admin_notes
        else None
    )


    item.listing_level = "business"

    item.ownership_status = "claimed"

    item.is_verified = True


    # ========================================================
    # FEATURED IS NOT AUTOMATIC
    # ========================================================
    #
    # Claiming a listing does NOT turn it into paid
    # Promotion.
    #
    # This also protects against an old seeded listing
    # accidentally retaining featured=True.
    #
    # ========================================================

    item.featured = False


    # ========================================================
    # NOTIFICATION DISTRIBUTION IS NOT AUTOMATIC
    # ========================================================

    item.notification_eligible = False


    # ========================================================
    # SAVE
    # ========================================================

    try:

        db.session.commit()


    except Exception as exc:

        db.session.rollback()


        current_app.logger.exception(
            "Failed to approve listing claim. "
            "claim_id=%s content_item_id=%s error=%s",
            claim.id,
            item.id,
            exc,
        )


        flash(
            "The claim could not be approved.",
            "error",
        )


        return redirect(
            url_for(
                "admin.claim_detail",
                claim_id=claim.id,
            )
        )


    flash(
        (
            "Claim approved. "
            "The listing is now business-controlled."
        ),
        "success",
    )


    return redirect(
        url_for(
            "admin.claim_detail",
            claim_id=claim.id,
        )
    )


# ============================================================
# REJECT LISTING CLAIM
# ============================================================

@admin_bp.route(
    "/claims/<int:claim_id>/reject",
    methods=["POST"],
)
def reject_claim(
    claim_id,
):

    # ========================================================
    # FIND CLAIM
    # ========================================================

    claim = (
        ListingClaim.query
        .filter_by(
            id=claim_id
        )
        .first_or_404()
    )


    # ========================================================
    # CLAIM MUST STILL BE PENDING
    # ========================================================

    if claim.status != "pending":

        flash(
            "This claim has already been reviewed.",
            "warning",
        )

        return redirect(
            url_for(
                "admin.claim_detail",
                claim_id=claim.id,
            )
        )


    # ========================================================
    # ADMIN NOTES
    # ========================================================

    admin_notes = (
        request.form.get(
            "admin_notes",
            ""
        )
        .strip()
    )


    # ========================================================
    # REJECT CLAIM
    # ========================================================
    #
    # IMPORTANT:
    #
    # We only update the claim.
    #
    # The ContentItem remains:
    #
    # listing_level = discovery
    # ownership_status = unclaimed
    # is_verified = False
    #
    # This means another legitimate owner can claim it later.
    #
    # ========================================================

    claim.status = "rejected"

    claim.reviewed_at = datetime.utcnow()

    claim.admin_notes = (
        admin_notes
        if admin_notes
        else None
    )


    # ========================================================
    # SAVE
    # ========================================================

    try:

        db.session.commit()


    except Exception as exc:

        db.session.rollback()


        current_app.logger.exception(
            "Failed to reject listing claim. "
            "claim_id=%s error=%s",
            claim.id,
            exc,
        )


        flash(
            "The claim could not be rejected.",
            "error",
        )


        return redirect(
            url_for(
                "admin.claim_detail",
                claim_id=claim.id,
            )
        )


    flash(
        "Claim rejected.",
        "success",
    )


    return redirect(
        url_for(
            "admin.claim_detail",
            claim_id=claim.id,
        )
    )



@admin_bp.route(
    "/access-points/"
    "<int:access_point_id>/qr/download"
)
def download_access_point_qr(
    access_point_id,
):

    access_point = (
        AccessPoint.query
        .get_or_404(access_point_id)
    )

    qr_buffer = (
        create_access_point_qr(
            access_point
        )
    )

    filename = (
        f"LaC-{access_point.code}.png"
    )

    return send_file(
        qr_buffer,
        mimetype="image/png",
        as_attachment=True,
        download_name=filename,
    )


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        correct_password = os.environ.get("LAC_ADMIN_PASSWORD")

        if not correct_password:
            flash("Admin password is not configured.", "error")
            return render_template("admin/login.html")

        if password == correct_password:
            session["lac_admin"] = True
            return redirect(url_for("admin.analytics"))

        flash("Incorrect password.", "error")

    return render_template("admin/login.html")


@admin_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))



@admin_bp.route("/")
@admin_bp.route("/analytics")
def analytics():

    auth = require_admin()

    if auth:
        return auth


    # =========================================================
    # DATE WINDOWS
    # =========================================================

    now = datetime.utcnow()

    today_start = datetime(
        now.year,
        now.month,
        now.day,
    )

    tomorrow_start = (
        today_start
        + timedelta(days=1)
    )

    seven_days_ago = (
        today_start
        - timedelta(days=6)
    )

    fourteen_days_ago = (
        today_start
        - timedelta(days=13)
    )

    thirty_days_ago = (
        today_start
        - timedelta(days=29)
    )

    previous_7_start = (
        seven_days_ago
        - timedelta(days=7)
    )


    # =========================================================
    # QR SCAN ANALYTICS
    # =========================================================

    total_scans = (
        QRScan.query
        .filter(
            QRScan.event_type == "scan"
        )
        .count()
    )


    today_scans = (
        QRScan.query
        .filter(
            QRScan.event_type == "scan",
            QRScan.scanned_at >= today_start,
            QRScan.scanned_at < tomorrow_start,
        )
        .count()
    )


    seven_day_scans = (
        QRScan.query
        .filter(
            QRScan.event_type == "scan",
            QRScan.scanned_at >= seven_days_ago,
        )
        .count()
    )


    thirty_day_scans = (
        QRScan.query
        .filter(
            QRScan.event_type == "scan",
            QRScan.scanned_at >= thirty_days_ago,
        )
        .count()
    )


    previous_7_scans = (
        QRScan.query
        .filter(
            QRScan.event_type == "scan",
            QRScan.scanned_at >= previous_7_start,
            QRScan.scanned_at < seven_days_ago,
        )
        .count()
    )


    if previous_7_scans > 0:

        seven_day_growth = (
            (
                seven_day_scans
                - previous_7_scans
            )
            / previous_7_scans
        ) * 100

    elif seven_day_scans > 0:

        seven_day_growth = 100.0

    else:

        seven_day_growth = 0.0


    # =========================================================
    # ACCESS POINT METRICS
    # =========================================================

    total_access_points = (
        AccessPoint.query
        .count()
    )


    active_access_points = (
        AccessPoint.query
        .filter(
            AccessPoint.active.is_(True)
        )
        .count()
    )


    scans_per_access_point = (
        thirty_day_scans
        / active_access_points

        if active_access_points

        else 0
    )


    # =========================================================
    # CATEGORY ACTIVITY
    # =========================================================

    total_category_views = (
        QRScan.query
        .filter(
            QRScan.event_type
            == "category_view"
        )
        .count()
    )


    category_results = (
        db.session.query(
            QRScan.category_selected,
            func.count(
                QRScan.id
            ).label(
                "view_count"
            ),
        )
        .filter(
            QRScan.event_type
            == "category_view",

            QRScan.category_selected
            .isnot(None),
        )
        .group_by(
            QRScan.category_selected
        )
        .order_by(
            func.count(
                QRScan.id
            ).desc()
        )
        .all()
    )


    category_map = {
        c.slug: c
        for c in get_categories(
            active_only=False
        )
    }


    category_activity = []


    for result in category_results:

        percentage = (
            (
                result.view_count
                / total_category_views
            )
            * 100

            if total_category_views

            else 0
        )


        record = (
            category_map.get(
                result.category_selected
            )
        )


        category_activity.append({

            "category":
                result.category_selected,

            "name":
                (
                    record.name
                    if record
                    else result.category_selected
                ),

            "icon":
                (
                    record.icon
                    if record
                    else ""
                ),

            "views":
                result.view_count,

            "percentage":
                round(
                    percentage,
                    1,
                ),

        })


    # =========================================================
    # TOP QR LOCATIONS
    # =========================================================

    top_locations_query = (
        db.session.query(

            AccessPoint.id,

            AccessPoint.code,

            AccessPoint.name,

            AccessPoint.location_type,

            Zone.name.label(
                "zone_name"
            ),

            func.count(
                QRScan.id
            ).label(
                "scan_count"
            ),

        )
        .join(
            Zone,
            AccessPoint.zone_id
            == Zone.id,
        )
        .outerjoin(
            QRScan,

            (
                QRScan.access_point_id
                == AccessPoint.id
            )
            &
            (
                QRScan.event_type
                == "scan"
            ),
        )
        .group_by(

            AccessPoint.id,

            AccessPoint.code,

            AccessPoint.name,

            AccessPoint.location_type,

            Zone.name,

        )
        .order_by(
            func.count(
                QRScan.id
            ).desc()
        )
        .limit(10)
        .all()
    )


    top_locations = []


    for point in top_locations_query:

        share = (
            (
                point.scan_count
                / total_scans
            )
            * 100

            if total_scans

            else 0
        )


        top_locations.append({

            "id":
                point.id,

            "code":
                point.code,

            "name":
                point.name,

            "location_type":
                point.location_type,

            "zone_name":
                point.zone_name,

            "scan_count":
                point.scan_count,

            "share":
                round(
                    share,
                    1,
                ),

        })


    # =========================================================
    # ZONE ACTIVITY
    # =========================================================

    zone_query = (
        db.session.query(

            Zone.id,

            Zone.name,

            func.count(
                QRScan.id
            ).label(
                "scan_count"
            ),

        )
        .outerjoin(
            AccessPoint,
            AccessPoint.zone_id
            == Zone.id,
        )
        .outerjoin(
            QRScan,

            (
                QRScan.access_point_id
                == AccessPoint.id
            )
            &
            (
                QRScan.event_type
                == "scan"
            ),
        )
        .group_by(
            Zone.id,
            Zone.name,
        )
        .order_by(
            func.count(
                QRScan.id
            ).desc()
        )
        .all()
    )


    zone_activity = []


    for zone in zone_query:

        percentage = (
            (
                zone.scan_count
                / total_scans
            )
            * 100

            if total_scans

            else 0
        )


        zone_activity.append({

            "name":
                zone.name,

            "scan_count":
                zone.scan_count,

            "percentage":
                round(
                    percentage,
                    1,
                ),

        })


    # =========================================================
    # LOW PERFORMING ACCESS POINTS
    # =========================================================

    recent_scan_counts = dict(

        db.session.query(
            QRScan.access_point_id,
            func.count(
                QRScan.id
            ),
        )
        .filter(
            QRScan.event_type
            == "scan",

            QRScan.scanned_at
            >= thirty_days_ago,
        )
        .group_by(
            QRScan.access_point_id
        )
        .all()

    )


    low_performing_points = []


    for point in (
        AccessPoint.query
        .filter(
            AccessPoint.active.is_(True)
        )
        .all()
    ):

        scan_count = (
            recent_scan_counts.get(
                point.id,
                0,
            )
        )


        if scan_count <= 5:

            low_performing_points.append({

                "name":
                    point.name,

                "code":
                    point.code,

                "zone":
                    point.zone.name,

                "scan_count":
                    scan_count,

            })


    low_performing_points.sort(
        key=lambda row:
            row["scan_count"]
    )


    # =========================================================
    # DAILY SCAN TREND
    # =========================================================

    scan_rows = (
        QRScan.query
        .filter(
            QRScan.event_type
            == "scan",

            QRScan.scanned_at
            >= fourteen_days_ago,
        )
        .all()
    )


    daily_counts = {}


    for number in range(14):

        day = (
            fourteen_days_ago.date()
            + timedelta(
                days=number
            )
        )

        daily_counts[
            day
        ] = 0


    for scan in scan_rows:

        scan_day = (
            scan.scanned_at.date()
        )


        if scan_day in daily_counts:

            daily_counts[
                scan_day
            ] += 1


    max_daily_scans = max(
        daily_counts.values(),
        default=0,
    )


    daily_scan_trend = []


    for scan_date, count in daily_counts.items():

        bar_percentage = (
            (
                count
                / max_daily_scans
            )
            * 100

            if max_daily_scans

            else 0
        )


        daily_scan_trend.append({

            "date":
                scan_date,

            "label":
                scan_date.strftime(
                    "%d %b"
                ),

            "count":
                count,

            "bar_percentage":
                round(
                    bar_percentage,
                    1,
                ),

        })


    # =========================================================
    # CATEGORY ENGAGEMENT RATE
    # =========================================================

    category_engagement_rate = (
        (
            total_category_views
            / total_scans
        )
        * 100

        if total_scans

        else 0
    )


    # =========================================================
    # RECENT SCANS
    # =========================================================

    recent_scans = (
        QRScan.query
        .filter(
            QRScan.event_type
            == "scan"
        )
        .order_by(
            QRScan.scanned_at.desc()
        )
        .limit(20)
        .all()
    )


    # =========================================================
    # PENDING SUBMISSIONS
    # =========================================================

    pending_submissions_count = (
        PendingSubmission.query
        .filter_by(
            status="pending"
        )
        .count()
    )


    # =========================================================
    # ENGAGEMENT ANALYTICS
    # =========================================================

    total_listing_views = (
        EngagementEvent.query
        .filter_by(
            event_type="listing_view"
        )
        .count()
    )


    total_whatsapp_clicks = (
        EngagementEvent.query
        .filter_by(
            event_type="whatsapp_click"
        )
        .count()
    )


    total_call_clicks = (
        EngagementEvent.query
        .filter_by(
            event_type="call_click"
        )
        .count()
    )


    total_share_clicks = (
        EngagementEvent.query
        .filter_by(
            event_type="share_click"
        )
        .count()
    )


    total_directions_clicks = (
        EngagementEvent.query
        .filter_by(
            event_type="directions_click"
        )
        .count()
    )


    total_useful_actions = (

        total_whatsapp_clicks
        +
        total_call_clicks
        +
        total_share_clicks
        +
        total_directions_clicks

    )


    if total_listing_views > 0:

        listing_action_rate = round(
            (
                total_useful_actions
                / total_listing_views
            )
            * 100,
            1,
        )

    else:

        listing_action_rate = 0


    # =========================================================
    # TOP LISTINGS / CONTENT PERFORMANCE
    # =========================================================

    content_performance_rows = (

        db.session.query(

            EngagementEvent.content_item_id,

            db.func.sum(
                db.case(
                    (
                        EngagementEvent.event_type
                        == "listing_view",
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "listing_views"
            ),

            db.func.sum(
                db.case(
                    (
                        EngagementEvent.event_type
                        == "whatsapp_click",
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "whatsapp_clicks"
            ),

            db.func.sum(
                db.case(
                    (
                        EngagementEvent.event_type
                        == "call_click",
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "call_clicks"
            ),

            db.func.sum(
                db.case(
                    (
                        EngagementEvent.event_type
                        == "directions_click",
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "directions_clicks"
            ),

            db.func.sum(
                db.case(
                    (
                        EngagementEvent.event_type
                        == "share_click",
                        1,
                    ),
                    else_=0,
                )
            ).label(
                "share_clicks"
            ),

        )

        .filter(
            EngagementEvent.content_item_id
            .isnot(None)
        )

        .group_by(
            EngagementEvent.content_item_id
        )

        .all()

    )


    content_performance = []


    for row in content_performance_rows:

        item = (
            db.session.get(
                ContentItem,
                row.content_item_id,
            )
        )


        # Content may have been deleted after
        # historical engagement was recorded.

        if item is None:
            continue


        views = int(
            row.listing_views
            or 0
        )

        whatsapp = int(
            row.whatsapp_clicks
            or 0
        )

        calls = int(
            row.call_clicks
            or 0
        )

        directions = int(
            row.directions_clicks
            or 0
        )

        shares = int(
            row.share_clicks
            or 0
        )


        useful_actions = (
            whatsapp
            + calls
            + directions
            + shares
        )


        if views > 0:

            actions_per_100_views = round(
                (
                    useful_actions
                    / views
                )
                * 100,
                1,
            )

        else:

            actions_per_100_views = 0


        content_performance.append({

            "id":
                item.id,

            "title":
                item.title,

            "category":
                item.category,

            "zone_id":
                item.zone_id,

            "views":
                views,

            "whatsapp":
                whatsapp,

            "calls":
                calls,

            "directions":
                directions,

            "shares":
                shares,

            "useful_actions":
                useful_actions,

            "actions_per_100_views":
                actions_per_100_views,

        })


    content_performance.sort(

        key=lambda row: (
            row["useful_actions"],
            row["views"],
        ),

        reverse=True,

    )


    top_content_performance = (
        content_performance[:10]
    )


    # =========================================================
    # MOST VIEWED LISTINGS
    # =========================================================

    most_viewed_listings = sorted(

        content_performance,

        key=lambda row:
            row["views"],

        reverse=True,

    )[:5]


    # =========================================================
    # HIGH INTEREST / LOW ACTION
    # =========================================================

    high_interest_low_action = [

        row

        for row in content_performance

        if (
            row["views"] > 0
            and
            row["useful_actions"] == 0
        )

    ]


    high_interest_low_action.sort(

        key=lambda row:
            row["views"],

        reverse=True,

    )


    high_interest_low_action = (
        high_interest_low_action[:5]
    )


    # =========================================================
    # ACCESS POINT PERFORMANCE
    # =========================================================

    engagement_by_access_point = {}


    engagement_access_rows = (

        db.session.query(

            EngagementEvent.access_point_id,

            EngagementEvent.event_type,

            func.count(
                EngagementEvent.id
            ).label(
                "event_count"
            ),

        )

        .filter(
            EngagementEvent.access_point_id
            .isnot(None)
        )

        .group_by(
            EngagementEvent.access_point_id,
            EngagementEvent.event_type,
        )

        .all()

    )


    for row in engagement_access_rows:

        if (
            row.access_point_id
            not in engagement_by_access_point
        ):

            engagement_by_access_point[
                row.access_point_id
            ] = {}


        engagement_by_access_point[
            row.access_point_id
        ][
            row.event_type
        ] = row.event_count


    access_point_performance = []


    for point in top_locations:

        events = (
            engagement_by_access_point.get(
                point["id"],
                {},
            )
        )


        listing_views = int(
            events.get(
                "listing_view",
                0,
            )
        )


        useful_actions = (

            int(
                events.get(
                    "whatsapp_click",
                    0,
                )
            )

            +

            int(
                events.get(
                    "call_click",
                    0,
                )
            )

            +

            int(
                events.get(
                    "directions_click",
                    0,
                )
            )

            +

            int(
                events.get(
                    "share_click",
                    0,
                )
            )

        )


        if listing_views > 0:

            actions_per_100_views = round(
                (
                    useful_actions
                    / listing_views
                )
                * 100,
                1,
            )

        else:

            actions_per_100_views = 0


        access_point_performance.append({

            **point,

            "listing_views":
                listing_views,

            "useful_actions":
                useful_actions,

            "actions_per_100_views":
                actions_per_100_views,

        })


    # =========================================================
    # ZONE PERFORMANCE
    # =========================================================

    zone_engagement_rows = (

        db.session.query(

            EngagementEvent.zone_id,

            EngagementEvent.event_type,

            func.count(
                EngagementEvent.id
            ).label(
                "event_count"
            ),

        )

        .filter(
            EngagementEvent.zone_id
            .isnot(None)
        )

        .group_by(
            EngagementEvent.zone_id,
            EngagementEvent.event_type,
        )

        .all()

    )


    zone_engagement_map = {}


    for row in zone_engagement_rows:

        if (
            row.zone_id
            not in zone_engagement_map
        ):

            zone_engagement_map[
                row.zone_id
            ] = {}


        zone_engagement_map[
            row.zone_id
        ][
            row.event_type
        ] = row.event_count


    zone_scan_map = {

        zone.name:
            zone.scan_count

        for zone in zone_query

    }


    zone_performance = []


    all_zones = (
        Zone.query
        .order_by(
            Zone.name.asc()
        )
        .all()
    )


    for zone in all_zones:

        events = (
            zone_engagement_map.get(
                zone.id,
                {},
            )
        )


        listing_views = int(
            events.get(
                "listing_view",
                0,
            )
        )


        useful_actions = (

            int(
                events.get(
                    "whatsapp_click",
                    0,
                )
            )

            +

            int(
                events.get(
                    "call_click",
                    0,
                )
            )

            +

            int(
                events.get(
                    "directions_click",
                    0,
                )
            )

            +

            int(
                events.get(
                    "share_click",
                    0,
                )
            )

        )


        scan_count = int(
            zone_scan_map.get(
                zone.name,
                0,
            )
        )


        percentage = (

            scan_count
            / total_scans
            * 100

            if total_scans

            else 0

        )


        zone_performance.append({

            "id":
                zone.id,

            "name":
                zone.name,

            "scan_count":
                scan_count,

            "listing_views":
                listing_views,

            "useful_actions":
                useful_actions,

            "percentage":
                round(
                    percentage,
                    1,
                ),

        })


    zone_performance.sort(

        key=lambda row:
            row["scan_count"],

        reverse=True,

    )


    # =========================================================
    # CATEGORY PERFORMANCE
    # =========================================================

    category_engagement_rows = (

        db.session.query(

            EngagementEvent.category,

            EngagementEvent.event_type,

            func.count(
                EngagementEvent.id
            ).label(
                "event_count"
            ),

        )

        .filter(
            EngagementEvent.category
            .isnot(None)
        )

        .group_by(
            EngagementEvent.category,
            EngagementEvent.event_type,
        )

        .all()

    )


    category_engagement_map = {}


    for row in category_engagement_rows:

        if (
            row.category
            not in category_engagement_map
        ):

            category_engagement_map[
                row.category
            ] = {}


        category_engagement_map[
            row.category
        ][
            row.event_type
        ] = row.event_count


    category_view_map = {

        item["category"]:
            item["views"]

        for item in category_activity

    }


    category_performance = []


    all_category_slugs = (
        set(
            category_view_map.keys()
        )
        |
        set(
            category_engagement_map.keys()
        )
    )


    for slug in all_category_slugs:

        events = (
            category_engagement_map.get(
                slug,
                {},
            )
        )


        category_record = (
            category_map.get(
                slug
            )
        )


        whatsapp = int(
            events.get(
                "whatsapp_click",
                0,
            )
        )


        calls = int(
            events.get(
                "call_click",
                0,
            )
        )


        directions = int(
            events.get(
                "directions_click",
                0,
            )
        )


        shares = int(
            events.get(
                "share_click",
                0,
            )
        )


        useful_actions = (
            whatsapp
            + calls
            + directions
            + shares
        )


        category_performance.append({

            "slug":
                slug,

            "name":
                (
                    category_record.name

                    if category_record

                    else slug.replace(
                        "-",
                        " ",
                    ).title()
                ),

            "icon":
                (
                    category_record.icon

                    if category_record

                    else ""
                ),

            "category_views":
                int(
                    category_view_map.get(
                        slug,
                        0,
                    )
                ),

            "listing_views":
                int(
                    events.get(
                        "listing_view",
                        0,
                    )
                ),

            "whatsapp":
                whatsapp,

            "calls":
                calls,

            "directions":
                directions,

            "shares":
                shares,

            "useful_actions":
                useful_actions,

        })


    category_performance.sort(

        key=lambda row: (

            row["listing_views"],
            row["useful_actions"],

        ),

        reverse=True,

    )


    # =========================================================
    # DAILY NETWORK ACTIVITY
    # =========================================================

    engagement_14_day_rows = (

        EngagementEvent.query

        .filter(
            EngagementEvent.created_at
            >= fourteen_days_ago
        )

        .all()

    )


    daily_engagement_counts = {}


    for number in range(14):

        day = (
            fourteen_days_ago.date()
            +
            timedelta(
                days=number
            )
        )


        daily_engagement_counts[
            day
        ] = {

            "listing_views":
                0,

            "useful_actions":
                0,

        }


    useful_event_types = {

        "whatsapp_click",

        "call_click",

        "directions_click",

        "share_click",

    }


    for event in engagement_14_day_rows:

        event_day = (
            event.created_at.date()
        )


        if (
            event_day
            not in daily_engagement_counts
        ):

            continue


        if (
            event.event_type
            == "listing_view"
        ):

            daily_engagement_counts[
                event_day
            ][
                "listing_views"
            ] += 1


        elif (
            event.event_type
            in useful_event_types
        ):

            daily_engagement_counts[
                event_day
            ][
                "useful_actions"
            ] += 1


    daily_activity = []


    for scan_day in daily_scan_trend:

        day = (
            scan_day["date"]
        )


        engagement = (
            daily_engagement_counts.get(
                day,
                {},
            )
        )


        daily_activity.append({

            "date":
                day,

            "label":
                scan_day["label"],

            "scans":
                scan_day["count"],

            "listing_views":
                engagement.get(
                    "listing_views",
                    0,
                ),

            "useful_actions":
                engagement.get(
                    "useful_actions",
                    0,
                ),

        })


    # =========================================================
    # REMINDER ANALYTICS
    # =========================================================

    reminder_analytics = (
        get_reminder_analytics(
            top_limit=10
        )
    )


    # =========================================================
    # RENDER DASHBOARD
    # =========================================================

    return render_template(

        "admin/analytics.html",

        total_scans=
            total_scans,

        today_scans=
            today_scans,

        seven_day_scans=
            seven_day_scans,

        thirty_day_scans=
            thirty_day_scans,

        seven_day_growth=
            round(
                seven_day_growth,
                1,
            ),

        total_access_points=
            total_access_points,

        active_access_points=
            active_access_points,

        scans_per_access_point=
            round(
                scans_per_access_point,
                1,
            ),

        total_category_views=
            total_category_views,

        category_engagement_rate=
            round(
                category_engagement_rate,
                1,
            ),

        category_activity=
            category_activity,

        top_locations=
            top_locations,

        zone_activity=
            zone_activity,

        low_performing_points=
            low_performing_points,

        daily_scan_trend=
            daily_scan_trend,

        recent_scans=
            recent_scans,

        pending_submissions_count=
            pending_submissions_count,


        # =====================================================
        # REMINDER ANALYTICS
        # =====================================================

        reminder_analytics=
            reminder_analytics,


        # =====================================================
        # ENGAGEMENT METRICS
        # =====================================================

        total_listing_views=
            total_listing_views,

        total_whatsapp_clicks=
            total_whatsapp_clicks,

        total_call_clicks=
            total_call_clicks,

        total_share_clicks=
            total_share_clicks,

        total_directions_clicks=
            total_directions_clicks,

        total_useful_actions=
            total_useful_actions,

        listing_action_rate=
            listing_action_rate,


        # =====================================================
        # CONTENT PERFORMANCE
        # =====================================================

        top_content_performance=
            top_content_performance,

        most_viewed_listings=
            most_viewed_listings,

        high_interest_low_action=
            high_interest_low_action,


        # =====================================================
        # NETWORK PERFORMANCE
        # =====================================================

        access_point_performance=
            access_point_performance,

        zone_performance=
            zone_performance,

        category_performance=
            category_performance,

        daily_activity=
            daily_activity,

    )

@admin_bp.route(
    "/notifications"
)
def notifications():

    require_admin()

    # =====================================================
    # FILTERS
    # =====================================================

    selected_zone_id = request.args.get(
        "zone_id",
        type=int,
    )

    selected_status = (
        request.args.get(
            "status",
            "",
        )
        .strip()
    )


    # =====================================================
    # NOTIFICATION QUERY
    # =====================================================

    query = (
        PushNotification.query
        .order_by(
            PushNotification.created_at.desc()
        )
    )


    if selected_zone_id:

        query = query.filter(
            PushNotification.zone_id ==
            selected_zone_id
        )


    if selected_status:

        query = query.filter(
            PushNotification.status ==
            selected_status
        )


    notifications = (
        query
        .limit(200)
        .all()
    )


    # =====================================================
    # ZONES
    # =====================================================

    zones = (
        Zone.query
        .order_by(
            Zone.name.asc()
        )
        .all()
    )


    zone_lookup = {
        zone.id: zone
        for zone in zones
    }


    # =====================================================
    # DASHBOARD COUNTERS
    # =====================================================

    active_subscribers = (
        PushSubscriber.query
        .filter_by(
            active=True
        )
        .count()
    )


    total_notifications = (
        PushNotification.query
        .count()
    )


    successful_notifications = (
        PushNotification.query
        .filter(
            PushNotification.status ==
            "sent"
        )
        .count()
    )


    problem_notifications = (
        PushNotification.query
        .filter(
            PushNotification.status.in_(
                [
                    "failed",
                    "partial_failure",
                ]
            )
        )
        .count()
    )


    pending_notifications = (
        PushNotification.query
        .filter(
            PushNotification.status ==
            "pending"
        )
        .count()
    )


    # =====================================================
    # TOTAL DELIVERY COUNTS
    # =====================================================

    sent_deliveries = (
        db.session.query(
            db.func.coalesce(
                db.func.sum(
                    PushNotification.sent_count
                ),
                0,
            )
        )
        .scalar()
    )


    failed_deliveries = (
        db.session.query(
            db.func.coalesce(
                db.func.sum(
                    PushNotification.failed_count
                ),
                0,
            )
        )
        .scalar()
    )


    return render_template(
        "admin/notifications.html",

        notifications=
            notifications,

        zones=
            zones,

        zone_lookup=
            zone_lookup,

        selected_zone_id=
            selected_zone_id,

        selected_status=
            selected_status,

        active_subscribers=
            active_subscribers,

        total_notifications=
            total_notifications,

        successful_notifications=
            successful_notifications,

        problem_notifications=
            problem_notifications,

        pending_notifications=
            pending_notifications,

        sent_deliveries=
            sent_deliveries,

        failed_deliveries=
            failed_deliveries,
    )

@admin_bp.route(
    "/notifications/<int:notification_id>/retry",
    methods=["POST"],
)
def retry_notification(
    notification_id,
):

    require_admin()


    notification = (
        PushNotification.query
        .get_or_404(
            notification_id
        )
    )


    # =====================================================
    # ONLY RETRY PROBLEM NOTIFICATIONS
    # =====================================================

    allowed_statuses = {
        "failed",
        "partial_failure",
        "pending",
        "no_subscribers",
    }


    if (
        notification.status
        not in allowed_statuses
    ):

        flash(
            "This notification does not need to be retried.",
            "info",
        )

        return redirect(
            url_for(
                "admin.notifications"
            )
        )


    # =====================================================
    # RESOLVE ORIGINAL CONTENT
    # =====================================================
    #
    # Category preferences are attached to the original
    # content category.
    #
    # We deliberately do NOT fall back to a zone-wide
    # broadcast if the original content cannot be found.
    # =====================================================

    if not notification.content_item_id:

        flash(
            (
                "Unable to retry this notification "
                "because it is not linked to content."
            ),
            "error",
        )

        return redirect(
            url_for(
                "admin.notifications"
            )
        )


    content = db.session.get(
        ContentItem,
        notification.content_item_id,
    )


    if content is None:

        flash(
            (
                "Unable to retry this notification "
                "because the original content "
                "no longer exists."
            ),
            "error",
        )

        return redirect(
            url_for(
                "admin.notifications"
            )
        )


    # =====================================================
    # ORIGINAL NOTIFICATION CATEGORY
    # =====================================================

    notification_category = (
        content.category
    )


    if not notification_category:

        flash(
            (
                "Unable to retry this notification "
                "because the original content "
                "has no category."
            ),
            "error",
        )

        return redirect(
            url_for(
                "admin.notifications"
            )
        )


    try:

        # =================================================
        # MARK RETRY ATTEMPT
        # =================================================

        notification.attempts += 1

        notification.status = (
            "pending"
        )

        notification.last_error = (
            None
        )

        db.session.commit()


        # =================================================
        # ATTEMPT CATEGORY-TARGETED DELIVERY
        # =================================================
        #
        # The push service will now target:
        #
        # active subscriber
        # +
        # same zone
        # +
        # matching notification preference
        #
        # Example:
        #
        # zone = KwaMhlanga
        # category = local-events
        #
        # Only KwaMhlanga subscribers who selected
        # "local-events" will receive the retry.
        # =================================================

        result = (
            send_zone_push_notification(

                zone_id=
                    notification.zone_id,

                category=
                    notification_category,

                title=
                    notification.title,

                body=
                    notification.body,

                url=
                    notification.target_url,

                tag=
                    (
                        f"notification-"
                        f"{notification.id}"
                    ),

            )
        )


        # =================================================
        # UPDATE DELIVERY COUNTS
        # =================================================

        notification.total_subscribers = (
            result["total"]
        )

        notification.sent_count = (
            result["sent"]
        )

        notification.failed_count = (
            result["failed"]
        )


        # =================================================
        # DETERMINE NEW STATUS
        # =================================================

        if (
            result["sent"] > 0
            and
            result["failed"] == 0
        ):

            notification.status = (
                "sent"
            )

            notification.sent_at = (
                datetime.utcnow()
            )

            notification.last_error = (
                None
            )


        elif (
            result["sent"] > 0
            and
            result["failed"] > 0
        ):

            notification.status = (
                "partial_failure"
            )

            notification.sent_at = (
                datetime.utcnow()
            )

            notification.last_error = (
                f"{result['failed']} "
                "subscriber delivery failures."
            )


        elif (
            result["total"] == 0
        ):

            notification.status = (
                "no_subscribers"
            )

            notification.sent_at = (
                None
            )

            notification.last_error = (
                (
                    "No active subscribers "
                    "in this zone selected "
                    f"the '{notification_category}' "
                    "notification category."
                )
            )


        else:

            notification.status = (
                "failed"
            )

            notification.sent_at = (
                None
            )

            notification.last_error = (
                "Push delivery failed "
                "for all matching subscribers."
            )


        # =================================================
        # SAVE RESULT
        # =================================================

        db.session.commit()


        # =================================================
        # LOG RESULT
        # =================================================

        current_app.logger.info(
            "[LaC Push] Notification retried "
            "notification_id=%s "
            "content_item_id=%s "
            "zone_id=%s "
            "category=%s "
            "total=%s "
            "sent=%s "
            "failed=%s "
            "status=%s",
            notification.id,
            notification.content_item_id,
            notification.zone_id,
            notification_category,
            notification.total_subscribers,
            notification.sent_count,
            notification.failed_count,
            notification.status,
        )


        # =================================================
        # ADMIN MESSAGE
        # =================================================

        if result["total"] == 0:

            flash(
                (
                    "Notification retry completed, "
                    "but no active subscribers in "
                    "this zone selected "
                    f"'{notification_category}'."
                ),
                "info",
            )

        else:

            flash(
                (
                    "Notification retry completed. "
                    f"Category: {notification_category}. "
                    f"Matching subscribers: "
                    f"{notification.total_subscribers}. "
                    f"Sent: {notification.sent_count}. "
                    f"Failed: {notification.failed_count}."
                ),
                "success",
            )


    except Exception as exc:

        db.session.rollback()


        current_app.logger.exception(
            "[LaC Push] Retry failed "
            "notification_id=%s "
            "content_item_id=%s "
            "zone_id=%s "
            "category=%s "
            "error=%s",
            notification.id,
            notification.content_item_id,
            notification.zone_id,
            notification_category,
            exc,
        )


        flash(
            "Unable to retry notification.",
            "error",
        )


    return redirect(
        url_for(
            "admin.notifications"
        )
    )

@admin_bp.route("/zones")
def zones():
    auth = require_admin()
    if auth:
        return auth

    zone_list = Zone.query.order_by(Zone.name.asc()).all()
    return render_template("admin/zones.html", zones=zone_list)


@admin_bp.route("/zones/new", methods=["GET", "POST"])
def create_zone():
    auth = require_admin()
    if auth:
        return auth

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = clean_slug(request.form.get("slug", ""))
        active = request.form.get("active") == "on"

        if not name or not slug:
            flash("Zone name and slug are required.", "error")
            return render_template("admin/zone_form.html", zone=None)

        existing = Zone.query.filter((Zone.slug == slug) | (Zone.name == name)).first()
        if existing:
            flash("A zone with that name or slug already exists.", "error")
            return render_template("admin/zone_form.html", zone=None)

        zone = Zone(name=name, slug=slug, active=active)
        db.session.add(zone)
        db.session.commit()
        flash(f"{name} zone created successfully.", "success")
        return redirect(url_for("admin.zones"))

    return render_template("admin/zone_form.html", zone=None)


@admin_bp.route("/zones/<int:zone_id>/edit", methods=["GET", "POST"])
def edit_zone(zone_id):
    auth = require_admin()
    if auth:
        return auth

    zone = Zone.query.get_or_404(zone_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = clean_slug(request.form.get("slug", ""))
        active = request.form.get("active") == "on"

        if not name or not slug:
            flash("Zone name and slug are required.", "error")
            return render_template("admin/zone_form.html", zone=zone)

        duplicate = (
            Zone.query
            .filter(Zone.id != zone.id)
            .filter((Zone.slug == slug) | (Zone.name == name))
            .first()
        )
        if duplicate:
            flash("Another zone already uses that name or slug.", "error")
            return render_template("admin/zone_form.html", zone=zone)

        zone.name = name
        zone.slug = slug
        zone.active = active
        db.session.commit()
        flash("Zone updated successfully.", "success")
        return redirect(url_for("admin.zones"))

    return render_template("admin/zone_form.html", zone=zone)


@admin_bp.route("/zones/<int:zone_id>/toggle", methods=["POST"])
def toggle_zone(zone_id):
    auth = require_admin()
    if auth:
        return auth

    zone = Zone.query.get_or_404(zone_id)
    zone.active = not zone.active
    db.session.commit()
    flash(f"{zone.name} {'activated' if zone.active else 'deactivated'}.", "success")
    return redirect(url_for("admin.zones"))

@admin_bp.route(
    "/zones/<int:zone_id>/delete",
    methods=["POST"]
)
def delete_zone(zone_id):

    auth = require_admin()
    if auth:
        return auth

    zone = Zone.query.get_or_404(zone_id)
    zone_name = zone.name

    try:
        # -----------------------------------------
        # Delete access points + their scan records
        # -----------------------------------------

        access_points = AccessPoint.query.filter_by(
            zone_id=zone.id
        ).all()

        for point in access_points:

            QRScan.query.filter_by(
                access_point_id=point.id
            ).delete(
                synchronize_session=False
            )

            db.session.delete(point)

        # -----------------------------------------
        # Delete content + attached images
        # -----------------------------------------

        content_items = ContentItem.query.filter_by(
            zone_id=zone.id
        ).all()

        for item in content_items:

            # Disconnect submissions that reference
            # published content.
            submissions = PendingSubmission.query.filter_by(
                published_content_id=item.id
            ).all()

            for submission in submissions:
                submission.published_content_id = None

            ContentImage.query.filter_by(
                content_item_id=item.id
            ).delete(
                synchronize_session=False
            )

            db.session.delete(item)

        # -----------------------------------------
        # Delete pending submissions + images
        # -----------------------------------------

        pending_submissions = PendingSubmission.query.filter_by(
            zone_id=zone.id
        ).all()

        for submission in pending_submissions:

            PendingSubmissionImage.query.filter_by(
                submission_id=submission.id
            ).delete(
                synchronize_session=False
            )

            db.session.delete(submission)

        # -----------------------------------------
        # Delete zone
        # -----------------------------------------

        db.session.delete(zone)
        db.session.commit()

        flash(
            f"{zone_name} permanently deleted.",
            "success"
        )

    except Exception as exc:

        db.session.rollback()

        flash(
            f"Unable to delete zone: {exc}",
            "error"
        )

    return redirect(
        url_for("admin.zones")
    )



@admin_bp.route("/categories")
def categories():
    auth = require_admin()
    if auth:
        return auth

    return render_template(
        "admin/categories.html",
        categories=get_categories(
            active_only=False
        ),
    )


# =========================================================
# CREATE CATEGORY
# =========================================================

@admin_bp.route(
    "/categories/new",
    methods=["GET", "POST"],
)
def create_category():

    auth = require_admin()
    if auth:
        return auth


    if request.method == "POST":

        name = (
            request.form
            .get("name", "")
            .strip()
        )

        slug = clean_slug(
            request.form.get(
                "slug",
                ""
            )
        )

        icon = (
            request.form
            .get("icon", "")
            .strip()
            or None
        )

        display_order = (
            request.form.get(
                "display_order",
                type=int,
            )
        )

        display_order = (
            0
            if display_order is None
            else display_order
        )

        active = (
            request.form.get("active")
            == "on"
        )


        # =============================================
        # VALIDATION
        # =============================================

        if not name or not slug:

            flash(
                "Name and slug are required.",
                "error",
            )

            return render_template(
                "admin/category_form.html",
                category=None,
            )


        if Category.query.filter_by(
            slug=slug
        ).first():

            flash(
                "That category slug already exists.",
                "error",
            )

            return render_template(
                "admin/category_form.html",
                category=None,
            )


        # =============================================
        # CATEGORY IMAGE UPLOADS
        #
        # Image 1 = category_image
        # Image 2 = category_image_2
        # Image 3 = category_image_3
        # =============================================

        image_file = request.files.get(
            "category_image"
        )

        image_file_2 = request.files.get(
            "category_image_2"
        )

        image_file_3 = request.files.get(
            "category_image_3"
        )


        image_url = None
        image_url_2 = None
        image_url_3 = None


        try:

            # -----------------------------------------
            # IMAGE 1
            # -----------------------------------------

            if (
                image_file
                and image_file.filename
            ):

                image_url = upload_lac_image(
                    image_file,
                    folder="lac/categories",
                )


            # -----------------------------------------
            # IMAGE 2
            # -----------------------------------------

            if (
                image_file_2
                and image_file_2.filename
            ):

                image_url_2 = upload_lac_image(
                    image_file_2,
                    folder="lac/categories",
                )


            # -----------------------------------------
            # IMAGE 3
            # -----------------------------------------

            if (
                image_file_3
                and image_file_3.filename
            ):

                image_url_3 = upload_lac_image(
                    image_file_3,
                    folder="lac/categories",
                )


        except ValueError as error:

            flash(
                str(error),
                "error",
            )

            return render_template(
                "admin/category_form.html",
                category=None,
            )


        # =============================================
        # CREATE CATEGORY
        # =============================================

        category = Category(

            name=name,

            slug=slug,

            icon=icon,

            image_url=image_url,

            image_url_2=image_url_2,

            image_url_3=image_url_3,

            display_order=display_order,

            active=active,

        )


        db.session.add(category)

        db.session.commit()


        flash(
            "Category created successfully.",
            "success",
        )


        return redirect(
            url_for(
                "admin.categories"
            )
        )


    return render_template(
        "admin/category_form.html",
        category=None,
    )


# =========================================================
# EDIT CATEGORY
# =========================================================

@admin_bp.route(
    "/categories/<int:category_id>/edit",
    methods=["GET", "POST"],
)
def edit_category(category_id):

    auth = require_admin()
    if auth:
        return auth


    category = Category.query.get_or_404(
        category_id
    )


    if request.method == "POST":

        name = (
            request.form
            .get("name", "")
            .strip()
        )

        new_slug = clean_slug(
            request.form.get(
                "slug",
                ""
            )
        )

        icon = (
            request.form
            .get("icon", "")
            .strip()
            or None
        )

        display_order = (
            request.form.get(
                "display_order",
                type=int,
            )
        )

        display_order = (
            0
            if display_order is None
            else display_order
        )

        active = (
            request.form.get("active")
            == "on"
        )


        # =============================================
        # VALIDATION
        # =============================================

        if not name or not new_slug:

            flash(
                "Name and slug are required.",
                "error",
            )

            return render_template(
                "admin/category_form.html",
                category=category,
            )


        duplicate = Category.query.filter(

            Category.slug == new_slug,

            Category.id != category.id,

        ).first()


        if duplicate:

            flash(
                "Another category already uses that slug.",
                "error",
            )

            return render_template(
                "admin/category_form.html",
                category=category,
            )


        # =============================================
        # CATEGORY IMAGE UPLOADS
        #
        # IMPORTANT:
        # Existing images are preserved unless the
        # admin selects a new file for that position.
        # =============================================

        image_file = request.files.get(
            "category_image"
        )

        image_file_2 = request.files.get(
            "category_image_2"
        )

        image_file_3 = request.files.get(
            "category_image_3"
        )


        try:

            # -----------------------------------------
            # REPLACE IMAGE 1
            # -----------------------------------------

            if (
                image_file
                and image_file.filename
            ):

                category.image_url = (
                    upload_lac_image(
                        image_file,
                        folder="lac/categories",
                    )
                )


            # -----------------------------------------
            # REPLACE IMAGE 2
            # -----------------------------------------

            if (
                image_file_2
                and image_file_2.filename
            ):

                category.image_url_2 = (
                    upload_lac_image(
                        image_file_2,
                        folder="lac/categories",
                    )
                )


            # -----------------------------------------
            # REPLACE IMAGE 3
            # -----------------------------------------

            if (
                image_file_3
                and image_file_3.filename
            ):

                category.image_url_3 = (
                    upload_lac_image(
                        image_file_3,
                        folder="lac/categories",
                    )
                )


        except ValueError as error:

            flash(
                str(error),
                "error",
            )

            return render_template(
                "admin/category_form.html",
                category=category,
            )


        # =============================================
        # PRESERVE EXISTING SLUG RELATIONSHIPS
        # =============================================

        old_slug = category.slug


        if new_slug != old_slug:

            # -----------------------------------------
            # CONTENT ITEMS
            # -----------------------------------------

            ContentItem.query.filter(
                ContentItem.category == old_slug
            ).update(
                {
                    ContentItem.category:
                    new_slug
                },
                synchronize_session=False,
            )


            # -----------------------------------------
            # ACCESS POINT DEFAULT CATEGORY
            # -----------------------------------------

            AccessPoint.query.filter(
                AccessPoint.default_category
                == old_slug
            ).update(
                {
                    AccessPoint.default_category:
                    new_slug
                },
                synchronize_session=False,
            )


            # -----------------------------------------
            # PENDING SUBMISSIONS
            # -----------------------------------------

            PendingSubmission.query.filter(
                PendingSubmission.category
                == old_slug
            ).update(
                {
                    PendingSubmission.category:
                    new_slug
                },
                synchronize_session=False,
            )


            # -----------------------------------------
            # HISTORICAL QR SCAN CATEGORY
            # -----------------------------------------

            QRScan.query.filter(
                QRScan.category_selected
                == old_slug
            ).update(
                {
                    QRScan.category_selected:
                    new_slug
                },
                synchronize_session=False,
            )


        # =============================================
        # UPDATE CATEGORY
        # =============================================

        category.name = name

        category.slug = new_slug

        category.icon = icon

        category.display_order = display_order

        category.active = active


        db.session.commit()


        flash(
            "Category updated successfully.",
            "success",
        )


        return redirect(
            url_for(
                "admin.categories"
            )
        )


    return render_template(
        "admin/category_form.html",
        category=category,
    )


# =========================================================
# ZONE CATEGORY APPEARANCE
# =========================================================

@admin_bp.route(
    "/zone-category-appearance",
    methods=["GET", "POST"],
)
def zone_category_appearance():

    auth = require_admin()
    if auth:
        return auth


    # =====================================================
    # LOAD ZONES + CATEGORIES
    # =====================================================

    zones = (
        Zone.query
        .order_by(Zone.name.asc())
        .all()
    )

    categories = (
        Category.query
        .order_by(
            Category.display_order.asc(),
            Category.name.asc(),
        )
        .all()
    )


    # =====================================================
    # POST — SAVE APPEARANCE
    # =====================================================

    if request.method == "POST":

        zone_id = request.form.get(
            "zone_id",
            type=int,
        )

        category_id = request.form.get(
            "category_id",
            type=int,
        )


        # -------------------------------------------------
        # VALIDATION
        # -------------------------------------------------

        if not zone_id or not category_id:

            flash(
                "Please select both a zone and a category.",
                "error",
            )

            return render_template(
                "admin/zone_category_appearance.html",
                zones=zones,
                categories=categories,
                appearance=None,
                selected_zone_id=zone_id,
                selected_category_id=category_id,
            )


        zone = db.session.get(
            Zone,
            zone_id,
        )

        category = db.session.get(
            Category,
            category_id,
        )


        if not zone or not category:

            flash(
                "The selected zone or category could not be found.",
                "error",
            )

            return redirect(
                url_for(
                    "admin.zone_category_appearance"
                )
            )


        # =================================================
        # FIND EXISTING ZONE + CATEGORY CONFIGURATION
        # =================================================

        appearance = (
            ZoneCategoryAppearance.query
            .filter_by(
                zone_id=zone.id,
                category_id=category.id,
            )
            .first()
        )


        # =================================================
        # CREATE IF IT DOES NOT EXIST
        # =================================================

        if appearance is None:

            appearance = ZoneCategoryAppearance(
                zone_id=zone.id,
                category_id=category.id,
            )

            db.session.add(
                appearance
            )


        # =================================================
        # GET UPLOADED FILES
        # =================================================

        image_file = request.files.get(
            "zone_category_image"
        )

        image_file_2 = request.files.get(
            "zone_category_image_2"
        )

        image_file_3 = request.files.get(
            "zone_category_image_3"
        )


        # =================================================
        # UPLOAD IMAGES
        #
        # Existing images remain unchanged if the admin
        # does not select a replacement file.
        # =================================================

        try:

            # ---------------------------------------------
            # IMAGE 1
            # ---------------------------------------------

            if (
                image_file
                and image_file.filename
            ):

                appearance.image_url = (
                    upload_lac_image(
                        image_file,
                        folder=(
                            "lac/zone-categories/"
                            f"{zone.id}/"
                            f"{category.slug}"
                        ),
                    )
                )


            # ---------------------------------------------
            # IMAGE 2
            # ---------------------------------------------

            if (
                image_file_2
                and image_file_2.filename
            ):

                appearance.image_url_2 = (
                    upload_lac_image(
                        image_file_2,
                        folder=(
                            "lac/zone-categories/"
                            f"{zone.id}/"
                            f"{category.slug}"
                        ),
                    )
                )


            # ---------------------------------------------
            # IMAGE 3
            # ---------------------------------------------

            if (
                image_file_3
                and image_file_3.filename
            ):

                appearance.image_url_3 = (
                    upload_lac_image(
                        image_file_3,
                        folder=(
                            "lac/zone-categories/"
                            f"{zone.id}/"
                            f"{category.slug}"
                        ),
                    )
                )


        except ValueError as error:

            db.session.rollback()

            flash(
                str(error),
                "error",
            )

            return render_template(
                "admin/zone_category_appearance.html",
                zones=zones,
                categories=categories,
                appearance=appearance,
                selected_zone_id=zone.id,
                selected_category_id=category.id,
            )


        # =================================================
        # SAVE DATABASE RECORD
        # =================================================

        try:

            db.session.commit()

        except Exception:

            db.session.rollback()

            current_app.logger.exception(
                "Failed to save zone category appearance."
            )

            flash(
                "The zone category appearance could not be saved.",
                "error",
            )

            return render_template(
                "admin/zone_category_appearance.html",
                zones=zones,
                categories=categories,
                appearance=appearance,
                selected_zone_id=zone.id,
                selected_category_id=category.id,
            )


        flash(
            (
                f"{category.name} appearance for "
                f"{zone.name} saved successfully."
            ),
            "success",
        )


        # Redirect back with the selection in the URL
        # so the admin immediately sees the saved images.

        return redirect(
            url_for(
                "admin.zone_category_appearance",
                zone_id=zone.id,
                category_id=category.id,
            )
        )


    # =====================================================
    # GET — LOAD SELECTED APPEARANCE
    # =====================================================

    selected_zone_id = request.args.get(
        "zone_id",
        type=int,
    )

    selected_category_id = request.args.get(
        "category_id",
        type=int,
    )

    appearance = None


    if (
        selected_zone_id
        and selected_category_id
    ):

        appearance = (
            ZoneCategoryAppearance.query
            .filter_by(
                zone_id=selected_zone_id,
                category_id=selected_category_id,
            )
            .first()
        )


    return render_template(
        "admin/zone_category_appearance.html",
        zones=zones,
        categories=categories,
        appearance=appearance,
        selected_zone_id=selected_zone_id,
        selected_category_id=selected_category_id,
    )


@admin_bp.route("/categories/<int:category_id>/toggle", methods=["POST"])
def toggle_category(category_id):
    auth = require_admin()
    if auth:
        return auth

    category = Category.query.get_or_404(category_id)
    category.active = not category.active
    db.session.commit()
    flash("Category activated." if category.active else "Category deactivated.", "success")
    return redirect(url_for("admin.categories"))



@admin_bp.route("/access-points/new", methods=["GET", "POST"])
def create_access_point():
    auth = require_admin()
    if auth:
        return auth

    zones = Zone.query.filter_by(active=True).order_by(Zone.name.asc()).all()
    categories = get_categories()

    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        name = request.form.get("name", "").strip()
        zone_id = request.form.get("zone_id", type=int)
        location_type = request.form.get("location_type", "").strip()
        qr_type = request.form.get("qr_type", "general").strip()
        default_category = request.form.get("default_category", "").strip() or None
        partner_name = request.form.get("partner_name", "").strip() or None

        if not code or not name or not zone_id:
            flash("Code, name and zone are required.", "error")
            return render_template("admin/create_access_point.html", zones=zones, categories=categories)

        zone = db.session.get(Zone, zone_id)
        if not zone or not zone.active:
            flash("Please select a valid active zone.", "error")
            return render_template("admin/create_access_point.html", zones=zones, categories=categories)

        if qr_type not in ("general", "category"):
            flash("Invalid QR behaviour.", "error")
            return render_template("admin/create_access_point.html", zones=zones, categories=categories)

        if qr_type == "category":
            if not get_category_by_slug(default_category):
                flash("Please select a valid active category.", "error")
                return render_template("admin/create_access_point.html", zones=zones, categories=categories)
        else:
            default_category = None

        if AccessPoint.query.filter_by(code=code).first():
            flash(f"Access point {code} already exists.", "error")
            return render_template("admin/create_access_point.html", zones=zones, categories=categories)

        access_point = AccessPoint(
            code=code,
            name=name,
            zone_id=zone_id,
            location_type=location_type,
            qr_type=qr_type,
            default_category=default_category,
            partner_name=partner_name,
            active=True,
        )
        db.session.add(access_point)
        db.session.commit()

        base_url = os.environ.get("LAC_BASE_URL", request.host_url.rstrip("/"))
        result = generate_access_qr(code=code, base_url=base_url)

        flash(f"QR access point {code} created.", "success")
        return render_template(
            "admin/qr_created.html",
            access_point=access_point,
            qr_filename=result["filename"],
            qr_url=result["url"],
        )

    return render_template("admin/create_access_point.html", zones=zones, categories=categories)


@admin_bp.route("/access-points")
def access_points():
    auth = require_admin()
    if auth:
        return auth

    zone_id = request.args.get("zone", type=int)
    status = request.args.get("status", "").strip()
    query = AccessPoint.query

    if zone_id:
        query = query.filter(AccessPoint.zone_id == zone_id)
    if status == "active":
        query = query.filter(AccessPoint.active.is_(True))
    elif status == "inactive":
        query = query.filter(AccessPoint.active.is_(False))

    points = query.order_by(AccessPoint.created_at.desc()).all()
    zones = Zone.query.order_by(Zone.name.asc()).all()

    return render_template(
        "admin/access_points.html",
        points=points,
        zones=zones,
        selected_zone=zone_id,
        selected_status=status,
    )


@admin_bp.route("/access-points/<int:point_id>/edit", methods=["GET", "POST"])
def edit_access_point(point_id):
    auth = require_admin()
    if auth:
        return auth

    point = AccessPoint.query.get_or_404(point_id)
    zones = Zone.query.order_by(Zone.name.asc()).all()
    categories = get_categories(active_only=False)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        zone_id = request.form.get("zone_id", type=int)
        location_type = request.form.get("location_type", "").strip()
        qr_type = request.form.get("qr_type", "general").strip()
        default_category = request.form.get("default_category", "").strip() or None
        partner_name = request.form.get("partner_name", "").strip() or None
        active = request.form.get("active") == "on"

        if not name or not zone_id:
            flash("Location name and zone are required.", "error")
            return render_template("admin/edit_access_point.html", point=point, zones=zones, categories=categories)

        if not db.session.get(Zone, zone_id):
            flash("Selected zone does not exist.", "error")
            return render_template("admin/edit_access_point.html", point=point, zones=zones, categories=categories)

        if qr_type not in ("general", "category"):
            flash("Invalid QR behaviour.", "error")
            return render_template("admin/edit_access_point.html", point=point, zones=zones, categories=categories)

        if qr_type == "category":
            if not get_category_by_slug(default_category, active_only=False):
                flash("Category-specific QR requires a valid category.", "error")
                return render_template("admin/edit_access_point.html", point=point, zones=zones, categories=categories)
        else:
            default_category = None

        point.name = name
        point.zone_id = zone_id
        point.location_type = location_type
        point.qr_type = qr_type
        point.default_category = default_category
        point.partner_name = partner_name
        point.active = active
        db.session.commit()

        flash("Access point updated successfully.", "success")
        return redirect(url_for("admin.access_points"))

    return render_template("admin/edit_access_point.html", point=point, zones=zones, categories=categories)


@admin_bp.route("/access-points/<int:point_id>/toggle", methods=["POST"])
def toggle_access_point(point_id):
    auth = require_admin()
    if auth:
        return auth

    point = AccessPoint.query.get_or_404(point_id)
    point.active = not point.active
    db.session.commit()
    flash(f"{point.name} {'activated' if point.active else 'deactivated'}.", "success")
    return redirect(url_for("admin.access_points"))

@admin_bp.route(
    "/access-points/<int:point_id>/delete",
    methods=["POST"]
)
def delete_access_point(point_id):

    auth = require_admin()
    if auth:
        return auth

    point = AccessPoint.query.get_or_404(
        point_id
    )

    point_name = point.name

    try:

        # -----------------------------------------
        # Delete QR analytics belonging to point
        # -----------------------------------------

        QRScan.query.filter_by(
            access_point_id=point.id
        ).delete(
            synchronize_session=False
        )

        # -----------------------------------------
        # Delete physical access point
        # -----------------------------------------

        db.session.delete(point)
        db.session.commit()

        flash(
            f"{point_name} permanently deleted.",
            "success"
        )

    except Exception as exc:

        db.session.rollback()

        flash(
            f"Unable to delete access point: {exc}",
            "error"
        )

    return redirect(
        url_for("admin.access_points")
    )

@admin_bp.route("/content")
def content_list():
    auth = require_admin()
    if auth:
        return auth

    archive_expired_content()
    zone_id = request.args.get("zone", type=int)
    category = request.args.get("category", "").strip()
    status = request.args.get("status", "").strip()

    query = ContentItem.query.filter(ContentItem.archived.is_(False))
    if zone_id:
        query = query.filter(ContentItem.zone_id == zone_id)
    if category:
        query = query.filter(ContentItem.category == category)

    items = query.order_by(ContentItem.created_at.desc()).all()
    today = date.today()
    content_rows = []
    expiring_soon_count = 0

    for item in items:
        row = {
            "item": item,
            "status": get_content_status(item, today),
            "expiry_info": get_expiry_intelligence(item, today),
            "archive_info": get_archive_intelligence(item, today),
        }
        if row["expiry_info"]:
            expiring_soon_count += 1
        content_rows.append(row)

    if status:
        content_rows = [row for row in content_rows if row["status"]["key"] == status]

    status_counts = {"live": 0, "upcoming": 0, "expired": 0, "inactive": 0, "archived": 0}
    for content_item in ContentItem.query.filter(ContentItem.archived.is_(False)).all():
        result = get_content_status(content_item, today)
        status_counts[result["key"]] += 1

    zones = Zone.query.filter_by(active=True).order_by(Zone.name.asc()).all()

    return render_template(
        "admin/content_list.html",
        content_rows=content_rows,
        zones=zones,
        categories=get_categories(),
        selected_zone=zone_id,
        selected_category=category,
        selected_status=status,
        status_counts=status_counts,
        expiring_soon_count=expiring_soon_count,
        today=today,
    )


@admin_bp.route("/content/archive")
def content_archive():
    auth = require_admin()
    if auth:
        return auth

    items = (
        ContentItem.query
        .filter(ContentItem.archived.is_(True))
        .order_by(ContentItem.archived_at.desc())
        .all()
    )
    return render_template("admin/content_archive.html", items=items)

@admin_bp.route(
    "/content/<int:item_id>/restore",
    methods=["POST"],
)
def restore_content(
    item_id,
):

    auth = require_admin()

    if auth:
        return auth

    item = (
        ContentItem.query
        .get_or_404(
            item_id
        )
    )

    item.archived = False
    item.archived_at = None

    # Restore public visibility.
    item.active = True

    db.session.commit()

    flash(
        "Content restored from archive.",
        "success",
    )

    return redirect(
        url_for(
            "admin.content_archive"
        )
    )

def _render_content_form(zones, categories, item):
    return render_template(
        "admin/content_form.html",
        zones=zones,
        categories=categories,
        item=item,
    )


# ============================================================
# CONTENT WORKFLOW RULES
# ============================================================

ADMIN_CONTENT_WORKFLOWS = {

    # ========================================================
    # PROPERTY
    # ========================================================

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


    # ========================================================
    # EVENTS
    # ========================================================

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


    # ========================================================
    # GROCERY / RETAIL SPECIALS
    # ========================================================

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


    # ========================================================
    # FOOD
    # ========================================================

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


    # ========================================================
    # JOBS / OPPORTUNITIES
    # ========================================================

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


    # ========================================================
    # SERVICES
    # ========================================================

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


    # ========================================================
    # BEAUTY / SALON
    # ========================================================

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


# ============================================================
# GET CONTENT WORKFLOW
# ============================================================

# ============================================================
# DATE VALIDATION + NORMALIZATION
# ============================================================

def _validate_and_normalize_content_dates(
    category,
    form,
    lifetime_type=None,
):

    start_date = (
        parse_date(
            form.get(
                "start_date"
            )
        )
    )

    end_date = (
        parse_date(
            form.get(
                "end_date"
            )
        )
    )

    publish_from = (
        parse_date(
            form.get(
                "publish_from"
            )
        )
    )

    event_date = (
        parse_date(
            form.get(
                "event_date"
            )
        )
    )

    event_end_date = (
        parse_date(
            form.get(
                "event_end_date"
            )
        )
    )

    # ========================================================
    # LEGACY FALLBACK
    # ========================================================

    if not lifetime_type:

        if category == "events":

            lifetime_type = (
                "time_specific"
            )

        elif end_date:

            lifetime_type = (
                "time_specific"
            )

        else:

            lifetime_type = (
                "ongoing"
            )

    # ========================================================
    # EVENTS
    # ========================================================

    if (
        category == "events"
        and
        lifetime_type == "time_specific"
    ):

        if not event_date:

            return (
                None,
                "Event Date is required for events.",
            )

        if (
            publish_from
            and
            publish_from > event_date
        ):

            return (
                None,
                "Publish From cannot be after Event Date.",
            )

        if (
            event_end_date
            and
            event_end_date < event_date
        ):

            return (
                None,
                "Event End Date cannot be before Event Date.",
            )

        # Events use event-specific date fields.
        start_date = None
        end_date = None

    # ========================================================
    # TIME-SPECIFIC NON-EVENT CONTENT
    # ========================================================

    elif lifetime_type == "time_specific":

        # Examples:
        #
        # Grocery special
        # Food promotion
        # Learnership deadline
        # Tender deadline
        # Accommodation special

        if not end_date:

            return (
                None,
                "End Date is required for this "
                "time-specific listing.",
            )

        if (
            start_date
            and
            end_date < start_date
        ):

            return (
                None,
                "End date cannot be before start date.",
            )

        # Clear event fields.
        publish_from = None
        event_date = None
        event_end_date = None

    # ========================================================
    # UNTIL UNAVAILABLE
    # ========================================================

    elif lifetime_type == "until_unavailable":

        # Examples:
        #
        # Room → until taken
        # Rental → until taken
        # Property sale → until sold
        # Job → until filled

        start_date = None
        end_date = None

        publish_from = None
        event_date = None
        event_end_date = None

    # ========================================================
    # ONGOING
    # ========================================================

    elif lifetime_type == "ongoing":

        # Examples:
        #
        # Restaurant
        # Hotel
        # Salon
        # Mechanic
        # Plumber

        start_date = None
        end_date = None

        publish_from = None
        event_date = None
        event_end_date = None

    # ========================================================
    # RECURRING
    # ========================================================

    elif lifetime_type == "recurring":

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

            return (
                None,
                "End date cannot be before start date.",
            )

    # ========================================================
    # UNKNOWN LIFETIME
    # ========================================================

    else:

        return (
            None,
            "Invalid listing lifetime type.",
        )

    # ========================================================
    # NORMALIZED RESULT
    # ========================================================

    return {

        "start_date":
            start_date,

        "end_date":
            end_date,

        "publish_from":
            publish_from,

        "event_date":
            event_date,

        "event_end_date":
            event_end_date,

    }, None


@admin_bp.route(
    "/content/new",
    methods=["GET", "POST"],
)
def create_content():

    # =====================================================
    # ADMIN AUTHENTICATION
    # =====================================================

    auth = require_admin()

    if auth:
        return auth


    # =====================================================
    # LOAD ZONES
    # =====================================================

    zones = (
        Zone.query
        .filter_by(
            active=True
        )
        .order_by(
            Zone.name.asc()
        )
        .all()
    )


    # =====================================================
    # LOAD CATEGORIES
    # =====================================================

    categories = (
        get_categories(
            active_only=False
        )
    )


    # =====================================================
    # POST — CREATE CONTENT
    # =====================================================

    if request.method == "POST":

        # =================================================
        # BASIC DATA
        # =================================================

        zone_id = request.form.get(
            "zone_id",
            type=int,
        )


        category = (
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


        # =================================================
        # CONTACT + ACTION FIELDS
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
        # REQUIRED FIELDS
        # =================================================

        if (
            not zone_id
            or not category
            or not title
        ):

            flash(
                "Zone, category and title are required.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # VALIDATE ZONE
        # =================================================

        zone = db.session.get(
            Zone,
            zone_id,
        )


        if not zone:

            flash(
                "Selected zone does not exist.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # VALIDATE CATEGORY
        # =================================================

        if not get_category_by_slug(
            category,
            active_only=False,
        ):

            flash(
                "Invalid content category.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # DETERMINE CONTENT WORKFLOW
        # =================================================

        workflow = (
            _get_content_workflow(
                category,
                content_type,
            )
        )


        lifetime_type = (
            workflow[
                "lifetime_type"
            ]
        )


        workflow_notification_eligible = bool(
            workflow.get(
                "notification_eligible",
                False,
            )
        )


        pricing_model = (
            workflow.get(
                "pricing_model"
            )
        )


        # =================================================
        # VALIDATE + NORMALIZE CONTENT DATES
        # =================================================

        try:

            dates, error = (
                _validate_and_normalize_content_dates(
                    category,
                    request.form,
                    lifetime_type=lifetime_type,
                )
            )

        except ValueError:

            flash(
                "Please enter valid dates.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        if error:

            flash(
                error,
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # CAMPAIGN START / END TIMES
        # =================================================

        try:

            start_time = (
                parse_optional_time(
                    request.form.get(
                        "start_time"
                    )
                )
            )


            end_time = (
                parse_optional_time(
                    request.form.get(
                        "end_time"
                    )
                )
            )

        except ValueError:

            flash(
                "Please enter valid start and end times.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # CANONICAL CATEGORY
        # =================================================

        canonical_category = (
            normalize_category(
                category
            )
        )


        # =================================================
        # DETERMINE EFFECTIVE CAMPAIGN DATES
        # =================================================

        if (
            canonical_category == "events"
            or lifetime_type == "event"
        ):

            effective_start_date = (
                dates.get(
                    "event_date"
                )
            )


            effective_end_date = (
                dates.get(
                    "event_end_date"
                )
                or
                effective_start_date
            )

        else:

            effective_start_date = (
                dates.get(
                    "start_date"
                )
            )


            effective_end_date = (
                dates.get(
                    "end_date"
                )
            )


        # =================================================
        # VALIDATE DATE ORDER
        # =================================================

        if (
            effective_start_date
            and effective_end_date
            and effective_end_date
            < effective_start_date
        ):

            flash(
                "End date cannot be before start date.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # VALIDATE SAME-DAY TIME ORDER
        # =================================================

        if (
            effective_start_date
            and effective_end_date
            and effective_start_date
            == effective_end_date
            and start_time
            and end_time
            and end_time <= start_time
        ):

            flash(
                (
                    "End time must be after start time "
                    "when the content starts and ends "
                    "on the same day."
                ),
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # END TIME WITHOUT END DATE
        # =================================================

        if (
            canonical_category != "events"
            and end_time
            and not effective_end_date
        ):

            flash(
                (
                    "Please enter an end date when "
                    "using an end time."
                ),
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # LISTING LEVEL
        # =================================================

        listing_level = (
            request.form.get(
                "listing_level",
                "discovery",
            )
            .strip()
            .lower()
        )


        allowed_listing_levels = {
            "discovery",
            "business",
            "promotion",
        }


        if (
            listing_level
            not in allowed_listing_levels
        ):

            listing_level = (
                "discovery"
            )


        # =================================================
        # OWNERSHIP + VERIFICATION
        # =================================================

        if (
            listing_level
            == "discovery"
        ):

            ownership_status = (
                "unclaimed"
            )

            is_verified = (
                False
            )


        else:

            ownership_status = (
                "claimed"
            )


            is_verified = (
                request.form.get(
                    "is_verified"
                )
                == "on"
            )


        # =================================================
        # FEATURED
        # =================================================

        featured = (
            request.form.get(
                "featured"
            )
            == "on"
        )


        # =================================================
        # SPONSORED VISIBILITY
        #
        # IMPORTANT:
        #
        # This is intentionally independent from:
        #
        # pricing_model
        # payment_status
        # amount_due
        # amount_paid
        # commercial_starts_at
        # commercial_expires_at
        #
        # Admin controls sponsorship manually for now.
        # =================================================

        is_sponsored = (
            request.form.get(
                "is_sponsored"
            )
            == "on"
        )


        sponsorship_status = (
            request.form.get(
                "sponsorship_status",
                "inactive",
            )
            .strip()
            .lower()
        )


        allowed_sponsorship_statuses = {
            "inactive",
            "scheduled",
            "active",
            "expired",
        }


        if (
            sponsorship_status
            not in allowed_sponsorship_statuses
        ):

            sponsorship_status = (
                "inactive"
            )


        sponsorship_reference = (
            request.form.get(
                "sponsorship_reference",
                "",
            )
            .strip()
            or None
        )


        try:

            sponsored_priority = int(
                request.form.get(
                    "sponsored_priority",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            sponsored_priority = 0


        sponsored_priority = max(
            0,
            min(
                sponsored_priority,
                100,
            ),
        )


        sponsored_starts_raw = (
            request.form.get(
                "sponsored_starts_at",
                "",
            )
            .strip()
        )


        sponsored_expires_raw = (
            request.form.get(
                "sponsored_expires_at",
                "",
            )
            .strip()
        )


        try:

            sponsored_starts_at = (

                datetime.fromisoformat(
                    sponsored_starts_raw
                )

                if sponsored_starts_raw

                else None
            )


            sponsored_expires_at = (

                datetime.fromisoformat(
                    sponsored_expires_raw
                )

                if sponsored_expires_raw

                else None
            )


        except ValueError:

            flash(
                "Please enter valid sponsorship dates.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # SPONSORSHIP DATE VALIDATION
        # =================================================

        if (
            sponsored_starts_at
            and sponsored_expires_at
            and sponsored_expires_at
            <= sponsored_starts_at
        ):

            flash(
                (
                    "Sponsored expiry must be after "
                    "the sponsored start time."
                ),
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # NON-SPONSORED SAFETY
        #
        # Prevent stale/manual sponsorship metadata from
        # affecting an ordinary listing.
        # =================================================

        if not is_sponsored:

            sponsorship_status = (
                "inactive"
            )

            sponsored_starts_at = (
                None
            )

            sponsored_expires_at = (
                None
            )

            sponsored_priority = (
                0
            )

            sponsorship_reference = (
                None
            )


        # =================================================
        # NOTIFICATION ELIGIBILITY
        # =================================================

        if (
            listing_level
            == "promotion"
        ):

            promotion_notification_requested = (
                request.form.get(
                    "notification_eligible"
                )
                == "on"
            )


            notification_eligible = (
                workflow_notification_eligible
                and
                promotion_notification_requested
            )


        else:

            notification_eligible = (
                False
            )


        # =================================================
        # BUSINESS / RICH LISTING FIELDS
        # =================================================

        if (
            listing_level
            in {
                "business",
                "promotion",
            }
        ):

            opening_hours = (
                request.form.get(
                    "opening_hours",
                    "",
                )
                .strip()
                or None
            )


            menu_highlights = (
                request.form.get(
                    "menu_highlights",
                    "",
                )
                .strip()
                or None
            )


            special_offer = (
                request.form.get(
                    "special_offer",
                    "",
                )
                .strip()
                or None
            )


        else:

            opening_hours = (
                None
            )

            menu_highlights = (
                None
            )

            special_offer = (
                None
            )


        # =================================================
        # IMAGE UPLOADS
        # =================================================

        uploaded_images = []


        for uploaded_file in request.files.getlist(
            "images"
        ):

            if (
                uploaded_file
                and
                uploaded_file.filename
            ):

                uploaded_images.append(
                    uploaded_file
                )


        legacy_image = (
            request.files.get(
                "image"
            )
        )


        if (
            legacy_image
            and legacy_image.filename
            and not uploaded_images
        ):

            uploaded_images.append(
                legacy_image
            )


        # =================================================
        # IMAGE LIMIT
        # =================================================

        if (
            listing_level
            == "discovery"
        ):

            maximum_images = (
                1
            )


        else:

            maximum_images = (
                3
            )


        if (
            len(
                uploaded_images
            )
            > maximum_images
        ):

            if (
                listing_level
                == "discovery"
            ):

                message = (
                    "Discovery listings can have "
                    "a maximum of 1 image."
                )


            else:

                message = (
                    "Business and Promotion listings "
                    "can have a maximum of 3 images."
                )


            flash(
                message,
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # UPLOAD IMAGES
        # =================================================

        uploaded_image_urls = []


        try:

            for uploaded_file in (
                uploaded_images
            ):

                image_url = (
                    upload_listing_image(
                        uploaded_file
                    )
                )


                if image_url:

                    uploaded_image_urls.append(
                        image_url
                    )


        except Exception as error:

            current_app.logger.exception(
                (
                    "Content image upload failed. "
                    "title=%s error=%s"
                ),
                title,
                error,
            )


            flash(
                f"Image upload failed: {error}",
                "error",
            )


            return _render_content_form(
                zones,
                categories,
                None,
            )


        primary_image_url = (

            uploaded_image_urls[0]

            if len(
                uploaded_image_urls
            ) >= 1

            else None
        )


        second_image_url = (

            uploaded_image_urls[1]

            if len(
                uploaded_image_urls
            ) >= 2

            else None
        )


        third_image_url = (

            uploaded_image_urls[2]

            if len(
                uploaded_image_urls
            ) >= 3

            else None
        )


        # =================================================
        # CREATE CONTENT ITEM
        # =================================================

        item = ContentItem(

            zone_id=(
                zone_id
            ),

            category=(
                category
            ),

            content_type=(
                content_type
            ),

            lifetime_type=(
                lifetime_type
            ),

            availability_status=(
                "available"
            ),

            title=(
                title
            ),

            start_time=(
                start_time
            ),

            end_time=(
                end_time
            ),

            description=(
                request.form.get(
                    "description",
                    "",
                )
                .strip()
                or None
            ),

            business_name=(
                request.form.get(
                    "business_name",
                    "",
                )
                .strip()
                or None
            ),

            venue=(
                request.form.get(
                    "venue",
                    "",
                )
                .strip()
                or None
            ),

            price=(
                request.form.get(
                    "price",
                    "",
                )
                .strip()
                or None
            ),

            contact=(
                contact
            ),

            whatsapp_number=(
                whatsapp_number
            ),

            directions_url=(
                directions_url
            ),

            ticket_url=(
                ticket_url
            ),

            listing_level=(
                listing_level
            ),

            ownership_status=(
                ownership_status
            ),

            is_verified=(
                is_verified
            ),

            opening_hours=(
                opening_hours
            ),

            menu_highlights=(
                menu_highlights
            ),

            special_offer=(
                special_offer
            ),

            image_url=(
                primary_image_url
            ),

            image_url_2=(

                second_image_url

                if listing_level
                in {
                    "business",
                    "promotion",
                }

                else None

            ),

            image_url_3=(

                third_image_url

                if listing_level
                in {
                    "business",
                    "promotion",
                }

                else None

            ),

            featured=(
                featured
            ),


            # =============================================
            # SPONSORED
            # =============================================

            is_sponsored=(
                is_sponsored
            ),

            sponsorship_status=(
                sponsorship_status
            ),

            sponsored_starts_at=(
                sponsored_starts_at
            ),

            sponsored_expires_at=(
                sponsored_expires_at
            ),

            sponsored_priority=(
                sponsored_priority
            ),

            sponsorship_reference=(
                sponsorship_reference
            ),


            # =============================================
            # NOTIFICATIONS
            # =============================================

            notification_eligible=(
                notification_eligible
            ),

            active=(
                request.form.get(
                    "active"
                )
                == "on"
            ),
        )


        # =================================================
        # APPLY NORMALIZED CONTENT DATES
        # =================================================

        for key, value in (
            dates.items()
        ):

            setattr(
                item,
                key,
                value,
            )


        # =================================================
        # CONFIGURE EXISTING COMMERCIAL PACKAGE
        #
        # Sponsorship does NOT enter this helper.
        # =================================================

        try:

            distribution_zone_ids = (
                _configure_commercial_content(
                    item=item,
                    category=category,
                    content_type=content_type,
                )
            )


        except ValueError as error:

            db.session.rollback()


            flash(
                str(
                    error
                ),
                "error",
            )


            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # CAMPAIGN HOME-ZONE RULE
        # =================================================

        if (
            pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            if (
                zone_id
                not in distribution_zone_ids
            ):

                db.session.rollback()


                flash(
                    (
                        "A campaign must include its "
                        "home zone as part of its reach."
                    ),
                    "error",
                )


                return _render_content_form(
                    zones,
                    categories,
                    None,
                )


            if (
                len(
                    distribution_zone_ids
                )
                > 3
            ):

                db.session.rollback()


                flash(
                    (
                        "Kalxa campaign packages currently "
                        "support a maximum of 3 zones."
                    ),
                    "error",
                )


                return _render_content_form(
                    zones,
                    categories,
                    None,
                )


        elif (
            pricing_model
            == PRICING_MODEL_PRESENCE
        ):

            distribution_zone_ids = (
                []
            )


        # =================================================
        # SAVE CONTENT + DISTRIBUTION AS ONE TRANSACTION
        # =================================================

        try:

            db.session.add(
                item
            )


            db.session.flush()


            for distribution_zone_id in (
                distribution_zone_ids
            ):

                distribution_link = (
                    ContentDistributionZone(

                        content_item_id=(
                            item.id
                        ),

                        zone_id=(
                            distribution_zone_id
                        ),

                    )
                )


                db.session.add(
                    distribution_link
                )


            db.session.commit()


        except Exception as error:

            db.session.rollback()


            current_app.logger.exception(
                (
                    "Failed to create content item. "
                    "title=%s error=%s"
                ),
                title,
                error,
            )


            flash(
                (
                    "Content could not be published. "
                    "Please try again."
                ),
                "error",
            )


            return _render_content_form(
                zones,
                categories,
                None,
            )


        # =================================================
        # SUCCESS
        # =================================================

        if (
            item.pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            flash(
                (
                    "Campaign published successfully. "
                    f"Reach: "
                    f"{len(distribution_zone_ids)} zone(s). "
                    f"Package price: "
                    f"{format_kalxa_price(item.amount_due)}."
                ),
                "success",
            )


        elif (
            item.pricing_model
            == PRICING_MODEL_PRESENCE
        ):

            flash(
                (
                    "Presence listing published successfully. "
                    f"Package price: "
                    f"{format_kalxa_price(item.amount_due)}."
                ),
                "success",
            )


        else:

            flash(
                "Content published successfully.",
                "success",
            )


        return redirect(
            url_for(
                "admin.content_list"
            )
        )


    # =====================================================
    # GET — SHOW CREATE FORM
    # =====================================================

    return _render_content_form(
        zones,
        categories,
        None,
    )


@admin_bp.route(
    "/content/<int:item_id>/edit",
    methods=["GET", "POST"],
)
def edit_content(
    item_id,
):

    # =====================================================
    # ADMIN AUTHENTICATION
    # =====================================================

    auth = require_admin()

    if auth:
        return auth


    # =====================================================
    # LOAD CONTENT ITEM
    # =====================================================

    item = (
        ContentItem.query
        .get_or_404(
            item_id
        )
    )


    # =====================================================
    # LOAD ZONES
    # =====================================================

    zones = (
        Zone.query
        .filter_by(
            active=True
        )
        .order_by(
            Zone.name.asc()
        )
        .all()
    )


    # =====================================================
    # LOAD CATEGORIES
    # =====================================================

    categories = (
        get_categories(
            active_only=False
        )
    )


    # =====================================================
    # POST — UPDATE CONTENT
    # =====================================================

    if request.method == "POST":

        # =================================================
        # SNAPSHOT EXISTING COMMERCIAL PACKAGE
        #
        # DO NOT mix sponsorship state into this snapshot.
        #
        # This block continues protecting the original
        # Presence/Campaign payment workflow.
        # =================================================

        old_pricing_model = (
            item.pricing_model
        )

        old_duration_days = (
            item.commercial_duration_days
        )

        old_payment_status = (
            item.payment_status
        )

        old_amount_due = (
            item.amount_due
        )

        old_amount_paid = (
            item.amount_paid
        )

        old_payment_reference = (
            item.payment_reference
        )

        old_paid_at = (
            item.paid_at
        )

        old_commercial_starts_at = (
            item.commercial_starts_at
        )

        old_commercial_expires_at = (
            item.commercial_expires_at
        )


        # =================================================
        # EXISTING DISTRIBUTION ZONES
        # =================================================

        old_distribution_zone_ids = {

            link.zone_id

            for link in (
                item.distribution_zone_links
            )

        }


        # =================================================
        # BASIC DATA
        # =================================================

        zone_id = request.form.get(
            "zone_id",
            type=int,
        )


        category = (
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


        # =================================================
        # CONTACT + ACTION FIELDS
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
        # REQUIRED FIELDS
        # =================================================

        if (
            not zone_id
            or not category
            or not title
        ):

            flash(
                "Zone, category and title are required.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # VALIDATE ZONE
        # =================================================

        zone = db.session.get(
            Zone,
            zone_id,
        )


        if not zone:

            flash(
                "Selected zone does not exist.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # VALIDATE CATEGORY
        # =================================================

        if not get_category_by_slug(
            category,
            active_only=False,
        ):

            flash(
                "Invalid content category.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # RECALCULATE WORKFLOW
        # =================================================

        workflow = (
            _get_content_workflow(
                category,
                content_type,
            )
        )


        lifetime_type = (
            workflow[
                "lifetime_type"
            ]
        )


        workflow_notification_eligible = bool(
            workflow.get(
                "notification_eligible",
                False,
            )
        )


        pricing_model = (
            workflow.get(
                "pricing_model"
            )
        )


        # =================================================
        # VALIDATE + NORMALIZE CONTENT DATES
        # =================================================

        try:

            dates, error = (
                _validate_and_normalize_content_dates(
                    category,
                    request.form,
                    lifetime_type=lifetime_type,
                )
            )

        except ValueError:

            flash(
                "Please enter valid dates.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        if error:

            flash(
                error,
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # CAMPAIGN START / END TIMES
        # =================================================

        try:

            start_time = (
                parse_optional_time(
                    request.form.get(
                        "start_time"
                    )
                )
            )


            end_time = (
                parse_optional_time(
                    request.form.get(
                        "end_time"
                    )
                )
            )

        except ValueError:

            flash(
                "Please enter valid start and end times.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # EFFECTIVE CAMPAIGN DATES
        # =================================================

        canonical_category = (
            normalize_category(
                category
            )
        )


        if (
            canonical_category == "events"
            or lifetime_type == "event"
        ):

            effective_start_date = (
                dates.get(
                    "event_date"
                )
            )


            effective_end_date = (
                dates.get(
                    "event_end_date"
                )
                or
                effective_start_date
            )

        else:

            effective_start_date = (
                dates.get(
                    "start_date"
                )
            )


            effective_end_date = (
                dates.get(
                    "end_date"
                )
            )


        # =================================================
        # DATE ORDER VALIDATION
        # =================================================

        if (
            effective_start_date
            and effective_end_date
            and effective_end_date
            < effective_start_date
        ):

            flash(
                "End date cannot be before start date.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # SAME-DAY TIME VALIDATION
        # =================================================

        if (
            effective_start_date
            and effective_end_date
            and effective_start_date
            == effective_end_date
            and start_time
            and end_time
            and end_time <= start_time
        ):

            flash(
                (
                    "End time must be after start time "
                    "when the campaign starts and ends "
                    "on the same day."
                ),
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # END TIME WITHOUT END DATE
        # =================================================

        if (
            canonical_category != "events"
            and end_time
            and not effective_end_date
        ):

            flash(
                (
                    "Please enter an end date when "
                    "using an end time."
                ),
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # LISTING LEVEL
        # =================================================

        listing_level = (
            request.form.get(
                "listing_level",
                item.listing_level
                or "discovery",
            )
            .strip()
            .lower()
        )


        allowed_listing_levels = {
            "discovery",
            "business",
            "promotion",
        }


        if (
            listing_level
            not in allowed_listing_levels
        ):

            listing_level = (
                "discovery"
            )


        # =================================================
        # CORE FIELDS
        # =================================================

        item.zone_id = (
            zone_id
        )

        item.category = (
            category
        )

        item.content_type = (
            content_type
        )

        item.lifetime_type = (
            lifetime_type
        )


        if not item.availability_status:

            item.availability_status = (
                "available"
            )


        item.title = (
            title
        )


        item.description = (
            request.form.get(
                "description",
                "",
            )
            .strip()
            or None
        )


        item.business_name = (
            request.form.get(
                "business_name",
                "",
            )
            .strip()
            or None
        )


        item.venue = (
            request.form.get(
                "venue",
                "",
            )
            .strip()
            or None
        )


        item.price = (
            request.form.get(
                "price",
                "",
            )
            .strip()
            or None
        )


        # =================================================
        # PUBLIC CONTACT + ACTION DATA
        # =================================================

        item.contact = (
            contact
        )

        item.whatsapp_number = (
            whatsapp_number
        )

        item.directions_url = (
            directions_url
        )

        item.ticket_url = (
            ticket_url
        )

        item.listing_level = (
            listing_level
        )


        # =================================================
        # OWNERSHIP + VERIFICATION
        # =================================================

        if (
            listing_level
            == "discovery"
        ):

            item.ownership_status = (
                "unclaimed"
            )

            item.is_verified = (
                False
            )


        else:

            item.ownership_status = (
                "claimed"
            )

            item.is_verified = (
                request.form.get(
                    "is_verified"
                )
                == "on"
            )


        # =================================================
        # BUSINESS FIELDS
        # =================================================

        if (
            listing_level
            in {
                "business",
                "promotion",
            }
        ):

            item.opening_hours = (
                request.form.get(
                    "opening_hours",
                    "",
                )
                .strip()
                or None
            )


            item.menu_highlights = (
                request.form.get(
                    "menu_highlights",
                    "",
                )
                .strip()
                or None
            )


            item.special_offer = (
                request.form.get(
                    "special_offer",
                    "",
                )
                .strip()
                or None
            )


        else:

            item.opening_hours = (
                None
            )

            item.menu_highlights = (
                None
            )

            item.special_offer = (
                None
            )

            item.image_url_2 = (
                None
            )

            item.image_url_3 = (
                None
            )


        # =================================================
        # FEATURED
        # =================================================

        item.featured = (
            request.form.get(
                "featured"
            )
            == "on"
        )


        # =================================================
        # SPONSORED VISIBILITY
        #
        # Completely independent from the existing
        # Presence/Campaign payment workflow.
        # =================================================

        is_sponsored = (
            request.form.get(
                "is_sponsored"
            )
            == "on"
        )


        sponsorship_status = (
            request.form.get(
                "sponsorship_status",
                item.sponsorship_status
                or "inactive",
            )
            .strip()
            .lower()
        )


        allowed_sponsorship_statuses = {
            "inactive",
            "scheduled",
            "active",
            "expired",
        }


        if (
            sponsorship_status
            not in allowed_sponsorship_statuses
        ):

            sponsorship_status = (
                "inactive"
            )


        sponsorship_reference = (
            request.form.get(
                "sponsorship_reference",
                "",
            )
            .strip()
            or None
        )


        try:

            sponsored_priority = int(
                request.form.get(
                    "sponsored_priority",
                    item.sponsored_priority
                    or 0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            sponsored_priority = (
                0
            )


        sponsored_priority = max(
            0,
            min(
                sponsored_priority,
                100,
            ),
        )


        sponsored_starts_raw = (
            request.form.get(
                "sponsored_starts_at",
                "",
            )
            .strip()
        )


        sponsored_expires_raw = (
            request.form.get(
                "sponsored_expires_at",
                "",
            )
            .strip()
        )


        try:

            sponsored_starts_at = (

                datetime.fromisoformat(
                    sponsored_starts_raw
                )

                if sponsored_starts_raw

                else None
            )


            sponsored_expires_at = (

                datetime.fromisoformat(
                    sponsored_expires_raw
                )

                if sponsored_expires_raw

                else None
            )


        except ValueError:

            flash(
                "Please enter valid sponsorship dates.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # SPONSORSHIP DATE VALIDATION
        # =================================================

        if (
            sponsored_starts_at
            and sponsored_expires_at
            and sponsored_expires_at
            <= sponsored_starts_at
        ):

            flash(
                (
                    "Sponsored expiry must be after "
                    "the sponsored start time."
                ),
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # APPLY SPONSORSHIP STATE
        # =================================================

        if is_sponsored:

            item.is_sponsored = (
                True
            )

            item.sponsorship_status = (
                sponsorship_status
            )

            item.sponsored_starts_at = (
                sponsored_starts_at
            )

            item.sponsored_expires_at = (
                sponsored_expires_at
            )

            item.sponsored_priority = (
                sponsored_priority
            )

            item.sponsorship_reference = (
                sponsorship_reference
            )


        else:

            item.is_sponsored = (
                False
            )

            item.sponsorship_status = (
                "inactive"
            )

            item.sponsored_starts_at = (
                None
            )

            item.sponsored_expires_at = (
                None
            )

            item.sponsored_priority = (
                0
            )

            item.sponsorship_reference = (
                None
            )


        # =================================================
        # NOTIFICATION ELIGIBILITY
        # =================================================

        if (
            listing_level
            == "promotion"
        ):

            promotion_notification_requested = (
                request.form.get(
                    "notification_eligible"
                )
                == "on"
            )


            item.notification_eligible = (
                workflow_notification_eligible
                and
                promotion_notification_requested
            )


        else:

            item.notification_eligible = (
                False
            )


        # =================================================
        # ACTIVE STATUS
        # =================================================

        item.active = (
            request.form.get(
                "active"
            )
            == "on"
        )


        # =================================================
        # APPLY NORMALIZED CONTENT DATES
        # =================================================

        for key, value in (
            dates.items()
        ):

            setattr(
                item,
                key,
                value,
            )


        # =================================================
        # APPLY CAMPAIGN TIMES
        # =================================================

        item.start_time = (
            start_time
        )

        item.end_time = (
            end_time
        )


        # =================================================
        # IMAGES
        # =================================================

        uploaded_images = []


        for uploaded_file in request.files.getlist(
            "images"
        ):

            if (
                uploaded_file
                and
                uploaded_file.filename
            ):

                uploaded_images.append(
                    uploaded_file
                )


        legacy_image = (
            request.files.get(
                "image"
            )
        )


        if (
            legacy_image
            and legacy_image.filename
            and not uploaded_images
        ):

            uploaded_images.append(
                legacy_image
            )


        # =================================================
        # IMAGE LIMIT
        # =================================================

        if (
            listing_level
            == "discovery"
        ):

            maximum_images = (
                1
            )


        else:

            maximum_images = (
                3
            )


        if (
            len(
                uploaded_images
            )
            > maximum_images
        ):

            if (
                listing_level
                == "discovery"
            ):

                message = (
                    "Discovery listings can have "
                    "a maximum of 1 image."
                )


            else:

                message = (
                    "Business and Promotion listings "
                    "can have a maximum of 3 images."
                )


            flash(
                message,
                "error",
            )


            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # UPLOAD NEW IMAGES
        # =================================================

        if uploaded_images:

            uploaded_image_urls = []


            try:

                for uploaded_file in (
                    uploaded_images
                ):

                    image_url = (
                        upload_listing_image(
                            uploaded_file
                        )
                    )


                    if image_url:

                        uploaded_image_urls.append(
                            image_url
                        )


            except Exception as error:

                db.session.rollback()


                current_app.logger.exception(
                    (
                        "Content image upload failed. "
                        "content_item_id=%s error=%s"
                    ),
                    item.id,
                    error,
                )


                flash(
                    f"Image upload failed: {error}",
                    "error",
                )


                return _render_content_form(
                    zones,
                    categories,
                    item,
                )


            item.image_url = (
                uploaded_image_urls[0]

                if len(
                    uploaded_image_urls
                ) >= 1

                else None
            )


            if (
                listing_level
                in {
                    "business",
                    "promotion",
                }
            ):

                item.image_url_2 = (
                    uploaded_image_urls[1]

                    if len(
                        uploaded_image_urls
                    ) >= 2

                    else None
                )


                item.image_url_3 = (
                    uploaded_image_urls[2]

                    if len(
                        uploaded_image_urls
                    ) >= 3

                    else None
                )


            else:

                item.image_url_2 = (
                    None
                )

                item.image_url_3 = (
                    None
                )


        # =================================================
        # FINAL DISCOVERY IMAGE SAFETY
        # =================================================

        if (
            listing_level
            == "discovery"
        ):

            item.image_url_2 = (
                None
            )

            item.image_url_3 = (
                None
            )


        # =================================================
        # CONFIGURE EXISTING COMMERCIAL PACKAGE
        #
        # Sponsorship does NOT affect this calculation.
        # =================================================

        try:

            distribution_zone_ids = (
                _configure_commercial_content(
                    item=item,
                    category=category,
                    content_type=content_type,
                )
            )


        except ValueError as error:

            db.session.rollback()


            flash(
                str(
                    error
                ),
                "error",
            )


            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # CAMPAIGN HOME-ZONE RULE
        # =================================================

        if (
            pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            if (
                zone_id
                not in distribution_zone_ids
            ):

                db.session.rollback()


                flash(
                    (
                        "A campaign must include its "
                        "home zone as part of its reach."
                    ),
                    "error",
                )


                return _render_content_form(
                    zones,
                    categories,
                    item,
                )


            if (
                len(
                    distribution_zone_ids
                )
                > 3
            ):

                db.session.rollback()


                flash(
                    (
                        "Kalxa campaign packages currently "
                        "support a maximum of 3 zones."
                    ),
                    "error",
                )


                return _render_content_form(
                    zones,
                    categories,
                    item,
                )


        elif (
            pricing_model
            == PRICING_MODEL_PRESENCE
        ):

            distribution_zone_ids = (
                []
            )


        # =================================================
        # DETERMINE WHETHER ORIGINAL COMMERCIAL PACKAGE
        # CHANGED
        #
        # Sponsorship is intentionally NOT included.
        # =================================================

        new_distribution_zone_ids = set(
            distribution_zone_ids
        )


        package_changed = (

            old_pricing_model
            != item.pricing_model

            or

            old_duration_days
            != item.commercial_duration_days

            or

            (
                item.pricing_model
                == PRICING_MODEL_CAMPAIGN

                and

                old_distribution_zone_ids
                != new_distribution_zone_ids
            )

            or

            (
                old_pricing_model
                == PRICING_MODEL_CAMPAIGN

                and

                item.pricing_model
                != PRICING_MODEL_CAMPAIGN
            )

        )


        # =================================================
        # PRESERVE EXISTING PAID / WAIVED PERIOD
        # =================================================

        if (
            not package_changed
            and
            old_payment_status
            == item.payment_status
            and
            item.payment_status
            in {
                "paid",
                "waived",
            }
        ):

            item.commercial_starts_at = (
                old_commercial_starts_at
            )

            item.commercial_expires_at = (
                old_commercial_expires_at
            )


            if (
                item.payment_status
                == "paid"
            ):

                item.paid_at = (
                    old_paid_at
                )

                item.amount_paid = (
                    old_amount_paid
                )


            else:

                item.paid_at = (
                    None
                )

                item.amount_paid = (
                    None
                )


        # =================================================
        # PRESERVE PAYMENT REFERENCE WHEN FORM LEFT EMPTY
        # =================================================

        if (
            not item.payment_reference
            and
            old_payment_reference
            and
            not package_changed
        ):

            item.payment_reference = (
                old_payment_reference
            )


        # =================================================
        # SAVE EVERYTHING AS ONE TRANSACTION
        # =================================================

        try:

            (
                ContentDistributionZone.query
                .filter_by(
                    content_item_id=item.id
                )
                .delete(
                    synchronize_session=False
                )
            )


            for distribution_zone_id in (
                distribution_zone_ids
            ):

                db.session.add(
                    ContentDistributionZone(

                        content_item_id=(
                            item.id
                        ),

                        zone_id=(
                            distribution_zone_id
                        ),

                    )
                )


            db.session.commit()


        except Exception as error:

            db.session.rollback()


            current_app.logger.exception(
                (
                    "Failed to update content item. "
                    "content_item_id=%s error=%s"
                ),
                item.id,
                error,
            )


            flash(
                (
                    "Content could not be updated. "
                    "Please try again."
                ),
                "error",
            )


            return _render_content_form(
                zones,
                categories,
                item,
            )


        # =================================================
        # SUCCESS MESSAGE
        # =================================================

        if (
            item.pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            flash(
                (
                    "Campaign updated successfully. "
                    f"Reach: "
                    f"{len(distribution_zone_ids)} zone(s). "
                    f"Package price: "
                    f"{format_kalxa_price(item.amount_due)}."
                ),
                "success",
            )


        elif (
            item.pricing_model
            == PRICING_MODEL_PRESENCE
        ):

            flash(
                (
                    "Presence listing updated successfully. "
                    f"Package price: "
                    f"{format_kalxa_price(item.amount_due)}."
                ),
                "success",
            )


        else:

            flash(
                "Content updated successfully.",
                "success",
            )


        return redirect(
            url_for(
                "admin.content_list"
            )
        )


    # =====================================================
    # GET — SHOW EDIT FORM
    # =====================================================

    return _render_content_form(
        zones,
        categories,
        item,
    )
       
@admin_bp.route(
    "/content/<int:item_id>/toggle",
    methods=["POST"],
)
def toggle_content(
    item_id,
):

    auth = require_admin()

    if auth:
        return auth

    item = (
        ContentItem.query
        .get_or_404(
            item_id
        )
    )

    item.active = (
        not item.active
    )

    db.session.commit()

    flash(
        (
            "Content activated."
            if item.active
            else
            "Content deactivated."
        ),
        "success",
    )

    return redirect(
        url_for(
            "admin.content_list"
        )
    )
@admin_bp.route(
    "/content/<int:item_id>/delete",
    methods=["POST"],
)
def delete_content(
    item_id,
):

    auth = require_admin()

    if auth:
        return auth

    item = (
        ContentItem.query
        .get_or_404(
            item_id
        )
    )

    try:

        # =================================================
        # REMOVE SUBMISSION REFERENCES
        # =================================================

        submissions = (
            PendingSubmission.query
            .filter_by(
                published_content_id=
                    item.id
            )
            .all()
        )

        for submission in submissions:

            submission.published_content_id = (
                None
            )

        # =================================================
        # DELETE ATTACHED IMAGES
        # =================================================

        ContentImage.query.filter_by(
            content_item_id=
                item.id
        ).delete(
            synchronize_session=False
        )

        # =================================================
        # DELETE CONTENT
        # =================================================

        db.session.delete(
            item
        )

        db.session.commit()

        flash(
            "Content permanently deleted.",
            "success",
        )

    except Exception as exc:

        db.session.rollback()

        print(
            "Delete content error:",
            exc,
        )

        flash(
            "Unable to permanently delete content.",
            "error",
        )

    return redirect(
        url_for(
            "admin.content_list"
        )
    )

@admin_bp.route("/content/<int:item_id>/archive", methods=["POST"])
def archive_content_now(item_id):
    auth = require_admin()
    if auth:
        return auth

    item = ContentItem.query.get_or_404(item_id)
    if item.archived:
        flash("Content is already archived.", "error")
        return redirect(url_for("admin.content_list"))

    item.archived = True
    item.active = False

    item.archived_at = (
      datetime.utcnow()
    )
    db.session.commit()
    flash(f"{item.title} archived.", "success")
    return redirect(url_for("admin.content_list"))



@admin_bp.route("/submissions")
def submissions():

    auth = require_admin()

    if auth:
        return auth


    # -------------------------------------------------
    # SELECTED STATUS
    # -------------------------------------------------

    selected_status = (
        request.args.get(
            "status",
            "pending",
        )
        .strip()
        .lower()
    )


    allowed_statuses = {
        "pending",
        "approved",
        "rejected",
    }


    if selected_status not in allowed_statuses:
        selected_status = "pending"


    # -------------------------------------------------
    # COUNTERS
    # -------------------------------------------------

    pending_count = (
        PendingSubmission.query
        .filter_by(
            status="pending"
        )
        .count()
    )


    approved_count = (
        PendingSubmission.query
        .filter_by(
            status="approved"
        )
        .count()
    )


    rejected_count = (
        PendingSubmission.query
        .filter_by(
            status="rejected"
        )
        .count()
    )


    # -------------------------------------------------
    # FILTER SUBMISSIONS
    # -------------------------------------------------

    items = (
        PendingSubmission.query
        .filter_by(
            status=selected_status
        )
        .order_by(
            PendingSubmission.created_at.desc()
        )
        .all()
    )


    # -------------------------------------------------
    # TEMPLATE
    # -------------------------------------------------

    return render_template(
        "admin/submissions.html",

        submissions=items,

        selected_status=
            selected_status,

        pending_count=
            pending_count,

        approved_count=
            approved_count,

        rejected_count=
            rejected_count,
    )


@admin_bp.route(
    "/submissions/<int:submission_id>/edit",
    methods=["GET", "POST"],
)
def edit_submission(submission_id):

    auth = require_admin()

    if auth:
        return auth


    submission = (
        PendingSubmission.query
        .get_or_404(
            submission_id
        )
    )


    # Only pending submissions should be edited.
    if submission.status != "pending":

        flash(
            "Only pending submissions can be edited.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status=submission.status,
            )
        )


    zones = (
        Zone.query
        .filter_by(
            active=True
        )
        .order_by(
            Zone.name.asc()
        )
        .all()
    )


    categories = get_categories()


    if request.method == "POST":

        zone_id = request.form.get(
            "zone_id",
            type=int,
        )

        category = (
            request.form.get(
                "category",
                "",
            )
            .strip()
        )

        title = (
            request.form.get(
                "title",
                "",
            )
            .strip()
        )


        # ---------------------------------------------
        # BASIC VALIDATION
        # ---------------------------------------------

        if (
            not zone_id
            or not category
            or not title
        ):

            flash(
                "Zone, category and title are required.",
                "error",
            )

            return render_template(
                "admin/submission_edit.html",
                submission=submission,
                zones=zones,
                categories=categories,
            )


        zone = db.session.get(
            Zone,
            zone_id,
        )

        if (
            not zone
            or not zone.active
        ):

            flash(
                "Please select a valid active zone.",
                "error",
            )

            return render_template(
                "admin/submission_edit.html",
                submission=submission,
                zones=zones,
                categories=categories,
            )


        if not get_category_by_slug(
            category
        ):

            flash(
                "Please select a valid active category.",
                "error",
            )

            return render_template(
                "admin/submission_edit.html",
                submission=submission,
                zones=zones,
                categories=categories,
            )


        # ---------------------------------------------
        # DATE VALIDATION
        # ---------------------------------------------

        try:

            dates, error = (
                _validate_and_normalize_content_dates(
                    category,
                    request.form,
                )
            )

        except ValueError:

            flash(
                "Please enter valid dates.",
                "error",
            )

            return render_template(
                "admin/submission_edit.html",
                submission=submission,
                zones=zones,
                categories=categories,
            )


        if error:

            flash(
                error,
                "error",
            )

            return render_template(
                "admin/submission_edit.html",
                submission=submission,
                zones=zones,
                categories=categories,
            )


        # ---------------------------------------------
        # UPDATE SUBMISSION
        # ---------------------------------------------

        submission.zone_id = zone_id
        submission.category = category
        submission.title = title

        submission.description = (
            request.form.get(
                "description",
                "",
            )
            .strip()
            or None
        )

        submission.business_name = (
            request.form.get(
                "business_name",
                "",
            )
            .strip()
            or None
        )

        submission.venue = (
            request.form.get(
                "venue",
                "",
            )
            .strip()
            or None
        )

        submission.price = (
            request.form.get(
                "price",
                "",
            )
            .strip()
            or None
        )

        submission.contact = (
            request.form.get(
                "contact",
                "",
            )
            .strip()
            or None
        )


        # Submitter contact information can also
        # be corrected by admin.

        submission.submitter_name = (
            request.form.get(
                "submitter_name",
                "",
            )
            .strip()
        )

        submission.submitter_phone = (
            request.form.get(
                "submitter_phone",
                "",
            )
            .strip()
        )

        submission.submitter_email = (
            request.form.get(
                "submitter_email",
                "",
            )
            .strip()
            or None
        )


        for key, value in dates.items():

            setattr(
                submission,
                key,
                value,
            )


        db.session.commit()


        flash(
            "Submission updated successfully. You can now approve it.",
            "success",
        )


        return redirect(
            url_for(
                "admin.submissions",
                status="pending",
            )
        )


    return render_template(
        "admin/submission_edit.html",
        submission=submission,
        zones=zones,
        categories=categories,
    )



@admin_bp.route(
    "/submissions/<int:submission_id>/approve",
    methods=["POST"],
)
def approve_submission(submission_id):

    auth = require_admin()

    if auth:
        return auth

    submission = (
        PendingSubmission.query
        .get_or_404(submission_id)
    )

    # =====================================================
    # ALREADY REVIEWED
    # =====================================================

    if submission.status != "pending":

        flash(
            "Submission has already been reviewed.",
            "error",
        )

        return redirect(
            url_for("admin.submissions")
        )

    # =====================================================
    # VALIDATE HOME / ORIGIN ZONE
    # =====================================================

    zone = db.session.get(
        Zone,
        submission.zone_id,
    )

    if not zone:

        flash(
            "The submission zone no longer exists.",
            "error",
        )

        return redirect(
            url_for("admin.submissions")
        )

    # =====================================================
    # VALIDATE CATEGORY
    #
    # IMPORTANT:
    # Keep the REAL public category slug.
    #
    # Examples:
    # discount-deals
    # upcoming-event-🥹🔥
    # beauty-salon
    # property
    # =====================================================

    category = get_category_by_slug(
        submission.category
    )

    if not category:

        flash(
            "The submission category is inactive or unavailable.",
            "error",
        )

        return redirect(
            url_for("admin.submissions")
        )

    # =====================================================
    # VALUES NEEDED AFTER COMMIT
    # =====================================================

    content = None
    notification_zone_ids = []

    try:

        # =================================================
        # DETERMINE MAIN / COVER IMAGE
        # =================================================

        submission_images = list(
            submission.images
        )

        first_image_url = None

        if submission_images:

            first_image = submission_images[0]

            if first_image.image_url:

                first_image_url = (
                    first_image.image_url
                )

        # Explicit cover image takes priority.
        if submission.image_url:

            first_image_url = (
                submission.image_url
            )

        # =================================================
        # LIFECYCLE / WORKFLOW FIELDS
        # =================================================

        content_type = (
            submission.content_type
            or None
        )

        lifetime_type = (
            submission.lifetime_type
            or None
        )

        availability_status = (
            submission.availability_status
            or "available"
        )

        notification_eligible = bool(
            submission.notification_eligible
        )

        # -------------------------------------------------
        # Legacy fallback
        # -------------------------------------------------

        if not lifetime_type:

            if submission.category in {
                "events",
                "upcoming-event-🥹🔥",
            }:

                lifetime_type = (
                    "time_specific"
                )

                notification_eligible = True

            elif submission.end_date:

                lifetime_type = (
                    "time_specific"
                )

            else:

                lifetime_type = (
                    "ongoing"
                )

        # =================================================
        # COMMERCIAL PACKAGE
        # =================================================

        pricing_model = (
            submission.pricing_model
            or None
        )

        commercial_duration_days = (
            submission.commercial_duration_days
        )

        amount_due = (
            submission.amount_due
        )

        payment_status = (
            submission.payment_status
            or (
                "unpaid"
                if pricing_model
                else "waived"
            )
        )

        allowed_payment_statuses = {
            "unpaid",
            "paid",
            "waived",
            "refunded",
        }

        if (
            payment_status
            not in allowed_payment_statuses
        ):

            payment_status = "unpaid"

        # =================================================
        # VALIDATE COMMERCIAL SUBMISSION
        # =================================================

        if pricing_model:

            if pricing_model not in {
                PRICING_MODEL_PRESENCE,
                PRICING_MODEL_CAMPAIGN,
            }:

                raise ValueError(
                    "Unsupported Kalxa pricing model."
                )

            if not commercial_duration_days:

                raise ValueError(
                    "Commercial submission has no "
                    "package duration."
                )

            try:

                commercial_duration_days = int(
                    commercial_duration_days
                )

            except (TypeError, ValueError):

                raise ValueError(
                    "Commercial submission has an "
                    "invalid package duration."
                )

            if amount_due is None:

                raise ValueError(
                    "Commercial submission has no "
                    "calculated amount due."
                )

        # =================================================
        # CAMPAIGN DISTRIBUTION ZONES
        # =================================================

        distribution_zone_ids = []

        if (
            pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            raw_zone_ids = (
                submission.distribution_zone_ids
                or []
            )

            # ---------------------------------------------
            # NORMALIZE ZONE IDs
            # ---------------------------------------------

            for raw_zone_id in raw_zone_ids:

                try:

                    zone_id = int(
                        raw_zone_id
                    )

                except (TypeError, ValueError):

                    continue

                if (
                    zone_id
                    not in distribution_zone_ids
                ):

                    distribution_zone_ids.append(
                        zone_id
                    )

            # ---------------------------------------------
            # ORIGIN ZONE MUST BE INCLUDED
            # ---------------------------------------------

            if (
                submission.zone_id
                not in distribution_zone_ids
            ):

                distribution_zone_ids.insert(
                    0,
                    submission.zone_id,
                )

            # ---------------------------------------------
            # MVP MAXIMUM: 3 ZONES
            # ---------------------------------------------

            if len(distribution_zone_ids) > 3:

                raise ValueError(
                    "Campaign submission exceeds "
                    "the current 3-zone limit."
                )

            if not distribution_zone_ids:

                raise ValueError(
                    "Campaign submission has no "
                    "distribution zones."
                )

            # ---------------------------------------------
            # VERIFY ZONES STILL EXIST
            # ---------------------------------------------

            valid_zone_ids = {
                row.id
                for row in (
                    Zone.query
                    .filter(
                        Zone.id.in_(
                            distribution_zone_ids
                        )
                    )
                    .all()
                )
            }

            if (
                len(valid_zone_ids)
                != len(distribution_zone_ids)
            ):

                raise ValueError(
                    "One or more campaign "
                    "distribution zones no longer exist."
                )

        # =================================================
        # PRESENCE
        #
        # Presence remains in home/origin zone.
        # =================================================

        elif (
            pricing_model
            == PRICING_MODEL_PRESENCE
        ):

            distribution_zone_ids = []

        # =================================================
        # COMMERCIAL ACTIVATION
        #
        # IMPORTANT:
        #
        # The paid duration begins when admin approves.
        #
        # PAID / WAIVED:
        #     commercial period begins now
        #
        # UNPAID / REFUNDED:
        #     approved at moderation layer,
        #     but remains commercially hidden.
        # =================================================

        commercial_starts_at = None
        commercial_expires_at = None

        amount_paid = None
        paid_at = None

        if (
            pricing_model
            and payment_status in {
                "paid",
                "waived",
            }
        ):

            commercial_starts_at = (
                datetime.utcnow()
            )

            commercial_expires_at = (
                commercial_starts_at
                + timedelta(
                    days=commercial_duration_days
                )
            )

            if payment_status == "paid":

                amount_paid = amount_due

                commercial_payment_time = (
                    getattr(
                        submission,
                        "paid_at",
                        None,
                    )
                )

                paid_at = (
                    commercial_payment_time
                    or commercial_starts_at
                )

        # =================================================
        # CREATE LIVE CONTENT
        # =================================================

        content = ContentItem(

            # ---------------------------------------------
            # ORIGIN
            # ---------------------------------------------

            zone_id=
                submission.zone_id,

            category=
                submission.category,

            # ---------------------------------------------
            # CONTENT WORKFLOW
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
            # COMMERCIAL PACKAGE
            # ---------------------------------------------

            pricing_model=
                pricing_model,

            commercial_duration_days=
                commercial_duration_days,

            commercial_starts_at=
                commercial_starts_at,

            commercial_expires_at=
                commercial_expires_at,

            payment_status=
                payment_status,

            amount_due=
                amount_due,

            amount_paid=
                amount_paid,

            paid_at=
                paid_at,

            # ---------------------------------------------
            # LISTING DATA
            # ---------------------------------------------

            title=
                submission.title,

            description=
                submission.description,

            business_name=
                submission.business_name,

            venue=
                submission.venue,

            price=
                submission.price,

            contact=
                submission.contact,

            image_url=
                first_image_url,

            # ---------------------------------------------
            # NATURAL CONTENT DATES
            # ---------------------------------------------

            publish_from=
                submission.publish_from,

            event_date=
                submission.event_date,

            event_end_date=
                submission.event_end_date,

            start_date=
                submission.start_date,

            end_date=
                submission.end_date,

            # ---------------------------------------------
            # MODERATION STATUS
            # ---------------------------------------------

            featured=False,

            active=True,

            archived=False,
        )

        db.session.add(
            content
        )

        # Generate content.id.
        db.session.flush()

        # =================================================
        # CREATE CAMPAIGN DISTRIBUTION LINKS
        #
        # ONE ContentItem.
        # MULTIPLE geographic distribution records.
        # =================================================

        if (
            pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            for distribution_zone_id in (
                distribution_zone_ids
            ):

                distribution_link = (
                    ContentDistributionZone(

                        content_item_id=
                            content.id,

                        zone_id=
                            distribution_zone_id,
                    )
                )

                db.session.add(
                    distribution_link
                )

        # =================================================
        # LINK SUBMISSION TO CONTENT
        # =================================================

        submission.published_content_id = (
            content.id
        )

        # =================================================
        # COPY CLOUDINARY IMAGES
        # =================================================

        for image in submission_images:

            if not image.image_url:
                continue

            content_image = ContentImage(

                content_item_id=
                    content.id,

                image_url=
                    image.image_url,

                display_order=
                    image.display_order,
            )

            db.session.add(
                content_image
            )

        # =================================================
        # MARK SUBMISSION APPROVED
        # =================================================

        submission.status = "approved"

        submission.reviewed_at = (
            datetime.utcnow()
        )

        # =================================================
        # DETERMINE NOTIFICATION GEOGRAPHY
        #
        # PRESENCE:
        #     home zone only
        #
        # CAMPAIGN:
        #     every purchased distribution zone
        #
        # NON-COMMERCIAL:
        #     home zone
        # =================================================

        if (
            pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            notification_zone_ids = list(
                distribution_zone_ids
            )

        else:

            notification_zone_ids = [
                content.zone_id
            ]

        # =================================================
        # COMMIT APPROVAL FIRST
        #
        # Push must NEVER happen before the content exists
        # publicly.
        # =================================================

        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        current_app.logger.exception(
            "[Kalxa] Approve submission failed "
            "submission_id=%s error=%s",
            submission_id,
            exc,
        )

        flash(
            "Unable to approve submission.",
            "error",
        )

        return redirect(
            url_for("admin.submissions")
        )

    # =====================================================
    # PUSH NOTIFICATION ELIGIBILITY
    #
    # CRITICAL:
    #
    # Do NOT notify users about commercially hidden content.
    #
    # Commercial content must be:
    #
    # paid/waived
    # + commercially activated
    # + approved
    #
    # before a push is sent.
    # =====================================================

    commercial_is_visible = (
        content.pricing_model is None
        or (
            content.payment_status
            in {
                "paid",
                "waived",
            }
            and
            content.commercial_starts_at
            is not None
            and
            content.commercial_expires_at
            is not None
        )
    )

    # =====================================================
    # SEND INSTANT APPROVAL NOTIFICATION
    # =====================================================

    if (
        content.active
        and content.notification_eligible
        and commercial_is_visible
    ):

        try:

            # =================================================
            # HUMAN-FRIENDLY CATEGORY NAME
            #
            # Prefer configured Category.name instead of
            # exposing production slugs such as:
            #
            # upcoming-event-🥹🔥
            # check-out-our-specials
            # =================================================

            category_label = (
                category.name
                if category
                else (
                    content.category
                    .replace("-", " ")
                    .replace("_", " ")
                    .title()
                )
            )

            notification_body = (
                content.title
            )

            notification_url = (
                f"/listing/{content.id}"
            )

            # =================================================
            # ONE PUSH RECORD PER TARGET ZONE
            #
            # This matters for campaigns.
            #
            # Example:
            #
            # KwaMhlanga
            # Siyabuswa
            # Kwaggafontein
            #
            # each receives its own delivery record.
            # =================================================

            for notification_zone_id in (
                notification_zone_ids
            ):

                notification_zone = (
                    db.session.get(
                        Zone,
                        notification_zone_id,
                    )
                )

                if not notification_zone:

                    current_app.logger.warning(
                        "[Kalxa Push] Notification "
                        "zone missing "
                        "content_id=%s "
                        "zone_id=%s",
                        content.id,
                        notification_zone_id,
                    )

                    continue

                # =============================================
                # NOTIFICATION TITLE
                # =============================================

                notification_title = (
                    f"New {category_label} "
                    f"in {notification_zone.name}"
                )

                # =============================================
                # DUPLICATE PROTECTION
                #
                # IMPORTANT:
                # A campaign may legitimately have multiple
                # PushNotification rows for one ContentItem,
                # one per zone.
                # =============================================

                existing_notification = (
                    PushNotification.query
                    .filter_by(
                        content_item_id=
                            content.id,

                        zone_id=
                            notification_zone_id,
                    )
                    .first()
                )

                if existing_notification:

                    current_app.logger.info(
                        "[Kalxa Push] Duplicate "
                        "notification skipped "
                        "content_id=%s "
                        "zone_id=%s "
                        "notification_id=%s",
                        content.id,
                        notification_zone_id,
                        existing_notification.id,
                    )

                    continue

                # =============================================
                # CREATE PUSH HISTORY / OUTBOX RECORD
                # =============================================

                push_record = PushNotification(

                    content_item_id=
                        content.id,

                    zone_id=
                        notification_zone_id,

                    title=
                        notification_title,

                    body=
                        notification_body,

                    target_url=
                        notification_url,

                    status=
                        "pending",

                    total_subscribers=
                        0,

                    sent_count=
                        0,

                    failed_count=
                        0,

                    attempts=
                        0,
                )

                db.session.add(
                    push_record
                )

                db.session.commit()

                # =============================================
                # ATTEMPT DELIVERY
                # =============================================

                push_record.attempts += 1

                push_result = (
                    send_zone_push_notification(

                        zone_id=
                            notification_zone_id,

                        category=
                            content.category,

                        title=
                            notification_title,

                        body=
                            notification_body,

                        url=
                            notification_url,

                        tag=
                            (
                                f"content-"
                                f"{content.id}-"
                                f"zone-"
                                f"{notification_zone_id}"
                            ),
                    )
                )

                # =============================================
                # DEFENSIVE RESULT NORMALIZATION
                # =============================================

                if not isinstance(
                    push_result,
                    dict,
                ):

                    push_result = {}

                total = int(
                    push_result.get(
                        "total",
                        0,
                    )
                    or 0
                )

                sent = int(
                    push_result.get(
                        "sent",
                        0,
                    )
                    or 0
                )

                failed = int(
                    push_result.get(
                        "failed",
                        0,
                    )
                    or 0
                )

                # =============================================
                # SAVE DELIVERY RESULT
                # =============================================

                push_record.total_subscribers = (
                    total
                )

                push_record.sent_count = (
                    sent
                )

                push_record.failed_count = (
                    failed
                )

                if (
                    sent > 0
                    and failed == 0
                ):

                    push_record.status = (
                        "sent"
                    )

                    push_record.sent_at = (
                        datetime.utcnow()
                    )

                    push_record.last_error = None

                elif (
                    sent > 0
                    and failed > 0
                ):

                    push_record.status = (
                        "partial_failure"
                    )

                    push_record.sent_at = (
                        datetime.utcnow()
                    )

                    push_record.last_error = (
                        f"{failed} subscriber "
                        "delivery failures."
                    )

                elif total == 0:

                    push_record.status = (
                        "no_subscribers"
                    )

                    push_record.last_error = (
                        "No active subscribers "
                        "were found for this zone "
                        "and category."
                    )

                else:

                    push_record.status = (
                        "failed"
                    )

                    push_record.last_error = (
                        "Push delivery failed for "
                        "all subscribers."
                    )

                db.session.commit()

                current_app.logger.info(
                    "[Kalxa Push] Approval "
                    "notification processed "
                    "notification_id=%s "
                    "content_id=%s "
                    "zone_id=%s "
                    "category=%s "
                    "total=%s "
                    "sent=%s "
                    "failed=%s "
                    "status=%s",
                    push_record.id,
                    content.id,
                    notification_zone_id,
                    content.category,
                    push_record.total_subscribers,
                    push_record.sent_count,
                    push_record.failed_count,
                    push_record.status,
                )

        except Exception as exc:

            # =================================================
            # DO NOT UNDO APPROVAL
            #
            # The ContentItem has already been committed.
            #
            # A push failure must not make a successfully
            # approved listing disappear.
            # =================================================

            db.session.rollback()

            current_app.logger.exception(
                "[Kalxa Push] Approval notification "
                "failed "
                "content_id=%s "
                "error=%s",
                content.id,
                exc,
            )

    # =====================================================
    # SUCCESS MESSAGE
    # =====================================================

    if (
        content.pricing_model
        and content.payment_status == "unpaid"
    ):

        flash(
            "Submission approved, but the listing is "
            "hidden until payment is confirmed.",
            "success",
        )

    elif (
        content.pricing_model
        and content.payment_status == "refunded"
    ):

        flash(
            "Submission approved, but the listing is "
            "hidden because its payment is refunded.",
            "success",
        )

    else:

        flash(
            "Submission approved and published.",
            "success",
        )

    return redirect(
        url_for("admin.submissions")
    )


            

   


@admin_bp.route(
    "/submissions/<int:submission_id>/confirm-payment",
    methods=["POST"],
)
def confirm_submission_payment(submission_id):

    auth = require_admin()

    if auth:
        return auth

    # =====================================================
    # FIND SUBMISSION
    # =====================================================

    submission = (
        PendingSubmission.query
        .get_or_404(submission_id)
    )

    # =====================================================
    # MUST ALREADY BE APPROVED
    # =====================================================

    if submission.status != "approved":

        flash(
            "Only approved submissions can have "
            "payment confirmed.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    # =====================================================
    # MUST HAVE PUBLISHED CONTENT
    # =====================================================

    if not submission.published_content_id:

        flash(
            "This submission does not have a "
            "published content record.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    content = db.session.get(
        ContentItem,
        submission.published_content_id,
    )

    if not content:

        flash(
            "The published listing could not be found.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    # =====================================================
    # MUST BE COMMERCIAL CONTENT
    # =====================================================

    if content.pricing_model not in {
        PRICING_MODEL_PRESENCE,
        PRICING_MODEL_CAMPAIGN,
    }:

        flash(
            "This listing does not use a Kalxa "
            "commercial package.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    # =====================================================
    # DUPLICATE PAYMENT PROTECTION
    # =====================================================

    if content.payment_status == "paid":

        flash(
            "Payment has already been confirmed "
            "for this listing.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    if content.payment_status == "waived":

        flash(
            "Payment for this listing has been waived.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    # =====================================================
    # PAYMENT MUST CURRENTLY BE UNPAID
    # =====================================================

    if content.payment_status != "unpaid":

        flash(
            "Payment cannot be confirmed from its "
            "current status.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    # =====================================================
    # VALIDATE PACKAGE
    # =====================================================

    if not content.commercial_duration_days:

        flash(
            "This listing has no commercial "
            "package duration.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    if content.amount_due is None:

        flash(
            "This listing has no amount due.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    try:

        duration_days = int(
            content.commercial_duration_days
        )

        if duration_days <= 0:
            raise ValueError(
                "Invalid commercial duration."
            )

        # =================================================
        # ACTIVATE COMMERCIAL PACKAGE
        #
        # The purchased time starts NOW, not when the
        # submission was originally approved.
        # =================================================

        now = datetime.utcnow()

        content.payment_status = "paid"

        content.amount_paid = (
            content.amount_due
        )

        content.paid_at = now

        content.commercial_starts_at = now

        content.commercial_expires_at = (
            now
            + timedelta(
                days=duration_days
            )
        )

        # Keep the PendingSubmission in sync.
        submission.payment_status = "paid"

        db.session.commit()

    except Exception as exc:

        db.session.rollback()

        current_app.logger.exception(
            "[Kalxa Payment] Unable to confirm payment "
            "submission_id=%s "
            "content_id=%s "
            "error=%s",
            submission.id,
            content.id,
            exc,
        )

        flash(
            "Unable to confirm payment.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="approved",
            )
        )

    # =====================================================
    # PUSH NOTIFICATION AFTER ACTIVATION
    #
    # The listing was previously hidden while unpaid.
    # This is the moment it actually becomes commercially
    # visible, so notification can happen now.
    # =====================================================

    if (
        content.active
        and content.notification_eligible
    ):

        try:

            # =============================================
            # DUPLICATE PROTECTION
            # =============================================

            existing_notification = (
                PushNotification.query
                .filter_by(
                    content_item_id=content.id
                )
                .first()
            )

            if not existing_notification:

                zone = db.session.get(
                    Zone,
                    content.zone_id,
                )

                zone_name = (
                    zone.name
                    if zone
                    else "your area"
                )

                category_label = (
                    content.category
                    .replace("-", " ")
                    .replace("_", " ")
                    .title()
                )

                notification_title = (
                    f"New {category_label} "
                    f"in {zone_name}"
                )

                notification_body = (
                    content.title
                )

                notification_url = (
                    f"/listing/{content.id}"
                )

                # =========================================
                # CREATE PUSH HISTORY RECORD
                # =========================================

                push_record = PushNotification(

                    content_item_id=
                        content.id,

                    zone_id=
                        content.zone_id,

                    title=
                        notification_title,

                    body=
                        notification_body,

                    target_url=
                        notification_url,

                    status=
                        "pending",

                    total_subscribers=
                        0,

                    sent_count=
                        0,

                    failed_count=
                        0,

                    attempts=
                        0,
                )

                db.session.add(
                    push_record
                )

                db.session.commit()

                # =========================================
                # SEND PUSH
                # =========================================

                push_record.attempts += 1

                push_result = (
                    send_zone_push_notification(

                        zone_id=
                            content.zone_id,

                        category=
                            content.category,

                        title=
                            notification_title,

                        body=
                            notification_body,

                        url=
                            notification_url,

                        tag=
                            f"content-{content.id}",
                    )
                )

                push_record.total_subscribers = (
                    push_result["total"]
                )

                push_record.sent_count = (
                    push_result["sent"]
                )

                push_record.failed_count = (
                    push_result["failed"]
                )

                # =========================================
                # SAVE PUSH RESULT
                # =========================================

                if (
                    push_result["sent"] > 0
                    and
                    push_result["failed"] == 0
                ):

                    push_record.status = "sent"

                    push_record.sent_at = (
                        datetime.utcnow()
                    )

                    push_record.last_error = None

                elif (
                    push_result["sent"] > 0
                    and
                    push_result["failed"] > 0
                ):

                    push_record.status = (
                        "partial_failure"
                    )

                    push_record.sent_at = (
                        datetime.utcnow()
                    )

                    push_record.last_error = (
                        f"{push_result['failed']} "
                        "subscriber delivery failures."
                    )

                elif push_result["total"] == 0:

                    push_record.status = (
                        "no_subscribers"
                    )

                    push_record.last_error = (
                        "No active subscribers "
                        "were found for this zone."
                    )

                else:

                    push_record.status = "failed"

                    push_record.last_error = (
                        "Push delivery failed for "
                        "all subscribers."
                    )

                db.session.commit()

                current_app.logger.info(
                    "[Kalxa Payment] Payment confirmed "
                    "and listing activated "
                    "submission_id=%s "
                    "content_id=%s "
                    "amount=%s "
                    "expires_at=%s",
                    submission.id,
                    content.id,
                    content.amount_paid,
                    content.commercial_expires_at,
                )

            else:

                current_app.logger.info(
                    "[Kalxa Push] Notification already "
                    "exists for content_id=%s. "
                    "Payment activation push skipped.",
                    content.id,
                )

        except Exception as exc:

            # Payment is ALREADY successfully committed.
            #
            # A push failure must never undo or invalidate
            # a customer's payment.
            db.session.rollback()

            current_app.logger.exception(
                "[Kalxa Push] Payment activation "
                "notification failed "
                "submission_id=%s "
                "content_id=%s "
                "error=%s",
                submission.id,
                content.id,
                exc,
            )

    # =====================================================
    # SUCCESS
    # =====================================================

    flash(
        "Payment confirmed. The Kalxa listing is now live.",
        "success",
    )

    return redirect(
        url_for(
            "admin.submissions",
            status="approved",
        )
    )
            
@admin_bp.route("/submissions/<int:submission_id>/reject", methods=["POST"])
def reject_submission(submission_id):
    auth = require_admin()
    if auth:
        return auth

    submission = PendingSubmission.query.get_or_404(submission_id)
    if submission.status != "pending":
        flash("Submission has already been reviewed.", "error")
        return redirect(url_for("admin.submissions"))

    submission.status = "rejected"
    submission.reviewed_at = datetime.utcnow()
    submission.admin_notes = request.form.get("admin_notes", "").strip() or None
    db.session.commit()

    flash("Submission rejected.", "success")
    return redirect(url_for("admin.submissions"))
