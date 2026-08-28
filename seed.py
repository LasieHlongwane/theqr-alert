from datetime import date

from app import app

from models import (
    db,
    Zone,
    AccessPoint,
    ContentItem,
    Category,
)


with app.app_context():

    # ---------------------------------------
    # Zone
    # ---------------------------------------

    kwamhlanga = Zone.query.filter_by(
        slug="kwamhlanga"
    ).first()

    if not kwamhlanga:

        kwamhlanga = Zone(
            name="KwaMhlanga",
            slug="kwamhlanga",
            active=True,
        )

        db.session.add(kwamhlanga)
        db.session.commit()


    # ---------------------------------------
    # General QR
    # ---------------------------------------

    general_qr = AccessPoint.query.filter_by(
        code="KWM-TAXI-001"
    ).first()

    if not general_qr:

        general_qr = AccessPoint(
            code="KWM-TAXI-001",
            name="KwaMhlanga Taxi Rank",
            zone_id=kwamhlanga.id,
            location_type="taxi_rank",
            qr_type="general",
            default_category=None,
            active=True,
        )

        db.session.add(general_qr)


    # ---------------------------------------
    # Events QR
    # ---------------------------------------

    event_qr = AccessPoint.query.filter_by(
        code="KWM-EVENT-001"
    ).first()

    if not event_qr:

        event_qr = AccessPoint(
            code="KWM-EVENT-001",
            name="KwaMhlanga Entertainment Venue",
            zone_id=kwamhlanga.id,
            location_type="entertainment",
            qr_type="category",
            default_category="events",
            active=True,
        )

        db.session.add(event_qr)

    # ---------------------------------------
# Categories
# ---------------------------------------

        categories = [
          {
           "name": "Events",
           "slug": "events",
           "icon": "🎵",
           "display_order": 1,
          },
          {
           "name": "Specials",
           "slug": "specials",
           "icon": "🛒",
           "display_order": 2,
          },
          {
           "name": "Jobs",
           "slug": "jobs",
           "icon": "💼",
           "display_order": 3,
          },
          {
           "name": "Food",
           "slug": "food",
           "icon": "🍔",
           "display_order": 4,
          },
          {
           "name": "Services",
           "slug": "services",
           "icon": "🛠️",
           "display_order": 5,
          },
          {
           "name": "Transport",
           "slug": "transport",
           "icon": "🚕",
           "display_order": 6,
          },
        ]

        for category_data in categories:

         existing_category = (
          Category.query
          .filter_by(
            slug=category_data["slug"]
          )
          .first()
         )

         if not existing_category:

          category = Category(
            name=category_data["name"],
            slug=category_data["slug"],
            icon=category_data["icon"],
            display_order=category_data[
                "display_order"
            ],
            active=True,
          )

          db.session.add(category)
    # ---------------------------------------
    # Sample Event
    # ---------------------------------------

    sunday_session = ContentItem.query.filter_by(
        title="KwaMhlanga Sunday Session",
        zone_id=kwamhlanga.id,
    ).first()

    if not sunday_session:

        sunday_session = ContentItem(
            zone_id=kwamhlanga.id,
            category="events",
            title="KwaMhlanga Sunday Session",
            description=(
                "Live music, DJs and local artists."
            ),
            business_name="LAC Test Event",
            venue="KwaMhlanga",
            price="R50",

            # Event visibility + event dates
            publish_from=date(
                2026,
                8,
                20,
            ),

            event_date=date(
                2026,
                8,
                30,
            ),

            event_end_date=date(
                2026,
                8,
                30,
            ),

            featured=True,
            active=True,
        )

        db.session.add(
            sunday_session
        )


    # ---------------------------------------
    # Sample Special
    # ---------------------------------------

    grocery_special = ContentItem.query.filter_by(
        title="Weekend Grocery Special",
        zone_id=kwamhlanga.id,
    ).first()

    if not grocery_special:

        grocery_special = ContentItem(
            zone_id=kwamhlanga.id,
            category="specials",
            title="Weekend Grocery Special",
            business_name="Local Supermarket",
            description=(
                "Selected household groceries on special."
            ),

            start_date=date(
                2026,
                8,
                26,
            ),

            end_date=date(
                2026,
                8,
                31,
            ),

            featured=False,
            active=True,
        )

        db.session.add(
            grocery_special
        )


    # ---------------------------------------
    # Sample Job
    # ---------------------------------------

    shop_job = ContentItem.query.filter_by(
        title="Shop Assistant Needed",
        zone_id=kwamhlanga.id,
    ).first()

    if not shop_job:

        shop_job = ContentItem(
            zone_id=kwamhlanga.id,
            category="jobs",
            title="Shop Assistant Needed",
            business_name="Local Retail Store",
            description=(
                "Local retail assistant position available."
            ),

            start_date=date(
                2026,
                8,
                26,
            ),

            end_date=date(
                2026,
                9,
                10,
            ),

            featured=False,
            active=True,
        )

        db.session.add(
            shop_job
        )


    # ---------------------------------------
    # Save Everything
    # ---------------------------------------

    db.session.commit()

    print(
        "LAC test data created successfully."
    )