import os
import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# ENVIRONMENT VARIABLES
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUANTGIST_API_KEY = os.getenv("QUANTGIST_API_KEY")

QUANTGIST_BASE_URL = "https://api.quantgist.com/v1"


# =========================
# BASIC CHECKS
# =========================

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

if not QUANTGIST_API_KEY:
    raise RuntimeError("QUANTGIST_API_KEY is missing")


# =========================
# QUANTGIST REQUEST
# =========================

def quantgist_request(path, params=None):
    url = QUANTGIST_BASE_URL + path

    headers = {
        "X-API-Key": QUANTGIST_API_KEY,
        "Accept": "application/json",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=20,
    )

    response.raise_for_status()

    return response.json()


# =========================
# EVENT EXTRACTION
# =========================

def extract_events(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    # Common possible structures
    for key in ("data", "events", "results", "items"):
        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            for nested_key in ("events", "results", "items", "data"):
                nested = value.get(nested_key)

                if isinstance(nested, list):
                    return nested

    return []


# =========================
# GET CALENDAR
# =========================

def get_calendar(days=14):
    return quantgist_request(
        "/macro/calendar",
        {
            "events": (
                "NFP,CPI,PCE,FOMC,GDP,"
                "UNEMPLOYMENT,RETAIL_SALES,PPI,ISM"
            ),
            "days": days,
        },
    )


# =========================
# DEBUG COMMAND
# =========================

async def debug_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        data = get_calendar(14)

        if isinstance(data, dict):

            events = extract_events(data)

            lines = [
                "🔎 QUANTGIST DEBUG",
                "",
                f"Response type: {type(data).__name__}",
                f"Top-level keys: {list(data.keys())}",
                f"Events extracted: {len(events)}",
            ]

            if events:

                first = events[0]

                lines.append("")
                lines.append("FIRST EVENT")
                lines.append(
                    f"Event keys: {list(first.keys())}"
                )

                # Only show useful event fields
                safe_fields = [
                    "id",
                    "title",
                    "event_type",
                    "currency",
                    "country",
                    "impact",
                    "release_time",
                    "date",
                    "time",
                    "actual",
                    "forecast",
                    "previous",
                    "is_released",
                    "has_actual",
                    "has_forecast",
                    "missing_fields",
                ]

                for key in safe_fields:
                    if key in first:
                        lines.append(
                            f"{key}: {first.get(key)}"
                        )

            else:
                lines.append("")
                lines.append(
                    "⚠️ No events were extracted."
                )

                # Show safe top-level information
                for key, value in data.items():

                    if key.lower() in {
                        "api_key",
                        "key",
                        "token",
                        "authorization",
                        "password",
                        "secret",
                    }:
                        continue

                    if isinstance(value, (str, int, float, bool)):
                        lines.append(
                            f"{key}: {value}"
                        )

            message = "\n".join(lines)

            await update.message.reply_text(
                message[:4000]
            )

        else:

            await update.message.reply_text(
                "🔎 QUANTGIST DEBUG\n\n"
                f"Response type: {type(data).__name__}\n"
                f"Response: {str(data)[:3000]}"
            )

    except requests.HTTPError as exc:

        status = (
            exc.response.status_code
            if exc.response is not None
            else "unknown"
        )

        body = (
            exc.response.text[:2500]
            if exc.response is not None
            else str(exc)
        )

        await update.message.reply_text(
            "❌ QUANTGIST HTTP ERROR\n\n"
            f"Status: {status}\n"
            f"Response:\n{body}"
        )

    except Exception as exc:

        await update.message.reply_text(
            "❌ DEBUG ERROR\n\n"
            f"{type(exc).__name__}: {exc}"
        )


# =========================
# START
# =========================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "FundamentalX is online.\n\n"
        "Commands:\n"
        "/debug - test QuantGist connection"
    )


# =========================
# HEALTH SERVER FOR RENDER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain",
        )
        self.end_headers()

        self.wfile.write(
            b"FundamentalX is running."
        )

    def log_message(self, format, *args):
        return


def run_health_server():

    port = int(
        os.environ.get("PORT", 10000)
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    server.serve_forever()


# =========================
# MAIN
# =========================

def main():

    threading.Thread(
        target=run_health_server,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "debug",
            debug_command,
        )
    )

    print("FundamentalX started.")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
