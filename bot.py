import os
import requests
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUANTGIST_API_KEY = os.getenv("QUANTGIST_API_KEY")

QUANTGIST_URL = "https://api.quantgist.com/v1/macro/calendar"

EVENT_ALIASES = (
    "NFP,CPI,PCE,FOMC,GDP,UNEMPLOYMENT,"
    "RETAIL_SALES,PPI,ISM"
)

# 30 days gives /next enough room to find upcoming events.
CALENDAR_DAYS = 30


# ============================================================
# BASIC HELPERS
# ============================================================

def parse_datetime(value):
    if not value:
        return None

    try:
        value = str(value).strip()

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def display_value(value):
    if value is None:
        return "Not available"

    if isinstance(value, str):
        value = value.strip()

        if not value or value.lower() in {"none", "null", "n/a"}:
            return "Not available"

        return value

    return str(value)


def is_high_impact(event):
    return str(event.get("impact", "")).lower() == "high"


def event_time(event):
    return parse_datetime(event.get("release_time"))


# ============================================================
# QUANTGIST API
# ============================================================

def get_calendar(days=CALENDAR_DAYS):
    if not QUANTGIST_API_KEY:
        raise RuntimeError("QUANTGIST_API_KEY is missing.")

    headers = {
        "X-API-Key": QUANTGIST_API_KEY
    }

    params = {
        "events": EVENT_ALIASES,
        "days": days
    }

    response = requests.get(
        QUANTGIST_URL,
        headers=headers,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# IMPORTANT:
# QuantGist returns:
#
# data
#   -> groups
#       -> group["data"]
#           -> actual event records
#
# ============================================================

def flatten_calendar(response_data):
    events = []

    if not isinstance(response_data, dict):
        return events

    groups = response_data.get("data", [])

    if not isinstance(groups, list):
        return events

    for group in groups:
        if not isinstance(group, dict):
            continue

        nested_events = group.get("data", [])

        if isinstance(nested_events, list):
            for event in nested_events:
                if isinstance(event, dict):
                    events.append(event)

    return events


# ============================================================
# EVENT IDENTIFICATION
# ============================================================

def identify_event_type(event):
    text = " ".join([
        str(event.get("event_type", "")),
        str(event.get("title", "")),
        str(event.get("title_normalized", "")),
        str(event.get("canonical_id", "")),
        str(event.get("source_event_id", ""))
    ]).lower()

    if "fomc" in text or "fed" in text:
        return "FOMC"

    if "non-farm" in text or "nonfarm" in text or "payroll" in text:
        return "NFP"

    if "consumer price" in text or "cpi" in text:
        return "CPI"

    if "pce" in text:
        return "PCE"

    if "unemployment" in text:
        return "UNEMPLOYMENT"

    if "gross domestic" in text or "gdp" in text:
        return "GDP"

    if "retail sales" in text:
        return "RETAIL_SALES"

    if "producer price" in text or "ppi" in text:
        return "PPI"

    if "ism" in text or "manufacturing" in text or "services pmi" in text:
        return "ISM"

    return str(event.get("event_type", "MACRO")).upper()


# ============================================================
# EDUCATIONAL MACRO ANALYSIS
# ============================================================

PROFILES = {

    "NFP": {
        "what": "Measures changes in U.S. non-farm employment.",
        "higher": (
            "A stronger-than-expected result can indicate a stronger labor "
            "market and may increase expectations for tighter U.S. monetary policy."
        ),
        "lower": (
            "A weaker-than-expected result can indicate labor-market cooling "
            "and may increase expectations for easier U.S. monetary policy."
        ),
    },

    "CPI": {
        "what": "Measures consumer-price inflation.",
        "higher": (
            "Higher-than-expected inflation can increase expectations that "
            "the central bank may keep policy tighter for longer."
        ),
        "lower": (
            "Lower-than-expected inflation can reduce pressure for restrictive "
            "policy and may increase expectations for future easing."
        ),
    },

    "PCE": {
        "what": "Measures consumer inflation and is closely watched by the Federal Reserve.",
        "higher": (
            "A stronger inflation reading can reinforce expectations for "
            "restrictive monetary policy."
        ),
        "lower": (
            "A softer reading can reduce expectations for restrictive policy."
        ),
    },

    "FOMC": {
        "what": "Reports the Federal Reserve's monetary-policy decision.",
        "higher": (
            "A more hawkish-than-expected decision can support the U.S. dollar "
            "and push U.S. yields higher."
        ),
        "lower": (
            "A more dovish-than-expected decision can weigh on the U.S. dollar "
            "and push U.S. yields lower."
        ),
    },

    "UNEMPLOYMENT": {
        "what": "Measures the percentage of the labor force that is unemployed.",
        "higher": (
            "A higher unemployment rate generally signals a softer labor market "
            "and can increase expectations for easier policy."
        ),
        "lower": (
            "A lower unemployment rate generally signals a stronger labor market "
            "and can reduce expectations for easier policy."
        ),
    },

    "GDP": {
        "what": "Measures the growth of economic output.",
        "higher": (
            "Stronger growth can support expectations for a resilient economy "
            "and potentially less need for monetary easing."
        ),
        "lower": (
            "Weaker growth can increase concerns about economic slowdown "
            "and potentially increase expectations for easier policy."
        ),
    },

    "RETAIL_SALES": {
        "what": "Measures consumer spending through retail activity.",
        "higher": (
            "Stronger consumer spending can signal stronger economic activity "
            "and may support expectations for tighter policy."
        ),
        "lower": (
            "Weaker consumer spending can signal economic cooling "
            "and may support expectations for easier policy."
        ),
    },

    "PPI": {
        "what": "Measures changes in producer-level prices.",
        "higher": (
            "Higher producer inflation can increase concern about persistent "
            "inflationary pressure."
        ),
        "lower": (
            "Lower producer inflation can reduce concern about inflationary pressure."
        ),
    },

    "ISM": {
        "what": "Measures business activity through the ISM survey.",
        "higher": (
            "A stronger reading generally signals stronger business activity."
        ),
        "lower": (
            "A weaker reading generally signals softer business activity."
        ),
    },
}


def build_analysis(event, released=False):
    event_type = identify_event_type(event)

    profile = PROFILES.get(
        event_type,
        {
            "what": "A major economic or monetary-policy release.",
            "higher": (
                "A stronger-than-expected result may indicate stronger "
                "economic conditions."
            ),
            "lower": (
                "A weaker-than-expected result may indicate softer "
                "economic conditions."
            ),
        }
    )

    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    lines = []

    lines.append(f"📊 *Fundamental Analysis — {event_type}*")
    lines.append("")
    lines.append(f"*What it measures:* {profile['what']}")
    lines.append("")

    lines.append(f"*Previous:* {display_value(previous)}")
    lines.append(f"*Forecast:* {display_value(forecast)}")
    lines.append(f"*Actual:* {display_value(actual)}")
    lines.append("")

    if released and actual is not None:

        lines.append("📌 *Release assessment*")

        if forecast is not None:
            try:
                actual_num = float(actual)
                forecast_num = float(forecast)

                if actual_num > forecast_num:
                    lines.append("• Actual came in ABOVE forecast.")
                    lines.append(f"• {profile['higher']}")

                elif actual_num < forecast_num:
                    lines.append("• Actual came in BELOW forecast.")
                    lines.append(f"• {profile['lower']}")

                else:
                    lines.append("• Actual matched forecast.")
                    lines.append(
                        "• The release was broadly in line with expectations."
                    )

            except (ValueError, TypeError):
                lines.append(
                    "• Actual and forecast are available but are not "
                    "simple numeric values for direct comparison."
                )

        else:
            lines.append(
                "• Actual is available, but no forecast was supplied "
                "by the calendar."
            )

    else:

        lines.append("🔮 *Before the release*")

        if forecast is not None:
            lines.append(
                "• The market will compare the actual figure with the forecast."
            )

            lines.append(
                f"• Above forecast: {profile['higher']}"
            )

            lines.append(
                f"• Below forecast: {profile['lower']}"
            )

        else:
            lines.append(
                "• No forecast is currently available from the calendar."
            )

        lines.append(
            "• The initial market reaction can change as traders "
            "interpret the number alongside other economic data."
        )

    return "\n".join(lines)


# ============================================================
# EVENT FORMAT
# ============================================================

def format_event(event, include_analysis=True):
    title = event.get("title", "Economic Event")
    country = event.get("country", "")
    currency = event.get("currency", "")
    impact = event.get("impact", "unknown")

    dt = event_time(event)

    if dt:
        time_text = dt.strftime("%d %b %Y • %H:%M UTC")
    else:
        time_text = "Time unavailable"

    released = bool(event.get("is_released"))
    event_type = identify_event_type(event)

    lines = [
        f"📰 *{title}*",
        f"Type: {event_type}",
        f"Country: {country or 'N/A'}",
        f"Currency: {currency or 'N/A'}",
        f"Impact: {impact}",
        f"Release: {time_text}",
        f"Status: {'RELEASED' if released else 'UPCOMING'}",
        "",
        f"Previous: {display_value(event.get('previous'))}",
        f"Forecast: {display_value(event.get('forecast'))}",
        f"Actual: {display_value(event.get('actual'))}",
    ]

    if include_analysis:
        lines.append("")
        lines.append(build_analysis(event, released))

    return "\n".join(lines)


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = (
        "👋 *Welcome to FundamentalX*\n\n"
        "Your economic-calendar and macro-analysis assistant.\n\n"
        "Commands:\n"
        "• /today — today's high-impact events\n"
        "• /next — upcoming high-impact events\n\n"
        "FundamentalX compares Previous, Forecast and Actual "
        "and explains possible market reactions."
    )

    await update.message.reply_text(
        message,
        parse_mode="Markdown"
    )


async def next_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:
        response_data = get_calendar(CALENDAR_DAYS)
        events = flatten_calendar(response_data)

        now = datetime.now(timezone.utc)

        upcoming = []

        for event in events:
            if not is_high_impact(event):
                continue

            dt = event_time(event)

            if dt and dt >= now:
                upcoming.append(event)

        upcoming.sort(
            key=lambda event: event_time(event)
            or datetime.max.replace(tzinfo=timezone.utc)
        )

        if not upcoming:
            await update.message.reply_text(
                "📅 No upcoming high-impact events found."
            )
            return

        # Show up to 8 upcoming events.
        selected = upcoming[:8]

        parts = [
            "📅 *Upcoming High-Impact Events*",
            ""
        ]

        for index, event in enumerate(selected, start=1):
            parts.append(
                f"*{index}. {event.get('title', 'Economic Event')}*"
            )

            dt = event_time(event)

            if dt:
                parts.append(
                    f"🕒 {dt.strftime('%d %b %Y • %H:%M UTC')}"
                )

            parts.append(
                f"Impact: {event.get('impact', 'Unknown')}"
            )

            parts.append(
                f"Previous: {display_value(event.get('previous'))}"
            )

            parts.append(
                f"Forecast: {display_value(event.get('forecast'))}"
            )

            parts.append("")

        await update.message.reply_text(
            "\n".join(parts),
            parse_mode="Markdown"
        )

    except Exception as error:

        print("NEXT ERROR:", repr(error))

        await update.message.reply_text(
            "⚠️ I couldn't retrieve the economic calendar right now."
        )


async def today_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:
        response_data = get_calendar(CALENDAR_DAYS)
        events = flatten_calendar(response_data)

        today = datetime.now(timezone.utc).date()

        todays_events = []

        for event in events:
            if not is_high_impact(event):
                continue

            dt = event_time(event)

            if dt and dt.date() == today:
                todays_events.append(event)

        todays_events.sort(
            key=lambda event: event_time(event)
            or datetime.max.replace(tzinfo=timezone.utc)
        )

        if not todays_events:
            await update.message.reply_text(
                "📅 No high-impact events found for today."
            )
            return

        for event in todays_events:
            await update.message.reply_text(
                format_event(event),
                parse_mode="Markdown"
            )

    except Exception as error:

        print("TODAY ERROR:", repr(error))

        await update.message.reply_text(
            "⚠️ I couldn't retrieve today's economic events."
        )


# ============================================================
# HEALTH CHECK FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"FundamentalX is running.")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.environ.get("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Health server running on port {port}")

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing from environment variables."
        )

    if not QUANTGIST_API_KEY:
        raise RuntimeError(
            "QUANTGIST_API_KEY is missing from environment variables."
        )

    import threading

    health_thread = threading.Thread(
        target=run_health_server,
        daemon=True
    )

    health_thread.start()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("next", next_command)
    )

    application.add_handler(
        CommandHandler("today", today_command)
    )

    print("FundamentalX Telegram bot starting...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
