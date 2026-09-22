# ============================================================
# KALXA RSS JOB IMPORT - SCHEDULED RUNNER
# ============================================================
#
# This script is designed to be executed by a Render Cron Job.
#
# It does NOT duplicate the RSS importer.
#
# Instead, it securely calls:
#
#     POST /internal/jobs/import/rss
#
# The Flask application then:
#
#     RSS / Atom feeds
#           ↓
#     Normalization
#           ↓
#     Duplicate detection
#           ↓
#     Expiry filtering
#           ↓
#     PendingSubmission
#           ↓
#     Admin moderation
#
# ============================================================

import json
import os
import sys

from urllib.error import (
    HTTPError,
    URLError,
)

from urllib.request import (
    Request,
    urlopen,
)


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_BASE_URL = (
    "https://lac-local-access.onrender.com"
)


REQUEST_TIMEOUT_SECONDS = 120


# ============================================================
# HELPERS
# ============================================================

def get_required_environment_variable(
    name,
):
    """
    Read a required environment variable.

    The process exits with an error if it is missing.
    """

    value = (
        os.environ.get(
            name,
            "",
        )
        .strip()
    )


    if not value:

        print(
            (
                "[Kalxa RSS Scheduler] "
                f"Missing required environment variable: "
                f"{name}"
            ),
            file=sys.stderr,
        )

        sys.exit(
            1
        )


    return value


def get_base_url():
    """
    Return the Kalxa web-service URL.

    KALXA_BASE_URL is optional because the current production
    URL has a safe default.
    """

    value = (
        os.environ.get(
            "KALXA_BASE_URL",
            DEFAULT_BASE_URL,
        )
        .strip()
    )


    return (
        value.rstrip(
            "/"
        )
    )


def decode_response_body(
    raw_body,
):
    """
    Decode a response as JSON when possible.
    """

    if not raw_body:

        return {}


    text = (
        raw_body
        .decode(
            "utf-8",
            errors="replace",
        )
        .strip()
    )


    if not text:

        return {}


    try:

        return json.loads(
            text
        )


    except json.JSONDecodeError:

        return {
            "raw_response":
                text,
        }


# ============================================================
# RUN IMPORT
# ============================================================

def run_rss_job_import():

    print(
        (
            "[Kalxa RSS Scheduler] "
            "Starting scheduled RSS/Atom Jobs import."
        )
    )


    base_url = (
        get_base_url()
    )


    import_token = (
        get_required_environment_variable(
            "KALXA_JOB_FEED_IMPORT_TOKEN"
        )
    )


    import_url = (
        f"{base_url}"
        "/internal/jobs/import/rss"
    )


    print(
        (
            "[Kalxa RSS Scheduler] "
            f"Calling importer: {import_url}"
        )
    )


    request_object = (
        Request(
            import_url,
            data=b"",
            method="POST",
            headers={
                "Authorization":
                    (
                        "Bearer "
                        f"{import_token}"
                    ),

                "Accept":
                    "application/json",

                "User-Agent":
                    (
                        "Kalxa-RSS-Jobs-"
                        "Scheduled-Importer/1.0"
                    ),
            },
        )
    )


    try:

        with urlopen(
            request_object,
            timeout=
                REQUEST_TIMEOUT_SECONDS,
        ) as response:

            status_code = (
                response.status
            )


            response_data = (
                decode_response_body(
                    response.read()
                )
            )


    except HTTPError as exc:

        response_data = (
            decode_response_body(
                exc.read()
            )
        )


        print(
            (
                "[Kalxa RSS Scheduler] "
                f"Importer returned HTTP {exc.code}."
            ),
            file=sys.stderr,
        )


        print(
            json.dumps(
                response_data,
                indent=2,
                default=str,
            ),
            file=sys.stderr,
        )


        sys.exit(
            1
        )


    except URLError as exc:

        print(
            (
                "[Kalxa RSS Scheduler] "
                "Unable to reach Kalxa: "
                f"{exc}"
            ),
            file=sys.stderr,
        )


        sys.exit(
            1
        )


    except TimeoutError:

        print(
            (
                "[Kalxa RSS Scheduler] "
                "Kalxa RSS importer timed out."
            ),
            file=sys.stderr,
        )


        sys.exit(
            1
        )


    except Exception as exc:

        print(
            (
                "[Kalxa RSS Scheduler] "
                "Unexpected scheduler error: "
                f"{exc}"
            ),
            file=sys.stderr,
        )


        sys.exit(
            1
        )


    # ========================================================
    # HTTP RESULT
    # ========================================================

    if not (
        200
        <= status_code
        < 300
    ):

        print(
            (
                "[Kalxa RSS Scheduler] "
                f"Importer failed with HTTP {status_code}."
            ),
            file=sys.stderr,
        )


        print(
            json.dumps(
                response_data,
                indent=2,
                default=str,
            ),
            file=sys.stderr,
        )


        sys.exit(
            1
        )


    # ========================================================
    # IMPORTER RESULT
    # ========================================================

    print(
        (
            "[Kalxa RSS Scheduler] "
            f"Importer responded HTTP {status_code}."
        )
    )


    print(
        json.dumps(
            response_data,
            indent=2,
            default=str,
        )
    )


    created = (
        response_data.get(
            "created",
            0,
        )
    )


    duplicates = (
        response_data.get(
            "duplicates",
            0,
        )
    )


    expired = (
        response_data.get(
            "expired",
            0,
        )
    )


    invalid = (
        response_data.get(
            "invalid",
            0,
        )
    )


    feed_errors = (
        response_data.get(
            "feed_errors",
            0,
        )
    )


    fetched = (
        response_data.get(
            "fetched",
            0,
        )
    )


    print(
        (
            "[Kalxa RSS Scheduler] "
            "Import complete. "
            f"fetched={fetched} "
            f"created={created} "
            f"duplicates={duplicates} "
            f"expired={expired} "
            f"invalid={invalid} "
            f"feed_errors={feed_errors}"
        )
    )


    # --------------------------------------------------------
    # Treat complete importer failure as a failed cron run.
    #
    # A partial feed failure does not necessarily mean the
    # entire operation failed, because other configured feeds
    # may have succeeded.
    # --------------------------------------------------------

    importer_success = (
        response_data.get(
            "success",
            True,
        )
    )


    if not importer_success:

        print(
            (
                "[Kalxa RSS Scheduler] "
                "The RSS importer reported failure."
            ),
            file=sys.stderr,
        )


        sys.exit(
            1
        )


    print(
        (
            "[Kalxa RSS Scheduler] "
            "Scheduled run finished successfully."
        )
    )


    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    sys.exit(
        run_rss_job_import()
    )
