from datetime import datetime, time
import secrets

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)


db = SQLAlchemy()


# ============================================================
# ORGANIZER
# ============================================================

class Organizer(db.Model):
    """
    A person or organization that submits content to Kalxa.

    This becomes the ownership identity shared between:

        Organizer
            ↓
        PendingSubmission
            ↓
        ContentItem
            ↓
        Kalxa Ticketing

    IMPORTANT:

    Existing Kalxa content may not have an organizer yet.
    For that reason organizer_id is initially nullable on
    PendingSubmission and ContentItem.

    New organizer submissions should always be linked to an
    Organizer account.
    """

    __tablename__ = "organizers"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    # --------------------------------------------------------
    # IDENTITY
    # --------------------------------------------------------

    name = db.Column(
        db.String(150),
        nullable=False,
    )

    business_name = db.Column(
        db.String(200),
        nullable=True,
    )

    email = db.Column(
        db.String(255),
        nullable=True,
        unique=True,
        index=True,
    )

    phone = db.Column(
        db.String(50),
        nullable=True,
        unique=True,
        index=True,
    )

    # --------------------------------------------------------
    # AUTHENTICATION
    # --------------------------------------------------------

    password_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    # --------------------------------------------------------
    # ACCOUNT STATUS
    # --------------------------------------------------------

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    is_verified = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    # --------------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------------

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    pending_submissions = db.relationship(
        "PendingSubmission",
        back_populates="organizer",
        lazy=True,
    )

    content_items = db.relationship(
        "ContentItem",
        back_populates="organizer",
        lazy=True,
    )

    # --------------------------------------------------------
    # PASSWORD HELPERS
    # --------------------------------------------------------

    def set_password(
        self,
        password,
    ):

        self.password_hash = (
            generate_password_hash(
                password
            )
        )


    def check_password(
        self,
        password,
    ):

        if not self.password_hash:
            return False

        return check_password_hash(
            self.password_hash,
            password,
        )


    # --------------------------------------------------------
    # EVENT HELPERS
    # --------------------------------------------------------

    @property
    def event_content_items(self):
        """
        Return this organizer's published event listings.

        We deliberately support both "events" and
        "local-events" while Kalxa's category naming evolves.
        """

        return [
            item
            for item in self.content_items
            if (
                item.category
                in {
                    "events",
                    "local-events",
                }
            )
        ]


    @property
    def has_event_listing(self):

        return bool(
            self.event_content_items
        )


    def __repr__(self):

        return (
            f"<Organizer "
            f"id={self.id} "
            f"name={self.name}>"
        )


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

    image_url = db.Column(
        db.String(500),
        nullable=True,
    )

    image_url_2 = db.Column(
        db.String(500),
        nullable=True,
    )

    image_url_3 = db.Column(
        db.String(500),
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
# ZONE CATEGORY APPEARANCE
# ============================================================

class ZoneCategoryAppearance(db.Model):

    __tablename__ = "zone_category_appearances"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "zones.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    category_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "categories.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    image_url = db.Column(
        db.String(500),
        nullable=True,
    )

    image_url_2 = db.Column(
        db.String(500),
        nullable=True,
    )

    image_url_3 = db.Column(
        db.String(500),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    zone = db.relationship(
        "Zone",
        backref=db.backref(
            "category_appearances",
            lazy=True,
            cascade="all, delete-orphan",
        ),
    )

    category = db.relationship(
        "Category",
        backref=db.backref(
            "zone_appearances",
            lazy=True,
            cascade="all, delete-orphan",
        ),
    )

    __table_args__ = (

        db.UniqueConstraint(
            "zone_id",
            "category_id",
            name="uq_zone_category_appearance",
        ),

    )

    @property
    def images(self):

        return [
            image
            for image in [
                self.image_url,
                self.image_url_2,
                self.image_url_3,
            ]
            if image
        ]

    def __repr__(self):

        return (
            f"<ZoneCategoryAppearance "
            f"zone_id={self.zone_id} "
            f"category_id={self.category_id}>"
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

    # ========================================================
    # ORGANIZER OWNERSHIP
    # ========================================================

    organizer_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "organizers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    organizer = db.relationship(
        "Organizer",
        back_populates="content_items",
    )


    # ========================================================
    # CATEGORY / CONTENT CLASSIFICATION
    # ========================================================

    category = db.Column(
        db.String(50),
        nullable=False,
    )

    content_type = db.Column(
        db.String(60),
        nullable=True,
        index=True,
    )

    listing_level = db.Column(
        db.String(30),
        nullable=False,
        default="discovery",
        index=True,
    )

    ownership_status = db.Column(
        db.String(30),
        nullable=False,
        default="unclaimed",
        index=True,
    )

    is_verified = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )


    # ========================================================
    # LIFETIME / AVAILABILITY
    # ========================================================

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


    # ========================================================
    # NOTIFICATION ELIGIBILITY
    # ========================================================

    notification_eligible = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )


    # ========================================================
    # BASIC LISTING INFORMATION
    # ========================================================

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


    # ========================================================
    # BUSINESS FEATURES
    # ========================================================

    price = db.Column(
        db.String(50),
        nullable=True,
    )

    contact = db.Column(
        db.String(100),
        nullable=True,
    )

    opening_hours = db.Column(
        db.String(255),
        nullable=True,
    )

    whatsapp_number = db.Column(
        db.String(50),
        nullable=True,
    )

    directions_url = db.Column(
        db.String(500),
        nullable=True,
    )

    ticket_url = db.Column(
        db.String(500),
        nullable=True,
    )

    menu_highlights = db.Column(
        db.Text,
        nullable=True,
    )

    special_offer = db.Column(
        db.Text,
        nullable=True,
    )


    # ========================================================
    # IMAGES
    # ========================================================

    image_url = db.Column(
        db.String(500),
        nullable=True,
    )

    image_url_2 = db.Column(
        db.String(500),
        nullable=True,
    )

    image_url_3 = db.Column(
        db.String(500),
        nullable=True,
    )


    # ========================================================
    # COMMERCIAL / MONETIZATION
    # ========================================================

    pricing_model = db.Column(
        db.String(30),
        nullable=True,
        index=True,
    )

    commercial_duration_days = db.Column(
        db.Integer,
        nullable=True,
    )

    commercial_starts_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    commercial_expires_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )


    # ========================================================
    # PAYMENT
    # ========================================================

    payment_status = db.Column(
        db.String(30),
        nullable=False,
        default="unpaid",
        index=True,
    )

    amount_due = db.Column(
        db.Numeric(
            10,
            2,
        ),
        nullable=True,
    )

    amount_paid = db.Column(
        db.Numeric(
            10,
            2,
        ),
        nullable=True,
    )

    payment_reference = db.Column(
        db.String(150),
        nullable=True,
        index=True,
    )

    paid_at = db.Column(
        db.DateTime,
        nullable=True,
    )


    # ========================================================
    # SPONSORED VISIBILITY
    # ========================================================

    is_sponsored = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    sponsorship_status = db.Column(
        db.String(30),
        nullable=False,
        default="inactive",
        index=True,
    )

    sponsored_duration_days = db.Column(
        db.Integer,
        nullable=True,
    )

    sponsorship_amount_due = db.Column(
        db.Numeric(
            10,
            2,
        ),
        nullable=True,
    )

    sponsored_starts_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    sponsored_expires_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    sponsored_priority = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        index=True,
    )

    sponsorship_reference = db.Column(
        db.String(150),
        nullable=True,
        index=True,
    )


    # ========================================================
    # ARCHIVING
    # ========================================================

    archived = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    archived_at = db.Column(
        db.DateTime,
        nullable=True,
    )


    # ========================================================
    # GENERAL DATES
    # ========================================================

    start_date = db.Column(
        db.Date,
        nullable=True,
    )

    start_time = db.Column(
        db.Time,
        nullable=True,
    )

    end_date = db.Column(
        db.Date,
        nullable=True,
    )

    end_time = db.Column(
        db.Time,
        nullable=True,
    )


    # ========================================================
    # EVENT-SPECIFIC DATES
    # ========================================================

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


    # ========================================================
    # DISPLAY / STATUS
    # ========================================================

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


    # ========================================================
    # RELATIONSHIPS
    # ========================================================

    claims = db.relationship(
        "ListingClaim",
        back_populates="content_item",
        lazy=True,
        cascade="all, delete-orphan",
    )


    # ========================================================
    # ORGANIZER HELPERS
    # ========================================================

    @property
    def has_organizer(self):

        return (
            self.organizer_id
            is not None
        )


    @property
    def is_event_listing(self):

        return (
            self.category
            in {
                "events",
                "local-events",
            }
        )


    # ========================================================
    # LIFECYCLE HELPERS
    # ========================================================

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


    # ========================================================
    # LISTING LEVEL HELPERS
    # ========================================================

    def can_use_business_features(self):

        return self.listing_level in {
            "business",
            "promotion",
        }


    def can_use_promotion_features(self):

        return (
            self.listing_level
            == "promotion"
        )


    # ========================================================
    # CLAIM HELPERS
    # ========================================================

    def can_be_claimed(self):

        return (
            self.listing_level
            == "discovery"
            and self.ownership_status
            == "unclaimed"
        )


    def has_pending_claim(self):

        return any(
            claim.status == "pending"
            for claim in self.claims
        )


    # ========================================================
    # CAMPAIGN HELPERS
    # ========================================================

    def has_campaign_dates(self):

        return bool(
            self.start_date
            or self.event_date
            or self.end_date
            or self.start_time
            or self.end_time
        )


    def get_campaign_target_date(self):

        canonical_category = (
            normalize_category(
                self.category
            )
        )


        if canonical_category == "events":

            return (
                self.event_date
                or self.start_date
            )


        return self.start_date


    def get_campaign_start_datetime(self):

        target_date = (
            self.get_campaign_target_date()
        )


        if not target_date:
            return None


        effective_time = (
            self.start_time
            or time.min
        )


        return datetime.combine(
            target_date,
            effective_time,
        )


    def get_campaign_end_datetime(self):

        if not self.end_date:
            return None


        effective_time = (
            self.end_time
            or time.max
        )


        return datetime.combine(
            self.end_date,
            effective_time,
        )


# ============================================================
# LISTING CLAIM
# ============================================================

class ListingClaim(db.Model):

    __tablename__ = "listing_claims"

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

    claimant_name = db.Column(
        db.String(120),
        nullable=False,
    )

    business_name = db.Column(
        db.String(200),
        nullable=False,
    )

    phone = db.Column(
        db.String(50),
        nullable=False,
    )

    email = db.Column(
        db.String(255),
        nullable=True,
    )

    proof_notes = db.Column(
        db.Text,
        nullable=True,
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default="pending",
        index=True,
    )

    admin_notes = db.Column(
        db.Text,
        nullable=True,
    )

    reviewed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    content_item = db.relationship(
        "ContentItem",
        back_populates="claims",
    )

    @property
    def is_pending(self):

        return (
            self.status
            == "pending"
        )


    @property
    def is_approved(self):

        return (
            self.status
            == "approved"
        )


    @property
    def is_rejected(self):

        return (
            self.status
            == "rejected"
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


    # ========================================================
    # ORGANIZER OWNERSHIP
    # ========================================================

    organizer_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "organizers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )


    category = db.Column(
        db.String(100),
        nullable=False,
        index=True,
    )


    # --------------------------------------------------------
    # CONTENT WORKFLOW
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
    # COMMERCIAL PACKAGE
    # --------------------------------------------------------

    pricing_model = db.Column(
        db.String(30),
        nullable=True,
        index=True,
    )

    commercial_duration_days = db.Column(
        db.Integer,
        nullable=True,
    )

    amount_due = db.Column(
        db.Numeric(
            10,
            2,
        ),
        nullable=True,
    )

    payment_status = db.Column(
        db.String(30),
        nullable=False,
        default="unpaid",
        index=True,
    )


    # --------------------------------------------------------
    # REQUESTED DISTRIBUTION
    # --------------------------------------------------------

    distribution_zone_ids = db.Column(
        db.JSON,
        nullable=True,
        default=list,
    )


    # --------------------------------------------------------
    # YOCO
    # --------------------------------------------------------

    yoco_checkout_id = db.Column(
        db.String(150),
        nullable=True,
        unique=True,
        index=True,
    )

    yoco_payment_id = db.Column(
        db.String(150),
        nullable=True,
        unique=True,
        index=True,
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


    # --------------------------------------------------------
    # PUBLIC CONTACT
    # --------------------------------------------------------

    contact = db.Column(
        db.String(100),
        nullable=True,
    )

    whatsapp_number = db.Column(
        db.String(50),
        nullable=True,
    )

    directions_url = db.Column(
        db.String(500),
        nullable=True,
    )

    ticket_url = db.Column(
        db.String(500),
        nullable=True,
    )


    # --------------------------------------------------------
    # IMAGE
    # --------------------------------------------------------

    image_url = db.Column(
        db.String(500),
        nullable=True,
    )


    # --------------------------------------------------------
    # SUBMITTER SNAPSHOT
    # --------------------------------------------------------
    #
    # Keep these fields.
    #
    # organizer_id identifies the account.
    #
    # These fields preserve the information that was entered
    # at the moment the listing was submitted.
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
    # GENERAL VALIDITY
    # --------------------------------------------------------

    start_date = db.Column(
        db.Date,
        nullable=True,
    )

    end_date = db.Column(
        db.Date,
        nullable=True,
    )

    start_time = db.Column(
        db.Time,
        nullable=True,
    )

    end_time = db.Column(
        db.Time,
        nullable=True,
    )


    # --------------------------------------------------------
    # MODERATION
    # --------------------------------------------------------

    status = db.Column(
        db.String(30),
        nullable=False,
        default="pending",
        index=True,
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
        db.ForeignKey(
            "content_items.id"
        ),
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


    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    zone = db.relationship(
        "Zone",
        backref="pending_submissions",
    )

    organizer = db.relationship(
        "Organizer",
        back_populates="pending_submissions",
    )

    published_content = db.relationship(
        "ContentItem",
        foreign_keys=[
            published_content_id,
        ],
    )


    # --------------------------------------------------------
    # HELPERS
    # --------------------------------------------------------

    @property
    def is_event_submission(self):

        return (
            self.category
            in {
                "events",
                "local-events",
            }
        )


    @property
    def belongs_to_organizer(self):

        return (
            self.organizer_id
            is not None
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
        ),
    )


# ============================================================
# CONTENT REMINDER
# ============================================================

class ContentReminder(db.Model):

    __tablename__ = "content_reminders"

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

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "zones.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    access_point_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "access_points.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    push_subscriber_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "push_subscribers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    reminder_type = db.Column(
        db.String(30),
        nullable=False,
        default="before_start",
        index=True,
    )

    reminder_minutes_before = db.Column(
        db.Integer,
        nullable=False,
        default=60,
    )

    scheduled_for = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default="pending",
        index=True,
    )

    processing_started_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    retry_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    next_retry_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True,
    )

    last_attempt_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    last_error = db.Column(
        db.Text,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.now(),
        index=True,
    )

    sent_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    content_item = db.relationship(
        "ContentItem",
        backref=db.backref(
            "reminders",
            lazy=True,
            cascade="all, delete-orphan",
        ),
    )

    zone = db.relationship(
        "Zone",
        backref=db.backref(
            "content_reminders",
            lazy=True,
        ),
    )

    access_point = db.relationship(
        "AccessPoint",
        backref=db.backref(
            "content_reminders",
            lazy=True,
        ),
    )

    push_subscriber = db.relationship(
        "PushSubscriber",
        backref=db.backref(
            "content_reminders",
            lazy=True,
        ),
    )

    def __repr__(self):

        return (
            f"<ContentReminder "
            f"id={self.id} "
            f"content_item_id={self.content_item_id} "
            f"push_subscriber_id={self.push_subscriber_id} "
            f"status={self.status}>"
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
        db.ForeignKey(
            "access_points.id"
        ),
        nullable=False,
    )

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
            order_by=(
                "ContentImage.display_order"
            ),
        ),
    )


# ============================================================
# PUSH SUBSCRIBER
# ============================================================

class PushSubscriber(db.Model):

    __tablename__ = "push_subscribers"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "zones.id"
        ),
        nullable=False,
        index=True,
    )

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

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

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

    zone = db.relationship(
        "Zone",
        backref=db.backref(
            "push_subscribers",
            lazy=True,
        ),
    )

    notification_preferences = db.relationship(
        "PushSubscriberPreference",
        back_populates="subscriber",
        cascade="all, delete-orphan",
        lazy=True,
    )

    def __repr__(self):

        return (
            f"<PushSubscriber "
            f"id={self.id} "
            f"zone_id={self.zone_id} "
            f"active={self.active}>"
        )


# ============================================================
# PUSH SUBSCRIBER PREFERENCE
# ============================================================

class PushSubscriberPreference(db.Model):

    __tablename__ = (
        "push_subscriber_preferences"
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    subscriber_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "push_subscribers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    category = db.Column(
        db.String(100),
        nullable=False,
        index=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.now(),
    )

    subscriber = db.relationship(
        "PushSubscriber",
        back_populates=(
            "notification_preferences"
        ),
    )

    __table_args__ = (

        db.UniqueConstraint(
            "subscriber_id",
            "category",
            name=(
                "uq_push_subscriber_category"
            ),
        ),

    )

    def __repr__(self):

        return (
            "<PushSubscriberPreference "
            f"subscriber_id="
            f"{self.subscriber_id} "
            f"category={self.category}>"
        )


# ============================================================
# PUSH NOTIFICATION
# ============================================================

class PushNotification(db.Model):

    __tablename__ = "push_notifications"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    content_item_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "content_items.id"
        ),
        nullable=True,
        index=True,
    )

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "zones.id"
        ),
        nullable=False,
        index=True,
    )

    title = db.Column(
        db.String(200),
        nullable=False,
    )

    body = db.Column(
        db.String(500),
        nullable=False,
    )

    target_url = db.Column(
        db.String(500),
        nullable=False,
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default="pending",
        index=True,
    )

    total_subscribers = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    sent_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    failed_count = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    attempts = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    __table_args__ = (

        db.UniqueConstraint(
            "content_item_id",
            name=(
                "uq_push_notification_content_item"
            ),
        ),

    )

    last_error = db.Column(
        db.Text,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.now(),
    )

    sent_at = db.Column(
        db.DateTime,
        nullable=True,
    )


# ============================================================
# ENGAGEMENT EVENT
# ============================================================

class EngagementEvent(db.Model):

    __tablename__ = "engagement_events"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    event_type = db.Column(
        db.String(50),
        nullable=False,
        index=True,
    )

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "zones.id"
        ),
        nullable=True,
        index=True,
    )

    access_point_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "access_points.id"
        ),
        nullable=True,
        index=True,
    )

    content_item_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "content_items.id"
        ),
        nullable=True,
        index=True,
    )

    category = db.Column(
        db.String(100),
        nullable=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.now(),
        index=True,
    )

    zone = db.relationship(
        "Zone",
        backref=db.backref(
            "engagement_events",
            lazy=True,
        ),
    )

    access_point = db.relationship(
        "AccessPoint",
        backref=db.backref(
            "engagement_events",
            lazy=True,
        ),
    )

    content_item = db.relationship(
        "ContentItem",
        backref=db.backref(
            "engagement_events",
            lazy=True,
        ),
    )


# ============================================================
# CONTENT DISTRIBUTION ZONE
# ============================================================

class ContentDistributionZone(db.Model):

    __tablename__ = (
        "content_distribution_zones"
    )

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

    zone_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "zones.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    content_item = db.relationship(
        "ContentItem",
        backref=db.backref(
            "distribution_zone_links",
            lazy=True,
            cascade="all, delete-orphan",
        ),
    )

    zone = db.relationship(
        "Zone",
        backref=db.backref(
            "distributed_content_links",
            lazy=True,
        ),
    )

    __table_args__ = (

        db.UniqueConstraint(
            "content_item_id",
            "zone_id",
            name=(
                "uq_content_item_distribution_zone"
            ),
        ),

    )

    def __repr__(self):

        return (
            "<ContentDistributionZone "
            f"content_item_id="
            f"{self.content_item_id} "
            f"zone_id={self.zone_id}>"
        )
