import io
import os
from datetime import date, datetime, timedelta

import qrcode
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import case, func

from categories import get_category_aliases, normalize_category
from cloud_storage import upload_listing_image
from image_utils import upload_lac_image
from models import (
    AccessPoint,
    Category,
    ContentDistributionZone,
    ContentImage,
    ContentItem,
    ContentReminder,
    EngagementEvent,
    ListingClaim,
    PendingSubmission,
    PendingSubmissionImage,
    PushNotification,
    PushSubscriber,
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
    calculate_sponsored_price,
    format_kalxa_price,
    get_pricing_model,
)
from push_service import send_zone_push_notification
from qr_generator import generate_access_qr


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ARCHIVE_GRACE_DAYS = 7
EXPIRING_SOON_DAYS = 3

BUSINESS_ANALYTICS_EVENTS = {
    "listing_view",
    "whatsapp_click",
    "call_click",
    "directions_click",
    "share_click",
    "job_apply_click",
}

ACTION_EVENTS = {
    "whatsapp_click",
    "call_click",
    "directions_click",
    "share_click",
    "job_apply_click",
}

ALLOWED_PAYMENT_STATUSES = {
    "unpaid",
    "paid",
    "waived",
    "refunded",
}

ALLOWED_SPONSORSHIP_STATUSES = {
    "inactive",
    "scheduled",
    "active",
    "expired",
}

ALLOWED_LISTING_LEVELS = {
    "discovery",
    "business",
    "promotion",
}


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


def clean_slug(value):
    return (
        (value or "")
        .strip()
        .lower()
        .replace(" ", "-")
        .replace("_", "-")
    )


def get_categories(active_only=True):
    query = Category.query
    if active_only:
        query = query.filter(Category.active.is_(True))
    return (
        query
        .order_by(
            Category.display_order.asc(),
            Category.name.asc(),
        )
        .all()
    )


def get_category_by_slug(slug, active_only=True):
    requested_slug = str(slug or "").strip().lower()
    if not requested_slug:
        return None

    exact_query = Category.query.filter(Category.slug == requested_slug)
    if active_only:
        exact_query = exact_query.filter(Category.active.is_(True))

    exact_category = exact_query.first()
    if exact_category:
        return exact_category

    canonical_category = normalize_category(requested_slug)
    if not canonical_category:
        return None

    equivalent_slugs = set(get_category_aliases(canonical_category) or [])
    equivalent_slugs.update({requested_slug, canonical_category})
    equivalent_slugs.discard(None)
    equivalent_slugs.discard("")

    if not equivalent_slugs:
        return None

    equivalent_query = Category.query.filter(
        Category.slug.in_(equivalent_slugs)
    )
    if active_only:
        equivalent_query = equivalent_query.filter(Category.active.is_(True))

    categories = equivalent_query.all()
    if not categories:
        return None

    for category_record in categories:
        if category_record.slug == canonical_category:
            return category_record

    categories.sort(
        key=lambda category_record: (
            category_record.display_order
            if category_record.display_order is not None
            else 999999,
            category_record.name or "",
            category_record.id,
        )
    )
    return categories[0]


def get_public_base_url():
    return os.environ.get(
        "PUBLIC_BASE_URL",
        "https://lac-local-access.onrender.com",
    ).rstrip("/")


def get_access_point_qr_url(access_point):
    return f"{get_public_base_url()}/q/{access_point.code}"


def create_access_point_qr(access_point):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(get_access_point_qr_url(access_point))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _render_content_form(zones, categories, item):
    return render_template(
        "admin/content_form.html",
        zones=zones,
        categories=categories,
        item=item,
    )


def _render_submission_edit(submission, zones, categories):
    return render_template(
        "admin/submission_edit.html",
        submission=submission,
        zones=zones,
        categories=categories,
    )


ADMIN_CONTENT_WORKFLOWS = {
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
    category = str(category or "").strip().lower()
    content_type = str(content_type or "").strip().lower()
    canonical_category = normalize_category(category)
    workflow = None

    for workflow_key in {category, canonical_category}:
        if not workflow_key:
            continue
        category_workflows = ADMIN_CONTENT_WORKFLOWS.get(workflow_key, {})
        if content_type in category_workflows:
            workflow = dict(category_workflows[content_type])
            break

    if workflow is None:
        if canonical_category == "jobs":
            workflow = {
                "lifetime_type": "until_unavailable" if content_type == "job" else "time_specific",
                "notification_eligible": True,
            }
        elif canonical_category == "events":
            workflow = {"lifetime_type": "time_specific", "notification_eligible": True}
        elif canonical_category in {"restaurants", "beauty", "accommodation", "delivery", "services", "building", "transport"}:
            workflow = {"lifetime_type": "ongoing", "notification_eligible": True}
        elif canonical_category in {"rentals", "property"}:
            workflow = {"lifetime_type": "until_unavailable", "notification_eligible": True}
        else:
            workflow = {"lifetime_type": "time_specific", "notification_eligible": True}

    workflow["pricing_model"] = (
        None
        if canonical_category == "jobs"
        else get_pricing_model(category, content_type)
    )
    return workflow


def calculate_content_price(category, content_type, duration_days, zone_count=1):
    workflow = get_content_workflow(category, content_type)
    pricing_model = workflow.get("pricing_model")
    if not pricing_model:
        raise KalxaPricingError("This listing does not have a valid Kalxa pricing model.")
    if pricing_model == PRICING_MODEL_PRESENCE:
        zone_count = 1
    return calculate_kalxa_price(
        pricing_model=pricing_model,
        duration_days=duration_days,
        zone_count=zone_count,
    )


def _parse_distribution_zone_ids():
    zone_ids = []
    for raw_zone_id in request.form.getlist("distribution_zone_ids"):
        try:
            zone_id = int(raw_zone_id)
        except (TypeError, ValueError):
            continue
        if zone_id not in zone_ids:
            zone_ids.append(zone_id)
    return zone_ids


def _clear_commercial_state(item):
    item.pricing_model = None
    item.commercial_duration_days = None
    item.commercial_starts_at = None
    item.commercial_expires_at = None
    item.payment_status = "waived"
    item.amount_due = None
    item.amount_paid = None
    item.payment_reference = None
    item.paid_at = None


def _configure_commercial_content(item, category, content_type):
    canonical_category = normalize_category(category)
    if canonical_category == "jobs":
        _clear_commercial_state(item)
        return []

    workflow = get_content_workflow(category, content_type)
    pricing_model = workflow.get("pricing_model")
    if not pricing_model:
        _clear_commercial_state(item)
        return []

    raw_duration = request.form.get("commercial_duration_days", "").strip()
    try:
        duration_days = int(raw_duration)
    except (TypeError, ValueError):
        raise ValueError("Please select a valid Kalxa package duration.")

    payment_status = request.form.get("payment_status", "unpaid").strip().lower()
    if payment_status not in ALLOWED_PAYMENT_STATUSES:
        payment_status = "unpaid"

    distribution_zone_ids = []
    if pricing_model == PRICING_MODEL_CAMPAIGN:
        distribution_zone_ids = _parse_distribution_zone_ids()
        if not distribution_zone_ids:
            raise ValueError("Campaign content must have at least one distribution zone.")
        if len(distribution_zone_ids) > 3:
            raise ValueError("The current Kalxa campaign packages support a maximum of 3 zones.")
        existing_zone_ids = {z.id for z in Zone.query.filter(Zone.id.in_(distribution_zone_ids)).all()}
        if len(existing_zone_ids) != len(distribution_zone_ids):
            raise ValueError("One or more selected campaign zones are invalid.")
        zone_count = len(distribution_zone_ids)
    else:
        zone_count = 1

    try:
        amount_due = calculate_kalxa_price(
            pricing_model=pricing_model,
            duration_days=duration_days,
            zone_count=zone_count,
        )
    except KalxaPricingError as exc:
        raise ValueError(str(exc))

    now = datetime.utcnow()
    commercial_starts_at = None
    commercial_expires_at = None
    if payment_status in {"paid", "waived"}:
        commercial_starts_at = now
        commercial_expires_at = now + timedelta(days=duration_days)

    item.pricing_model = pricing_model
    item.commercial_duration_days = duration_days
    item.commercial_starts_at = commercial_starts_at
    item.commercial_expires_at = commercial_expires_at
    item.payment_status = payment_status
    item.amount_due = amount_due
    item.payment_reference = request.form.get("payment_reference", "").strip() or None

    if payment_status == "paid":
        item.amount_paid = amount_due
        item.paid_at = now
    else:
        item.amount_paid = None
        item.paid_at = None

    return distribution_zone_ids


def _validate_and_normalize_content_dates(category, form, lifetime_type=None):
    canonical_category = normalize_category(category)
    start_date = parse_date(form.get("start_date"))
    end_date = parse_date(form.get("end_date"))
    publish_from = parse_date(form.get("publish_from"))
    event_date = parse_date(form.get("event_date"))
    event_end_date = parse_date(form.get("event_end_date"))

    if not lifetime_type:
        if canonical_category == "events":
            lifetime_type = "time_specific"
        elif end_date:
            lifetime_type = "time_specific"
        else:
            lifetime_type = "ongoing"

    if canonical_category == "events" and lifetime_type == "time_specific":
        if not event_date:
            return None, "Event Date is required for events."
        if publish_from and publish_from > event_date:
            return None, "Publish From cannot be after Event Date."
        if event_end_date and event_end_date < event_date:
            return None, "Event End Date cannot be before Event Date."
        start_date = None
        end_date = None
    elif lifetime_type == "time_specific":
        if not end_date:
            return None, "End Date is required for this time-specific listing."
        if start_date and end_date < start_date:
            return None, "End date cannot be before start date."
        publish_from = None
        event_date = None
        event_end_date = None
    elif lifetime_type in {"until_unavailable", "ongoing"}:
        start_date = None
        end_date = None
        publish_from = None
        event_date = None
        event_end_date = None
    elif lifetime_type == "recurring":
        publish_from = None
        event_date = None
        event_end_date = None
        if start_date and end_date and end_date < start_date:
            return None, "End date cannot be before start date."
    else:
        return None, "Invalid listing lifetime type."

    return {
        "start_date": start_date,
        "end_date": end_date,
        "publish_from": publish_from,
        "event_date": event_date,
        "event_end_date": event_end_date,
    }, None


def get_content_status(item, today=None):
    today = today or date.today()
    if item.archived:
        return {"key": "archived", "label": "ARCHIVED", "icon": "📦"}
    if not item.active:
        return {"key": "inactive", "label": "INACTIVE", "icon": "⚪"}

    availability_status = getattr(item, "availability_status", None) or "available"
    if availability_status in {"taken", "sold", "filled", "closed"}:
        return {"key": "inactive", "label": availability_status.upper(), "icon": "⚫"}
    if availability_status == "expired":
        return {"key": "expired", "label": "EXPIRED", "icon": "🔴"}

    canonical_category = normalize_category(item.category)
    lifetime_type = getattr(item, "lifetime_type", None)
    if not lifetime_type:
        if canonical_category == "events":
            lifetime_type = "time_specific"
        elif item.end_date:
            lifetime_type = "time_specific"
        else:
            lifetime_type = "ongoing"

    if lifetime_type == "time_specific":
        if canonical_category == "events":
            if item.publish_from and item.publish_from > today:
                return {"key": "upcoming", "label": "UPCOMING", "icon": "🟡"}
            expiry_date = item.event_end_date or item.event_date
            if expiry_date and expiry_date < today:
                return {"key": "expired", "label": "EXPIRED", "icon": "🔴"}
            return {"key": "live", "label": "LIVE", "icon": "🟢"}
        if item.start_date and item.start_date > today:
            return {"key": "upcoming", "label": "UPCOMING", "icon": "🟡"}
        if item.end_date and item.end_date < today:
            return {"key": "expired", "label": "EXPIRED", "icon": "🔴"}

    return {"key": "live", "label": "LIVE", "icon": "🟢"}


def get_content_expiry_date(item):
    canonical_category = normalize_category(item.category)
    lifetime_type = getattr(item, "lifetime_type", None)
    if not lifetime_type:
        if canonical_category == "events":
            lifetime_type = "time_specific"
        elif item.end_date:
            lifetime_type = "time_specific"
        else:
            lifetime_type = "ongoing"
    if lifetime_type not in {"time_specific", "recurring"}:
        return None
    if canonical_category == "events":
        return item.event_end_date or item.event_date
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
            item.archived_at = datetime.utcnow()
            archived_count += 1
    if archived_count:
        db.session.commit()
    return archived_count


def get_reminder_analytics(top_limit=10):
    summary = (
        db.session.query(
            func.count(ContentReminder.id).label("total"),
            func.sum(case((ContentReminder.status == "pending", 1), else_=0)).label("pending"),
            func.sum(case((ContentReminder.status == "processing", 1), else_=0)).label("processing"),
            func.sum(case((ContentReminder.status == "sent", 1), else_=0)).label("sent"),
            func.sum(case((ContentReminder.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((ContentReminder.status == "cancelled", 1), else_=0)).label("cancelled"),
            func.sum(case((ContentReminder.retry_count > 0, 1), else_=0)).label("retried"),
        )
        .one()
    )

    total = int(summary.total or 0)
    pending = int(summary.pending or 0)
    processing = int(summary.processing or 0)
    sent = int(summary.sent or 0)
    failed = int(summary.failed or 0)
    cancelled = int(summary.cancelled or 0)
    retried = int(summary.retried or 0)

    completed_delivery_attempts = sent + failed
    delivery_rate = round((sent / completed_delivery_attempts) * 100, 1) if completed_delivery_attempts else 0.0

    top_content_rows = (
        db.session.query(
            ContentItem.id.label("content_item_id"),
            ContentItem.title.label("title"),
            ContentItem.category.label("category"),
            ContentItem.event_date.label("event_date"),
            func.count(ContentReminder.id).label("reminder_count"),
            func.sum(case((ContentReminder.status == "sent", 1), else_=0)).label("sent_count"),
            func.sum(case((ContentReminder.status == "pending", 1), else_=0)).label("pending_count"),
        )
        .join(ContentReminder, ContentReminder.content_item_id == ContentItem.id)
        .group_by(ContentItem.id, ContentItem.title, ContentItem.category, ContentItem.event_date)
        .order_by(func.count(ContentReminder.id).desc(), ContentItem.id.desc())
        .limit(top_limit)
        .all()
    )

    top_content = [
        {
            "content_item_id": row.content_item_id,
            "title": row.title,
            "category": normalize_category(row.category),
            "event_date": row.event_date,
            "reminder_count": int(row.reminder_count or 0),
            "sent_count": int(row.sent_count or 0),
            "pending_count": int(row.pending_count or 0),
        }
        for row in top_content_rows
    ]

    recent_rows = (
        db.session.query(ContentReminder, ContentItem)
        .join(ContentItem, ContentItem.id == ContentReminder.content_item_id)
        .order_by(ContentReminder.created_at.desc())
        .limit(10)
        .all()
    )

    recent_activity = [
        {
            "id": reminder.id,
            "content_item_id": item.id,
            "title": item.title,
            "category": normalize_category(item.category),
            "status": reminder.status,
            "minutes_before": reminder.reminder_minutes_before,
            "retry_count": reminder.retry_count or 0,
            "created_at": reminder.created_at,
            "scheduled_for": reminder.scheduled_for,
            "sent_at": reminder.sent_at,
        }
        for reminder, item in recent_rows
    ]

    return {
        "total": total,
        "pending": pending,
        "processing": processing,
        "sent": sent,
        "failed": failed,
        "cancelled": cancelled,
        "retried": retried,
        "delivery_rate": delivery_rate,
        "top_content": top_content,
        "recent_activity": recent_activity,
    }


def _get_analytics_date_range():
    range_key = request.args.get("range", "30d").strip().lower()
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
    start_date = today - timedelta(days=days - 1)
    return {
        "key": range_key,
        "days": days,
        "label": label,
        "start_date": start_date,
        "end_date": today,
    }


def _build_business_analytics(item, analytics_range):
    start_datetime = datetime.combine(analytics_range["start_date"], datetime.min.time())
    end_datetime = datetime.combine(analytics_range["end_date"] + timedelta(days=1), datetime.min.time())

    event_rows = (
        db.session.query(EngagementEvent.event_type, func.count(EngagementEvent.id))
        .filter(
            EngagementEvent.content_item_id == item.id,
            EngagementEvent.event_type.in_(BUSINESS_ANALYTICS_EVENTS),
            EngagementEvent.created_at >= start_datetime,
            EngagementEvent.created_at < end_datetime,
        )
        .group_by(EngagementEvent.event_type)
        .all()
    )
    counts = {event_type: count for event_type, count in event_rows}

    listing_views = counts.get("listing_view", 0)
    whatsapp_clicks = counts.get("whatsapp_click", 0)
    call_clicks = counts.get("call_click", 0)
    directions_clicks = counts.get("directions_click", 0)
    share_clicks = counts.get("share_click", 0)
    job_apply_clicks = counts.get("job_apply_click", 0)
    total_actions = whatsapp_clicks + call_clicks + directions_clicks + share_clicks + job_apply_clicks
    overall_action_rate = round((total_actions / listing_views) * 100, 1) if listing_views else 0.0

    daily_view_rows = (
        db.session.query(
            func.date(EngagementEvent.created_at).label("day"),
            func.count(EngagementEvent.id).label("views"),
        )
        .filter(
            EngagementEvent.content_item_id == item.id,
            EngagementEvent.event_type == "listing_view",
            EngagementEvent.created_at >= start_datetime,
            EngagementEvent.created_at < end_datetime,
        )
        .group_by(func.date(EngagementEvent.created_at))
        .order_by(func.date(EngagementEvent.created_at))
        .all()
    )
    daily_view_map = {str(day): count for day, count in daily_view_rows}
    daily_views = []
    current_day = analytics_range["start_date"]
    while current_day <= analytics_range["end_date"]:
        day_key = current_day.isoformat()
        daily_views.append({"date": day_key, "label": current_day.strftime("%d %b"), "views": daily_view_map.get(day_key, 0)})
        current_day += timedelta(days=1)

    best_day = max(daily_views, key=lambda row: row["views"]) if daily_views else {"date": None, "label": "—", "views": 0}
    if best_day["views"] == 0:
        best_day = {"date": None, "label": "—", "views": 0}

    action_breakdown = [
        {"event_type": "whatsapp_click", "label": "WhatsApp", "icon": "💬", "count": whatsapp_clicks},
        {"event_type": "call_click", "label": "Calls", "icon": "📞", "count": call_clicks},
        {"event_type": "directions_click", "label": "Directions", "icon": "🧭", "count": directions_clicks},
        {"event_type": "share_click", "label": "Shares", "icon": "↗", "count": share_clicks},
        {"event_type": "job_apply_click", "label": "Job Applies", "icon": "💼", "count": job_apply_clicks},
    ]

    access_point_rows = (
        db.session.query(
            EngagementEvent.access_point_id,
            AccessPoint.name,
            func.sum(case((EngagementEvent.event_type == "listing_view", 1), else_=0)).label("views"),
            func.sum(case((EngagementEvent.event_type.in_(ACTION_EVENTS), 1), else_=0)).label("actions"),
        )
        .join(AccessPoint, EngagementEvent.access_point_id == AccessPoint.id)
        .filter(
            EngagementEvent.content_item_id == item.id,
            EngagementEvent.created_at >= start_datetime,
            EngagementEvent.created_at < end_datetime,
            EngagementEvent.access_point_id.isnot(None),
        )
        .group_by(EngagementEvent.access_point_id, AccessPoint.name)
        .order_by(func.sum(case((EngagementEvent.event_type == "listing_view", 1), else_=0)).desc())
        .all()
    )

    access_points = []
    for access_point_id, access_point_name, views, actions in access_point_rows:
        views = int(views or 0)
        actions = int(actions or 0)
        point_action_rate = round((actions / views) * 100, 1) if views else 0.0
        access_points.append({
            "id": access_point_id,
            "name": access_point_name,
            "views": views,
            "actions": actions,
            "action_rate": point_action_rate,
        })

    return {
        "listing_views": listing_views,
        "whatsapp_clicks": whatsapp_clicks,
        "call_clicks": call_clicks,
        "directions_clicks": directions_clicks,
        "share_clicks": share_clicks,
        "job_apply_clicks": job_apply_clicks,
        "total_actions": total_actions,
        "action_rate": overall_action_rate,
        "daily_views": daily_views,
        "best_day": best_day,
        "access_points": access_points,
        "action_breakdown": action_breakdown,
    }


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

    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    tomorrow_start = today_start + timedelta(days=1)
    seven_days_ago = today_start - timedelta(days=6)
    fourteen_days_ago = today_start - timedelta(days=13)
    thirty_days_ago = today_start - timedelta(days=29)
    previous_7_start = seven_days_ago - timedelta(days=7)

    total_scans = QRScan.query.filter(QRScan.event_type == "scan").count()
    today_scans = QRScan.query.filter(QRScan.event_type == "scan", QRScan.scanned_at >= today_start, QRScan.scanned_at < tomorrow_start).count()
    seven_day_scans = QRScan.query.filter(QRScan.event_type == "scan", QRScan.scanned_at >= seven_days_ago).count()
    thirty_day_scans = QRScan.query.filter(QRScan.event_type == "scan", QRScan.scanned_at >= thirty_days_ago).count()
    previous_7_scans = QRScan.query.filter(QRScan.event_type == "scan", QRScan.scanned_at >= previous_7_start, QRScan.scanned_at < seven_days_ago).count()

    if previous_7_scans > 0:
        seven_day_growth = ((seven_day_scans - previous_7_scans) / previous_7_scans) * 100
    elif seven_day_scans > 0:
        seven_day_growth = 100.0
    else:
        seven_day_growth = 0.0

    total_access_points = AccessPoint.query.count()
    active_access_points = AccessPoint.query.filter(AccessPoint.active.is_(True)).count()
    scans_per_access_point = thirty_day_scans / active_access_points if active_access_points else 0

    total_category_views = QRScan.query.filter(QRScan.event_type == "category_view").count()
    category_results = (
        db.session.query(QRScan.category_selected, func.count(QRScan.id).label("view_count"))
        .filter(QRScan.event_type == "category_view", QRScan.category_selected.isnot(None))
        .group_by(QRScan.category_selected)
        .order_by(func.count(QRScan.id).desc())
        .all()
    )
    category_records = get_categories(active_only=False)
    category_map = {c.slug: c for c in category_records}
    category_activity = []
    for result in category_results:
        percentage = (result.view_count / total_category_views) * 100 if total_category_views else 0
        record = category_map.get(result.category_selected)
        category_activity.append({
            "category": result.category_selected,
            "name": record.name if record else result.category_selected,
            "icon": record.icon if record else "",
            "views": result.view_count,
            "percentage": round(percentage, 1),
        })

    top_locations_query = (
        db.session.query(
            AccessPoint.id,
            AccessPoint.code,
            AccessPoint.name,
            AccessPoint.location_type,
            Zone.name.label("zone_name"),
            func.count(QRScan.id).label("scan_count"),
        )
        .join(Zone, AccessPoint.zone_id == Zone.id)
        .outerjoin(QRScan, (QRScan.access_point_id == AccessPoint.id) & (QRScan.event_type == "scan"))
        .group_by(AccessPoint.id, AccessPoint.code, AccessPoint.name, AccessPoint.location_type, Zone.name)
        .order_by(func.count(QRScan.id).desc())
        .limit(10)
        .all()
    )
    top_locations = []
    for point in top_locations_query:
        share = (point.scan_count / total_scans) * 100 if total_scans else 0
        top_locations.append({
            "id": point.id,
            "code": point.code,
            "name": point.name,
            "location_type": point.location_type,
            "zone_name": point.zone_name,
            "scan_count": point.scan_count,
            "share": round(share, 1),
        })

    zone_query = (
        db.session.query(Zone.id, Zone.name, func.count(QRScan.id).label("scan_count"))
        .outerjoin(AccessPoint, AccessPoint.zone_id == Zone.id)
        .outerjoin(QRScan, (QRScan.access_point_id == AccessPoint.id) & (QRScan.event_type == "scan"))
        .group_by(Zone.id, Zone.name)
        .order_by(func.count(QRScan.id).desc())
        .all()
    )
    zone_activity = []
    for zone_row in zone_query:
        percentage = (zone_row.scan_count / total_scans) * 100 if total_scans else 0
        zone_activity.append({"name": zone_row.name, "scan_count": zone_row.scan_count, "percentage": round(percentage, 1)})

    recent_scan_counts = dict(
        db.session.query(QRScan.access_point_id, func.count(QRScan.id))
        .filter(QRScan.event_type == "scan", QRScan.scanned_at >= thirty_days_ago)
        .group_by(QRScan.access_point_id)
        .all()
    )
    low_performing_points = []
    for point in AccessPoint.query.filter(AccessPoint.active.is_(True)).all():
        scan_count = recent_scan_counts.get(point.id, 0)
        if scan_count <= 5:
            low_performing_points.append({"name": point.name, "code": point.code, "zone": point.zone.name, "scan_count": scan_count})
    low_performing_points.sort(key=lambda row: row["scan_count"])

    scan_rows = QRScan.query.filter(QRScan.event_type == "scan", QRScan.scanned_at >= fourteen_days_ago).all()
    daily_counts = {}
    for number in range(14):
        day = fourteen_days_ago.date() + timedelta(days=number)
        daily_counts[day] = 0
    for scan in scan_rows:
        scan_day = scan.scanned_at.date()
        if scan_day in daily_counts:
            daily_counts[scan_day] += 1
    max_daily_scans = max(daily_counts.values(), default=0)
    daily_scan_trend = []
    for scan_date, count in daily_counts.items():
        bar_percentage = (count / max_daily_scans) * 100 if max_daily_scans else 0
        daily_scan_trend.append({"date": scan_date, "label": scan_date.strftime("%d %b"), "count": count, "bar_percentage": round(bar_percentage, 1)})

    category_engagement_rate = (total_category_views / total_scans) * 100 if total_scans else 0
    recent_scans = QRScan.query.filter(QRScan.event_type == "scan").order_by(QRScan.scanned_at.desc()).limit(20).all()
    pending_submissions_count = PendingSubmission.query.filter_by(status="pending").count()

    total_listing_views = EngagementEvent.query.filter_by(event_type="listing_view").count()
    total_whatsapp_clicks = EngagementEvent.query.filter_by(event_type="whatsapp_click").count()
    total_call_clicks = EngagementEvent.query.filter_by(event_type="call_click").count()
    total_share_clicks = EngagementEvent.query.filter_by(event_type="share_click").count()
    total_directions_clicks = EngagementEvent.query.filter_by(event_type="directions_click").count()
    total_job_apply_clicks = EngagementEvent.query.filter_by(event_type="job_apply_click").count()
    total_useful_actions = total_whatsapp_clicks + total_call_clicks + total_share_clicks + total_directions_clicks + total_job_apply_clicks
    listing_action_rate = round((total_useful_actions / total_listing_views) * 100, 1) if total_listing_views else 0

    content_performance_rows = (
        db.session.query(
            EngagementEvent.content_item_id,
            func.sum(case((EngagementEvent.event_type == "listing_view", 1), else_=0)).label("listing_views"),
            func.sum(case((EngagementEvent.event_type == "whatsapp_click", 1), else_=0)).label("whatsapp_clicks"),
            func.sum(case((EngagementEvent.event_type == "call_click", 1), else_=0)).label("call_clicks"),
            func.sum(case((EngagementEvent.event_type == "directions_click", 1), else_=0)).label("directions_clicks"),
            func.sum(case((EngagementEvent.event_type == "share_click", 1), else_=0)).label("share_clicks"),
            func.sum(case((EngagementEvent.event_type == "job_apply_click", 1), else_=0)).label("job_apply_clicks"),
        )
        .filter(EngagementEvent.content_item_id.isnot(None))
        .group_by(EngagementEvent.content_item_id)
        .all()
    )
    content_performance = []
    for row in content_performance_rows:
        item = db.session.get(ContentItem, row.content_item_id)
        if item is None:
            continue
        views = int(row.listing_views or 0)
        whatsapp = int(row.whatsapp_clicks or 0)
        calls = int(row.call_clicks or 0)
        directions = int(row.directions_clicks or 0)
        shares = int(row.share_clicks or 0)
        job_applies = int(row.job_apply_clicks or 0)
        useful_actions = whatsapp + calls + directions + shares + job_applies
        actions_per_100_views = round((useful_actions / views) * 100, 1) if views else 0
        content_performance.append({
            "id": item.id,
            "title": item.title,
            "category": item.category,
            "zone_id": item.zone_id,
            "views": views,
            "whatsapp": whatsapp,
            "calls": calls,
            "directions": directions,
            "shares": shares,
            "job_applies": job_applies,
            "useful_actions": useful_actions,
            "actions_per_100_views": actions_per_100_views,
        })

    content_performance.sort(key=lambda row: (row["useful_actions"], row["views"]), reverse=True)
    top_content_performance = content_performance[:10]
    most_viewed_listings = sorted(content_performance, key=lambda row: row["views"], reverse=True)[:5]
    high_interest_low_action = [row for row in content_performance if row["views"] > 0 and row["useful_actions"] == 0]
    high_interest_low_action.sort(key=lambda row: row["views"], reverse=True)
    high_interest_low_action = high_interest_low_action[:5]

    engagement_by_access_point = {}
    engagement_access_rows = (
        db.session.query(EngagementEvent.access_point_id, EngagementEvent.event_type, func.count(EngagementEvent.id).label("event_count"))
        .filter(EngagementEvent.access_point_id.isnot(None))
        .group_by(EngagementEvent.access_point_id, EngagementEvent.event_type)
        .all()
    )
    for row in engagement_access_rows:
        engagement_by_access_point.setdefault(row.access_point_id, {})
        engagement_by_access_point[row.access_point_id][row.event_type] = row.event_count

    access_point_performance = []
    for point in top_locations:
        events = engagement_by_access_point.get(point["id"], {})
        listing_views = int(events.get("listing_view", 0))
        useful_actions = sum(int(events.get(event_type, 0)) for event_type in ACTION_EVENTS)
        actions_per_100_views = round((useful_actions / listing_views) * 100, 1) if listing_views else 0
        access_point_performance.append({**point, "listing_views": listing_views, "useful_actions": useful_actions, "actions_per_100_views": actions_per_100_views})

    zone_engagement_rows = (
        db.session.query(EngagementEvent.zone_id, EngagementEvent.event_type, func.count(EngagementEvent.id).label("event_count"))
        .filter(EngagementEvent.zone_id.isnot(None))
        .group_by(EngagementEvent.zone_id, EngagementEvent.event_type)
        .all()
    )
    zone_engagement_map = {}
    for row in zone_engagement_rows:
        zone_engagement_map.setdefault(row.zone_id, {})
        zone_engagement_map[row.zone_id][row.event_type] = row.event_count
    zone_scan_map = {zone_row.name: zone_row.scan_count for zone_row in zone_query}
    zone_performance = []
    for zone_record in Zone.query.order_by(Zone.name.asc()).all():
        events = zone_engagement_map.get(zone_record.id, {})
        listing_views = int(events.get("listing_view", 0))
        useful_actions = sum(int(events.get(event_type, 0)) for event_type in ACTION_EVENTS)
        scan_count = int(zone_scan_map.get(zone_record.name, 0))
        percentage = scan_count / total_scans * 100 if total_scans else 0
        zone_performance.append({"id": zone_record.id, "name": zone_record.name, "scan_count": scan_count, "listing_views": listing_views, "useful_actions": useful_actions, "percentage": round(percentage, 1)})
    zone_performance.sort(key=lambda row: row["scan_count"], reverse=True)

    category_engagement_rows = (
        db.session.query(EngagementEvent.category, EngagementEvent.event_type, func.count(EngagementEvent.id).label("event_count"))
        .filter(EngagementEvent.category.isnot(None))
        .group_by(EngagementEvent.category, EngagementEvent.event_type)
        .all()
    )
    category_engagement_map = {}
    for row in category_engagement_rows:
        category_engagement_map.setdefault(row.category, {})
        category_engagement_map[row.category][row.event_type] = row.event_count
    category_view_map = {item["category"]: item["views"] for item in category_activity}
    category_performance = []
    all_category_slugs = set(category_view_map) | set(category_engagement_map)
    for slug in all_category_slugs:
        events = category_engagement_map.get(slug, {})
        category_record = category_map.get(slug)
        whatsapp = int(events.get("whatsapp_click", 0))
        calls = int(events.get("call_click", 0))
        directions = int(events.get("directions_click", 0))
        shares = int(events.get("share_click", 0))
        job_applies = int(events.get("job_apply_click", 0))
        useful_actions = whatsapp + calls + directions + shares + job_applies
        category_performance.append({
            "slug": slug,
            "name": category_record.name if category_record else slug.replace("-", " ").title(),
            "icon": category_record.icon if category_record else "",
            "category_views": int(category_view_map.get(slug, 0)),
            "listing_views": int(events.get("listing_view", 0)),
            "whatsapp": whatsapp,
            "calls": calls,
            "directions": directions,
            "shares": shares,
            "job_applies": job_applies,
            "useful_actions": useful_actions,
        })
    category_performance.sort(key=lambda row: (row["listing_views"], row["useful_actions"]), reverse=True)

    engagement_14_day_rows = EngagementEvent.query.filter(EngagementEvent.created_at >= fourteen_days_ago).all()
    daily_engagement_counts = {}
    for number in range(14):
        day = fourteen_days_ago.date() + timedelta(days=number)
        daily_engagement_counts[day] = {"listing_views": 0, "useful_actions": 0}
    for event in engagement_14_day_rows:
        event_day = event.created_at.date()
        if event_day not in daily_engagement_counts:
            continue
        if event.event_type == "listing_view":
            daily_engagement_counts[event_day]["listing_views"] += 1
        elif event.event_type in ACTION_EVENTS:
            daily_engagement_counts[event_day]["useful_actions"] += 1

    daily_activity = []
    for scan_day in daily_scan_trend:
        day = scan_day["date"]
        engagement = daily_engagement_counts.get(day, {})
        daily_activity.append({
            "date": day,
            "label": scan_day["label"],
            "scans": scan_day["count"],
            "listing_views": engagement.get("listing_views", 0),
            "useful_actions": engagement.get("useful_actions", 0),
        })

    reminder_analytics = get_reminder_analytics(top_limit=10)

    return render_template(
        "admin/analytics.html",
        total_scans=total_scans,
        today_scans=today_scans,
        seven_day_scans=seven_day_scans,
        thirty_day_scans=thirty_day_scans,
        seven_day_growth=round(seven_day_growth, 1),
        total_access_points=total_access_points,
        active_access_points=active_access_points,
        scans_per_access_point=round(scans_per_access_point, 1),
        total_category_views=total_category_views,
        category_engagement_rate=round(category_engagement_rate, 1),
        category_activity=category_activity,
        top_locations=top_locations,
        zone_activity=zone_activity,
        low_performing_points=low_performing_points,
        daily_scan_trend=daily_scan_trend,
        recent_scans=recent_scans,
        pending_submissions_count=pending_submissions_count,
        reminder_analytics=reminder_analytics,
        total_listing_views=total_listing_views,
        total_whatsapp_clicks=total_whatsapp_clicks,
        total_call_clicks=total_call_clicks,
        total_share_clicks=total_share_clicks,
        total_directions_clicks=total_directions_clicks,
        total_job_apply_clicks=total_job_apply_clicks,
        total_useful_actions=total_useful_actions,
        listing_action_rate=listing_action_rate,
        top_content_performance=top_content_performance,
        most_viewed_listings=most_viewed_listings,
        high_interest_low_action=high_interest_low_action,
        access_point_performance=access_point_performance,
        zone_performance=zone_performance,
        category_performance=category_performance,
        daily_activity=daily_activity,
    )


@admin_bp.route("/business-analytics")
def business_analytics():
    auth = require_admin()
    if auth:
        return auth
    analytics_range = _get_analytics_date_range()
    start_datetime = datetime.combine(analytics_range["start_date"], datetime.min.time())
    end_datetime = datetime.combine(analytics_range["end_date"] + timedelta(days=1), datetime.min.time())

    listings = ContentItem.query.filter(ContentItem.listing_level.in_({"business", "promotion"})).order_by(ContentItem.created_at.desc()).all()
    listing_ids = [item.id for item in listings]
    analytics_by_listing = {}
    if listing_ids:
        rows = (
            db.session.query(EngagementEvent.content_item_id, EngagementEvent.event_type, func.count(EngagementEvent.id).label("event_count"))
            .filter(
                EngagementEvent.content_item_id.in_(listing_ids),
                EngagementEvent.event_type.in_(BUSINESS_ANALYTICS_EVENTS),
                EngagementEvent.created_at >= start_datetime,
                EngagementEvent.created_at < end_datetime,
            )
            .group_by(EngagementEvent.content_item_id, EngagementEvent.event_type)
            .all()
        )
        for content_item_id, event_type, event_count in rows:
            analytics_by_listing.setdefault(content_item_id, {})
            analytics_by_listing[content_item_id][event_type] = event_count

    listing_reports = []
    for item in listings:
        counts = analytics_by_listing.get(item.id, {})
        views = counts.get("listing_view", 0)
        whatsapp = counts.get("whatsapp_click", 0)
        calls = counts.get("call_click", 0)
        directions = counts.get("directions_click", 0)
        shares = counts.get("share_click", 0)
        job_applies = counts.get("job_apply_click", 0)
        actions = whatsapp + calls + directions + shares + job_applies
        action_rate = round((actions / views) * 100, 1) if views else 0.0
        listing_reports.append({
            "item": item,
            "views": views,
            "actions": actions,
            "whatsapp": whatsapp,
            "calls": calls,
            "directions": directions,
            "shares": shares,
            "job_applies": job_applies,
            "action_rate": action_rate,
        })

    listing_reports.sort(key=lambda row: (row["views"], row["actions"]), reverse=True)
    total_views = sum(row["views"] for row in listing_reports)
    total_actions = sum(row["actions"] for row in listing_reports)
    totals = {
        "views": total_views,
        "actions": total_actions,
        "whatsapp": sum(row["whatsapp"] for row in listing_reports),
        "calls": sum(row["calls"] for row in listing_reports),
        "directions": sum(row["directions"] for row in listing_reports),
        "shares": sum(row["shares"] for row in listing_reports),
        "job_applies": sum(row["job_applies"] for row in listing_reports),
        "action_rate": round((total_actions / total_views) * 100, 1) if total_views else 0.0,
    }
    return render_template("admin/business_analytics.html", analytics_range=analytics_range, listing_reports=listing_reports, totals=totals)


@admin_bp.route("/business-analytics/<int:item_id>")
def business_analytics_detail(item_id):
    auth = require_admin()
    if auth:
        return auth
    item = ContentItem.query.get_or_404(item_id)
    if item.listing_level not in {"business", "promotion"}:
        flash("Business analytics are available for Business and Promotion listings.", "error")
        return redirect(url_for("admin.business_analytics"))
    analytics_range = _get_analytics_date_range()
    analytics = _build_business_analytics(item, analytics_range)
    return render_template("admin/business_analytics_detail.html", item=item, analytics=analytics, analytics_range=analytics_range)


@admin_bp.route("/notifications")
def notifications():
    auth = require_admin()
    if auth:
        return auth

    selected_zone_id = request.args.get("zone_id", type=int)
    selected_status = request.args.get("status", "").strip()
    query = PushNotification.query.order_by(PushNotification.created_at.desc())
    if selected_zone_id:
        query = query.filter(PushNotification.zone_id == selected_zone_id)
    if selected_status:
        query = query.filter(PushNotification.status == selected_status)

    notification_items = query.limit(200).all()
    zones = Zone.query.order_by(Zone.name.asc()).all()
    zone_lookup = {zone.id: zone for zone in zones}
    active_subscribers = PushSubscriber.query.filter_by(active=True).count()
    total_notifications = PushNotification.query.count()
    successful_notifications = PushNotification.query.filter(PushNotification.status == "sent").count()
    problem_notifications = PushNotification.query.filter(PushNotification.status.in_(["failed", "partial_failure"])).count()
    pending_notifications = PushNotification.query.filter(PushNotification.status == "pending").count()
    sent_deliveries = db.session.query(func.coalesce(func.sum(PushNotification.sent_count), 0)).scalar()
    failed_deliveries = db.session.query(func.coalesce(func.sum(PushNotification.failed_count), 0)).scalar()

    return render_template(
        "admin/notifications.html",
        notifications=notification_items,
        zones=zones,
        zone_lookup=zone_lookup,
        selected_zone_id=selected_zone_id,
        selected_status=selected_status,
        active_subscribers=active_subscribers,
        total_notifications=total_notifications,
        successful_notifications=successful_notifications,
        problem_notifications=problem_notifications,
        pending_notifications=pending_notifications,
        sent_deliveries=sent_deliveries,
        failed_deliveries=failed_deliveries,
    )


@admin_bp.route("/notifications/<int:notification_id>/retry", methods=["POST"])
def retry_notification(notification_id):
    auth = require_admin()
    if auth:
        return auth

    notification = PushNotification.query.get_or_404(notification_id)
    if notification.status not in {"failed", "partial_failure", "pending", "no_subscribers"}:
        flash("This notification does not need to be retried.", "info")
        return redirect(url_for("admin.notifications"))
    if not notification.content_item_id:
        flash("Unable to retry this notification because it is not linked to content.", "error")
        return redirect(url_for("admin.notifications"))

    content = db.session.get(ContentItem, notification.content_item_id)
    if content is None:
        flash("Unable to retry this notification because the original content no longer exists.", "error")
        return redirect(url_for("admin.notifications"))
    notification_category = content.category
    if not notification_category:
        flash("Unable to retry this notification because the original content has no category.", "error")
        return redirect(url_for("admin.notifications"))

    try:
        notification.attempts = (notification.attempts or 0) + 1
        notification.status = "pending"
        notification.last_error = None
        db.session.commit()

        result = send_zone_push_notification(
            zone_id=notification.zone_id,
            category=notification_category,
            title=notification.title,
            body=notification.body,
            url=notification.target_url,
            tag=f"notification-{notification.id}",
        )
        if not isinstance(result, dict):
            result = {}
        total = int(result.get("total", 0) or 0)
        sent = int(result.get("sent", 0) or 0)
        failed = int(result.get("failed", 0) or 0)
        notification.total_subscribers = total
        notification.sent_count = sent
        notification.failed_count = failed

        if sent > 0 and failed == 0:
            notification.status = "sent"
            notification.sent_at = datetime.utcnow()
            notification.last_error = None
        elif sent > 0 and failed > 0:
            notification.status = "partial_failure"
            notification.sent_at = datetime.utcnow()
            notification.last_error = f"{failed} subscriber delivery failures."
        elif total == 0:
            notification.status = "no_subscribers"
            notification.sent_at = None
            notification.last_error = f"No active subscribers in this zone selected the '{notification_category}' notification category."
        else:
            notification.status = "failed"
            notification.sent_at = None
            notification.last_error = "Push delivery failed for all matching subscribers."
        db.session.commit()

        if total == 0:
            flash(f"Notification retry completed, but no active subscribers in this zone selected '{notification_category}'.", "info")
        else:
            flash(f"Notification retry completed. Category: {notification_category}. Matching subscribers: {total}. Sent: {sent}. Failed: {failed}.", "success")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("[LaC Push] Retry failed notification_id=%s error=%s", notification.id, exc)
        flash("Unable to retry notification.", "error")

    return redirect(url_for("admin.notifications"))


@admin_bp.route("/zones")
def zones():
    auth = require_admin()
    if auth:
        return auth
    return render_template("admin/zones.html", zones=Zone.query.order_by(Zone.name.asc()).all())


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
        duplicate = Zone.query.filter(Zone.id != zone.id).filter((Zone.slug == slug) | (Zone.name == name)).first()
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


@admin_bp.route("/zones/<int:zone_id>/delete", methods=["POST"])
def delete_zone(zone_id):
    auth = require_admin()
    if auth:
        return auth
    zone = Zone.query.get_or_404(zone_id)
    zone_name = zone.name
    try:
        for point in AccessPoint.query.filter_by(zone_id=zone.id).all():
            QRScan.query.filter_by(access_point_id=point.id).delete(synchronize_session=False)
            db.session.delete(point)
        for item in ContentItem.query.filter_by(zone_id=zone.id).all():
            for submission in PendingSubmission.query.filter_by(published_content_id=item.id).all():
                submission.published_content_id = None
            ContentImage.query.filter_by(content_item_id=item.id).delete(synchronize_session=False)
            ContentDistributionZone.query.filter_by(content_item_id=item.id).delete(synchronize_session=False)
            db.session.delete(item)
        for submission in PendingSubmission.query.filter_by(zone_id=zone.id).all():
            PendingSubmissionImage.query.filter_by(submission_id=submission.id).delete(synchronize_session=False)
            db.session.delete(submission)
        ZoneCategoryAppearance.query.filter_by(zone_id=zone.id).delete(synchronize_session=False)
        db.session.delete(zone)
        db.session.commit()
        flash(f"{zone_name} permanently deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unable to delete zone zone_id=%s error=%s", zone_id, exc)
        flash(f"Unable to delete zone: {exc}", "error")
    return redirect(url_for("admin.zones"))


@admin_bp.route("/categories")
def categories():
    auth = require_admin()
    if auth:
        return auth
    return render_template("admin/categories.html", categories=get_categories(active_only=False))


@admin_bp.route("/categories/new", methods=["GET", "POST"])
def create_category():
    auth = require_admin()
    if auth:
        return auth
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = clean_slug(request.form.get("slug", ""))
        icon = request.form.get("icon", "").strip() or None
        display_order = request.form.get("display_order", type=int)
        display_order = 0 if display_order is None else display_order
        active = request.form.get("active") == "on"
        if not name or not slug:
            flash("Name and slug are required.", "error")
            return render_template("admin/category_form.html", category=None)
        if Category.query.filter_by(slug=slug).first():
            flash("That category slug already exists.", "error")
            return render_template("admin/category_form.html", category=None)

        image_url = image_url_2 = image_url_3 = None
        try:
            image_file = request.files.get("category_image")
            image_file_2 = request.files.get("category_image_2")
            image_file_3 = request.files.get("category_image_3")
            if image_file and image_file.filename:
                image_url = upload_lac_image(image_file, folder="lac/categories")
            if image_file_2 and image_file_2.filename:
                image_url_2 = upload_lac_image(image_file_2, folder="lac/categories")
            if image_file_3 and image_file_3.filename:
                image_url_3 = upload_lac_image(image_file_3, folder="lac/categories")
        except ValueError as error:
            flash(str(error), "error")
            return render_template("admin/category_form.html", category=None)

        category = Category(name=name, slug=slug, icon=icon, image_url=image_url, image_url_2=image_url_2, image_url_3=image_url_3, display_order=display_order, active=active)
        db.session.add(category)
        db.session.commit()
        flash("Category created successfully.", "success")
        return redirect(url_for("admin.categories"))
    return render_template("admin/category_form.html", category=None)


@admin_bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
def edit_category(category_id):
    auth = require_admin()
    if auth:
        return auth
    category = Category.query.get_or_404(category_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        new_slug = clean_slug(request.form.get("slug", ""))
        icon = request.form.get("icon", "").strip() or None
        display_order = request.form.get("display_order", type=int)
        display_order = 0 if display_order is None else display_order
        active = request.form.get("active") == "on"
        if not name or not new_slug:
            flash("Name and slug are required.", "error")
            return render_template("admin/category_form.html", category=category)
        duplicate = Category.query.filter(Category.slug == new_slug, Category.id != category.id).first()
        if duplicate:
            flash("Another category already uses that slug.", "error")
            return render_template("admin/category_form.html", category=category)
        try:
            image_file = request.files.get("category_image")
            image_file_2 = request.files.get("category_image_2")
            image_file_3 = request.files.get("category_image_3")
            if image_file and image_file.filename:
                category.image_url = upload_lac_image(image_file, folder="lac/categories")
            if image_file_2 and image_file_2.filename:
                category.image_url_2 = upload_lac_image(image_file_2, folder="lac/categories")
            if image_file_3 and image_file_3.filename:
                category.image_url_3 = upload_lac_image(image_file_3, folder="lac/categories")
        except ValueError as error:
            flash(str(error), "error")
            return render_template("admin/category_form.html", category=category)

        old_slug = category.slug
        try:
            if new_slug != old_slug:
                ContentItem.query.filter(ContentItem.category == old_slug).update({ContentItem.category: new_slug}, synchronize_session=False)
                AccessPoint.query.filter(AccessPoint.default_category == old_slug).update({AccessPoint.default_category: new_slug}, synchronize_session=False)
                PendingSubmission.query.filter(PendingSubmission.category == old_slug).update({PendingSubmission.category: new_slug}, synchronize_session=False)
                QRScan.query.filter(QRScan.category_selected == old_slug).update({QRScan.category_selected: new_slug}, synchronize_session=False)
                EngagementEvent.query.filter(EngagementEvent.category == old_slug).update({EngagementEvent.category: new_slug}, synchronize_session=False)
            category.name = name
            category.slug = new_slug
            category.icon = icon
            category.display_order = display_order
            category.active = active
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Unable to edit category category_id=%s error=%s", category.id, exc)
            flash("Category could not be updated.", "error")
            return render_template("admin/category_form.html", category=category)
        flash("Category updated successfully.", "success")
        return redirect(url_for("admin.categories"))
    return render_template("admin/category_form.html", category=category)





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


@admin_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
def delete_category(category_id):
    auth = require_admin()
    if auth:
        return auth
    category = Category.query.get_or_404(category_id)
    category_name = category.name
    category_slug = category.slug
    try:
        for item in ContentItem.query.filter_by(category=category_slug).all():
            for submission in PendingSubmission.query.filter_by(published_content_id=item.id).all():
                submission.published_content_id = None
            ContentImage.query.filter_by(content_item_id=item.id).delete(synchronize_session=False)
            ContentDistributionZone.query.filter_by(content_item_id=item.id).delete(synchronize_session=False)
            db.session.delete(item)
        for submission in PendingSubmission.query.filter_by(category=category_slug).all():
            PendingSubmissionImage.query.filter_by(submission_id=submission.id).delete(synchronize_session=False)
            db.session.delete(submission)
        for point in AccessPoint.query.filter_by(default_category=category_slug).all():
            point.qr_type = "general"
            point.default_category = None
        ZoneCategoryAppearance.query.filter_by(category_id=category.id).delete(synchronize_session=False)
        db.session.delete(category)
        db.session.commit()
        flash(f"{category_name} permanently deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unable to delete category category_id=%s error=%s", category_id, exc)
        flash(f"Unable to delete category: {exc}", "error")
    return redirect(url_for("admin.categories"))


@admin_bp.route("/zone-category-appearance", methods=["GET", "POST"])
def zone_category_appearance():
    auth = require_admin()
    if auth:
        return auth
    zones = Zone.query.order_by(Zone.name.asc()).all()
    categories = Category.query.order_by(Category.display_order.asc(), Category.name.asc()).all()
    if request.method == "POST":
        zone_id = request.form.get("zone_id", type=int)
        category_id = request.form.get("category_id", type=int)
        if not zone_id or not category_id:
            flash("Please select both a zone and a category.", "error")
            return render_template("admin/zone_category_appearance.html", zones=zones, categories=categories, appearance=None, selected_zone_id=zone_id, selected_category_id=category_id)
        zone = db.session.get(Zone, zone_id)
        category = db.session.get(Category, category_id)
        if not zone or not category:
            flash("The selected zone or category could not be found.", "error")
            return redirect(url_for("admin.zone_category_appearance"))
        appearance = ZoneCategoryAppearance.query.filter_by(zone_id=zone.id, category_id=category.id).first()
        if appearance is None:
            appearance = ZoneCategoryAppearance(zone_id=zone.id, category_id=category.id)
            db.session.add(appearance)
        folder = f"lac/zone-categories/{zone.id}/{category.slug}"
        try:
            image_file = request.files.get("zone_category_image")
            image_file_2 = request.files.get("zone_category_image_2")
            image_file_3 = request.files.get("zone_category_image_3")
            if image_file and image_file.filename:
                appearance.image_url = upload_lac_image(image_file, folder=folder)
            if image_file_2 and image_file_2.filename:
                appearance.image_url_2 = upload_lac_image(image_file_2, folder=folder)
            if image_file_3 and image_file_3.filename:
                appearance.image_url_3 = upload_lac_image(image_file_3, folder=folder)
            db.session.commit()
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return render_template("admin/zone_category_appearance.html", zones=zones, categories=categories, appearance=appearance, selected_zone_id=zone.id, selected_category_id=category.id)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Failed to save zone category appearance error=%s", exc)
            flash("The zone category appearance could not be saved.", "error")
            return render_template("admin/zone_category_appearance.html", zones=zones, categories=categories, appearance=appearance, selected_zone_id=zone.id, selected_category_id=category.id)
        flash(f"{category.name} appearance for {zone.name} saved successfully.", "success")
        return redirect(url_for("admin.zone_category_appearance", zone_id=zone.id, category_id=category.id))

    selected_zone_id = request.args.get("zone_id", type=int)
    selected_category_id = request.args.get("category_id", type=int)
    appearance = None
    if selected_zone_id and selected_category_id:
        appearance = ZoneCategoryAppearance.query.filter_by(zone_id=selected_zone_id, category_id=selected_category_id).first()
    return render_template("admin/zone_category_appearance.html", zones=zones, categories=categories, appearance=appearance, selected_zone_id=selected_zone_id, selected_category_id=selected_category_id)


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
        access_point = AccessPoint(code=code, name=name, zone_id=zone_id, location_type=location_type, qr_type=qr_type, default_category=default_category, partner_name=partner_name, active=True)
        db.session.add(access_point)
        db.session.commit()
        base_url = os.environ.get("LAC_BASE_URL", request.host_url.rstrip("/"))
        result = generate_access_qr(code=code, base_url=base_url)
        flash(f"QR access point {code} created.", "success")
        return render_template("admin/qr_created.html", access_point=access_point, qr_filename=result["filename"], qr_url=result["url"])
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
    return render_template("admin/access_points.html", points=points, zones=zones, selected_zone=zone_id, selected_status=status)


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


@admin_bp.route("/access-points/<int:point_id>/delete", methods=["POST"])
def delete_access_point(point_id):
    auth = require_admin()
    if auth:
        return auth
    point = AccessPoint.query.get_or_404(point_id)
    point_name = point.name
    try:
        QRScan.query.filter_by(access_point_id=point.id).delete(synchronize_session=False)
        db.session.delete(point)
        db.session.commit()
        flash(f"{point_name} permanently deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unable to delete access point point_id=%s error=%s", point_id, exc)
        flash(f"Unable to delete access point: {exc}", "error")
    return redirect(url_for("admin.access_points"))


@admin_bp.route("/access-points/<int:access_point_id>/qr")
def access_point_qr(access_point_id):
    auth = require_admin()
    if auth:
        return auth
    access_point = AccessPoint.query.get_or_404(access_point_id)
    return send_file(create_access_point_qr(access_point), mimetype="image/png")


@admin_bp.route("/access-points/<int:access_point_id>/qr/download")
def download_access_point_qr(access_point_id):
    auth = require_admin()
    if auth:
        return auth
    access_point = AccessPoint.query.get_or_404(access_point_id)
    return send_file(create_access_point_qr(access_point), mimetype="image/png", as_attachment=True, download_name=f"LaC-{access_point.code}.png")


@admin_bp.route("/claims")
def claims():
    auth = require_admin()
    if auth:
        return auth
    status_filter = request.args.get("status", "pending").strip().lower()
    if status_filter not in {"pending", "approved", "rejected", "all"}:
        status_filter = "pending"
    query = ListingClaim.query
    if status_filter != "all":
        query = query.filter(ListingClaim.status == status_filter)
    claim_items = query.order_by(ListingClaim.created_at.desc()).all()
    return render_template(
        "admin/claims.html",
        claims=claim_items,
        status_filter=status_filter,
        pending_count=ListingClaim.query.filter(ListingClaim.status == "pending").count(),
        approved_count=ListingClaim.query.filter(ListingClaim.status == "approved").count(),
        rejected_count=ListingClaim.query.filter(ListingClaim.status == "rejected").count(),
        total_count=ListingClaim.query.count(),
    )


@admin_bp.route("/claims/<int:claim_id>")
def claim_detail(claim_id):
    auth = require_admin()
    if auth:
        return auth
    claim = ListingClaim.query.filter_by(id=claim_id).first_or_404()
    return render_template("admin/claim_detail.html", claim=claim, item=claim.content_item)


@admin_bp.route("/claims/<int:claim_id>/approve", methods=["POST"])
def approve_claim(claim_id):
    auth = require_admin()
    if auth:
        return auth
    claim = ListingClaim.query.filter_by(id=claim_id).first_or_404()
    item = claim.content_item
    if claim.status != "pending":
        flash("This claim has already been reviewed.", "warning")
        return redirect(url_for("admin.claim_detail", claim_id=claim.id))
    if not item.can_be_claimed():
        flash("This listing can no longer be claimed. Its ownership or listing level has changed.", "warning")
        return redirect(url_for("admin.claim_detail", claim_id=claim.id))
    claim.status = "approved"
    claim.reviewed_at = datetime.utcnow()
    claim.admin_notes = request.form.get("admin_notes", "").strip() or None
    item.listing_level = "business"
    item.ownership_status = "claimed"
    item.is_verified = True
    item.featured = False
    item.notification_eligible = False
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to approve listing claim claim_id=%s error=%s", claim.id, exc)
        flash("The claim could not be approved.", "error")
        return redirect(url_for("admin.claim_detail", claim_id=claim.id))
    flash("Claim approved. The listing is now business-controlled.", "success")
    return redirect(url_for("admin.claim_detail", claim_id=claim.id))


@admin_bp.route("/claims/<int:claim_id>/reject", methods=["POST"])
def reject_claim(claim_id):
    auth = require_admin()
    if auth:
        return auth
    claim = ListingClaim.query.filter_by(id=claim_id).first_or_404()
    if claim.status != "pending":
        flash("This claim has already been reviewed.", "warning")
        return redirect(url_for("admin.claim_detail", claim_id=claim.id))
    claim.status = "rejected"
    claim.reviewed_at = datetime.utcnow()
    claim.admin_notes = request.form.get("admin_notes", "").strip() or None
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to reject listing claim claim_id=%s error=%s", claim.id, exc)
        flash("The claim could not be rejected.", "error")
        return redirect(url_for("admin.claim_detail", claim_id=claim.id))
    flash("Claim rejected.", "success")
    return redirect(url_for("admin.claim_detail", claim_id=claim.id))


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
        canonical_category = normalize_category(category)
        aliases = set(get_category_aliases(canonical_category) or [])
        aliases.update({category, canonical_category})
        aliases.discard(None)
        aliases.discard("")
        query = query.filter(ContentItem.category.in_(aliases))

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
        status_counts[result["key"]] = status_counts.get(result["key"], 0) + 1

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
    items = ContentItem.query.filter(ContentItem.archived.is_(True)).order_by(ContentItem.archived_at.desc()).all()
    return render_template("admin/content_archive.html", items=items)


@admin_bp.route("/content/<int:item_id>/restore", methods=["POST"])
def restore_content(item_id):
    auth = require_admin()
    if auth:
        return auth
    item = ContentItem.query.get_or_404(item_id)
    item.archived = False
    item.archived_at = None
    item.active = True
    db.session.commit()
    flash("Content restored from archive.", "success")
    return redirect(url_for("admin.content_archive"))


def _get_listing_level_from_form(default="discovery"):
    listing_level = request.form.get("listing_level", default).strip().lower()
    return listing_level if listing_level in ALLOWED_LISTING_LEVELS else "discovery"


def _read_uploaded_listing_images():
    uploaded_images = [f for f in request.files.getlist("images") if f and f.filename]
    legacy_image = request.files.get("image")
    if legacy_image and legacy_image.filename and not uploaded_images:
        uploaded_images.append(legacy_image)
    return uploaded_images


def _validate_image_count(uploaded_images, listing_level):
    maximum_images = 1 if listing_level == "discovery" else 3
    if len(uploaded_images) <= maximum_images:
        return None
    if listing_level == "discovery":
        return "Discovery listings can have a maximum of 1 image."
    return "Business and Promotion listings can have a maximum of 3 images."


def _upload_listing_images(uploaded_images):
    urls = []
    for uploaded_file in uploaded_images:
        image_url = upload_listing_image(uploaded_file)
        if image_url:
            urls.append(image_url)
    return urls


def _parse_sponsorship(item=None):
    is_sponsored = request.form.get("is_sponsored") == "on"
    if not is_sponsored:
        return {
            "is_sponsored": False,
            "sponsorship_status": "inactive",
            "sponsored_duration_days": None,
            "sponsorship_amount_due": None,
            "sponsored_starts_at": None,
            "sponsored_expires_at": None,
            "sponsored_priority": 0,
            "sponsorship_reference": None,
        }, None

    sponsorship_status = request.form.get("sponsorship_status", getattr(item, "sponsorship_status", None) or "inactive").strip().lower()
    if sponsorship_status not in ALLOWED_SPONSORSHIP_STATUSES:
        sponsorship_status = "inactive"

    raw_duration = request.form.get("sponsored_duration_days", "").strip()
    if not raw_duration:
        return None, "Please select a Sponsored Boost duration."
    try:
        sponsored_duration_days = int(raw_duration)
        sponsorship_amount_due = calculate_sponsored_price(sponsored_duration_days)
    except (TypeError, ValueError, KalxaPricingError):
        return None, "Invalid Sponsored Boost duration selected."

    try:
        sponsored_priority = int(request.form.get("sponsored_priority", getattr(item, "sponsored_priority", None) or 10) or 10)
    except (TypeError, ValueError):
        sponsored_priority = 10
    sponsored_priority = max(0, min(sponsored_priority, 100))

    sponsored_starts_raw = request.form.get("sponsored_starts_at", "").strip()
    if not sponsored_starts_raw:
        return None, "Please select when the Sponsored Boost should start."
    try:
        sponsored_starts_at = datetime.fromisoformat(sponsored_starts_raw)
    except ValueError:
        return None, "Please enter a valid Sponsored start date."

    return {
        "is_sponsored": True,
        "sponsorship_status": sponsorship_status,
        "sponsored_duration_days": sponsored_duration_days,
        "sponsorship_amount_due": sponsorship_amount_due,
        "sponsored_starts_at": sponsored_starts_at,
        "sponsored_expires_at": sponsored_starts_at + timedelta(days=sponsored_duration_days),
        "sponsored_priority": sponsored_priority,
        "sponsorship_reference": request.form.get("sponsorship_reference", "").strip() or None,
    }, None


def _apply_sponsorship(item, sponsorship):
    for field, value in sponsorship.items():
        setattr(item, field, value)


def _validate_effective_campaign_dates(canonical_category, lifetime_type, dates, start_time, end_time):
    if canonical_category == "events":
        effective_start_date = dates.get("event_date")
        effective_end_date = dates.get("event_end_date") or effective_start_date
    else:
        effective_start_date = dates.get("start_date")
        effective_end_date = dates.get("end_date")

    if effective_start_date and effective_end_date and effective_end_date < effective_start_date:
        return "End date cannot be before start date."
    if effective_start_date and effective_end_date and effective_start_date == effective_end_date and start_time and end_time and end_time <= start_time:
        return "End time must be after start time when the content starts and ends on the same day."
    if canonical_category != "events" and end_time and not effective_end_date:
        return "Please enter an end date when using an end time."
    return None


def _populate_content_common_fields(item, listing_level, workflow_notification_eligible):
    item.description = request.form.get("description", "").strip() or None
    item.business_name = request.form.get("business_name", "").strip() or None
    item.venue = request.form.get("venue", "").strip() or None
    item.price = request.form.get("price", "").strip() or None
    item.contact = request.form.get("contact", "").strip() or None
    item.whatsapp_number = request.form.get("whatsapp_number", "").strip() or None
    item.directions_url = request.form.get("directions_url", "").strip() or None
    item.ticket_url = request.form.get("ticket_url", "").strip() or None
    item.listing_level = listing_level

    if listing_level == "discovery":
        item.ownership_status = "unclaimed"
        item.is_verified = False
    else:
        item.ownership_status = "claimed"
        item.is_verified = request.form.get("is_verified") == "on"

    if listing_level in {"business", "promotion"}:
        item.opening_hours = request.form.get("opening_hours", "").strip() or None
        item.menu_highlights = request.form.get("menu_highlights", "").strip() or None
        item.special_offer = request.form.get("special_offer", "").strip() or None
    else:
        item.opening_hours = None
        item.menu_highlights = None
        item.special_offer = None
        item.image_url_2 = None
        item.image_url_3 = None

    item.featured = request.form.get("featured") == "on"
    if listing_level == "promotion":
        item.notification_eligible = workflow_notification_eligible and request.form.get("notification_eligible") == "on"
    else:
        item.notification_eligible = False
    item.active = request.form.get("active") == "on"


@admin_bp.route("/content/new", methods=["GET", "POST"])
def create_content():
    auth = require_admin()
    if auth:
        return auth
    zones = Zone.query.filter_by(active=True).order_by(Zone.name.asc()).all()
    categories = get_categories(active_only=False)

    if request.method == "POST":
        zone_id = request.form.get("zone_id", type=int)
        category = request.form.get("category", "").strip().lower()
        content_type = request.form.get("content_type", "").strip().lower() or None
        title = request.form.get("title", "").strip()
        if not zone_id or not category or not title:
            flash("Zone, category and title are required.", "error")
            return _render_content_form(zones, categories, None)
        zone = db.session.get(Zone, zone_id)
        if not zone:
            flash("Selected zone does not exist.", "error")
            return _render_content_form(zones, categories, None)
        if not get_category_by_slug(category, active_only=False):
            flash("Invalid content category.", "error")
            return _render_content_form(zones, categories, None)

        workflow = get_content_workflow(category, content_type)
        lifetime_type = workflow["lifetime_type"]
        workflow_notification_eligible = bool(workflow.get("notification_eligible", False))
        pricing_model = workflow.get("pricing_model")

        try:
            dates, error = _validate_and_normalize_content_dates(category, request.form, lifetime_type=lifetime_type)
        except ValueError:
            flash("Please enter valid dates.", "error")
            return _render_content_form(zones, categories, None)
        if error:
            flash(error, "error")
            return _render_content_form(zones, categories, None)

        try:
            start_time = parse_optional_time(request.form.get("start_time"))
            end_time = parse_optional_time(request.form.get("end_time"))
        except ValueError:
            flash("Please enter valid start and end times.", "error")
            return _render_content_form(zones, categories, None)

        canonical_category = normalize_category(category)
        date_error = _validate_effective_campaign_dates(canonical_category, lifetime_type, dates, start_time, end_time)
        if date_error:
            flash(date_error, "error")
            return _render_content_form(zones, categories, None)

        listing_level = _get_listing_level_from_form("discovery")
        sponsorship, sponsorship_error = _parse_sponsorship()
        if sponsorship_error:
            flash(sponsorship_error, "error")
            return _render_content_form(zones, categories, None)

        uploaded_images = _read_uploaded_listing_images()
        image_error = _validate_image_count(uploaded_images, listing_level)
        if image_error:
            flash(image_error, "error")
            return _render_content_form(zones, categories, None)
        try:
            uploaded_image_urls = _upload_listing_images(uploaded_images)
        except Exception as error:
            current_app.logger.exception("Content image upload failed title=%s error=%s", title, error)
            flash(f"Image upload failed: {error}", "error")
            return _render_content_form(zones, categories, None)

        item = ContentItem(
            zone_id=zone_id,
            category=category,
            content_type=content_type,
            lifetime_type=lifetime_type,
            availability_status="available",
            title=title,
            start_time=start_time,
            end_time=end_time,
            image_url=uploaded_image_urls[0] if len(uploaded_image_urls) >= 1 else None,
            image_url_2=uploaded_image_urls[1] if listing_level in {"business", "promotion"} and len(uploaded_image_urls) >= 2 else None,
            image_url_3=uploaded_image_urls[2] if listing_level in {"business", "promotion"} and len(uploaded_image_urls) >= 3 else None,
        )
        _populate_content_common_fields(item, listing_level, workflow_notification_eligible)
        _apply_sponsorship(item, sponsorship)
        for key, value in dates.items():
            setattr(item, key, value)

        try:
            distribution_zone_ids = _configure_commercial_content(item, category, content_type)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_content_form(zones, categories, None)

        if pricing_model == PRICING_MODEL_CAMPAIGN and zone_id not in distribution_zone_ids:
            db.session.rollback()
            flash("A campaign must include its home zone as part of its reach.", "error")
            return _render_content_form(zones, categories, None)
        if pricing_model == PRICING_MODEL_PRESENCE:
            distribution_zone_ids = []

        try:
            db.session.add(item)
            db.session.flush()
            for distribution_zone_id in distribution_zone_ids:
                db.session.add(ContentDistributionZone(content_item_id=item.id, zone_id=distribution_zone_id))
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            current_app.logger.exception("Failed to create content item title=%s error=%s", title, error)
            flash("Content could not be published. Please try again.", "error")
            return _render_content_form(zones, categories, None)

        if item.pricing_model == PRICING_MODEL_CAMPAIGN:
            flash(f"Campaign published successfully. Reach: {len(distribution_zone_ids)} zone(s). Package price: {format_kalxa_price(item.amount_due)}.", "success")
        elif item.pricing_model == PRICING_MODEL_PRESENCE:
            flash(f"Presence listing published successfully. Package price: {format_kalxa_price(item.amount_due)}.", "success")
        else:
            flash("Content published successfully.", "success")
        if item.is_sponsored and item.sponsored_duration_days:
            flash(f"Sponsored Boost configured: {item.sponsored_duration_days} days — {format_kalxa_price(item.sponsorship_amount_due)}. Expires: {item.sponsored_expires_at.strftime('%d %b %Y %H:%M')}.", "success")
        return redirect(url_for("admin.content_list"))

    return _render_content_form(zones, categories, None)


@admin_bp.route("/content/<int:item_id>/edit", methods=["GET", "POST"])
def edit_content(item_id):
    auth = require_admin()
    if auth:
        return auth
    item = ContentItem.query.get_or_404(item_id)
    zones = Zone.query.filter_by(active=True).order_by(Zone.name.asc()).all()
    categories = get_categories(active_only=False)

    if request.method == "POST":
        old_pricing_model = item.pricing_model
        old_duration_days = item.commercial_duration_days
        old_payment_status = item.payment_status
        old_amount_paid = item.amount_paid
        old_payment_reference = item.payment_reference
        old_paid_at = item.paid_at
        old_commercial_starts_at = item.commercial_starts_at
        old_commercial_expires_at = item.commercial_expires_at
        old_distribution_zone_ids = {link.zone_id for link in item.distribution_zone_links}

        zone_id = request.form.get("zone_id", type=int)
        category = request.form.get("category", "").strip().lower()
        content_type = request.form.get("content_type", "").strip().lower() or None
        title = request.form.get("title", "").strip()
        if not zone_id or not category or not title:
            flash("Zone, category and title are required.", "error")
            return _render_content_form(zones, categories, item)
        if not db.session.get(Zone, zone_id):
            flash("Selected zone does not exist.", "error")
            return _render_content_form(zones, categories, item)
        if not get_category_by_slug(category, active_only=False):
            flash("Invalid content category.", "error")
            return _render_content_form(zones, categories, item)

        workflow = get_content_workflow(category, content_type)
        lifetime_type = workflow["lifetime_type"]
        workflow_notification_eligible = bool(workflow.get("notification_eligible", False))
        pricing_model = workflow.get("pricing_model")

        try:
            dates, error = _validate_and_normalize_content_dates(category, request.form, lifetime_type=lifetime_type)
        except ValueError:
            flash("Please enter valid dates.", "error")
            return _render_content_form(zones, categories, item)
        if error:
            flash(error, "error")
            return _render_content_form(zones, categories, item)

        try:
            start_time = parse_optional_time(request.form.get("start_time"))
            end_time = parse_optional_time(request.form.get("end_time"))
        except ValueError:
            flash("Please enter valid start and end times.", "error")
            return _render_content_form(zones, categories, item)

        canonical_category = normalize_category(category)
        date_error = _validate_effective_campaign_dates(canonical_category, lifetime_type, dates, start_time, end_time)
        if date_error:
            flash(date_error, "error")
            return _render_content_form(zones, categories, item)

        listing_level = _get_listing_level_from_form(item.listing_level or "discovery")
        item.zone_id = zone_id
        item.category = category
        item.content_type = content_type
        item.lifetime_type = lifetime_type
        item.availability_status = item.availability_status or "available"
        item.title = title
        item.start_time = start_time
        item.end_time = end_time
        _populate_content_common_fields(item, listing_level, workflow_notification_eligible)

        sponsorship, sponsorship_error = _parse_sponsorship(item)
        if sponsorship_error:
            flash(sponsorship_error, "error")
            return _render_content_form(zones, categories, item)
        _apply_sponsorship(item, sponsorship)
        for key, value in dates.items():
            setattr(item, key, value)

        uploaded_images = _read_uploaded_listing_images()
        image_error = _validate_image_count(uploaded_images, listing_level)
        if image_error:
            flash(image_error, "error")
            return _render_content_form(zones, categories, item)
        if uploaded_images:
            try:
                urls = _upload_listing_images(uploaded_images)
            except Exception as error:
                db.session.rollback()
                current_app.logger.exception("Content image upload failed content_item_id=%s error=%s", item.id, error)
                flash(f"Image upload failed: {error}", "error")
                return _render_content_form(zones, categories, item)
            item.image_url = urls[0] if len(urls) >= 1 else None
            item.image_url_2 = urls[1] if listing_level in {"business", "promotion"} and len(urls) >= 2 else None
            item.image_url_3 = urls[2] if listing_level in {"business", "promotion"} and len(urls) >= 3 else None
        if listing_level == "discovery":
            item.image_url_2 = None
            item.image_url_3 = None

        try:
            distribution_zone_ids = _configure_commercial_content(item, category, content_type)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_content_form(zones, categories, item)

        if pricing_model == PRICING_MODEL_CAMPAIGN and zone_id not in distribution_zone_ids:
            db.session.rollback()
            flash("A campaign must include its home zone as part of its reach.", "error")
            return _render_content_form(zones, categories, item)
        if pricing_model == PRICING_MODEL_PRESENCE:
            distribution_zone_ids = []

        new_distribution_zone_ids = set(distribution_zone_ids)
        package_changed = (
            old_pricing_model != item.pricing_model
            or old_duration_days != item.commercial_duration_days
            or (item.pricing_model == PRICING_MODEL_CAMPAIGN and old_distribution_zone_ids != new_distribution_zone_ids)
            or (old_pricing_model == PRICING_MODEL_CAMPAIGN and item.pricing_model != PRICING_MODEL_CAMPAIGN)
        )
        if not package_changed and old_payment_status == item.payment_status and item.payment_status in {"paid", "waived"}:
            item.commercial_starts_at = old_commercial_starts_at
            item.commercial_expires_at = old_commercial_expires_at
            if item.payment_status == "paid":
                item.paid_at = old_paid_at
                item.amount_paid = old_amount_paid
            else:
                item.paid_at = None
                item.amount_paid = None
        if not item.payment_reference and old_payment_reference and not package_changed:
            item.payment_reference = old_payment_reference

        try:
            ContentDistributionZone.query.filter_by(content_item_id=item.id).delete(synchronize_session=False)
            for distribution_zone_id in distribution_zone_ids:
                db.session.add(ContentDistributionZone(content_item_id=item.id, zone_id=distribution_zone_id))
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            current_app.logger.exception("Failed to update content item content_item_id=%s error=%s", item.id, error)
            flash("Content could not be updated. Please try again.", "error")
            return _render_content_form(zones, categories, item)

        if item.pricing_model == PRICING_MODEL_CAMPAIGN:
            flash(f"Campaign updated successfully. Reach: {len(distribution_zone_ids)} zone(s). Package price: {format_kalxa_price(item.amount_due)}.", "success")
        elif item.pricing_model == PRICING_MODEL_PRESENCE:
            flash(f"Presence listing updated successfully. Package price: {format_kalxa_price(item.amount_due)}.", "success")
        else:
            flash("Content updated successfully.", "success")
        if item.is_sponsored and item.sponsored_duration_days:
            flash(f"Sponsored Boost: {item.sponsored_duration_days} days — {format_kalxa_price(item.sponsorship_amount_due)}. Expires: {item.sponsored_expires_at.strftime('%d %b %Y %H:%M')}.", "success")
        return redirect(url_for("admin.content_list"))

    return _render_content_form(zones, categories, item)


@admin_bp.route("/content/<int:item_id>/toggle", methods=["POST"])
def toggle_content(item_id):
    auth = require_admin()
    if auth:
        return auth
    item = ContentItem.query.get_or_404(item_id)
    item.active = not item.active
    db.session.commit()
    flash("Content activated." if item.active else "Content deactivated.", "success")
    return redirect(url_for("admin.content_list"))


@admin_bp.route("/content/<int:item_id>/delete", methods=["POST"])
def delete_content(item_id):
    auth = require_admin()
    if auth:
        return auth
    item = ContentItem.query.get_or_404(item_id)
    try:
        for submission in PendingSubmission.query.filter_by(published_content_id=item.id).all():
            submission.published_content_id = None
        ContentImage.query.filter_by(content_item_id=item.id).delete(synchronize_session=False)
        ContentDistributionZone.query.filter_by(content_item_id=item.id).delete(synchronize_session=False)
        db.session.delete(item)
        db.session.commit()
        flash("Content permanently deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Delete content error item_id=%s error=%s", item_id, exc)
        flash("Unable to permanently delete content.", "error")
    return redirect(url_for("admin.content_list"))


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
    item.archived_at = datetime.utcnow()
    db.session.commit()
    flash(f"{item.title} archived.", "success")
    return redirect(url_for("admin.content_list"))


# ============================================================
# SHARED SUBMISSION PUBLISHING HELPER
# ============================================================

def _publish_pending_submission(
    submission,
):
    """
    Convert one PendingSubmission into a public ContentItem.

    IMPORTANT:
    - This helper does NOT commit.
    - This helper does NOT redirect.
    - This helper does NOT flash messages.
    - This helper does NOT send push notifications.

    The calling route controls the transaction.

    Used by:
    - approve_submission()
    - approve_all_jobs()
    """

    # ========================================================
    # VALIDATE STATUS
    # ========================================================

    if (
        submission.status
        != "pending"
    ):

        raise ValueError(
            "Submission has already been reviewed."
        )


    # ========================================================
    # VALIDATE ZONE
    # ========================================================

    zone = (
        db.session.get(
            Zone,
            submission.zone_id,
        )
    )


    if not zone:

        raise ValueError(
            "The submission zone no longer exists."
        )


    # ========================================================
    # VALIDATE CATEGORY
    # ========================================================

    category = (
        get_category_by_slug(
            submission.category
        )
    )


    if not category:

        raise ValueError(
            (
                "The submission category is inactive "
                "or unavailable."
            )
        )


    canonical_category = (
        normalize_category(
            submission.category
        )
    )


    # ========================================================
    # IMAGES
    # ========================================================

    submission_images = (
        list(
            submission.images
        )
    )


    first_image_url = (
        submission.image_url
        or
        (
            submission_images[0].image_url
            if submission_images
            else None
        )
    )


    # ========================================================
    # BASIC WORKFLOW DATA
    # ========================================================

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


    notification_eligible = (
        bool(
            submission.notification_eligible
        )
    )


    # ========================================================
    # LOCATION CLASSIFICATION
    # ========================================================

    location_classification = (
        None
    )


    if (
        canonical_category
        == "jobs"
    ):

        candidate_location_classification = (
            str(
                submission.location_classification
                or ""
            )
            .strip()
        )


        if (
            candidate_location_classification
            in
            KALXA_ALLOWED_JOB_LOCATION_CLASSES
        ):

            location_classification = (
                candidate_location_classification
            )


    # ========================================================
    # WORKFLOW FALLBACK
    # ========================================================

    if not lifetime_type:

        workflow = (
            get_content_workflow(
                submission.category,
                content_type,
            )
        )


        lifetime_type = (
            workflow.get(
                "lifetime_type"
            )
            or "ongoing"
        )


        notification_eligible = (
            bool(
                workflow.get(
                    "notification_eligible",
                    notification_eligible,
                )
            )
        )


    # ========================================================
    # JOBS / OPPORTUNITIES
    # ========================================================

    if (
        canonical_category
        == "jobs"
    ):

        pricing_model = (
            None
        )


        commercial_duration_days = (
            None
        )


        amount_due = (
            None
        )


        payment_status = (
            "waived"
        )


        distribution_zone_ids = (
            []
        )


        lifetime_type = (
            "time_specific"
            if submission.end_date
            else "until_unavailable"
        )


        notification_eligible = (
            True
        )


    # ========================================================
    # COMMERCIAL CONTENT
    # ========================================================

    else:

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
            or
            (
                "unpaid"
                if pricing_model
                else "waived"
            )
        )


        if (
            payment_status
            not in
            ALLOWED_PAYMENT_STATUSES
        ):

            payment_status = (
                "unpaid"
            )


        if pricing_model:

            if (
                pricing_model
                not in {
                    PRICING_MODEL_PRESENCE,
                    PRICING_MODEL_CAMPAIGN,
                }
            ):

                raise ValueError(
                    "Unsupported Kalxa pricing model."
                )


            if (
                not commercial_duration_days
            ):

                raise ValueError(
                    (
                        "Commercial submission has "
                        "no package duration."
                    )
                )


            try:

                commercial_duration_days = (
                    int(
                        commercial_duration_days
                    )
                )

            except (
                TypeError,
                ValueError,
            ):

                raise ValueError(
                    (
                        "Commercial submission has "
                        "an invalid package duration."
                    )
                )


            if (
                amount_due
                is None
            ):

                raise ValueError(
                    (
                        "Commercial submission has "
                        "no calculated amount due."
                    )
                )


        distribution_zone_ids = (
            []
        )


        # ====================================================
        # CAMPAIGN DISTRIBUTION
        # ====================================================

        if (
            pricing_model
            == PRICING_MODEL_CAMPAIGN
        ):

            for raw_zone_id in (
                submission.distribution_zone_ids
                or []
            ):

                try:

                    distribution_zone_id = (
                        int(
                            raw_zone_id
                        )
                    )

                except (
                    TypeError,
                    ValueError,
                ):

                    continue


                if (
                    distribution_zone_id
                    not in
                    distribution_zone_ids
                ):

                    distribution_zone_ids.append(
                        distribution_zone_id
                    )


            if (
                submission.zone_id
                not in
                distribution_zone_ids
            ):

                distribution_zone_ids.insert(
                    0,
                    submission.zone_id,
                )


            if (
                len(
                    distribution_zone_ids
                )
                > 3
            ):

                raise ValueError(
                    (
                        "Campaign submission exceeds "
                        "the current 3-zone limit."
                    )
                )


            valid_zone_ids = {
                zone_record.id
                for zone_record in (
                    Zone
                    .query
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
                    valid_zone_ids
                )
                !=
                len(
                    distribution_zone_ids
                )
            ):

                raise ValueError(
                    (
                        "One or more campaign "
                        "distribution zones no "
                        "longer exist."
                    )
                )


        # ====================================================
        # PRESENCE DISTRIBUTION
        # ====================================================

        elif (
            pricing_model
            == PRICING_MODEL_PRESENCE
        ):

            distribution_zone_ids = (
                []
            )


    # ========================================================
    # COMMERCIAL ACTIVATION
    # ========================================================

    commercial_starts_at = (
        None
    )


    commercial_expires_at = (
        None
    )


    amount_paid = (
        None
    )


    paid_at = (
        None
    )


    if (
        pricing_model
        and
        payment_status
        in {
            "paid",
            "waived",
        }
    ):

        commercial_starts_at = (
            datetime.utcnow()
        )


        commercial_expires_at = (
            commercial_starts_at
            +
            timedelta(
                days=
                    commercial_duration_days
            )
        )


        if (
            payment_status
            == "paid"
        ):

            amount_paid = (
                amount_due
            )


            paid_at = (
                getattr(
                    submission,
                    "paid_at",
                    None,
                )
                or
                commercial_starts_at
            )


    # ========================================================
    # CREATE PUBLIC CONTENT ITEM
    # ========================================================

    content = (
        ContentItem(

            organizer_id=
                submission.organizer_id,

            zone_id=
                submission.zone_id,

            category=
                submission.category,

            content_type=
                content_type,

            lifetime_type=
                lifetime_type,

            availability_status=
                availability_status,

            notification_eligible=
                notification_eligible,

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

            title=
                submission.title,

            description=
                submission.description,

            business_name=
                submission.business_name,

            venue=
                submission.venue,

            location_classification=
                location_classification,

            price=
                submission.price,

            contact=
                submission.contact,

            whatsapp_number=
                submission.whatsapp_number,

            directions_url=
                submission.directions_url,

            ticket_url=
                submission.ticket_url,

            image_url=
                first_image_url,

            publish_from=
                submission.publish_from,

            event_date=
                submission.event_date,

            event_end_date=
                submission.event_end_date,

            start_date=
                submission.start_date,

            start_time=
                submission.start_time,

            end_date=
                submission.end_date,

            end_time=
                submission.end_time,

            listing_level=
                "discovery",

            ownership_status=
                "unclaimed",

            is_verified=
                False,

            featured=
                False,

            active=
                True,

            archived=
                False,
        )
    )


    db.session.add(
        content
    )


    db.session.flush()


    # ========================================================
    # DISTRIBUTION ZONES
    # ========================================================

    if (
        pricing_model
        == PRICING_MODEL_CAMPAIGN
    ):

        for distribution_zone_id in (
            distribution_zone_ids
        ):

            db.session.add(
                ContentDistributionZone(

                    content_item_id=
                        content.id,

                    zone_id=
                        distribution_zone_id,
                )
            )


    # ========================================================
    # LINK PUBLISHED CONTENT
    # ========================================================

    submission.published_content_id = (
        content.id
    )


    # ========================================================
    # COPY SUBMISSION IMAGES
    # ========================================================

    for image in (
        submission_images
    ):

        if image.image_url:

            db.session.add(
                ContentImage(

                    content_item_id=
                        content.id,

                    image_url=
                        image.image_url,

                    display_order=
                        image.display_order,
                )
            )


    # ========================================================
    # COMPLETE MODERATION
    # ========================================================

    submission.status = (
        "approved"
    )


    submission.reviewed_at = (
        datetime.utcnow()
    )


    # ========================================================
    # JOBS ARE ALWAYS FREE
    # ========================================================

    if (
        canonical_category
        == "jobs"
    ):

        submission.pricing_model = (
            None
        )


        submission.commercial_duration_days = (
            None
        )


        submission.amount_due = (
            None
        )


        submission.payment_status = (
            "waived"
        )


        submission.distribution_zone_ids = (
            []
        )


    # ========================================================
    # RETURN DATA TO CALLING ROUTE
    # ========================================================

    return {
        "content": content,
        "category": category,
        "canonical_category": canonical_category,
    }


# ============================================================
# PUSH NOTIFICATION AFTER APPROVAL
# ============================================================

def _send_approved_content_push(
    content,
    category,
):
    """
    Send a push notification after the ContentItem has
    already been committed.

    A push failure must never undo a successful approval.
    """

    commercial_is_visible = (
        content.pricing_model
        is None
        or
        (
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


    if not (
        content.active
        and
        content.notification_eligible
        and
        commercial_is_visible
    ):

        return


    try:

        _send_content_push_notification(
            content,
            category_record=
                category,
        )

    except Exception as exc:

        current_app.logger.exception(
            (
                "[Kalxa Push] Approval notification "
                "failed content_id=%s error=%s"
            ),
            content.id,
            exc,
        )


# ============================================================
# SINGLE SUBMISSION APPROVAL
# ============================================================


# ============================================================
# APPROVE NEXT 10 JOBS
#
# IMPORTANT:
# The endpoint/function name remains approve_all_jobs so your
# current submissions.html url_for("admin.approve_all_jobs")
# continues working without a BuildError.
# ============================================================

@admin_bp.route(
    "/submissions/jobs/approve-all",
    methods=[
        "POST",
    ],
)
def approve_all_jobs():

    auth = require_admin()

    if auth:
        return auth


    # ========================================================
    # LOAD ONLY THE NEXT 10 PENDING JOBS
    # ========================================================

    pending_jobs = (
        PendingSubmission
        .query
        .filter(
            PendingSubmission.status
            == "pending",

            PendingSubmission.category.in_(
                [
                    "jobs",
                    "opportunities",
                ]
            ),
        )
        .order_by(
            PendingSubmission.created_at.asc(),
            PendingSubmission.id.asc(),
        )
        .limit(
            10
        )
        .all()
    )


    # ========================================================
    # NOTHING TO APPROVE
    # ========================================================

    if not pending_jobs:

        flash(
            "There are no pending jobs to approve.",
            "info",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status="pending",
            )
        )


    # ========================================================
    # COUNTERS
    # ========================================================

    approved_count = (
        0
    )


    failed_count = (
        0
    )


    failed_jobs = (
        []
    )


    approved_contents = (
        []
    )


    # ========================================================
    # PROCESS MAXIMUM OF 10
    # ========================================================

    for submission in pending_jobs:

        submission_id = (
            submission.id
        )


        submission_title = (
            submission.title
            or
            f"Submission #{submission_id}"
        )


        try:

            # ------------------------------------------------
            # Use shared publishing logic.
            # ------------------------------------------------

            result = (
                _publish_pending_submission(
                    submission
                )
            )


            content = (
                result["content"]
            )


            category = (
                result["category"]
            )


            # ------------------------------------------------
            # Commit EACH job independently.
            #
            # Therefore:
            # - Job #1 succeeds independently.
            # - Job #2 may fail without undoing #1.
            # - The remaining jobs continue processing.
            # ------------------------------------------------

            db.session.commit()


            approved_count += (
                1
            )


            approved_contents.append(
                (
                    content.id,
                    category.slug
                    if hasattr(
                        category,
                        "slug",
                    )
                    else submission.category,
                )
            )


            current_app.logger.info(
                (
                    "[KALXA BULK JOB APPROVAL] "
                    "Approved submission #%s: %s"
                ),
                submission_id,
                submission_title,
            )


        except Exception as exc:

            db.session.rollback()


            failed_count += (
                1
            )


            failed_jobs.append(
                (
                    submission_id,
                    submission_title,
                    str(
                        exc
                    ),
                )
            )


            current_app.logger.exception(
                (
                    "[KALXA BULK JOB APPROVAL ERROR] "
                    "Submission #%s: %s | error=%s"
                ),
                submission_id,
                submission_title,
                exc,
            )


    # ========================================================
    # SEND PUSH NOTIFICATIONS AFTER DATABASE WORK
    #
    # We deliberately do this after the 10 approval transactions
    # so a notification failure cannot mark a job as failed.
    # ========================================================

    for (
        content_id,
        category_slug,
    ) in approved_contents:

        try:

            content = (
                db.session.get(
                    ContentItem,
                    content_id,
                )
            )


            if not content:

                continue


            category_record = (
                get_category_by_slug(
                    category_slug
                )
            )


            if not category_record:

                category_record = (
                    get_category_by_slug(
                        content.category
                    )
                )


            if category_record:

                _send_approved_content_push(
                    content,
                    category_record,
                )


        except Exception as exc:

            current_app.logger.exception(
                (
                    "[KALXA BULK PUSH WARNING] "
                    "Content #%s push failed: %s"
                ),
                content_id,
                exc,
            )


    # ========================================================
    # COUNT REMAINING JOBS
    # ========================================================

    remaining_count = (
        PendingSubmission
        .query
        .filter(
            PendingSubmission.status
            == "pending",

            PendingSubmission.category.in_(
                [
                    "jobs",
                    "opportunities",
                ]
            ),
        )
        .count()
    )


    # ========================================================
    # RESULT MESSAGE
    # ========================================================

    if (
        approved_count > 0
        and
        failed_count == 0
    ):

        flash(
            (
                f"✓ {approved_count} "
                f"{'job was' if approved_count == 1 else 'jobs were'} "
                "approved successfully. "
                f"{remaining_count} "
                f"{'job remains' if remaining_count == 1 else 'jobs remain'} "
                "pending."
            ),
            "success",
        )


    elif (
        approved_count > 0
        and
        failed_count > 0
    ):

        flash(
            (
                f"{approved_count} "
                f"{'job was' if approved_count == 1 else 'jobs were'} "
                "approved. "
                f"{failed_count} "
                f"{'job failed' if failed_count == 1 else 'jobs failed'}. "
                f"{remaining_count} "
                f"{'job remains' if remaining_count == 1 else 'jobs remain'} "
                "pending."
            ),
            "warning",
        )


    else:

        flash(
            (
                f"0 jobs approved. "
                f"{failed_count} "
                f"{'job failed' if failed_count == 1 else 'jobs failed'}. "
                "Check the Render logs for the exact error."
            ),
            "danger",
        )


    # ========================================================
    # LOG FAILURES
    # ========================================================

    if failed_jobs:

        for (
            failed_submission_id,
            failed_submission_title,
            failed_error,
        ) in failed_jobs:

            current_app.logger.error(
                (
                    "[KALXA BULK JOB FAILED] "
                    "#%s | %s | %s"
                ),
                failed_submission_id,
                failed_submission_title,
                failed_error,
            )


    # ========================================================
    # RETURN TO PENDING
    # ========================================================

    return redirect(
        url_for(
            "admin.submissions",
            status="pending",
        )
    )


# ============================================================
# SUBMISSIONS PAGE
# ============================================================

@admin_bp.route(
    "/submissions"
)
def submissions():

    auth = require_admin()

    if auth:
        return auth


    selected_status = (
        request.args.get(
            "status",
            "pending",
        )
        .strip()
        .lower()
    )


    if (
        selected_status
        not in {
            "pending",
            "approved",
            "rejected",
        }
    ):

        selected_status = (
            "pending"
        )


    # ========================================================
    # CURRENT TAB ITEMS
    # ========================================================

    items = (
        PendingSubmission
        .query
        .filter_by(
            status=
                selected_status
        )
        .order_by(
            PendingSubmission
            .created_at
            .desc()
        )
        .all()
    )


    # ========================================================
    # STATUS COUNTS
    # ========================================================

    pending_count = (
        PendingSubmission
        .query
        .filter_by(
            status=
                "pending"
        )
        .count()
    )


    approved_count = (
        PendingSubmission
        .query
        .filter_by(
            status=
                "approved"
        )
        .count()
    )


    rejected_count = (
        PendingSubmission
        .query
        .filter_by(
            status=
                "rejected"
        )
        .count()
    )


    # ========================================================
    # PENDING JOBS / OPPORTUNITIES COUNT
    # ========================================================

    pending_jobs_count = (
        PendingSubmission
        .query
        .filter(
            PendingSubmission.status
            == "pending",

            PendingSubmission.category.in_(
                [
                    "jobs",
                    "opportunities",
                ]
            ),
        )
        .count()
    )


    # ========================================================
    # NUMBER THAT THE NEXT BULK ACTION WILL PROCESS
    # ========================================================

    next_job_batch_count = (
        min(
            pending_jobs_count,
            10,
        )
    )


    # ========================================================
    # RENDER
    # ========================================================

    return render_template(
        "admin/submissions.html",

        submissions=
            items,

        selected_status=
            selected_status,

        pending_count=
            pending_count,

        approved_count=
            approved_count,

        rejected_count=
            rejected_count,

        pending_jobs_count=
            pending_jobs_count,

        next_job_batch_count=
            next_job_batch_count,
    )

@admin_bp.route(
    "/submissions/<int:submission_id>/edit",
    methods=[
        "GET",
        "POST",
    ],
)
def edit_submission(
    submission_id,
):

    auth = require_admin()

    if auth:
        return auth


    submission = (
        PendingSubmission
        .query
        .get_or_404(
            submission_id
        )
    )


    if (
        submission.status
        != "pending"
    ):

        flash(
            "Only pending submissions can be edited.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions",
                status=
                    submission.status,
            )
        )


    zones = (
        Zone
        .query
        .filter_by(
            active=True
        )
        .order_by(
            Zone.name.asc()
        )
        .all()
    )


    categories = (
        get_categories()
    )


    if (
        request.method
        == "POST"
    ):

        zone_id = (
            request.form.get(
                "zone_id",
                type=int,
            )
        )


        raw_category = (
            request.form.get(
                "category",
                "",
            )
            .strip()
            .lower()
        )


        category = (
            normalize_category(
                raw_category
            )
        )


        content_type = (
            request.form.get(
                "content_type",
                submission.content_type
                or "",
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


        if (
            not zone_id
            or
            not category
            or
            not title
        ):

            flash(
                "Zone, category and title are required.",
                "error",
            )

            return (
                _render_submission_edit(
                    submission,
                    zones,
                    categories,
                )
            )


        zone = (
            db.session.get(
                Zone,
                zone_id,
            )
        )


        if (
            not zone
            or
            not zone.active
        ):

            flash(
                "Please select a valid active zone.",
                "error",
            )

            return (
                _render_submission_edit(
                    submission,
                    zones,
                    categories,
                )
            )


        if (
            not get_category_by_slug(
                category
            )
        ):

            flash(
                "Please select a valid active category.",
                "error",
            )

            return (
                _render_submission_edit(
                    submission,
                    zones,
                    categories,
                )
            )


        workflow = (
            get_content_workflow(
                category,
                content_type,
            )
        )


        canonical_category = (
            normalize_category(
                category
            )
        )


        lifetime_type = (
            submission.lifetime_type
            or
            workflow.get(
                "lifetime_type"
            )
        )


        # ====================================================
        # JOB-SPECIFIC MODERATION
        # ====================================================

        if (
            canonical_category
            == "jobs"
        ):

            closing_date_raw = (
                request.form.get(
                    "end_date",
                    "",
                )
                .strip()
            )


            lifetime_type = (
                "time_specific"
                if closing_date_raw
                else "until_unavailable"
            )


            raw_location_classification = (
                request.form.get(
                    "location_classification",
                    "",
                )
                .strip()
            )


            if (
                raw_location_classification
                in
                KALXA_ALLOWED_JOB_LOCATION_CLASSES
            ):

                location_classification = (
                    raw_location_classification
                )

            else:

                location_classification = (
                    None
                )

        else:

            # ------------------------------------------------
            # Non-job listings should never retain a stale
            # jobs-region classification.
            # ------------------------------------------------

            location_classification = (
                None
            )


        # ====================================================
        # DATES
        # ====================================================

        try:

            dates, error = (
                _validate_and_normalize_content_dates(
                    category,
                    request.form,
                    lifetime_type=
                        lifetime_type,
                )
            )

        except ValueError:

            flash(
                "Please enter valid dates.",
                "error",
            )

            return (
                _render_submission_edit(
                    submission,
                    zones,
                    categories,
                )
            )


        if error:

            flash(
                error,
                "error",
            )

            return (
                _render_submission_edit(
                    submission,
                    zones,
                    categories,
                )
            )


        # ====================================================
        # TIMES
        # ====================================================

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

            return (
                _render_submission_edit(
                    submission,
                    zones,
                    categories,
                )
            )


        # ====================================================
        # SAVE CORE SUBMISSION FIELDS
        # ====================================================

        submission.zone_id = (
            zone.id
        )


        submission.category = (
            category
        )


        submission.content_type = (
            content_type
        )


        submission.lifetime_type = (
            lifetime_type
        )


        submission.notification_eligible = (
            bool(
                workflow.get(
                    "notification_eligible",
                    True,
                )
            )
        )


        submission.title = (
            title
        )


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


        # ====================================================
        # JOB LOCATION CLASSIFICATION
        # ====================================================

        submission.location_classification = (
            location_classification
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


        submission.whatsapp_number = (
            request.form.get(
                "whatsapp_number",
                "",
            )
            .strip()
            or None
        )


        submission.directions_url = (
            request.form.get(
                "directions_url",
                "",
            )
            .strip()
            or None
        )


        submission.ticket_url = (
            request.form.get(
                "ticket_url",
                "",
            )
            .strip()
            or None
        )


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
            or None
        )


        submission.submitter_email = (
            request.form.get(
                "submitter_email",
                "",
            )
            .strip()
            or None
        )


        # ====================================================
        # NORMALIZED DATES
        # ====================================================

        for key, value in (
            dates.items()
        ):

            setattr(
                submission,
                key,
                value,
            )


        submission.start_time = (
            start_time
        )


        submission.end_time = (
            end_time
        )


        # ====================================================
        # JOBS ARE FREE
        # ====================================================

        if (
            canonical_category
            == "jobs"
        ):

            submission.pricing_model = (
                None
            )

            submission.commercial_duration_days = (
                None
            )

            submission.amount_due = (
                None
            )

            submission.payment_status = (
                "waived"
            )

            submission.distribution_zone_ids = (
                []
            )


        # ====================================================
        # COMMIT
        # ====================================================

        try:

            db.session.commit()

        except Exception as exc:

            db.session.rollback()


            current_app.logger.exception(
                (
                    "[Kalxa Admin] Unable to update "
                    "submission submission_id=%s error=%s"
                ),
                submission.id,
                exc,
            )


            flash(
                "Submission could not be updated.",
                "error",
            )


            return (
                _render_submission_edit(
                    submission,
                    zones,
                    categories,
                )
            )


        flash(
            (
                "Submission updated successfully. "
                "You can now approve it."
            ),
            "success",
        )


        return redirect(
            url_for(
                "admin.submissions",
                status="pending",
            )
        )


    return (
        _render_submission_edit(
            submission,
            zones,
            categories,
        )
    )

def _send_content_push_notification(content, category_record=None):
    existing_notification = PushNotification.query.filter_by(content_item_id=content.id).first()
    if existing_notification:
        return existing_notification

    zone = db.session.get(Zone, content.zone_id)
    if not zone:
        return None

    canonical_category = normalize_category(content.category)
    if canonical_category == "jobs":
        category_label = "Job Opportunity"
    elif category_record:
        category_label = category_record.name
    else:
        category_label = content.category.replace("-", " ").replace("_", " ").title()

    notification_title = f"New {category_label} in {zone.name}"
    notification_body = content.title
    notification_url = f"/listing/{content.id}"

    push_record = PushNotification(
        content_item_id=content.id,
        zone_id=content.zone_id,
        title=notification_title,
        body=notification_body,
        target_url=notification_url,
        status="pending",
        total_subscribers=0,
        sent_count=0,
        failed_count=0,
        attempts=0,
    )
    db.session.add(push_record)
    db.session.commit()

    push_record.attempts = (push_record.attempts or 0) + 1
    result = send_zone_push_notification(
        zone_id=content.zone_id,
        category=content.category,
        title=notification_title,
        body=notification_body,
        url=notification_url,
        tag=f"content-{content.id}",
    )
    if not isinstance(result, dict):
        result = {}
    total = int(result.get("total", 0) or 0)
    sent = int(result.get("sent", 0) or 0)
    failed = int(result.get("failed", 0) or 0)
    push_record.total_subscribers = total
    push_record.sent_count = sent
    push_record.failed_count = failed

    if sent > 0 and failed == 0:
        push_record.status = "sent"
        push_record.sent_at = datetime.utcnow()
        push_record.last_error = None
    elif sent > 0 and failed > 0:
        push_record.status = "partial_failure"
        push_record.sent_at = datetime.utcnow()
        push_record.last_error = f"{failed} subscriber delivery failures."
    elif total == 0:
        push_record.status = "no_subscribers"
        push_record.sent_at = None
        push_record.last_error = "No active subscribers were found for this zone and category."
    else:
        push_record.status = "failed"
        push_record.sent_at = None
        push_record.last_error = "Push delivery failed for all subscribers."
    db.session.commit()
    return push_record


@admin_bp.route(
    "/submissions/<int:submission_id>/approve",
    methods=[
        "POST",
    ],
)
def approve_submission(
    submission_id,
):

    auth = require_admin()

    if auth:
        return auth


    submission = (
        PendingSubmission
        .query
        .get_or_404(
            submission_id
        )
    )


    # ========================================================
    # ALREADY REVIEWED
    # ========================================================

    if (
        submission.status
        != "pending"
    ):

        flash(
            "Submission has already been reviewed.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions"
            )
        )


    # ========================================================
    # PUBLISH
    # ========================================================

    try:

        result = (
            _publish_pending_submission(
                submission
            )
        )


        content = (
            result["content"]
        )


        category = (
            result["category"]
        )


        canonical_category = (
            result[
                "canonical_category"
            ]
        )


        # ----------------------------------------------------
        # Commit the approval before push notifications.
        # ----------------------------------------------------

        db.session.commit()


    except Exception as exc:

        db.session.rollback()


        current_app.logger.exception(
            (
                "[Kalxa] Approve submission failed "
                "submission_id=%s error=%s"
            ),
            submission_id,
            exc,
        )


        flash(
            (
                "Unable to approve submission. "
                f"{exc}"
            ),
            "error",
        )


        return redirect(
            url_for(
                "admin.submissions",
                status="pending",
            )
        )


    # ========================================================
    # PUSH NOTIFICATION
    # ========================================================

    _send_approved_content_push(
        content,
        category,
    )


    # ========================================================
    # SUCCESS MESSAGE
    # ========================================================

    if (
        canonical_category
        == "jobs"
    ):

        flash(
            (
                "Free job opportunity approved "
                "and published."
            ),
            "success",
        )


    elif (
        content.pricing_model
        and
        content.payment_status
        == "unpaid"
    ):

        flash(
            (
                "Submission approved, but the listing "
                "is hidden until payment is confirmed."
            ),
            "success",
        )


    elif (
        content.pricing_model
        and
        content.payment_status
        == "refunded"
    ):

        flash(
            (
                "Submission approved, but the listing "
                "is hidden because its payment is "
                "refunded."
            ),
            "success",
        )


    else:

        flash(
            "Submission approved and published.",
            "success",
        )


    return redirect(
        url_for(
            "admin.submissions",
            status="pending",
        )
    )


@admin_bp.route("/submissions/<int:submission_id>/confirm-payment", methods=["POST"])
def confirm_submission_payment(submission_id):
    auth = require_admin()
    if auth:
        return auth

    submission = PendingSubmission.query.get_or_404(submission_id)
    if submission.status != "approved":
        flash("Only approved submissions can have payment confirmed.", "error")
        return redirect(url_for("admin.submissions", status="approved"))
    if not submission.published_content_id:
        flash("This submission does not have a published content record.", "error")
        return redirect(url_for("admin.submissions", status="approved"))

    content = db.session.get(ContentItem, submission.published_content_id)
    if not content:
        flash("The published listing could not be found.", "error")
        return redirect(url_for("admin.submissions", status="approved"))
    if normalize_category(content.category) == "jobs":
        flash("Kalxa Job opportunities are free. No payment confirmation is required.", "info")
        return redirect(url_for("admin.submissions", status="approved"))

    if submission.organizer_id and content.organizer_id is None:
        content.organizer_id = submission.organizer_id
    elif submission.organizer_id and content.organizer_id != submission.organizer_id:
        flash("Payment cannot be confirmed because the listing ownership does not match.", "error")
        return redirect(url_for("admin.submissions", status="approved"))

    if content.pricing_model not in {PRICING_MODEL_PRESENCE, PRICING_MODEL_CAMPAIGN}:
        flash("This listing does not use a Kalxa commercial package.", "error")
        return redirect(url_for("admin.submissions", status="approved"))
    if content.payment_status == "paid":
        flash("Payment has already been confirmed for this listing.", "error")
        return redirect(url_for("admin.submissions", status="approved"))
    if content.payment_status == "waived":
        flash("Payment for this listing has been waived.", "error")
        return redirect(url_for("admin.submissions", status="approved"))
    if content.payment_status != "unpaid":
        flash("Payment cannot be confirmed from its current status.", "error")
        return redirect(url_for("admin.submissions", status="approved"))
    if not content.commercial_duration_days or content.amount_due is None:
        flash("This listing has an incomplete commercial package.", "error")
        return redirect(url_for("admin.submissions", status="approved"))

    try:
        duration_days = int(content.commercial_duration_days)
        if duration_days <= 0:
            raise ValueError("Invalid commercial duration.")
        now = datetime.utcnow()
        content.payment_status = "paid"
        content.amount_paid = content.amount_due
        content.paid_at = now
        content.commercial_starts_at = now
        content.commercial_expires_at = now + timedelta(days=duration_days)
        submission.payment_status = "paid"
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("[Kalxa Payment] Unable to confirm payment submission_id=%s content_id=%s error=%s", submission.id, content.id, exc)
        flash("Unable to confirm payment.", "error")
        return redirect(url_for("admin.submissions", status="approved"))

    if content.active and content.notification_eligible:
        try:
            _send_content_push_notification(content)
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("[Kalxa Push] Payment activation notification failed submission_id=%s content_id=%s error=%s", submission.id, content.id, exc)

    flash("Payment confirmed. The Kalxa listing is now live.", "success")
    return redirect(url_for("admin.submissions", status="approved"))


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
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unable to reject submission submission_id=%s error=%s", submission.id, exc)
        flash("Submission could not be rejected.", "error")
        return redirect(url_for("admin.submissions"))
    flash("Submission rejected.", "success")
    return redirect(url_for("admin.submissions"))
