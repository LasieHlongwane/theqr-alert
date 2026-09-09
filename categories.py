# ============================================================
# KALXA CATEGORY ARCHITECTURE
# ============================================================
#
# BUSINESS_CATEGORIES
# -------------------
# Stable taxonomy used by:
#
# - public submission forms
# - admin forms
# - database classification
# - analytics
# - pricing/workflows
#
#
# CONSUMER_CATEGORIES
# -------------------
# Presentation language used by:
#
# - QR landing pages
# - category cards
# - consumer navigation
# - headings
#
# Consumer wording can change without changing the
# underlying business taxonomy.
# ============================================================


# ============================================================
# STABLE BUSINESS TAXONOMY
# ============================================================

BUSINESS_CATEGORIES = {

    "events": {
        "label": "Events & Entertainment",
    },

    "restaurants": {
        "label": "Restaurant & Food",
    },

    "beauty": {
        "label": "Salon, Barber & Beauty",
    },

    "retail_specials": {
        "label": "Retail & Specials",
    },

    "accommodation": {
        "label": "Accommodation",
    },

    "rentals": {
        "label": "Property Rentals",
    },

    "delivery": {
        "label": "Delivery & Pickup Services",
    },

    "services": {
        "label": "Local Services",
    },

    "jobs": {
        "label": "Jobs & Opportunities",
    },

    "emergency": {
        "label": "Emergency Services",
    },

    "announcements": {
        "label": "Community Announcements",
    },

    "building": {
        "label": "Building & Hardware",
    },

}


# ============================================================
# CONSUMER PRESENTATION
# ============================================================

CONSUMER_CATEGORIES = {

    "events": {

        "title": "WHAT'S ON?",

        "subtitle":
            "Events happening around you",

        "icon": "🎵",

    },


    "restaurants": {

        "title": "HUNGRY?",

        "subtitle":
            "Find something good to eat",

        "icon": "🍔",

    },


    "beauty": {

        "title": "GET FRESH",

        "subtitle":
            "Barbers · Salons · Nails",

        "icon": "💇",

    },


    "retail_specials": {

        "title": "SPECIALS TODAY",

        "subtitle":
            "Deals worth knowing about",

        "icon": "🛒",

    },


    "accommodation": {

        "title": "STAY TONIGHT",

        "subtitle":
            "Find somewhere to stay",

        "icon": "🛏️",

    },


    "rentals": {

        "title": "NEED A ROOM?",

        "subtitle":
            "Rooms · Houses · Rentals",

        "icon": "🏠",

    },


    "delivery": {

        "title": "BRING IT TO ME",

        "subtitle":
            "Delivery · Pickup · Courier",

        "icon": "🛵",

    },


    "services": {

        "title": "NEED A HAND?",

        "subtitle":
            "Find trusted local services",

        "icon": "🛠️",

    },


    "jobs": {

        "title": "LOOKING FOR WORK?",

        "subtitle":
            "Jobs and opportunities nearby",

        "icon": "💼",

    },


    "emergency": {

        "title": "NEED HELP?",

        "subtitle":
            "Important local emergency information",

        "icon": "🚨",

    },


    "announcements": {

        "title": "COMMUNITY",

        "subtitle":
            "Important updates around you",

        "icon": "📢",

    },


    "building": {

        "title": "BUILDING SOMETHING?",

        "subtitle":
            "Hardware · Materials · Builders",

        "icon": "🧱",

    },

}


# ============================================================
# HELPERS
# ============================================================

def get_business_category(category_key):

    return BUSINESS_CATEGORIES.get(
        category_key,
        {
            "label":
                category_key
                .replace("_", " ")
                .title()
        },
    )


def get_consumer_category(category_key):

    business_category = get_business_category(
        category_key
    )

    return CONSUMER_CATEGORIES.get(
        category_key,
        {
            "title":
                business_category["label"],

            "subtitle":
                "Discover what's available around you",

            "icon":
                "📍",
        },
    )


def get_business_category_choices():

    return [
        (
            key,
            config["label"],
        )
        for key, config
        in BUSINESS_CATEGORIES.items()
    ]


# ============================================================
# LEGACY CATEGORY MAPPING
# ============================================================
#
# Temporary compatibility layer for existing Kalxa data.
#
# Old database/category slugs continue to work while the
# application gradually moves toward stable taxonomy keys.
# ============================================================

LEGACY_CATEGORY_MAP = {

    "upcoming-event-🥹🔥":
        "events",

    "local-events":
        "events",

    "events":
        "events",


    "check-out-our-specials":
        "restaurants",

    "foods":
        "restaurants",

    "local-restaurants":
        "restaurants",

    "restaurants":
        "restaurants",


    "beauty-salon":
        "beauty",

    "beauty":
        "beauty",


    "discount-deals":
        "retail_specials",

    "retail_specials":
        "retail_specials",


    "property":
        "rentals",

    "rentals":
        "rentals",


    "transport":
        "delivery",

    "delivery":
        "delivery",


    "services":
        "services",


    "jobs":
        "jobs",


    "build":
        "building",


    "emergency":
        "emergency",


    "announcemnent":
        "announcements",

    "announcements":
        "announcements",

}


def normalize_category(category_key):

    if not category_key:
        return None

    return LEGACY_CATEGORY_MAP.get(
        category_key,
        category_key,
    )
