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
# =========================================================


# =========================================================
# PRESENCE PRICING
#
# Used for longer-life commercial listings.
#
# Price depends on DURATION only.
# =========================================================

KALXA_PRESENCE_PRICING = {

    30: Decimal("49.00"),

    90: Decimal("99.00"),

    180: Decimal("169.00"),

    365: Decimal("249.00"),

}


# =========================================================
# CAMPAIGN PRICING
#
# Used for shorter-life / time-sensitive content.
#
# Price depends on:
#
# 1. Duration
# 2. Number of distribution zones
#
# Format:
#
# duration_days: {
#     zone_count: price
# }
# =========================================================

KALXA_CAMPAIGN_PRICING = {

    7: {

        1: Decimal("49.00"),

        2: Decimal("79.00"),

        3: Decimal("109.00"),

    },

    14: {

        1: Decimal("79.00"),

        2: Decimal("119.00"),

        3: Decimal("159.00"),

    },

    30: {

        1: Decimal("129.00"),

        2: Decimal("179.00"),

        3: Decimal("229.00"),

    },

}


# =========================================================
# SUPPORTED PRICING MODELS
# =========================================================

PRICING_MODEL_PRESENCE = "presence"

PRICING_MODEL_CAMPAIGN = "campaign"


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

class KalxaPricingError(ValueError):
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
# UNIVERSAL PRICE CALCULATOR
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
# Useful for the admin form in Step 3.
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
# GET PRICING OPTIONS
#
# This will be useful for rendering admin UI.
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

            duration: price

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

            duration: dict(
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
# FORMAT PRICE
#
# Example:
#
# Decimal("99.00")
#
# becomes:
#
# R99
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
