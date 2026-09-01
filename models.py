
from datetime import datetime
import secrets

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


# ============================================================
# CATEGORY
# ============================================================

class Category(db.Model):

    __tablename__ = "categories"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(100),
        nullable=False,
    )

    slug = db.Column(
        db.String(100),
        nullable=False,
        unique=True,
        index=True,
    )

    icon = db.Column(
        db.String(20),
        nullable=True,
    )

    display_order = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self):
        return (
            f"<Category "
            f"{self.slug}>"
        )


# ============================================================
# ZONE
# ============================================================

class Zone(db.Model):

    __tablename__ = "zones"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(100),
        nullable=False,
    )

    slug = db.Column(
        db.String(100),
        unique=True,
        nullable=False,
    )

    active = db.Column(
        db.Boolean,
        default=True,
    )

    access_points = db.relationship(
        "AccessPoint",
        backref="zone",
        lazy=True,
    )

    content_items = db.relationship(
        "ContentItem",
        backref="zone",
        lazy=True,
    )


# ============================================================
# ACCESS POINT
# ============================================================

class AccessPoint(db.Model):

    __tablename__ = "access_points"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    code = db.Column(
        db.String(100),
        unique=True,
        nullable=False,
    )

    name = db.Column(
        db.String(150),
        nullable=False,
    )

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey("zones.id"),
        nullable=False,
    )

    location_type = db.Column(
        db.String(50),
        nullable=True,
    )

    qr_type = db.Column(
        db.String(30),
        default="general",
    )

    default_category = db.Column(
        db.String(50),
        nullable=True,
    )

    partner_name = db.Column(
        db.String(150),
        nullable=True,
    )

    active = db.Column(
        db.Boolean,
        default=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
    )


# ============================================================
# CONTENT ITEM
# ============================================================

class ContentItem(db.Model):

    __tablename__ = "content_items"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey("zones.id"),
        nullable=False,
    )

    # --------------------------------------------------------
    # CATEGORY
    #
    # Examples:
    # property
    # events
    # discount-deals
    # local-restaurants
    # jobs
    # services
    # --------------------------------------------------------

    category = db.Column(
        db.String(50),
        nullable=False,
    )

    # --------------------------------------------------------
    # CONTENT TYPE
    #
    # Describes WHAT the listing is inside its category.
    #
    # Examples:
    #
    # Property:
    # room
    # rental
    # property_sale
    # hotel
    # accommodation_special
    #
    # Food:
    # restaurant
    # daily_special
    # weekend_special
    #
    # Opportunities:
    # job
    # learnership
    # internship
    # tender
    #
    # Existing/legacy records may initially have NULL.
    # --------------------------------------------------------

    content_type = db.Column(
        db.String(60),
        nullable=True,
        index=True,
    )

    # --------------------------------------------------------
    # LIFETIME TYPE
    #
    # Controls HOW LONG the listing should remain relevant.
    #
    # Supported values:
    #
    # time_specific
    # until_unavailable
    # ongoing
    # recurring
    #
    # NULL is temporarily allowed for legacy records.
    # --------------------------------------------------------

    lifetime_type = db.Column(
        db.String(30),
        nullable=True,
        index=True,
    )

    # --------------------------------------------------------
    # AVAILABILITY STATUS
    #
    # Current lifecycle state of the listing.
    #
    # Common values:
    #
    # available
    # taken
    # sold
    # filled
    # closed
    # expired
    #
    # We default NEW records to available.
    # --------------------------------------------------------

    availability_status = db.Column(
        db.String(30),
        nullable=False,
        default="available",
        index=True,
    )

    # --------------------------------------------------------
    # NOTIFICATION ELIGIBILITY
    #
    # True:
    # This listing may later trigger category notifications.
    #
    # False:
    # Normally discovery/search only.
    #
    # Default False is intentional while push notifications
    # are not yet implemented.
    # --------------------------------------------------------

    notification_eligible = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    # --------------------------------------------------------
    # LISTING INFORMATION
    # --------------------------------------------------------

    title = db.Column(
        db.String(200),
        nullable=False,
    )

    description = db.Column(
        db.Text,
        nullable=True,
    )

    business_name = db.Column(
        db.String(150),
        nullable=True,
    )

    venue = db.Column(
        db.String(150),
        nullable=True,
    )

    price = db.Column(
        db.String(50),
        nullable=True,
    )

    contact = db.Column(
        db.String(100),
        nullable=True,
    )

    image_url = db.Column(
        db.String(500),
        nullable=True,
    )

    # --------------------------------------------------------
    # ARCHIVING
    # --------------------------------------------------------

    archived = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    archived_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    # --------------------------------------------------------
    # GENERAL VALIDITY WINDOW
    #
    # Used primarily by time-specific non-event listings.
    #
    # Examples:
    # grocery specials
    # food specials
    # temporary promotions
    # --------------------------------------------------------

    start_date = db.Column(
        db.Date,
        nullable=True,
    )

    end_date = db.Column(
        db.Date,
        nullable=True,
    )

    # --------------------------------------------------------
    # EVENT-SPECIFIC DATES
    # --------------------------------------------------------

    publish_from = db.Column(
        db.Date,
        nullable=True,
    )

    event_date = db.Column(
        db.Date,
        nullable=True,
    )

    event_end_date = db.Column(
        db.Date,
        nullable=True,
    )

    # --------------------------------------------------------
    # DISPLAY / STATUS
    # --------------------------------------------------------

    featured = db.Column(
        db.Boolean,
        default=False,
    )

    active = db.Column(
        db.Boolean,
        default=True,
    )

    view_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
    )

    # --------------------------------------------------------
    # LIFECYCLE HELPERS
    # --------------------------------------------------------

    @property
    def is_time_specific(self):
        return (
            self.lifetime_type
            == "time_specific"
        )

    @property
    def is_until_unavailable(self):
        return (
            self.lifetime_type
            == "until_unavailable"
        )

    @property
    def is_ongoing(self):
        return (
            self.lifetime_type
            == "ongoing"
        )

    @property
    def is_recurring(self):
        return (
            self.lifetime_type
            == "recurring"
        )

    @property
    def is_available(self):
        return (
            self.availability_status
            == "available"
        )


# ============================================================
# PENDING SUBMISSION
# ============================================================

class PendingSubmission(db.Model):

    __tablename__ = "pending_submissions"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey("zones.id"),
        nullable=False,
    )

    category = db.Column(
        db.String(50),
        nullable=False,
    )

    # --------------------------------------------------------
    # NEW WORKFLOW FIELDS
    #
    # These fields are copied into ContentItem when an admin
    # approves the submission.
    # --------------------------------------------------------

    content_type = db.Column(
        db.String(60),
        nullable=True,
        index=True,
    )

    lifetime_type = db.Column(
        db.String(30),
        nullable=True,
        index=True,
    )

    availability_status = db.Column(
        db.String(30),
        nullable=False,
        default="available",
        index=True,
    )

    notification_eligible = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    # --------------------------------------------------------
    # LISTING INFORMATION
    # --------------------------------------------------------

    title = db.Column(
        db.String(200),
        nullable=False,
    )

    description = db.Column(
        db.Text,
        nullable=True,
    )

    business_name = db.Column(
        db.String(150),
        nullable=True,
    )

    venue = db.Column(
        db.String(150),
        nullable=True,
    )

    price = db.Column(
        db.String(50),
        nullable=True,
    )

    contact = db.Column(
        db.String(100),
        nullable=True,
    )

    image_url = db.Column(
        db.String(500),
        nullable=True,
    )

    # --------------------------------------------------------
    # SUBMITTER INFORMATION
    # --------------------------------------------------------

    submitter_name = db.Column(
        db.String(150),
        nullable=False,
    )

    submitter_email = db.Column(
        db.String(200),
        nullable=True,
    )

    submitter_phone = db.Column(
        db.String(100),
        nullable=True,
    )

    # --------------------------------------------------------
    # EVENT DATES
    # --------------------------------------------------------

    publish_from = db.Column(
        db.Date,
        nullable=True,
    )

    event_date = db.Column(
        db.Date,
        nullable=True,
    )

    event_end_date = db.Column(
        db.Date,
        nullable=True,
    )

    # --------------------------------------------------------
    # GENERAL VALIDITY DATES
    # --------------------------------------------------------

    start_date = db.Column(
        db.Date,
        nullable=True,
    )

    end_date = db.Column(
        db.Date,
        nullable=True,
    )

    # --------------------------------------------------------
    # SUBMISSION STATUS
    # --------------------------------------------------------

    status = db.Column(
        db.String(30),
        nullable=False,
        default="pending",
    )

    tracking_code = db.Column(
        db.String(40),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: (
            "LAC-"
            + secrets.token_hex(4).upper()
        ),
    )

    admin_notes = db.Column(
        db.Text,
        nullable=True,
    )

    published_content_id = db.Column(
        db.Integer,
        db.ForeignKey("content_items.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    reviewed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    zone = db.relationship(
        "Zone",
        backref="pending_submissions",
    )


# ============================================================
# PENDING SUBMISSION IMAGE
# ============================================================

class PendingSubmissionImage(db.Model):

    __tablename__ = "pending_submission_images"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    submission_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "pending_submissions.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    image_url = db.Column(
        db.String(500),
        nullable=False,
    )

    display_order = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    submission = db.relationship(
        "PendingSubmission",
        backref=db.backref(
            "images",
            lazy=True,
            cascade="all, delete-orphan",
            order_by=
                "PendingSubmissionImage.display_order",
        ),
    )


# ============================================================
# QR SCAN
# ============================================================

class QRScan(db.Model):

    __tablename__ = "qr_scans"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    access_point_id = db.Column(
        db.Integer,
        db.ForeignKey("access_points.id"),
        nullable=False,
    )

    # scan = physical QR opened
    # category_view = category selected

    event_type = db.Column(
        db.String(30),
        nullable=False,
        default="scan",
    )

    category_selected = db.Column(
        db.String(50),
        nullable=True,
    )

    user_agent = db.Column(
        db.String(500),
        nullable=True,
    )

    scanned_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True,
    )

    access_point = db.relationship(
        "AccessPoint",
        backref="scans",
    )


# ============================================================
# CONTENT IMAGE
# ============================================================

class ContentImage(db.Model):

    __tablename__ = "content_images"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    content_item_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "content_items.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    image_url = db.Column(
        db.String(500),
        nullable=False,
    )

    display_order = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    content_item = db.relationship(
        "ContentItem",
        backref=db.backref(
            "images",
            lazy=True,
            cascade="all, delete-orphan",
            order_by=
                "ContentImage.display_order",
        ),
    )

# =========================================================
# PUSH SUBSCRIBERS
# =========================================================

class PushSubscriber(db.Model):

    __tablename__ = "push_subscribers"


    id = db.Column(
        db.Integer,
        primary_key=True,
    )


    # -----------------------------------------------------
    # ZONE
    # -----------------------------------------------------

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey("zones.id"),
        nullable=False,
        index=True,
    )


    # -----------------------------------------------------
    # BROWSER PUSH SUBSCRIPTION
    # -----------------------------------------------------

    endpoint = db.Column(
        db.Text,
        nullable=False,
        unique=True,
    )


    p256dh = db.Column(
        db.Text,
        nullable=False,
    )


    auth_key = db.Column(
        db.Text,
        nullable=False,
    )


    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )


    # -----------------------------------------------------
    # TIMESTAMPS
    # -----------------------------------------------------

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.now(),
    )


    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )


    # -----------------------------------------------------
    # RELATIONSHIP
    # -----------------------------------------------------

    zone = db.relationship(
        "Zone",
        backref=db.backref(
            "push_subscribers",
            lazy=True,
        ),
    )


    def __repr__(self):

        return (
            f"<PushSubscriber "
            f"id={self.id} "
            f"zone_id={self.zone_id} "
            f"active={self.active}>"
        )
