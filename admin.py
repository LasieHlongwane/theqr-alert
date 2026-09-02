import os
import io

from datetime import date, datetime, timedelta
from push_service import (
    send_zone_push_notification,
)
import qrcode

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_file,
    current_app
)

from sqlalchemy import func

from cloud_storage import upload_listing_image

from models import (
    db,
    Zone,
    Category,
    AccessPoint,
    QRScan,
    ContentItem,
    PendingSubmission,
    PendingSubmissionImage,
    ContentImage,
    PushNotification,
    PushSubscriber,
    EngagementEvent,
)
ONGOING_CATEGORIES = {
    "property",
    "transport",
    "services",
}
from qr_generator import generate_access_qr
ARCHIVE_GRACE_DAYS = 7
EXPIRING_SOON_DAYS = 3

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
    query = Category.query
    if active_only:
        query = query.filter(Category.active.is_(True))
    return query.order_by(Category.display_order.asc(), Category.name.asc()).all()


def get_category_by_slug(slug, active_only=True):
    query = Category.query.filter(Category.slug == slug)
    if active_only:
        query = query.filter(Category.active.is_(True))
    return query.first()


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


def _get_content_workflow(
    category,
    content_type,
):

    category = (
        category or ""
    ).strip().lower()

    content_type = (
        content_type or ""
    ).strip().lower()


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

    if workflow:
        return workflow


    # -----------------------------------------
    # EVENTS ALWAYS EXPIRE
    # -----------------------------------------

    if category == "events":

        return {
            "lifetime_type": "time_specific",
            "notification_eligible": True,
        }


    # -----------------------------------------
    # ONLY THESE CATEGORIES ARE ONGOING
    # -----------------------------------------

    ongoing_categories = {
        "property",
        "transport",
        "services",
    }

    if category in ongoing_categories:

        return {
            "lifetime_type": "ongoing",
            "notification_eligible": False,
        }


    # -----------------------------------------
    # EVERYTHING ELSE EXPIRES
    # -----------------------------------------

    return {
        "lifetime_type": "time_specific",
        "notification_eligible": True,
    }




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

    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    tomorrow_start = today_start + timedelta(days=1)
    seven_days_ago = today_start - timedelta(days=6)
    fourteen_days_ago = today_start - timedelta(days=13)
    thirty_days_ago = today_start - timedelta(days=29)
    previous_7_start = seven_days_ago - timedelta(days=7)

    total_scans = QRScan.query.filter(QRScan.event_type == "scan").count()
    today_scans = QRScan.query.filter(
        QRScan.event_type == "scan",
        QRScan.scanned_at >= today_start,
        QRScan.scanned_at < tomorrow_start,
    ).count()
    seven_day_scans = QRScan.query.filter(
        QRScan.event_type == "scan",
        QRScan.scanned_at >= seven_days_ago,
    ).count()
    thirty_day_scans = QRScan.query.filter(
        QRScan.event_type == "scan",
        QRScan.scanned_at >= thirty_days_ago,
    ).count()
    previous_7_scans = QRScan.query.filter(
        QRScan.event_type == "scan",
        QRScan.scanned_at >= previous_7_start,
        QRScan.scanned_at < seven_days_ago,
    ).count()

    if previous_7_scans > 0:
        seven_day_growth = ((seven_day_scans - previous_7_scans) / previous_7_scans) * 100
    elif seven_day_scans > 0:
        seven_day_growth = 100.0
    else:
        seven_day_growth = 0.0

    total_access_points = AccessPoint.query.count()
    active_access_points = AccessPoint.query.filter(AccessPoint.active.is_(True)).count()
    scans_per_access_point = (
        thirty_day_scans / active_access_points if active_access_points else 0
    )

    total_category_views = QRScan.query.filter(
        QRScan.event_type == "category_view"
    ).count()

    category_results = (
        db.session.query(
            QRScan.category_selected,
            func.count(QRScan.id).label("view_count"),
        )
        .filter(
            QRScan.event_type == "category_view",
            QRScan.category_selected.isnot(None),
        )
        .group_by(QRScan.category_selected)
        .order_by(func.count(QRScan.id).desc())
        .all()
    )

    category_map = {c.slug: c for c in get_categories(active_only=False)}
    category_activity = []
    for result in category_results:
        percentage = (result.view_count / total_category_views * 100) if total_category_views else 0
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
        .outerjoin(
            QRScan,
            (QRScan.access_point_id == AccessPoint.id)
            & (QRScan.event_type == "scan"),
        )
        .group_by(
            AccessPoint.id,
            AccessPoint.code,
            AccessPoint.name,
            AccessPoint.location_type,
            Zone.name,
        )
        .order_by(func.count(QRScan.id).desc())
        .limit(10)
        .all()
    )

    top_locations = []
    for point in top_locations_query:
        share = (point.scan_count / total_scans * 100) if total_scans else 0
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
        db.session.query(
            Zone.id,
            Zone.name,
            func.count(QRScan.id).label("scan_count"),
        )
        .outerjoin(AccessPoint, AccessPoint.zone_id == Zone.id)
        .outerjoin(
            QRScan,
            (QRScan.access_point_id == AccessPoint.id)
            & (QRScan.event_type == "scan"),
        )
        .group_by(Zone.id, Zone.name)
        .order_by(func.count(QRScan.id).desc())
        .all()
    )

    zone_activity = []
    for zone in zone_query:
        percentage = (zone.scan_count / total_scans * 100) if total_scans else 0
        zone_activity.append({
            "name": zone.name,
            "scan_count": zone.scan_count,
            "percentage": round(percentage, 1),
        })

    recent_scan_counts = dict(
        db.session.query(QRScan.access_point_id, func.count(QRScan.id))
        .filter(
            QRScan.event_type == "scan",
            QRScan.scanned_at >= thirty_days_ago,
        )
        .group_by(QRScan.access_point_id)
        .all()
    )

    low_performing_points = []
    for point in AccessPoint.query.filter(AccessPoint.active.is_(True)).all():
        scan_count = recent_scan_counts.get(point.id, 0)
        if scan_count <= 5:
            low_performing_points.append({
                "name": point.name,
                "code": point.code,
                "zone": point.zone.name,
                "scan_count": scan_count,
            })
    low_performing_points.sort(key=lambda row: row["scan_count"])

    scan_rows = QRScan.query.filter(
        QRScan.event_type == "scan",
        QRScan.scanned_at >= fourteen_days_ago,
    ).all()

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
        bar_percentage = (count / max_daily_scans * 100) if max_daily_scans else 0
        daily_scan_trend.append({
            "date": scan_date,
            "label": scan_date.strftime("%d %b"),
            "count": count,
            "bar_percentage": round(bar_percentage, 1),
        })

    category_engagement_rate = (
        total_category_views / total_scans * 100 if total_scans else 0
    )

    recent_scans = (
        QRScan.query
        .filter(QRScan.event_type == "scan")
        .order_by(QRScan.scanned_at.desc())
        .limit(20)
        .all()
    )

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
            /
            total_listing_views
        )
        * 100,
        1,
      )

    else:

      listing_action_rate = 0

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
        total_listing_views=total_listing_views,

        total_whatsapp_clicks=(
          total_whatsapp_clicks
        ),

        total_call_clicks=(
          total_call_clicks
        ),

        total_share_clicks=(
          total_share_clicks
        ),

        total_directions_clicks=(
          total_directions_clicks
        ),

        total_useful_actions=(
          total_useful_actions
        ),

        listing_action_rate=(
          listing_action_rate
        ),
    
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


    try:

        notification.attempts += 1

        notification.status = (
            "pending"
        )

        db.session.commit()


        # =================================================
        # ATTEMPT DELIVERY
        # =================================================

        result = (
            send_zone_push_notification(

                zone_id=
                    notification.zone_id,

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

            notification.last_error = (
                "No active subscribers "
                "were found for this zone."
            )


        else:

            notification.status = (
                "failed"
            )

            notification.last_error = (
                "Push delivery failed "
                "for all subscribers."
            )


        db.session.commit()


        current_app.logger.info(
            "[LaC Push] Notification retried "
            "notification_id=%s "
            "zone_id=%s "
            "sent=%s "
            "failed=%s "
            "status=%s",
            notification.id,
            notification.zone_id,
            notification.sent_count,
            notification.failed_count,
            notification.status,
        )


        flash(
            (
                "Notification retry completed. "
                f"Sent: {notification.sent_count}, "
                f"Failed: {notification.failed_count}."
            ),
            "success",
        )


    except Exception as exc:

        db.session.rollback()

        current_app.logger.exception(
            "[LaC Push] Retry failed "
            "notification_id=%s "
            "error=%s",
            notification.id,
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

        category = Category(
            name=name,
            slug=slug,
            icon=icon,
            display_order=display_order,
            active=active,
        )
        db.session.add(category)
        db.session.commit()
        flash("Category created.", "success")
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

        if not name or not new_slug:
            flash("Name and slug are required.", "error")
            return render_template("admin/category_form.html", category=category)

        duplicate = Category.query.filter(
            Category.slug == new_slug,
            Category.id != category.id,
        ).first()
        if duplicate:
            flash("Another category already uses that slug.", "error")
            return render_template("admin/category_form.html", category=category)

        old_slug = category.slug
        if new_slug != old_slug:
            ContentItem.query.filter(ContentItem.category == old_slug).update(
                {ContentItem.category: new_slug}, synchronize_session=False
            )
            AccessPoint.query.filter(AccessPoint.default_category == old_slug).update(
                {AccessPoint.default_category: new_slug}, synchronize_session=False
            )
            PendingSubmission.query.filter(PendingSubmission.category == old_slug).update(
                {PendingSubmission.category: new_slug}, synchronize_session=False
            )
            QRScan.query.filter(QRScan.category_selected == old_slug).update(
                {QRScan.category_selected: new_slug}, synchronize_session=False
            )

        category.name = name
        category.slug = new_slug
        category.icon = icon
        category.display_order = display_order
        category.active = request.form.get("active") == "on"
        db.session.commit()
        flash("Category updated.", "success")
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

    auth = require_admin()

    if auth:
        return auth

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

    categories = (
        get_categories()
    )

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
        # REQUIRED FIELD VALIDATION
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

        if (
            not zone
            or not zone.active
        ):

            flash(
                "Please select a valid active zone.",
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

        category_record = (
            get_category_by_slug(
                category
            )
        )

        if not category_record:

            flash(
                "Invalid or inactive content category.",
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

        notification_eligible = bool(
            workflow.get(
                "notification_eligible",
                False,
            )
        )

        availability_status = (
            "available"
        )

        # =================================================
        # VALIDATE + NORMALIZE DATES
        # =================================================

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
        # IMAGE UPLOAD
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

        if len(uploaded_images) > 3:

            flash(
                "You can upload a maximum of 3 images.",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )

        # =================================================
        # CREATE CONTENT
        # =================================================

        item = ContentItem(

            zone_id=
                zone_id,

            category=
                category,

            # ---------------------------------------------
            # NEW
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
            # NORMAL LISTING DATA
            # ---------------------------------------------

            title=
                title,

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
                request.form.get(
                    "contact",
                    "",
                )
                .strip()
                or None
            ),

            featured=(
                request.form.get(
                    "featured"
                )
                == "on"
            ),

            active=(
                request.form.get(
                    "active"
                )
                == "on"
            ),

            **dates,
        )

        db.session.add(
            item
        )

        db.session.flush()

        # =================================================
        # IMAGES
        # =================================================

        try:

            first_image_url = None

            for (
                index,
                uploaded_image,
            ) in enumerate(
                uploaded_images,
                start=1,
            ):

                image_url = (
                    upload_listing_image(
                        uploaded_image
                    )
                )

                if not image_url:
                    continue

                if not first_image_url:

                    first_image_url = (
                        image_url
                    )

                content_image = (
                    ContentImage(

                        content_item_id=
                            item.id,

                        image_url=
                            image_url,

                        display_order=
                            index,
                    )
                )

                db.session.add(
                    content_image
                )

            # Keep ContentItem.image_url as fallback/cover.
            if first_image_url:

                item.image_url = (
                    first_image_url
                )

        except Exception as error:

            db.session.rollback()

            flash(
                f"Image upload failed: {error}",
                "error",
            )

            return _render_content_form(
                zones,
                categories,
                None,
            )

        # =================================================
        # SAVE
        # =================================================

        db.session.commit()

        flash(
            "Content published successfully.",
            "success",
        )

        return redirect(
            url_for(
                "admin.content_list"
            )
        )

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

    auth = require_admin()

    if auth:
        return auth

    item = (
        ContentItem.query
        .get_or_404(
            item_id
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

    categories = (
        get_categories(
            active_only=False
        )
    )

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
        #
        # If admin changes:
        #
        # Property → Room
        # to
        # Property → Hotel
        #
        # LaC must also change:
        #
        # until_unavailable
        # to
        # ongoing
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

        notification_eligible = bool(
            workflow.get(
                "notification_eligible",
                False,
            )
        )

        # =================================================
        # VALIDATE + NORMALIZE DATES
        # =================================================

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
        # UPDATE CORE FIELDS
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

        item.notification_eligible = (
            notification_eligible
        )

        # -------------------------------------------------
        # Do NOT blindly reset closed listings to available
        # when editing ordinary text.
        #
        # Only initialise if somehow empty/legacy.
        # -------------------------------------------------

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

        item.contact = (
            request.form.get(
                "contact",
                "",
            )
            .strip()
            or None
        )

        # =================================================
        # LEGACY SINGLE IMAGE UPLOAD
        # =================================================

        uploaded_image = (
            request.files.get(
                "image"
            )
        )

        if (
            uploaded_image
            and uploaded_image.filename
        ):

            try:

                item.image_url = (
                    upload_listing_image(
                        uploaded_image
                    )
                )

            except Exception as error:

                db.session.rollback()

                flash(
                    f"Image upload failed: {error}",
                    "error",
                )

                return _render_content_form(
                    zones,
                    categories,
                    item,
                )

        # =================================================
        # FLAGS
        # =================================================

        item.featured = (
            request.form.get(
                "featured"
            )
            == "on"
        )

        item.active = (
            request.form.get(
                "active"
            )
            == "on"
        )

        # =================================================
        # APPLY NORMALIZED DATE FIELDS
        # =================================================

        for key, value in dates.items():

            setattr(
                item,
                key,
                value,
            )

        # =================================================
        # SAVE
        # =================================================

        db.session.commit()

        flash(
            "Content updated successfully.",
            "success",
        )

        return redirect(
            url_for(
                "admin.content_list"
            )
        )

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
def approve_submission(
    submission_id,
):

    auth = require_admin()

    if auth:
        return auth

    submission = (
        PendingSubmission.query
        .get_or_404(
            submission_id
        )
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
            url_for(
                "admin.submissions"
            )
        )

    # =====================================================
    # VALIDATE ZONE
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
            url_for(
                "admin.submissions"
            )
        )

    # =====================================================
    # VALIDATE CATEGORY
    # =====================================================

    category = (
        get_category_by_slug(
            submission.category
        )
    )

    if not category:

        flash(
            "The submission category is inactive or unavailable.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions"
            )
        )

    try:

        # =================================================
        # DETERMINE MAIN / COVER IMAGE
        # =================================================

        submission_images = list(
            submission.images
        )

        first_image_url = None

        if submission_images:

            first_image = (
                submission_images[0]
            )

            if first_image.image_url:

                first_image_url = (
                    first_image.image_url
                )

        # Prefer explicitly stored cover image if available.
        if submission.image_url:

            first_image_url = (
                submission.image_url
            )

        # =================================================
        # LEGACY / SAFETY FALLBACK
        #
        # Older pending submissions may not yet have the
        # new lifecycle fields populated.
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

        if submission.category in ONGOING_CATEGORIES:

          notification_eligible = False

        else:

          notification_eligible = True

        # -------------------------------------------------
        # For old submissions created before lifecycle
        # support existed.
        # -------------------------------------------------

        if not lifetime_type:

            if submission.category == "events":

                lifetime_type = (
                    "time_specific"
                )

                notification_eligible = (
                    True
                )

            elif submission.end_date:

                lifetime_type = (
                    "time_specific"
                )

            else:

                lifetime_type = (
                    "ongoing"
                )

        # =================================================
        # CREATE LIVE CONTENT
        # =================================================

        content = ContentItem(

            zone_id=
                submission.zone_id,

            category=
                submission.category,

            # ---------------------------------------------
            # NEW LIFECYCLE FIELDS
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
            # DATES
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
            # STATUS
            # ---------------------------------------------

            featured=False,

            active=True,

            archived=False,
        )

        db.session.add(
            content
        )

        # Generate content.id
        db.session.flush()

        # =================================================
        # LINK SUBMISSION TO PUBLISHED CONTENT
        # =================================================

        submission.published_content_id = (
            content.id
        )

        # =================================================
        # COPY CLOUDINARY IMAGE URLS
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

        submission.status = (
            "approved"
        )

        submission.reviewed_at = (
            datetime.utcnow()
        )

        # =================================================
        # SAVE
        # =================================================

        db.session.commit()


    except Exception as exc:

        db.session.rollback()

        print(
            "Approve submission error:",
            exc,
        )

        flash(
            "Unable to approve submission.",
            "error",
        )

        return redirect(
            url_for(
                "admin.submissions"
            )
        )

    # =====================================================
    # SUCCESS
    # =====================================================
        # =====================================================
    # PUSH NOTIFICATION AFTER SUCCESSFUL CONTENT COMMIT
    # =====================================================
    if (
        content.active
        and content.notification_eligible
    ):

        try:

            category_label = (
                content.category
                .replace("-", " ")
                .replace("_", " ")
                .title()
            )

            notification_title = (
                f"New {category_label} in {zone.name}"
            )

            notification_body = (
                content.title
            )

            notification_url = (
                f"/listing/{content.id}"
            )

            # =================================================
            # DUPLICATE PROTECTION
            # =================================================

            existing_notification = (
                PushNotification.query
                .filter_by(
                    content_item_id=content.id
                )
                .first()
            )

            if existing_notification:

                current_app.logger.info(
                    "[LaC Push] Duplicate notification skipped "
                    "content_id=%s notification_id=%s",
                    content.id,
                    existing_notification.id,
                )

            else:

                # =============================================
                # CREATE HISTORY / OUTBOX RECORD
                # =============================================

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


                # =============================================
                # ATTEMPT DELIVERY
                # =============================================

                push_record.attempts += 1

                push_result = (
                    send_zone_push_notification(

                        zone_id=
                            content.zone_id,

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


                # =============================================
                # SAVE RESULT
                # =============================================

                push_record.total_subscribers = (
                    push_result["total"]
                )

                push_record.sent_count = (
                    push_result["sent"]
                )

                push_record.failed_count = (
                    push_result["failed"]
                )


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
                    "[LaC Push] Approval notification processed "
                    "notification_id=%s "
                    "content_id=%s "
                    "zone_id=%s "
                    "total=%s "
                    "sent=%s "
                    "failed=%s "
                    "status=%s",
                    push_record.id,
                    content.id,
                    content.zone_id,
                    push_record.total_subscribers,
                    push_record.sent_count,
                    push_record.failed_count,
                    push_record.status,
                )


        except Exception as exc:

            db.session.rollback()

            current_app.logger.exception(
                "[LaC Push] Approval notification failed "
                "content_id=%s "
                "zone_id=%s "
                "error=%s",
                content.id,
                content.zone_id,
                exc,
            )



    flash(
        "Submission approved and published.",
        "success",
    )

    return redirect(
        url_for(
            "admin.submissions"
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
