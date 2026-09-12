from decimal import Decimal


# =========================================================
# KALXA COMMERCIAL PRICING
# =========================================================
#
# IMPORTANT:
#
# Keep all MVP pricing in ONE place.
#
# Do not scatter prices throughout:
#
# - admin.py
# - templates
# - routes
# - models
#
# Later this can be replaced by database-driven pricing
# without changing the rest of the application.
#
# COMMERCIAL STRUCTURE:
#
# Presence
#     = pay to remain discoverable on Kalxa
#
# Campaign
#     = pay for time-sensitive distribution/reach
#
# Sponsored
#     = optional paid visibility boost
#
# IMPORTANT:
#
# Sponsored is NOT a pricing_model.
#
# It is an optional add-on that can sit on top of an
# already valid Presence or Campaign listing.
# =========================================================


# =========================================================
# PRESENCE PRICING
#
# Used for longer-life commercial listings.
#
# Price depends on DURATION only.
# =========================================================

KALXA_PRESENCE_PRICING = {

    30:
        Decimal("99.00"),

    90:
        Decimal("179.00"),

    180:
        Decimal("299.00"),

    365:
        Decimal("499.00"),

}


# =========================================================
# CAMPAIGN PRICING
#
# Used for:
#
# - events
# - specials
# - jobs
# - opportunities
# - other time-sensitive content
#
# Price depends on:
#
# duration + number of zones reached
# =========================================================

KALXA_CAMPAIGN_PRICING = {

    7: {

        1:
            Decimal("79.00"),

        2:
            Decimal("99.00"),

        3:
            Decimal("119.00"),

    },

    14: {

        1:
            Decimal("99.00"),

        2:
            Decimal("119.00"),

        3:
            Decimal("159.00"),

    },

    30: {

        1:
            Decimal("179.00"),

        2:
            Decimal("199.00"),

        3:
            Decimal("249.00"),

    },

}


# =========================================================
# SPONSORED PRICING
#
# Sponsored is an OPTIONAL VISIBILITY BOOST.
#
# It does not replace:
#
# - Presence pricing
# - Campaign pricing
# - existing payment_status
# - existing commercial expiry
#
# Example:
#
# Business already has a valid listing.
#
# Then it can optionally buy:
#
# 7 days  -> R50
# 14 days -> R80
# 30 days -> R150
# =========================================================

KALXA_SPONSORED_PRICING = {

    7:
        Decimal("50.00"),

    14:
        Decimal("80.00"),

    30:
        Decimal("150.00"),

}


# =========================================================
# SUPPORTED PRICING MODELS
#
# IMPORTANT:
#
# Sponsored is intentionally NOT included here.
#
# Sponsored is an add-on, not a listing pricing model.
# =========================================================

PRICING_MODEL_PRESENCE = (
    "presence"
)

PRICING_MODEL_CAMPAIGN = (
    "campaign"
)


ALLOWED_PRICING_MODELS = {

    PRICING_MODEL_PRESENCE,

    PRICING_MODEL_CAMPAIGN,

}


# =========================================================
# PRESENCE DURATION OPTIONS
# =========================================================

PRESENCE_DURATION_OPTIONS = (

    30,

    90,

    180,

    365,

)


# =========================================================
# CAMPAIGN DURATION OPTIONS
# =========================================================

CAMPAIGN_DURATION_OPTIONS = (

    7,

    14,

    30,

)


# =========================================================
# SPONSORED DURATION OPTIONS
# =========================================================

SPONSORED_DURATION_OPTIONS = (

    7,

    14,

    30,

)


# =========================================================
# CURRENT MVP MAXIMUM CAMPAIGN REACH
#
# We currently price:
#
# 1 zone
# 2 zones
# 3 zones
#
# Later Kalxa can support:
#
# - 4+ zones
# - regional bundles
# - all-network campaigns
#
# without changing the ContentDistributionZone architecture.
# =========================================================

MAX_CAMPAIGN_ZONES = 3


# =========================================================
# PRICING EXCEPTION
# =========================================================

class KalxaPricingError(
    ValueError
):
    """
    Raised when an invalid Kalxa pricing combination
    is requested.
    """

    pass


# =========================================================
# CALCULATE PRESENCE PRICE
# =========================================================

def calculate_presence_price(
    duration_days,
):

    try:

        duration_days = int(
            duration_days
        )

    except (
        TypeError,
        ValueError,
    ):

        raise KalxaPricingError(
            "Invalid presence duration."
        )


    price = (
        KALXA_PRESENCE_PRICING.get(
            duration_days
        )
    )


    if price is None:

        raise KalxaPricingError(
            (
                "Unsupported presence duration: "
                f"{duration_days} days."
            )
        )


    return price


# =========================================================
# CALCULATE CAMPAIGN PRICE
# =========================================================

def calculate_campaign_price(
    duration_days,
    zone_count,
):

    try:

        duration_days = int(
            duration_days
        )

        zone_count = int(
            zone_count
        )

    except (
        TypeError,
        ValueError,
    ):

        raise KalxaPricingError(
            "Invalid campaign duration or reach."
        )


    duration_prices = (
        KALXA_CAMPAIGN_PRICING.get(
            duration_days
        )
    )


    if duration_prices is None:

        raise KalxaPricingError(
            (
                "Unsupported campaign duration: "
                f"{duration_days} days."
            )
        )


    price = (
        duration_prices.get(
            zone_count
        )
    )


    if price is None:

        raise KalxaPricingError(
            (
                "Unsupported campaign reach: "
                f"{zone_count} zone(s)."
            )
        )


    return price


# =========================================================
# CALCULATE SPONSORED PRICE
#
# Sponsored pricing is based only on sponsored duration.
#
# It remains separate from calculate_kalxa_price()
# because Sponsored is an add-on rather than a
# pricing_model.
# =========================================================

def calculate_sponsored_price(
    duration_days,
):

    try:

        duration_days = int(
            duration_days
        )

    except (
        TypeError,
        ValueError,
    ):

        raise KalxaPricingError(
            "Invalid sponsored duration."
        )


    price = (
        KALXA_SPONSORED_PRICING.get(
            duration_days
        )
    )


    if price is None:

        raise KalxaPricingError(
            (
                "Unsupported sponsored duration: "
                f"{duration_days} days."
            )
        )


    return price


# =========================================================
# UNIVERSAL LISTING PRICE CALCULATOR
#
# IMPORTANT:
#
# This calculator remains responsible only for the
# listing's primary commercial pricing model:
#
# - Presence
# - Campaign
#
# Sponsored is calculated separately using:
#
# calculate_sponsored_price()
# =========================================================

def calculate_kalxa_price(
    pricing_model,
    duration_days,
    zone_count=1,
):

    pricing_model = (
        str(
            pricing_model
            or ""
        )
        .strip()
        .lower()
    )


    # =====================================================
    # PRESENCE
    #
    # Reach does NOT affect Presence pricing.
    # =====================================================

    if (
        pricing_model
        == PRICING_MODEL_PRESENCE
    ):

        return calculate_presence_price(
            duration_days
        )


    # =====================================================
    # CAMPAIGN
    #
    # Duration + reach determine price.
    # =====================================================

    if (
        pricing_model
        == PRICING_MODEL_CAMPAIGN
    ):

        return calculate_campaign_price(
            duration_days,
            zone_count,
        )


    # =====================================================
    # INVALID MODEL
    # =====================================================

    raise KalxaPricingError(
        (
            "Unsupported pricing model: "
            f"{pricing_model or 'empty'}."
        )
    )


# =========================================================
# GET DURATION OPTIONS
#
# Used for the main commercial listing pricing.
#
# Sponsored duration is intentionally handled separately.
# =========================================================

def get_duration_options(
    pricing_model,
):

    pricing_model = (
        str(
            pricing_model
            or ""
        )
        .strip()
        .lower()
    )


    if (
        pricing_model
        == PRICING_MODEL_PRESENCE
    ):

        return list(
            PRESENCE_DURATION_OPTIONS
        )


    if (
        pricing_model
        == PRICING_MODEL_CAMPAIGN
    ):

        return list(
            CAMPAIGN_DURATION_OPTIONS
        )


    return []


# =========================================================
# GET SPONSORED DURATION OPTIONS
# =========================================================

def get_sponsored_duration_options():
    """
    Return the supported Kalxa Sponsored durations.
    """

    return list(
        SPONSORED_DURATION_OPTIONS
    )


# =========================================================
# GET PRICING OPTIONS
#
# Used for Presence / Campaign admin UI.
# =========================================================

def get_pricing_options(
    pricing_model,
):

    pricing_model = (
        str(
            pricing_model
            or ""
        )
        .strip()
        .lower()
    )


    if (
        pricing_model
        == PRICING_MODEL_PRESENCE
    ):

        return {

            duration:
                price

            for (
                duration,
                price
            )
            in
            KALXA_PRESENCE_PRICING.items()

        }


    if (
        pricing_model
        == PRICING_MODEL_CAMPAIGN
    ):

        return {

            duration:
                dict(
                    prices
                )

            for (
                duration,
                prices
            )
            in
            KALXA_CAMPAIGN_PRICING.items()

        }


    return {}


# =========================================================
# GET SPONSORED PRICING OPTIONS
#
# Useful for:
#
# - admin form
# - public sponsored upgrade page
# - future Yoco checkout page
#
# Returns:
#
# {
#     7: Decimal("50.00"),
#     14: Decimal("80.00"),
#     30: Decimal("150.00"),
# }
# =========================================================

def get_sponsored_pricing_options():

    return {

        duration:
            price

        for (
            duration,
            price
        )
        in
        KALXA_SPONSORED_PRICING.items()

    }


# =========================================================
# FORMAT PRICE
#
# Example:
#
# Decimal("99.00")
#
# becomes:
#
# R99
#
# Decimal("99.50")
#
# becomes:
#
# R99.50
# =========================================================

def format_kalxa_price(
    amount,
):

    if amount is None:

        return "—"


    amount = Decimal(
        str(
            amount
        )
    )


    if (
        amount
        == amount.to_integral()
    ):

        return (
            f"R{int(amount)}"
        )


    return (
        f"R{amount:.2f}"
    )


# ============================================================
# KALXA COMMERCIAL CATEGORY CLASSIFICATION
# ============================================================

KALXA_PRESENCE_CATEGORIES = {

    "local-restaurants",

    "beauty-salon",

    "services",

}


KALXA_CAMPAIGN_CATEGORIES = {

    "events",

    "discount-deals",

    "jobs",

    "opportunities",

}


# ============================================================
# CONTENT-TYPE OVERRIDES
# ============================================================

KALXA_PRICING_MODEL_OVERRIDES = {

    # --------------------------------------------------------
    # PROPERTY
    # --------------------------------------------------------

    (
        "property",
        "room",
    ):
        PRICING_MODEL_CAMPAIGN,

    (
        "property",
        "rental",
    ):
        PRICING_MODEL_CAMPAIGN,

    (
        "property",
        "property_sale",
    ):
        PRICING_MODEL_CAMPAIGN,

    (
        "property",
        "hotel_lodge",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "property",
        "accommodation_special",
    ):
        PRICING_MODEL_CAMPAIGN,


    # --------------------------------------------------------
    # EVENTS
    # --------------------------------------------------------

    (
        "events",
        "event",
    ):
        PRICING_MODEL_CAMPAIGN,


    # --------------------------------------------------------
    # STORE SPECIALS
    # --------------------------------------------------------

    (
        "discount-deals",
        "grocery_special",
    ):
        PRICING_MODEL_CAMPAIGN,

    (
        "discount-deals",
        "retail_special",
    ):
        PRICING_MODEL_CAMPAIGN,

    (
        "discount-deals",
        "special",
    ):
        PRICING_MODEL_CAMPAIGN,


    # --------------------------------------------------------
    # RESTAURANTS / FOOD
    # --------------------------------------------------------

    (
        "local-restaurants",
        "restaurant",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "local-restaurants",
        "takeaway",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "local-restaurants",
        "general_listing",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "local-restaurants",
        "daily_special",
    ):
        PRICING_MODEL_CAMPAIGN,

    (
        "local-restaurants",
        "weekend_special",
    ):
        PRICING_MODEL_CAMPAIGN,

    (
        "local-restaurants",
        "food_deal",
    ):
        PRICING_MODEL_CAMPAIGN,


    # --------------------------------------------------------
    # BEAUTY
    # --------------------------------------------------------

    (
        "beauty-salon",
        "salon",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "beauty-salon",
        "barber",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "beauty-salon",
        "beauty_service",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "beauty-salon",
        "general_listing",
    ):
        PRICING_MODEL_PRESENCE,

    (
        "beauty-salon",
        "beauty_special",
    ):
        PRICING_MODEL_CAMPAIGN,


    # --------------------------------------------------------
    # SERVICES
    # --------------------------------------------------------

    (
        "services",
        "general_listing",
    ):
        PRICING_MODEL_PRESENCE,

}


# ============================================================
# GET PRICING MODEL
# ============================================================

def get_pricing_model(
    category,
    content_type=None,
):
    """
    Determine whether Kalxa content uses:

        presence
        campaign
        None

    Exact content-type rules take priority over
    category-level defaults.

    Sponsored is deliberately not returned here because
    Sponsored is an optional visibility add-on rather than
    the listing's primary commercial pricing model.
    """

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


    override_key = (
        category,
        content_type,
    )


    # ========================================================
    # EXACT CONTENT TYPE OVERRIDE
    # ========================================================

    if (
        override_key
        in KALXA_PRICING_MODEL_OVERRIDES
    ):

        return (
            KALXA_PRICING_MODEL_OVERRIDES[
                override_key
            ]
        )


    # ========================================================
    # CATEGORY DEFAULT
    # ========================================================

    if (
        category
        in KALXA_PRESENCE_CATEGORIES
    ):

        return (
            PRICING_MODEL_PRESENCE
        )


    if (
        category
        in KALXA_CAMPAIGN_CATEGORIES
    ):

        return (
            PRICING_MODEL_CAMPAIGN
        )


    # ========================================================
    # COMMUNITY / NON-COMMERCIAL CATEGORY
    # ========================================================

    return None
