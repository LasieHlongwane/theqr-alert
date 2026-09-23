import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

KALXA_IMPORT_URL = (
    os.getenv(
        "KALXA_RETAIL_IMPORT_URL",
        ""
    )
    .strip()
)


KALXA_IMPORT_TOKEN = (
    os.getenv(
        "KALXA_RETAIL_IMPORT_TOKEN",
        ""
    )
    .strip()
)


KALXA_ZONE_ID = int(
    os.getenv(
        "KALXA_RETAIL_ZONE_ID",
        "1",
    )
)


BOXER_BASE_URL = (
    "https://www.boxer.co.za"
)


BOXER_PROMOTION_SOURCES = [
    {
        "name": "Boxer Mpumalanga",
        "url": (
            "https://www.boxer.co.za/"
            "promotions/mpumalanga/superstores"
        ),
        "location": "Mpumalanga",
    },
    {
        "name": "Boxer Gauteng",
        "url": (
            "https://www.boxer.co.za/"
            "promotions/gauteng/superstores"
        ),
        "location": "Gauteng",
    },
]


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; KalxaRetailImporter/1.0; "
        "+https://lac-acess-delivered.onrender.com/)"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
}


# ============================================================
# NORMALIZE TEXT
# ============================================================

def clean_text(
    value,
):

    return (
        re.sub(
            r"\s+",
            " ",
            str(
                value
                or ""
            ),
        )
        .strip()
    )


# ============================================================
# PARSE BOXER DATE RANGE
#
# Example:
# Valid: 21/09/2026 - 07/10/2026
# ============================================================

def parse_boxer_date_range(
    value,
):

    value = (
        clean_text(
            value
        )
    )


    match = re.search(
        (
            r"Valid:\s*"
            r"(\d{1,2}/\d{1,2}/\d{4})"
            r"\s*-\s*"
            r"(\d{1,2}/\d{1,2}/\d{4})"
        ),
        value,
        flags=re.IGNORECASE,
    )


    if not match:

        return (
            None,
            None,
        )


    try:

        start_date = (
            datetime.strptime(
                match.group(1),
                "%d/%m/%Y",
            )
            .date()
        )


        end_date = (
            datetime.strptime(
                match.group(2),
                "%d/%m/%Y",
            )
            .date()
        )


        return (
            start_date,
            end_date,
        )


    except ValueError:

        return (
            None,
            None,
        )


# ============================================================
# FIND LEAFLET URL
# ============================================================

def find_boxer_leaflet_url(
    heading,
):

    current = (
        heading
    )


    # Search the elements that follow the campaign heading.
    # Boxer currently places:
    #
    # campaign title
    # validity text
    # View Leaflet
    # Download
    #
    # together in the same promotion block.

    for _ in range(
        12
    ):

        current = (
            current.find_next()
            if current
            else None
        )


        if not current:

            break


        if (
            current.name
            in {
                "h3",
                "h4",
            }
            and
            current is not heading
        ):

            break


        if (
            current.name
            == "a"
        ):

            link_text = (
                clean_text(
                    current.get_text(
                        " ",
                        strip=True,
                    )
                )
                .lower()
            )


            href = (
                current.get(
                    "href"
                )
            )


            if (
                href
                and
                (
                    "view leaflet"
                    in link_text
                    or
                    "download"
                    in link_text
                )
            ):

                return (
                    urljoin(
                        BOXER_BASE_URL,
                        href,
                    )
                )


    return None


# ============================================================
# FIND VALIDITY TEXT
# ============================================================

def find_boxer_validity_text(
    heading,
):

    current = (
        heading
    )


    for _ in range(
        12
    ):

        current = (
            current.find_next()
            if current
            else None
        )


        if not current:

            break


        if (
            current.name
            in {
                "h3",
                "h4",
            }
            and
            current is not heading
        ):

            break


        text = (
            clean_text(
                current.get_text(
                    " ",
                    strip=True,
                )
            )
        )


        if (
            text.lower()
            .startswith(
                "valid:"
            )
        ):

            return text


    return ""


# ============================================================
# PARSE BOXER PROMOTIONS PAGE
# ============================================================

def parse_boxer_promotions_page(
    html,
    page_url,
    location,
):

    soup = (
        BeautifulSoup(
            html,
            "html.parser",
        )
    )


    campaigns = (
        []
    )


    # Boxer campaign headings currently appear as H4 elements.
    headings = (
        soup.find_all(
            "h4"
        )
    )


    for heading in headings:

        title = (
            clean_text(
                heading.get_text(
                    " ",
                    strip=True,
                )
            )
        )


        if not title:

            continue


        title_lower = (
            title.lower()
        )


        # ====================================================
        # IGNORE NON-PROMOTION HEADINGS
        # ====================================================

        if title_lower in {
            "promotions",
            "customer care",
            "subscribe to our newsletter",
        }:

            continue


        # ====================================================
        # EXCLUDE LIQUOR CAMPAIGNS
        # ====================================================

        if (
            "liquor"
            in title_lower
        ):

            print(
                "[BOXER] Skipping liquor campaign:",
                title,
            )

            continue


        validity_text = (
            find_boxer_validity_text(
                heading
            )
        )


        (
            start_date,
            end_date,
        ) = (
            parse_boxer_date_range(
                validity_text
            )
        )


        # Campaigns without a recognisable validity period
        # are ignored. This protects Kalxa from accidentally
        # importing navigation/footer headings.

        if (
            not start_date
            and
            not end_date
        ):

            continue


        leaflet_url = (
            find_boxer_leaflet_url(
                heading
            )
        )


        source_url = (
            leaflet_url
            or page_url
        )


        campaign = {

            "retailer":
                "Boxer",

            "title":
                f"Boxer {title}",

            "description":
                (
                    f"Official Boxer promotion for "
                    f"{location}. "
                    "View the current Boxer leaflet "
                    "for available specials."
                ),

            "location":
                location,

            "start_date":
                (
                    start_date.isoformat()
                    if start_date
                    else None
                ),

            "end_date":
                (
                    end_date.isoformat()
                    if end_date
                    else None
                ),

            "source_url":
                source_url,
        }


        campaigns.append(
            campaign
        )


    return campaigns


# ============================================================
# FETCH ONE BOXER SOURCE
# ============================================================

def fetch_boxer_source(
    source,
):

    print(
        (
            "[BOXER] Fetching "
            f"{source['name']}: "
            f"{source['url']}"
        )
    )


    response = (
        requests.get(
            source["url"],
            headers=
                REQUEST_HEADERS,
            timeout=
                30,
        )
    )


    response.raise_for_status()


    campaigns = (
        parse_boxer_promotions_page(
            html=
                response.text,

            page_url=
                source["url"],

            location=
                source["location"],
        )
    )


    print(
        (
            f"[BOXER] {source['name']} "
            f"found {len(campaigns)} "
            "non-liquor campaign(s)."
        )
    )


    return campaigns


# ============================================================
# REMOVE DUPLICATES INSIDE CURRENT RUN
# ============================================================

def deduplicate_campaigns(
    campaigns,
):

    unique = (
        []
    )


    seen = (
        set()
    )


    for campaign in campaigns:

        key = (
            campaign.get(
                "source_url"
            ),
            campaign.get(
                "title"
            ),
            campaign.get(
                "location"
            ),
        )


        if key in seen:

            continue


        seen.add(
            key
        )


        unique.append(
            campaign
        )


    return unique


# ============================================================
# SEND TO KALXA
# ============================================================

def send_campaigns_to_kalxa(
    campaigns,
):

    if not KALXA_IMPORT_URL:

        raise RuntimeError(
            (
                "Missing GitHub secret/environment "
                "KALXA_RETAIL_IMPORT_URL."
            )
        )


    if not KALXA_IMPORT_TOKEN:

        raise RuntimeError(
            (
                "Missing GitHub secret/environment "
                "KALXA_RETAIL_IMPORT_TOKEN."
            )
        )


    payload = {

        "zone_id":
            KALXA_ZONE_ID,

        "campaigns":
            campaigns,
    }


    print(
        (
            "[BOXER] Sending "
            f"{len(campaigns)} campaign(s) "
            "to Kalxa."
        )
    )


    response = (
        requests.post(
            KALXA_IMPORT_URL,

            headers={
                "Authorization":
                    (
                        "Bearer "
                        f"{KALXA_IMPORT_TOKEN}"
                    ),

                "Content-Type":
                    "application/json",
            },

            json=
                payload,

            timeout=
                60,
        )
    )


    print(
        (
            "[BOXER] Kalxa response "
            f"status={response.status_code}"
        )
    )


    try:

        response_json = (
            response.json()
        )

    except ValueError:

        response_json = {
            "raw":
                response.text[:2000]
        }


    print(
        json.dumps(
            response_json,
            indent=2,
            default=str,
        )
    )


    response.raise_for_status()


    return response_json


# ============================================================
# MAIN
# ============================================================

def main():

    all_campaigns = (
        []
    )


    for source in (
        BOXER_PROMOTION_SOURCES
    ):

        try:

            campaigns = (
                fetch_boxer_source(
                    source
                )
            )


            all_campaigns.extend(
                campaigns
            )


        except Exception as exc:

            print(
                (
                    "[BOXER ERROR] "
                    f"{source['name']}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                file=sys.stderr,
            )


    all_campaigns = (
        deduplicate_campaigns(
            all_campaigns
        )
    )


    print(
        (
            "[BOXER] Total unique "
            f"campaigns: {len(all_campaigns)}"
        )
    )


    if not all_campaigns:

        print(
            "[BOXER] No campaigns found."
        )

        return


    send_campaigns_to_kalxa(
        all_campaigns
    )


if __name__ == "__main__":

    main()
