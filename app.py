import os
from datetime import date, datetime
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    abort,
    redirect,
    url_for,
    flash,
)
from flask_migrate import Migrate
from sqlalchemy import or_
import re
from urllib.parse import quote
from models import (
    db,
    Zone,
    AccessPoint,
    ContentItem,
    QRScan,
    PendingSubmission,
    PendingSubmissionImage,
    Category,
)

from admin import admin_bp


# =========================================================
# ENVIRONMENT
# =========================================================

load_dotenv()


# =========================================================
# APP
# =========================================================

app = Flask(__name__)

# ---------------------------------------------------------
# PHONE / WHATSAPP HELPERS
# ---------------------------------------------------------

def normalize_phone_number(value):

    if not value:
        return ""

    digits = re.sub(
        r"\D",
        "",
        value,
    )

    # South African international format
    # 0791234567 -> 27791234567

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

UPLOAD_FOLDER = os.path.join(
    app.root_path,
    "static",
    "uploads",
)

ALLOWED_IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
}

app.config[
    "UPLOAD_FOLDER"
] = UPLOAD_FOLDER

app.config[
    "MAX_CONTENT_LENGTH"
] = 5 * 1024 * 1024


os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True,
)


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


def save_uploaded_image(file):

    if (
        not file
        or not file.filename
    ):
        return None

    if not allowed_image_file(
        file.filename
    ):
        raise ValueError(
            "Only PNG, JPG, JPEG and WEBP images are allowed."
        )

    filename = secure_filename(
        file.filename
    )

    timestamp = datetime.utcnow().strftime(
        "%Y%m%d%H%M%S%f"
    )

    filename = (
        f"{timestamp}_{filename}"
    )

    full_path = os.path.join(
        app.config[
            "UPLOAD_FOLDER"
        ],
        filename,
    )

    file.save(
        full_path
    )

    return url_for(
        "static",
        filename=f"uploads/{filename}",
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


db.init_app(app)

migrate = Migrate(
    app,
    db,
)

app.register_blueprint(
    admin_bp
)


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return render_template(
         "qr_entry.html"
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
    # Hide them only after they expire.
    # -----------------------------------------------------

    query = query.filter(
        or_(
            ContentItem.end_date.is_(None),
            ContentItem.end_date >= today,
        ),
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
# =========================================================
# QR ACCESS POINT
# =========================================================

@app.route("/q/<code>")
def qr_access(code):

    access_point = (
        AccessPoint.query
        .filter_by(
            code=code,
            active=True,
        )
        .first()
    )

    if not access_point:

        return render_template(
            "qr_error.html"
        ), 404


    # -----------------------------------------------------
    # RECORD PHYSICAL QR SCAN
    # -----------------------------------------------------

    scan = QRScan(
        access_point_id=
            access_point.id,

        event_type=
            "scan",

        user_agent=
            request.headers.get(
                "User-Agent",
                "",
            ),
    )

    db.session.add(
        scan
    )

    db.session.commit()


    # -----------------------------------------------------
    # CATEGORY-SPECIFIC QR
    # -----------------------------------------------------

    if (
        access_point.qr_type
        == "category"
        and access_point.default_category
    ):

        category_slug = (
            access_point
            .default_category
        )


        # Make sure the configured category
        # still exists and is active.

        category_record = (
            get_active_category_by_slug(
                category_slug
            )
        )

        if not category_record:
            abort(404)


        # Redirect directly to the category page.
        # User does NOT see the category chooser.

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

    return render_template(
        "access.html",

        zone=
            access_point.zone,

        access_point=
            access_point,

        categories=
            get_active_categories(),
    )

# =========================================================
# CATEGORY PAGE
# =========================================================

@app.route(
    "/q/<code>/<category>"
)
def qr_category(
    code,
    category,
):

    access_point = (
        AccessPoint.query
        .filter_by(
            code=code,
            active=True,
        )
        .first()
    )

    if not access_point:
        abort(404)


    # -----------------------------------------------------
    # VALIDATE DATABASE CATEGORY
    # -----------------------------------------------------

    category_record = (
        get_active_category_by_slug(
            category
        )
    )

    if not category_record:
        abort(404)


    # -----------------------------------------------------
    # GET CONTENT
    # -----------------------------------------------------

    items = get_active_content(
        zone_id=
            access_point.zone_id,

        category_slug=
            category,
    )


    # -----------------------------------------------------
    # RECORD CATEGORY SELECTION
    # -----------------------------------------------------

    category_event = QRScan(
        access_point_id=
            access_point.id,

        event_type=
            "category_view",

        category_selected=
            category,

        user_agent=
            request.headers.get(
                "User-Agent",
                "",
            ),
    )

    db.session.add(
        category_event
    )

    db.session.commit()


    # -----------------------------------------------------
    # DISPLAY CATEGORY
    # -----------------------------------------------------

    return render_template(
       "category.html",
       zone=access_point.zone,
       category=category_record,
       items=items,
       access_point=access_point,
       today=date.today(),
    )

# =========================================================
# PUBLIC LISTING DETAIL
# =========================================================
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
# PUBLIC CONTENT SUBMISSION
# =========================================================
@app.route(
    "/submit",
    methods=["GET", "POST"],
)
def submit_content():

    zones = (
        Zone.query
        .filter_by(active=True)
        .order_by(Zone.name)
        .all()
    )

    categories = get_active_categories()

    if request.method == "POST":

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
        )

        if (
            not zone_id
            or not category_slug
            or not title
            or not submitter_name
            or not submitter_phone
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

        try:

            publish_from = parse_form_date(
                "publish_from"
            )

            event_date = parse_form_date(
                "event_date"
            )

            event_end_date = parse_form_date(
                "event_end_date"
            )

            start_date = parse_form_date(
                "start_date"
            )

            end_date = parse_form_date(
                "end_date"
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
                and publish_from > event_date
            ):

                flash(
                    "Publish From cannot be after Event Date.",
                    "error",
                )

                return render_template(
                    "submit.html",
                    zones=zones,
                    categories=categories,
                )

            if (
                event_end_date
                and event_end_date < event_date
            ):

                flash(
                    "Event End Date cannot be before Event Date.",
                    "error",
                )

                return render_template(
                    "submit.html",
                    zones=zones,
                    categories=categories,
                )

            start_date = None
            end_date = None

        else:

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

            publish_from = None
            event_date = None
            event_end_date = None

        uploaded_images = (
            request.files.getlist(
                "images"
            )
        )

        uploaded_images = [
            image
            for image in uploaded_images
            if image and image.filename
        ]

        if len(uploaded_images) > 3:

            flash(
                "You can upload a maximum of 3 images.",
                "error",
            )

            return render_template(
                "submit.html",
                zones=zones,
                categories=categories,
            )

        submission = PendingSubmission(
            zone_id=zone_id,
            category=category_slug,
            title=title,
            description=description,
            business_name=business_name,
            venue=venue,
            price=price,
            contact=contact,
            submitter_name=submitter_name,
            submitter_email=submitter_email,
            submitter_phone=submitter_phone,
            publish_from=publish_from,
            event_date=event_date,
            event_end_date=event_end_date,
            start_date=start_date,
            end_date=end_date,
            status="pending",
        )

        try:

            db.session.add(
                submission
            )

            db.session.flush()

            for index, uploaded_image in enumerate(
                uploaded_images,
                start=1,
            ):

                image_url = (
                    save_uploaded_image(
                        uploaded_image
                    )
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

        return redirect(
            url_for(
                "submission_success",
                code=submission.tracking_code,
            )
        )

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
    expiry_date = None
    days_remaining = None


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


        # -------------------------------------------------
        # EXPIRY
        # -------------------------------------------------

        if (
            published_content.category
            == "events"
        ):

            expiry_date = (
                published_content.event_end_date
                or published_content.event_date
            )

        else:

            expiry_date = (
                published_content.end_date
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

        expiry_date=
            expiry_date,

        days_remaining=
            days_remaining,
            
        listing_expired=
            listing_expired,
    )
# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return {
        "status": "ok"
    }, 200


# =========================================================
# PUBLIC LISTING DETAIL
# =========================================================


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=True,
    )
