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
# LEGACY CATEGORY MAPPING
# ============================================================
#
# Temporary compatibility layer for existing Kalxa data.
#
# IMPORTANT:
#
# Existing production ContentItem.category values must
# continue to work while Kalxa moves toward the new stable
# business taxonomy.
#
# Example:
#
#     foods
#     check-out-our-specials
#     local-restaurants
#
# all belong to:
#
#     restaurants
#
# ============================================================

LEGACY_CATEGORY_MAP = {

    # ========================================================
    # EVENTS
    # ========================================================

    "upcoming-event-🥹🔥":
        "events",

    "local-events":
        "events",

    "events":
        "events",


    # ========================================================
    # RESTAURANTS / FOOD
    # ========================================================

    "check-out-our-specials":
        "restaurants",

    "foods":
        "restaurants",

    "local-restaurants":
        "restaurants",

    "restaurants":
        "restaurants",


    # ========================================================
    # BEAUTY
    # ========================================================

    "beauty-salon":
        "beauty",

    "beauty":
        "beauty",


    # ========================================================
    # RETAIL / STORE / DRINK SPECIALS
    # ========================================================

    "discount-deals":
        "retail_specials",

    "liqour-specials🔥":
        "retail_specials",

    "retail_specials":
        "retail_specials",


    # ========================================================
    # ACCOMMODATION
    #
    # No known older production slug currently needs to be
    # mapped here.
    # ========================================================

    "accommodation":
        "accommodation",


    # ========================================================
    # PROPERTY RENTALS
    # ========================================================

    "property":
        "rentals",

    "rentals":
        "rentals",


    # ========================================================
    # DELIVERY / PICKUP
    # ========================================================

    "transport":
        "delivery",

    "delivery":
        "delivery",


    # ========================================================
    # LOCAL SERVICES
    # ========================================================

    "services":
        "services",


    # ========================================================
    # JOBS
    # ========================================================

    "jobs":
        "jobs",


    # ========================================================
    # BUILDING / HARDWARE
    # ========================================================

    "build":
        "building",

    "building":
        "building",


    # ========================================================
    # EMERGENCY
    # ========================================================

    "emergency":
        "emergency",


    # ========================================================
    # COMMUNITY ANNOUNCEMENTS
    #
    # Keep the misspelled production slug for compatibility.
    # ========================================================

    "announcemnent":
        "announcements",

    "announcements":
        "announcements",

}


# ============================================================
# CATEGORY NORMALIZATION
# ============================================================

def normalize_category(category_key):
    """
    Convert a legacy or canonical category value into the
    stable Kalxa business taxonomy key.

    Examples:

        foods
            -> restaurants

        check-out-our-specials
            -> restaurants

        upcoming-event-🥹🔥
            -> events

        discount-deals
            -> retail_specials

        restaurants
            -> restaurants
    """

    if not category_key:
        return None


    # --------------------------------------------------------
    # Normalize incoming values consistently.
    #
    # This protects against accidental whitespace or casing
    # differences coming from forms/routes/database values.
    # --------------------------------------------------------

    cleaned_category = (
        str(category_key)
        .strip()
        .lower()
    )


    if not cleaned_category:
        return None


    return LEGACY_CATEGORY_MAP.get(
        cleaned_category,
        cleaned_category,
    )


# ============================================================
# CATEGORY ALIASES
# ============================================================

def get_category_aliases(category_key):
    """
    Return every legacy + canonical category value belonging
    to the same Kalxa business taxonomy category.

    Example:

        get_category_aliases("restaurants")

    returns:

        {
            "restaurants",
            "foods",
            "check-out-our-specials",
            "local-restaurants",
        }

    This allows old ContentItem rows and new canonical rows
    to appear together without immediately migrating the
    production database.
    """

    canonical_category = normalize_category(
        category_key
    )


    if not canonical_category:
        return set()


    aliases = set()


    # --------------------------------------------------------
    # Find every legacy key that resolves to this canonical
    # category.
    # --------------------------------------------------------

    for legacy_key, mapped_category in LEGACY_CATEGORY_MAP.items():

        mapped_canonical = normalize_category(
            mapped_category
        )


        if mapped_canonical == canonical_category:

            aliases.add(
                legacy_key
            )


    # --------------------------------------------------------
    # Always include canonical key itself.
    #
    # This supports new ContentItem rows already using the
    # stable taxonomy.
    # --------------------------------------------------------

    aliases.add(
        canonical_category
    )


    return aliases


# ============================================================
# BUSINESS CATEGORY HELPER
# ============================================================

def get_business_category(category_key):
    """
    Return the business-facing category configuration.
    """

    canonical_category = normalize_category(
        category_key
    )


    if not canonical_category:

        return {
            "label": "Category",
        }


    return BUSINESS_CATEGORIES.get(
        canonical_category,
        {
            "label":
                canonical_category
                .replace("_", " ")
                .title(),
        },
    )


# ============================================================
# CONSUMER CATEGORY HELPER
# ============================================================

def get_consumer_category(category_key):
    """
    Return consumer-facing Kalxa presentation information.

    Legacy category values are normalized first.

    Example:

        foods

    will therefore receive the same consumer presentation as:

        restaurants
    """

    canonical_category = normalize_category(
        category_key
    )


    business_category = get_business_category(
        canonical_category
    )


    return CONSUMER_CATEGORIES.get(
        canonical_category,
        {
            "title":
                business_category["label"],

            "subtitle":
                "Discover what's available around you",

            "icon":
                "📍",
        },
    )


# ============================================================
# BUSINESS CATEGORY CHOICES
# ============================================================

def get_business_category_choices():
    """
    Return stable taxonomy choices for forms.

    Example:

        [
            ("events", "Events & Entertainment"),
            ("restaurants", "Restaurant & Food"),
            ...
        ]
    """

    return [

        (
            key,
            config["label"],
        )

        for key, config
        in BUSINESS_CATEGORIES.items()

    ]
