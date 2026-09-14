import os
import time
import threading
import logging
import asyncio
from datetime import datetime, timezone, timedelta

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUANTGIST_API_KEY = os.getenv("QUANTGIST_API_KEY")

CALENDAR_URL = "https://api.quantgist.com/v1/macro/calendar"
EVENTS_URL = "https://api.quantgist.com/v2/events"

PORT = int(os.getenv("PORT", "10000"))

CALENDAR_CACHE_SECONDS = 15 * 60
DETAIL_CACHE_SECONDS = 15 * 60
MONITOR_INTERVAL_SECONDS = 60

ALERT_WINDOWS = {
    "24H": 24 * 60,
    "1H": 60,
    "15M": 15,
}

EVENT_KEYWORDS = [
    "NFP",
    "NON-FARM",
    "PAYROLL",
    "CPI",
    "CONSUMER PRICE",
    "PCE",
    "PERSONAL CONSUMPTION",
    "FOMC",
    "FEDERAL RESERVE",
    "RATE DECISION",
    "GDP",
    "UNEMPLOYMENT",
    "JOBLESS",
    "RETAIL SALES",
    "PPI",
    "PRODUCER PRICE",
    "ISM",
]

SUBSCRIBERS = set()
SENT_ALERTS = set()

CALENDAR_CACHE = {
    "timestamp": 0,
    "events": [],
}

EVENT_DETAIL_CACHE = {}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# TIME / TEXT HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def parse_datetime(value):
    if not value:
        return None

    try:
        value = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def format_datetime(value):
    dt = parse_datetime(value)

    if not dt:
        return "Unknown"

    return dt.strftime("%d %b %Y • %H:%M UTC")


def safe_value(value):
    if value is None or value == "":
        return "Not available"

    return str(value)


def normalize_text(value):
    return str(value or "").strip().lower()


def get_event_title(event):
    return (
        event.get("title")
        or event.get("event_type")
        or event.get("title_normalized")
        or "Economic Event"
    )


# ============================================================
# API
# ============================================================

def api_get(url, params=None):
    if not QUANTGIST_API_KEY:
        raise RuntimeError("QUANTGIST_API_KEY is missing.")

    headers = {
        "X-API-Key": QUANTGIST_API_KEY,
        "Accept": "application/json",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# CALENDAR
# ============================================================

def flatten_calendar(payload):
    groups = payload.get("data", [])

    events = []

    if isinstance(groups, list):

        for group in groups:

            if not isinstance(group, dict):
                continue

            group_data = group.get("data", [])

            if isinstance(group_data, list):
                events.extend(group_data)

    return events


def fetch_calendar_from_api():
    """
    Fetch the QuantGist calendar.

    No days parameter is sent here because the calendar endpoint
    already returns its available calendar data.
    """

    payload = api_get(CALENDAR_URL)

    return flatten_calendar(payload)


def get_calendar(days=30, force=False):
    """
    Cached calendar.

    The calendar is refreshed every 15 minutes instead of every
    60 seconds so the free API allowance is not exhausted.
    """

    current_time = time.time()

    cache_age = (
        current_time - CALENDAR_CACHE["timestamp"]
    )

    if (
        not force
        and CALENDAR_CACHE["events"]
        and cache_age < CALENDAR_CACHE_SECONDS
    ):
        return CALENDAR_CACHE["events"]

    try:

        events = fetch_calendar_from_api()

        CALENDAR_CACHE["events"] = events
        CALENDAR_CACHE["timestamp"] = current_time

        logger.info(
            "Calendar refreshed: %s raw events",
            len(events),
        )

        return events

    except Exception as exc:

        logger.error(
            "Calendar request failed: %s",
            exc,
        )

        return CALENDAR_CACHE["events"]


# ============================================================
# EVENT DETAIL
# ============================================================

def get_event_detail_by_canonical(
    canonical_id,
    target_release_time=None,
):

    if not canonical_id:
        return None

    try:

        payload = api_get(
            EVENTS_URL,
            params={
                "canonical_id": canonical_id,
                "per_page": 50,
            },
        )

        records = payload.get("data", [])

        if not isinstance(records, list):
            return None

        target_dt = parse_datetime(
            target_release_time
        )

        # Match the detail record to the EXACT upcoming
        # calendar release instead of taking data[0].
        if target_dt:

            best_record = None
            best_difference = None

            for record in records:

                record_dt = parse_datetime(
                    record.get("release_time")
                )

                if not record_dt:
                    continue

                difference = abs(
                    (
                        record_dt - target_dt
                    ).total_seconds()
                )

                if difference <= 5 * 60:

                    if (
                        best_difference is None
                        or difference < best_difference
                    ):
                        best_record = record
                        best_difference = difference

            return best_record

        # If no target date was provided, find the next future record.
        current = now_utc()

        future_records = []

        for record in records:

            record_dt = parse_datetime(
                record.get("release_time")
            )

            if record_dt and record_dt >= current:
                future_records.append(
                    (record_dt, record)
                )

        if future_records:

            future_records.sort(
                key=lambda item: item[0]
            )

            return future_records[0][1]

        return None

    except Exception as exc:

        logger.error(
            "Event detail request failed: %s",
            exc,
        )

        return None


def merge_event_data(event, detail):
    """
    Only fundamental fields are taken from the detail endpoint.

    The calendar remains the source of truth for event identity
    and release time.
    """

    merged = dict(event)

    allowed_fields = [
        "actual",
        "forecast",
        "previous",
        "revised_previous",
        "surprise_pct",
        "surprise_score",
        "sentiment_score",
        "sentiment_label",
        "has_actual",
        "has_forecast",
    ]

    for field in allowed_fields:

        if field in detail:

            value = detail.get(field)

            if value is not None:
                merged[field] = value

    return merged


def get_event_detail(event):

    canonical_id = event.get("canonical_id")
    release_time = event.get("release_time")

    if not canonical_id:
        return event

    cache_key = (
        f"{canonical_id}:{release_time}"
    )

    current_time = time.time()

    cached = EVENT_DETAIL_CACHE.get(cache_key)

    if cached:

        if (
            current_time - cached["timestamp"]
            < DETAIL_CACHE_SECONDS
        ):

            detail = cached.get("detail")

            if detail:
                return merge_event_data(
                    event,
                    detail,
                )

    detail = get_event_detail_by_canonical(
        canonical_id,
        release_time,
    )

    EVENT_DETAIL_CACHE[cache_key] = {
        "timestamp": current_time,
        "detail": detail,
    }

    if not detail:
        return event

    return merge_event_data(
        event,
        detail,
    )


# ============================================================
# UPCOMING EVENTS
# ============================================================

def get_upcoming_events(days=30):
    """
    Return upcoming major economic events.

    IMPORTANT:
    The QuantGist calendar is the source of truth.

    An event is NOT rejected just because the detail endpoint
    doesn't contain forecast/previous/actual yet.
    """

    events = get_calendar(days)

    current = now_utc()
    end_time = current + timedelta(days=days)

    upcoming = []

    for event in events:

        if not isinstance(event, dict):
            continue

        release_time = (
            event.get("release_time")
            or event.get("date")
            or event.get("datetime")
            or event.get("scheduled_at")
        )

        release_dt = parse_datetime(release_time)

        if not release_dt:
            continue

        # Future events only.
        if release_dt < current:
            continue

        if release_dt > end_time:
            continue

        title = normalize_text(
            event.get("title")
            or event.get("event_type")
            or event.get("title_normalized")
            or ""
        )

        canonical = normalize_text(
            event.get("canonical_id")
            or ""
        )

        combined = f"{title} {canonical}"

        # Only major events we care about.
        important_event = any(
            keyword.lower() in combined
            for keyword in EVENT_KEYWORDS
        )

        if not important_event:
            continue

        # Missing impact is allowed.
        # Only explicitly low-impact events are rejected.
        impact = normalize_text(
            event.get("impact")
        )

        if impact in {
            "low",
            "1",
            "minor",
        }:
            continue

        final_event = dict(event)

        # Try to add actual/forecast/previous.
        # Failure here MUST NOT remove the event.
        try:

            enriched = get_event_detail(event)

            if enriched:
                final_event.update(enriched)

        except Exception as exc:

            logger.warning(
                "Detail enrichment failed for %s: %s",
                title,
                exc,
            )

        # Calendar release time ALWAYS wins.
        final_event["release_time"] = release_time

        if event.get("canonical_id"):
            final_event["canonical_id"] = (
                event.get("canonical_id")
            )

        if event.get("title"):
            final_event["title"] = (
                event.get("title")
            )

        if event.get("impact") is not None:
            final_event["impact"] = (
                event.get("impact")
            )

        upcoming.append(final_event)

    # Remove duplicates.
    unique = {}

    for event in upcoming:

        key = (
            event.get("canonical_id")
            or event.get("title"),
            event.get("release_time"),
        )

        unique[key] = event

    upcoming = list(unique.values())

    upcoming.sort(
        key=lambda event: (
            parse_datetime(
                event.get("release_time")
            )
            or datetime.max.replace(
                tzinfo=timezone.utc
            )
        )
    )

    return upcoming


def get_today_events():

    current = now_utc()

    start = current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    end = start + timedelta(days=1)

    # Today needs a slightly wider window because
    # get_upcoming_events only returns future events.
    events = get_upcoming_events(days=2)

    result = []

    for event in events:

        release_dt = parse_datetime(
            event.get("release_time")
        )

        if not release_dt:
            continue

        if start <= release_dt < end:
            result.append(event)

    return result


# ============================================================
# ANALYSIS
# ============================================================

def classify_event(event):

    title = normalize_text(
        get_event_title(event)
    )

    canonical = normalize_text(
        event.get("canonical_id")
    )

    combined = f"{title} {canonical}"

    if (
        "nfp" in combined
        or "non-farm" in combined
        or "payroll" in combined
    ):
        return "NFP"

    if "cpi" in combined:
        return "CPI"

    if "pce" in combined:
        return "PCE"

    if (
        "fomc" in combined
        or "rate decision" in combined
        or "federal reserve" in combined
    ):
        return "FOMC"

    if "gdp" in combined:
        return "GDP"

    if (
        "unemployment" in combined
        or "jobless" in combined
    ):
        return "UNEMPLOYMENT"

    if "retail sales" in combined:
        return "RETAIL_SALES"

    if (
        "ppi" in combined
        or "producer price" in combined
    ):
        return "PPI"

    if "ism" in combined:
        return "ISM"

    return "GENERAL"


def build_analysis(event, post_release=False):

    event_type = classify_event(event)
    title = get_event_title(event)

    previous = event.get("previous")
    forecast = event.get("forecast")
    actual = event.get("actual")

    lines = []

    if post_release:

        lines.append("📊 Post-Release Analysis")
        lines.append("")
        lines.append(f"Event: {title}")
        lines.append("")
        lines.append(
            f"Previous: {safe_value(previous)}"
        )
        lines.append(
            f"Forecast: {safe_value(forecast)}"
        )
        lines.append(
            f"Actual: {safe_value(actual)}"
        )
        lines.append("")

        if actual is not None and forecast is not None:

            try:

                actual_num = float(actual)
                forecast_num = float(forecast)

                if actual_num > forecast_num:
                    result = "Above forecast"

                elif actual_num < forecast_num:
                    result = "Below forecast"

                else:
                    result = "In line with forecast"

                lines.append(
                    f"Result: {result}"
                )

            except Exception:

                lines.append(
                    "Result: Compare Actual with Forecast."
                )

        lines.append("")

        if event_type == "NFP":

            lines.append("Why it matters:")
            lines.append(
                "The employment result can influence "
                "expectations about economic strength "
                "and monetary policy."
            )

        elif event_type in {"CPI", "PCE", "PPI"}:

            lines.append("Why it matters:")
            lines.append(
                "Inflation data can influence expectations "
                "about future interest-rate policy."
            )

        elif event_type == "FOMC":

            lines.append("Why it matters:")
            lines.append(
                "The rate decision, statement and press "
                "conference can change expectations about "
                "the Fed's policy path."
            )

        elif event_type == "GDP":

            lines.append("Why it matters:")
            lines.append(
                "GDP provides information about the pace "
                "of economic growth."
            )

        elif event_type == "UNEMPLOYMENT":

            lines.append("Why it matters:")
            lines.append(
                "The unemployment rate provides information "
                "about labour-market conditions."
            )

        else:

            lines.append("Why it matters:")
            lines.append(
                "The release can change expectations about "
                "economic conditions and monetary policy."
            )

        lines.append("")
        lines.append(
            "Market reaction depends on the size of the "
            "surprise, revisions, positioning and the "
            "wider macro environment."
        )

    else:

        lines.append("📚 Pre-Release Analysis")
        lines.append("")
        lines.append(f"Event: {title}")
        lines.append(
            f"Previous: {safe_value(previous)}"
        )
        lines.append(
            f"Forecast: {safe_value(forecast)}"
        )
        lines.append("")

        if event_type == "NFP":

            lines.append("Why it matters:")
            lines.append(
                "NFP measures changes in US non-farm "
                "employment and is closely watched "
                "as a labour-market indicator."
            )

        elif event_type in {"CPI", "PCE"}:

            lines.append("Why it matters:")
            lines.append(
                "Inflation data helps markets assess "
                "price pressures and possible monetary "
                "policy changes."
            )

        elif event_type == "PPI":

            lines.append("Why it matters:")
            lines.append(
                "Producer-price data provides information "
                "about upstream price pressures."
            )

        elif event_type == "FOMC":

            lines.append("Why it matters:")
            lines.append(
                "The FOMC communicates the Federal Reserve's "
                "interest-rate stance and policy outlook."
            )

        elif event_type == "GDP":

            lines.append("Why it matters:")
            lines.append(
                "GDP measures economic growth and provides "
                "information about economic activity."
            )

        elif event_type == "UNEMPLOYMENT":

            lines.append("Why it matters:")
            lines.append(
                "The unemployment rate provides information "
                "about labour-market conditions."
            )

        elif event_type == "RETAIL_SALES":

            lines.append("Why it matters:")
            lines.append(
                "Retail sales provide information about "
                "consumer spending."
            )

        else:

            lines.append("Why it matters:")
            lines.append(
                "The release can affect expectations about "
                "economic conditions and monetary policy."
            )

        lines.append("")
        lines.append("Scenario framework:")

        lines.append(
            "• Above forecast → expectations may shift "
            "toward stronger conditions or tighter policy, "
            "depending on the event."
        )

        lines.append(
            "• Near forecast → attention may shift toward "
            "revisions, details and wider macro context."
        )

        lines.append(
            "• Below forecast → expectations may shift "
            "toward weaker conditions or less restrictive "
            "policy, depending on the event."
        )

    lines.append("")
    lines.append(
        "⚠️ Educational macro analysis only — not a "
        "guaranteed market direction or personalized "
        "trade signal."
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM FORMATTING
# ============================================================

def event_summary(event, number=None):

    title = get_event_title(event)

    release_time = format_datetime(
        event.get("release_time")
    )

    impact = safe_value(
        event.get("impact")
    )

    previous = safe_value(
        event.get("previous")
    )

    forecast = safe_value(
        event.get("forecast")
    )

    prefix = ""

    if number is not None:
        prefix = f"{number}. "

    return (
        f"{prefix}{title}\n"
        f"🕒 {release_time}\n"
        f"Impact: {impact}\n"
        f"Previous: {previous}\n"
        f"Forecast: {forecast}"
    )


def alert_message(event, window_name):

    title = get_event_title(event)

    release_time = format_datetime(
        event.get("release_time")
    )

    previous = safe_value(
        event.get("previous")
    )

    forecast = safe_value(
        event.get("forecast")
    )

    analysis = build_analysis(
        event,
        post_release=False,
    )

    return (
        f"🔔 FUNDAMENTALX ALERT — {window_name}\n\n"
        f"📌 {title}\n"
        f"🕒 {release_time}\n\n"
        f"Previous: {previous}\n"
        f"Forecast: {forecast}\n\n"
        f"{analysis}"
    )


def release_message(event):

    title = get_event_title(event)

    release_time = format_datetime(
        event.get("release_time")
    )

    analysis = build_analysis(
        event,
        post_release=True,
    )

    return (
        "🚨 FUNDAMENTALX — RELEASED\n\n"
        f"📌 {title}\n"
        f"🕒 {release_time}\n\n"
        f"{analysis}"
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(update, context):

    chat_id = update.effective_chat.id

    SUBSCRIBERS.add(chat_id)

    await update.message.reply_text(
        "👋 Welcome to FundamentalX.\n\n"
        "I monitor major high-impact economic releases "
        "and provide educational macro analysis.\n\n"
        "🔔 Automatic alerts are now ON.\n\n"
        "Commands:\n"
        "/next — upcoming high-impact events\n"
        "/today — today's events\n"
        "/analysis — next event analysis\n"
        "/alerts — turn alerts on\n"
        "/stopalerts — turn alerts off\n"
        "/help — show commands"
    )


async def help_command(update, context):

    await update.message.reply_text(
        "📚 FundamentalX Commands\n\n"
        "/start — start the bot and enable alerts\n"
        "/next — upcoming high-impact events\n"
        "/today — today's high-impact events\n"
        "/analysis — analysis of the next event\n"
        "/alerts — enable automatic alerts\n"
        "/stopalerts — disable automatic alerts\n"
        "/help — show this menu"
    )


async def alerts_command(update, context):

    chat_id = update.effective_chat.id

    SUBSCRIBERS.add(chat_id)

    await update.message.reply_text(
        "🔔 Automatic FundamentalX alerts are ON."
    )


async def stopalerts_command(update, context):

    chat_id = update.effective_chat.id

    SUBSCRIBERS.discard(chat_id)

    await update.message.reply_text(
        "🔕 Automatic FundamentalX alerts are OFF."
    )


async def next_command(update, context):

    try:

        events = get_upcoming_events(days=30)

        if not events:

            await update.message.reply_text(
                "No upcoming high-impact events found."
            )

            return

        lines = [
            "📅 Upcoming High-Impact Events",
            "",
        ]

        for index, event in enumerate(
            events[:10],
            start=1,
        ):

            lines.append(
                event_summary(
                    event,
                    index,
                )
            )

            lines.append("")

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as exc:

        logger.error(
            "/next failed: %s",
            exc,
        )

        await update.message.reply_text(
            "⚠️ Unable to retrieve the economic calendar right now."
        )


async def today_command(update, context):

    try:

        events = get_today_events()

        if not events:

            await update.message.reply_text(
                "📅 No high-impact events found for today."
            )

            return

        lines = [
            "📅 Today's High-Impact Events",
            "",
        ]

        for index, event in enumerate(
            events,
            start=1,
        ):

            lines.append(
                event_summary(
                    event,
                    index,
                )
            )

            lines.append("")

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as exc:

        logger.error(
            "/today failed: %s",
            exc,
        )

        await update.message.reply_text(
            "⚠️ Unable to retrieve today's events."
        )


async def analysis_command(update, context):

    try:

        events = get_upcoming_events(days=30)

        if not events:

            await update.message.reply_text(
                "No upcoming high-impact event found."
            )

            return

        event = events[0]

        await update.message.reply_text(
            build_analysis(
                event,
                post_release=False,
            )
        )

    except Exception as exc:

        logger.error(
            "/analysis failed: %s",
            exc,
        )

        await update.message.reply_text(
            "⚠️ Unable to generate analysis right now."
        )


# ============================================================
# ALERT SYSTEM
# ============================================================

def alert_key(event, window_name):

    return (
        event.get("canonical_id")
        or event.get("title"),
        event.get("release_time"),
        window_name,
    )


def release_alert_key(event):

    return (
        event.get("canonical_id")
        or event.get("title"),
        event.get("release_time"),
        "RELEASE",
    )


async def send_to_subscribers(
    application,
    message,
):

    if not SUBSCRIBERS:
        return

    for chat_id in list(SUBSCRIBERS):

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=message,
            )

        except Exception as exc:

            logger.error(
                "Failed sending alert to %s: %s",
                chat_id,
                exc,
            )


async def monitor_events(application):

    logger.info(
        "FundamentalX alert monitor started."
    )

    while True:

        try:

            events = get_upcoming_events(
                days=30
            )

            current = now_utc()

            for event in events:

                release_dt = parse_datetime(
                    event.get("release_time")
                )

                if not release_dt:
                    continue

                seconds_remaining = (
                    release_dt - current
                ).total_seconds()

                minutes_remaining = (
                    seconds_remaining / 60
                )

                # -------------------------------
                # PRE-RELEASE ALERTS
                # -------------------------------

                for (
                    window_name,
                    target_minutes,
                ) in ALERT_WINDOWS.items():

                    if abs(
                        minutes_remaining
                        - target_minutes
                    ) <= 2:

                        key = alert_key(
                            event,
                            window_name,
                        )

                        if key in SENT_ALERTS:
                            continue

                        SENT_ALERTS.add(key)

                        await send_to_subscribers(
                            application,
                            alert_message(
                                event,
                                window_name,
                            ),
                        )

                # -------------------------------
                # POST-RELEASE ALERT
                # -------------------------------

                if (
                    -15
                    <= minutes_remaining
                    <= 0
                ):

                    actual = event.get("actual")

                    if actual is not None:

                        key = release_alert_key(
                            event
                        )

                        if key not in SENT_ALERTS:

                            SENT_ALERTS.add(key)

                            await send_to_subscribers(
                                application,
                                release_message(
                                    event
                                ),
                            )

        except Exception as exc:

            logger.error(
                "Alert monitor error: %s",
                exc,
            )

        await asyncio.sleep(
            MONITOR_INTERVAL_SECONDS
        )


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

def start_health_server():

    from http.server import (
        BaseHTTPRequestHandler,
        HTTPServer,
    )

    class HealthHandler(
        BaseHTTPRequestHandler
    ):

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

        def log_message(
            self,
            format,
            *args,
        ):
            return

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler,
    )

    logger.info(
        "Health server running on port %s",
        PORT,
    )

    server.serve_forever()


# ============================================================
# STARTUP
# ============================================================

async def post_init(application):

    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    application.create_task(
        monitor_events(application)
    )

    logger.info(
        "FundamentalX startup complete."
    )


def main():

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    if not QUANTGIST_API_KEY:
        raise RuntimeError(
            "QUANTGIST_API_KEY is missing."
        )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
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
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "next",
            next_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            today_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "analysis",
            analysis_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "alerts",
            alerts_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "stopalerts",
            stopalerts_command,
        )
    )

    logger.info(
        "Starting FundamentalX Telegram bot..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
