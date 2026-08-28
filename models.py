
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
import secrets

db = SQLAlchemy()

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
    
class Zone(db.Model):
    __tablename__ = "zones"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True)

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


class AccessPoint(db.Model):
    __tablename__ = "access_points"

    id = db.Column(db.Integer, primary_key=True)

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

    category = db.Column(
        db.String(50),
        nullable=False,
    )

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

    archived = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

    archived_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    # General validity window
    start_date = db.Column(
        db.Date,
        nullable=True,
    )

    end_date = db.Column(
        db.Date,
        nullable=True,
    )

    # Event-specific dates
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


from datetime import datetime


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
        nullable=False,
    )

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

    start_date = db.Column(
        db.Date,
        nullable=True,
    )

    end_date = db.Column(
        db.Date,
        nullable=True,
    )

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
            order_by="PendingSubmissionImage.display_order",
        ),
    )

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
            order_by="ContentImage.display_order",
        ),
    )