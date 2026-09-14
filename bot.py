import os
import threading
from datetime import datetime, timezone

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUANTGIST_API_KEY = os.getenv("QUANTGIST_API_KEY")

CALENDAR_URL = "https://api.quantgist.com/v1/macro/calendar"
EVENT_DETAIL_URL = "https://api.quantgist.com/v2/events"

EVENT_ALIASES = (
    "NFP,CPI,PCE,FOMC,GDP,UNEMPLOYMENT,"
    "RETAIL_SALES,PPI,ISM"
)

CALENDAR_DAYS = 30


# ============================================================
# QUANTGIST HELPERS
# ============================================================

def get_headers():
    return {
        "X-API-Key": QUANTGIST_API_KEY
    }


def get_calendar():
    response = requests.get(
        CALENDAR_URL,
        headers=get_headers(),
        params={
            "events": EVENT_ALIASES,
            "days": CALENDAR_DAYS
        },
        timeout=30
    )

    response.raise_for_status()
    return response.json()


def get_event_detail(event_id):
    """
    QuantGist v2:
    GET /v2/events/{event_id}

    Used when the calendar record does not contain
    previous/forecast/actual.
    """

    if not event_id:
        return {}

    url = f"{EVENT_DETAIL_URL}/{event_id}"

    response = requests.get(
        url,
        headers=get_headers(),
        timeout=20
    )

    if response.status_code != 200:
        print(
            "DETAIL ERROR:",
            response.status_code,
            response.text[:500]
        )
        return {}

    data = response.json()

    if isinstance(data, dict):
        return data

    return {}


def enrich_event(event):
    """
    Keep the calendar event, but retrieve the detailed
    event record when values are missing.
    """

    needs_detail = any(
        event.get(field) is None
        for field in (
            "previous",
            "forecast",
            "actual"
        )
    )

    if not needs_detail:
        return event

    event_id = event.get("id")

    if not event_id:
        return event

    detail = get_event_detail(event_id)

    if not detail:
        return event

    # Keep calendar data if detail doesn't provide a value.
    for field in (
        "actual",
        "forecast",
        "previous",
        "surprise_pct",
        "impact",
        "release_time",
        "title",
        "currency",
        "symbols"
    ):
        if detail.get(field) is not None:
            event[field] = detail[field]

    return event


def flatten_calendar(payload):
    """
    QuantGist calendar response contains groups:

    data = [
        {
            alias: "...",
            label: "...",
            country: "...",
            data: [...]
        }
    ]
    """

    events = []

    groups = payload.get("data", [])

    if not isinstance(groups, list):
        return events

    for group in groups:

        if not isinstance(group, dict):
            continue

        group_events = group.get("data", [])

        if isinstance(group_events, list):
            events.extend(group_events)

    return events


# ============================================================
# GENERAL HELPERS
# ============================================================

def parse_release_time(event):
    value = event.get("release_time")

    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except Exception:
        return None


def format_value(value):
    if value is None or value == "":
        return "Not available"

    return str(value)


def identify_event_type(event):
    text = (
        str(event.get("event_type", "")) + " " +
        str(event.get("title", ""))
    ).lower()

    if "nonfarm" in text or "nfp" in text:
        return "NFP"

    if "consumer price" in text or "cpi" in text:
        return "CPI"

    if "pce" in text:
        return "PCE"

    if "fomc" in text or "rate decision" in text:
        return "FOMC"

    if "unemployment" in text:
        return "UNEMPLOYMENT"

    if "gross domestic" in text or "gdp" in text:
        return "GDP"

    if "retail sales" in text:
        return "RETAIL_SALES"

    if "producer price" in text or "ppi" in text:
        return "PPI"

    if "ism" in text:
        return "ISM"

    return "MACRO"


# ============================================================
# EDUCATIONAL ANALYSIS
# ============================================================

def build_analysis(event):

    event_type = identify_event_type(event)

    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    title = event.get(
        "title",
        "Economic Event"
    )

    lines = [
        f"📊 {title}",
        "",
        f"Event Type: {event_type}",
        f"Previous: {format_value(previous)}",
        f"Forecast: {format_value(forecast)}",
        f"Actual: {format_value(actual)}",
        ""
    ]

    if actual is None:

        lines.extend([
            "🧠 BEFORE RELEASE",
            "",
            "The market will compare the new figure "
            "with the previous result and the forecast.",
            "",
            "A meaningful surprise can change expectations "
            "around inflation, growth, employment or "
            "monetary policy.",
            "",
            "⚠️ This is educational scenario analysis, "
            "not a guaranteed market direction."
        ])

    else:

        lines.extend([
            "🧠 AFTER RELEASE",
            "",
            "Compare Actual with Forecast first.",
            "",
            "A significant surprise may change expectations "
            "for monetary policy and affect related markets.",
            "",
            "The broader macro context should also be considered."
        ])

        surprise = event.get("surprise_pct")

        if surprise is not None:
            lines.extend([
                "",
                f"Surprise: {surprise}%"
            ])

    return "\n".join(lines)


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "🤖 FundamentalX\n\n"
        "Economic calendar + macro analysis assistant.\n\n"
        "Commands:\n"
        "/next - Upcoming high-impact events\n"
        "/today - Today's high-impact events"
    )

    await update.message.reply_text(message)


# ============================================================
# /NEXT
# ============================================================

async def next_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        payload = get_calendar()

        events = flatten_calendar(payload)

        now = datetime.now(timezone.utc)

        upcoming = []

        for event in events:

            release_time = parse_release_time(event)

            if not release_time:
                continue

            if release_time <= now:
                continue

            if str(
                event.get("impact", "")
            ).lower() != "high":
                continue

            upcoming.append(
                (release_time, event)
            )

        upcoming.sort(
            key=lambda item: item[0]
        )

        if not upcoming:

            await update.message.reply_text(
                "📅 No upcoming high-impact events found."
            )

            return

        lines = [
            "📅 Upcoming High-Impact Events",
            ""
        ]

        for index, (release_time, event) in enumerate(
            upcoming[:8],
            start=1
        ):

            # Get missing data from detailed endpoint.
            event = enrich_event(event)

            title = event.get(
                "title",
                "Economic Event"
            )

            previous = format_value(
                event.get("previous")
            )

            forecast = format_value(
                event.get("forecast")
            )

            lines.extend([
                f"{index}. {title}",
                (
                    f"🕒 {release_time.strftime('%d %b %Y')} "
                    f"• {release_time.strftime('%H:%M')} UTC"
                ),
                f"Impact: {event.get('impact', 'unknown')}",
                f"Previous: {previous}",
                f"Forecast: {forecast}",
                ""
            ])

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as error:

        print("NEXT ERROR:", error)

        await update.message.reply_text(
            "⚠️ Unable to retrieve the economic calendar."
        )


# ============================================================
# /TODAY
# ============================================================

async def today_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        payload = get_calendar()

        events = flatten_calendar(payload)

        today = datetime.now(
            timezone.utc
        ).date()

        found = []

        for event in events:

            release_time = parse_release_time(event)

            if not release_time:
                continue

            if release_time.date() != today:
                continue

            if str(
                event.get("impact", "")
            ).lower() != "high":
                continue

            found.append(
                (release_time, event)
            )

        found.sort(
            key=lambda item: item[0]
        )

        if not found:

            await update.message.reply_text(
                "📅 No high-impact events scheduled today."
            )

            return

        for release_time, event in found:

            event = enrich_event(event)

            analysis = build_analysis(event)

            header = (
                f"🕒 {release_time.strftime('%H:%M')} UTC\n"
                f"Impact: {event.get('impact', 'unknown')}\n\n"
            )

            await update.message.reply_text(
                header + analysis
            )

    except Exception as error:

        print("TODAY ERROR:", error)

        await update.message.reply_text(
            "⚠️ Unable to retrieve today's events."
        )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

def run_health_server():

    from http.server import (
        BaseHTTPRequestHandler,
        HTTPServer
    )

    port = int(
        os.getenv("PORT", "10000")
    )

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):

            self.send_response(200)
            self.end_headers()

            self.wfile.write(
                b"FundamentalX is running."
            )

        def log_message(self, format, *args):
            return

    server = HTTPServer(
        ("0.0.0.0", port),
        Handler
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not QUANTGIST_API_KEY:
        raise RuntimeError(
            "QUANTGIST_API_KEY is missing."
        )

    threading.Thread(
        target=run_health_server,
        daemon=True
    ).start()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "next",
            next_command
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            today_command
        )
    )

    print("FundamentalX is running...")

    application.run_polling()


if __name__ == "__main__":
    main()
