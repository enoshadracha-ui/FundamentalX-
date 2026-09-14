import os
import time
import threading
import logging
from datetime import datetime, timezone, timedelta

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ============================================================
# FUNDAMENTALX
# Economic Calendar + Macro Analysis + Telegram Alerts
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUANTGIST_API_KEY = os.getenv("QUANTGIST_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

if not QUANTGIST_API_KEY:
    raise RuntimeError("QUANTGIST_API_KEY is missing")


# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

CALENDAR_URL = "https://api.quantgist.com/v1/macro/calendar"
EVENTS_URL = "https://api.quantgist.com/v2/events"

EVENT_ALIASES = [
    "NFP",
    "CPI",
    "PCE",
    "FOMC",
    "GDP",
    "UNEMPLOYMENT",
    "RETAIL_SALES",
    "PPI",
    "ISM",
]

HIGH_IMPACT = "high"

# How often the bot checks the calendar
CHECK_INTERVAL_SECONDS = 60

# Alert windows
ALERT_WINDOWS = [
    ("24H", 24 * 60),
    ("1H", 60),
    ("15M", 15),
]

# Only these chats receive automatic alerts after /start
SUBSCRIBERS = set()

# Prevent duplicate alerts
SENT_ALERTS = set()

# Cache to reduce unnecessary API requests
EVENT_CACHE = {}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("FundamentalX")


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

def headers():
    return {
        "X-API-Key": QUANTGIST_API_KEY,
        "Accept": "application/json",
        "User-Agent": "FundamentalX/1.0",
    }


def api_get(url, params=None, timeout=20):
    try:
        response = requests.get(
            url,
            headers=headers(),
            params=params,
            timeout=timeout,
        )

        if response.status_code != 200:
            logger.error(
                "QuantGist HTTP %s: %s",
                response.status_code,
                response.text[:500],
            )
            return None

        return response.json()

    except Exception as exc:
        logger.error("QuantGist request failed: %s", exc)
        return None


# ------------------------------------------------------------
# CALENDAR
# ------------------------------------------------------------

def get_calendar(days=30):
    params = {
        "events": ",".join(EVENT_ALIASES),
        "days": days,
    }

    return api_get(CALENDAR_URL, params)


def flatten_calendar(response):
    """
    QuantGist /macro/calendar returns:

    data = [
        {
            alias: "...",
            label: "...",
            country: "...",
            data: [event, event, ...]
        }
    ]

    Flatten those groups into event objects.
    """

    if not isinstance(response, dict):
        return []

    groups = response.get("data", [])

    events = []

    if not isinstance(groups, list):
        return events

    for group in groups:
        if not isinstance(group, dict):
            continue

        nested = group.get("data", [])

        if isinstance(nested, list):
            for event in nested:
                if isinstance(event, dict):
                    events.append(event)

    return events


# ------------------------------------------------------------
# EVENT DETAIL
# ------------------------------------------------------------

def get_event_detail_by_canonical(canonical_id):
    if not canonical_id:
        return None

    params = {
        "canonical_id": canonical_id,
        "per_page": 50,
    }

    response = api_get(EVENTS_URL, params)

    if not isinstance(response, dict):
        return None

    data = response.get("data", [])

    if isinstance(data, list) and data:
        # Prefer the closest/current record.
        return data[0]

    return None


def get_event_detail(event):
    """
    First use the values already returned by the calendar.

    If Previous / Forecast / Actual are missing,
    use the documented v2 canonical_id endpoint.
    """

    if not isinstance(event, dict):
        return event

    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    canonical_id = event.get("canonical_id")

    # FOMC and other scheduled events can legitimately
    # have no forecast yet.
    needs_detail = (
        actual is None
        or forecast is None
        or previous is None
    )

    if not needs_detail:
        return event

    if not canonical_id:
        return event

    # Small in-memory cache
    now = time.time()

    cached = EVENT_CACHE.get(canonical_id)

    if cached:
        cached_time, cached_event = cached

        # Cache for 10 minutes
        if now - cached_time < 600:
            merged = dict(event)
            merged.update(cached_event)
            return merged

    detail = get_event_detail_by_canonical(canonical_id)

    if not detail:
        return event

    EVENT_CACHE[canonical_id] = (now, detail)

    merged = dict(event)

    for key in [
        "actual",
        "forecast",
        "previous",
        "revised_previous",
        "surprise_pct",
        "surprise_score",
        "sentiment_score",
        "sentiment_label",
        "impact",
        "symbols",
        "canonical_id",
        "title",
        "release_time",
        "is_released",
        "has_actual",
        "has_forecast",
    ]:
        if detail.get(key) is not None:
            merged[key] = detail[key]

    return merged


# ------------------------------------------------------------
# DATE / VALUE HELPERS
# ------------------------------------------------------------

def parse_datetime(value):
    if not value:
        return None

    try:
        text = str(value)

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def format_value(value):
    if value is None or value == "":
        return "Not available"

    if isinstance(value, float):
        return f"{value:g}"

    return str(value)


def format_time(dt):
    if not dt:
        return "Unknown"

    return dt.strftime("%d %b %Y • %H:%M UTC")


def minutes_until(dt):
    if not dt:
        return None

    now = datetime.now(timezone.utc)

    return (dt - now).total_seconds() / 60


# ------------------------------------------------------------
# EVENT IDENTIFICATION
# ------------------------------------------------------------

def identify_event_type(event):
    title = str(
        event.get("title")
        or event.get("label")
        or event.get("event_type")
        or ""
    ).lower()

    if "nonfarm" in title or "non-farm" in title or "payroll" in title:
        return "NFP"

    if "consumer price" in title or "cpi" in title:
        return "CPI"

    if "pce" in title:
        return "PCE"

    if "fomc" in title or "fed" in title or "rate decision" in title:
        return "FOMC"

    if "gross domestic" in title or "gdp" in title:
        return "GDP"

    if "unemployment" in title:
        return "UNEMPLOYMENT"

    if "retail sales" in title:
        return "RETAIL SALES"

    if "producer price" in title or "ppi" in title:
        return "PPI"

    if "ism" in title:
        return "ISM"

    return "MACRO"


# ------------------------------------------------------------
# SURPRISE
# ------------------------------------------------------------

def numeric(value):
    if value is None:
        return None

    try:
        text = str(value).replace(",", "").replace("%", "").strip()
        return float(text)
    except Exception:
        return None


def calculate_surprise(event):
    actual = numeric(event.get("actual"))
    forecast = numeric(event.get("forecast"))

    if actual is None or forecast is None:
        return None

    if forecast == 0:
        return None

    return ((actual - forecast) / abs(forecast)) * 100


# ------------------------------------------------------------
# EDUCATIONAL MACRO ANALYSIS
# ------------------------------------------------------------

def build_analysis(event, released=False):
    event_type = identify_event_type(event)

    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    surprise = calculate_surprise(event)

    if released and actual is not None:
        if forecast is not None and surprise is not None:

            if surprise > 0:
                surprise_text = (
                    "Actual came in above forecast. "
                    "That is an upside surprise relative to consensus."
                )
            elif surprise < 0:
                surprise_text = (
                    "Actual came in below forecast. "
                    "That is a downside surprise relative to consensus."
                )
            else:
                surprise_text = (
                    "Actual was broadly in line with forecast."
                )

        else:
            surprise_text = (
                "A forecast comparison is not available for this release."
            )

        return (
            f"📊 <b>Post-Release Analysis</b>\n\n"
            f"<b>Event:</b> {event.get('title', 'Unknown')}\n"
            f"<b>Actual:</b> {format_value(actual)}\n"
            f"<b>Forecast:</b> {format_value(forecast)}\n"
            f"<b>Previous:</b> {format_value(previous)}\n\n"
            f"<b>Interpretation:</b>\n"
            f"{surprise_text}\n\n"
            f"<b>Market mechanics:</b>\n"
            f"The reaction depends on how the result changes expectations "
            f"for inflation, growth, employment and central-bank policy. "
            f"A surprise can therefore affect rates, currencies, bonds "
            f"and risk-sensitive assets, but the direction and size of "
            f"the reaction are not guaranteed."
        )

    # Pre-release analysis

    descriptions = {
        "NFP": (
            "NFP is a major employment release. Stronger employment "
            "can increase expectations for economic strength and may "
            "change expectations around Federal Reserve policy."
        ),
        "CPI": (
            "CPI measures consumer-price inflation. A higher-than-expected "
            "inflation reading can increase concern about persistent "
            "inflation and influence expectations for interest rates."
        ),
        "PCE": (
            "PCE inflation is closely watched by the Federal Reserve. "
            "The result can influence expectations about the future path "
            "of monetary policy."
        ),
        "FOMC": (
            "The FOMC decision communicates the Federal Reserve's current "
            "interest-rate stance. The statement and press conference can "
            "matter as much as the rate decision itself."
        ),
        "GDP": (
            "GDP measures economic growth. A stronger result can suggest "
            "greater economic momentum, while a weaker result can increase "
            "concern about slowing activity."
        ),
        "UNEMPLOYMENT": (
            "The unemployment rate provides information about labour-market "
            "conditions and can influence expectations about monetary policy."
        ),
        "RETAIL SALES": (
            "Retail sales provide information about consumer spending and "
            "economic demand."
        ),
        "PPI": (
            "PPI measures producer-level price pressures and can provide "
            "information about inflationary conditions."
        ),
        "ISM": (
            "ISM surveys provide information about business activity and "
            "economic momentum."
        ),
    }

    context = descriptions.get(
        event_type,
        "This is a scheduled macroeconomic release that may influence "
        "market expectations."
    )

    return (
        f"📚 <b>Pre-Release Analysis</b>\n\n"
        f"<b>Event:</b> {event.get('title', 'Unknown')}\n"
        f"<b>Previous:</b> {format_value(previous)}\n"
        f"<b>Forecast:</b> {format_value(forecast)}\n\n"
        f"<b>Why it matters:</b>\n"
        f"{context}\n\n"
        f"<b>Scenario framework:</b>\n"
        f"• Above forecast → expectations may shift toward stronger "
        f"economic conditions or tighter policy, depending on the event.\n"
        f"• Near forecast → the market may focus more heavily on details, "
        f"revisions and central-bank communication.\n"
        f"• Below forecast → expectations may shift toward weaker growth "
        f"or less restrictive policy, depending on the event.\n\n"
        f"⚠️ <b>Important:</b> These are macroeconomic scenarios, "
        f"not guaranteed market directions or personalized trade signals."
    )


# ------------------------------------------------------------
# EVENT FORMAT
# ------------------------------------------------------------

def event_title(event):
    return (
        event.get("title")
        or event.get("label")
        or event.get("event_type")
        or "Unknown Event"
    )


def event_key(event):
    return (
        event.get("id")
        or event.get("canonical_id")
        or event.get("dedupe_key")
        or f"{event_title(event)}:{event.get('release_time')}"
    )


def is_high_impact(event):
    impact = str(event.get("impact", "")).lower()
    return impact == HIGH_IMPACT


def get_upcoming_events(days=30):
    response = get_calendar(days)

    events = flatten_calendar(response)

    results = []

    now = datetime.now(timezone.utc)

    for event in events:

        if not is_high_impact(event):
            continue

        release_time = parse_datetime(
            event.get("release_time")
        )

        if not release_time:
            continue

        if release_time < now:
            continue

        results.append(event)

    results.sort(
        key=lambda x: parse_datetime(x.get("release_time"))
        or datetime.max.replace(tzinfo=timezone.utc)
    )

    return results


# ------------------------------------------------------------
# /START
# ------------------------------------------------------------

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    SUBSCRIBERS.add(chat_id)

    await update.message.reply_text(
        "🤖 <b>FundamentalX is active.</b>\n\n"
        "I monitor high-impact economic events and provide "
        "educational macro analysis.\n\n"
        "<b>Commands:</b>\n"
        "/today — today's high-impact events\n"
        "/next — upcoming high-impact events\n"
        "/analysis — next major event analysis\n"
        "/alerts — enable automatic alerts\n"
        "/stopalerts — stop automatic alerts\n"
        "/help — show commands\n\n"
        "Automatic alerts are now enabled for this chat.",
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /HELP
# ------------------------------------------------------------

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🤖 <b>FundamentalX Commands</b>\n\n"
        "/start — start the bot and enable alerts\n"
        "/today — today's high-impact events\n"
        "/next — upcoming high-impact events\n"
        "/analysis — analysis of the next major event\n"
        "/alerts — enable automatic alerts\n"
        "/stopalerts — disable automatic alerts\n"
        "/help — show this menu",
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /ALERTS
# ------------------------------------------------------------

async def alerts_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    SUBSCRIBERS.add(chat_id)

    await update.message.reply_text(
        "🔔 Automatic FundamentalX alerts are ON.",
    )


async def stopalerts_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    SUBSCRIBERS.discard(chat_id)

    await update.message.reply_text(
        "🔕 Automatic FundamentalX alerts are OFF.",
    )


# ------------------------------------------------------------
# /NEXT
# ------------------------------------------------------------

async def next_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    events = get_upcoming_events(days=30)

    if not events:
        await update.message.reply_text(
            "📅 No upcoming high-impact events found."
        )
        return

    lines = [
        "📅 <b>Upcoming High-Impact Events</b>\n"
    ]

    for index, raw_event in enumerate(events[:10], start=1):

        event = get_event_detail(raw_event)

        release_time = parse_datetime(
            event.get("release_time")
        )

        lines.append(
            f"<b>{index}. {event_title(event)}</b>\n"
            f"🕒 {format_time(release_time)}\n"
            f"Impact: {event.get('impact', 'unknown')}\n"
            f"Previous: {format_value(event.get('previous'))}\n"
            f"Forecast: {format_value(event.get('forecast'))}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /TODAY
# ------------------------------------------------------------

async def today_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    events = get_upcoming_events(days=1)

    today = datetime.now(timezone.utc).date()

    filtered = []

    for raw_event in events:

        event = get_event_detail(raw_event)

        dt = parse_datetime(event.get("release_time"))

        if dt and dt.date() == today:
            filtered.append(event)

    if not filtered:
        await update.message.reply_text(
            "📅 No upcoming high-impact events scheduled today."
        )
        return

    lines = [
        "📅 <b>Today's High-Impact Events</b>\n"
    ]

    for event in filtered:

        dt = parse_datetime(event.get("release_time"))

        lines.append(
            f"<b>{event_title(event)}</b>\n"
            f"🕒 {format_time(dt)}\n"
            f"Previous: {format_value(event.get('previous'))}\n"
            f"Forecast: {format_value(event.get('forecast'))}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# /ANALYSIS
# ------------------------------------------------------------

async def analysis_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    events = get_upcoming_events(days=30)

    if not events:
        await update.message.reply_text(
            "No upcoming high-impact event found."
        )
        return

    event = get_event_detail(events[0])

    analysis = build_analysis(event, released=False)

    await update.message.reply_text(
        analysis,
        parse_mode="HTML",
    )


# ------------------------------------------------------------
# AUTOMATIC ALERT ENGINE
# ------------------------------------------------------------

def alert_window_label(minutes):
    if minutes <= 15:
        return "15M"

    if minutes <= 60:
        return "1H"

    return "24H"


async def send_alert(
    application,
    event,
    window_label,
):
    if not SUBSCRIBERS:
        return

    key = event_key(event)

    alert_id = f"{key}:{window_label}"

    if alert_id in SENT_ALERTS:
        return

    release_time = parse_datetime(
        event.get("release_time")
    )

    if not release_time:
        return

    analysis = build_analysis(
        event,
        released=False,
    )

    message = (
        f"🚨 <b>FUNDAMENTALX ALERT</b>\n\n"
        f"<b>{event_title(event)}</b>\n"
        f"🕒 {format_time(release_time)}\n"
        f"Impact: {event.get('impact', 'high')}\n\n"
        f"<b>Previous:</b> "
        f"{format_value(event.get('previous'))}\n"
        f"<b>Forecast:</b> "
        f"{format_value(event.get('forecast'))}\n\n"
        f"⏳ <b>{window_label} before release</b>\n\n"
        f"{analysis}"
    )

    for chat_id in list(SUBSCRIBERS):
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error(
                "Failed alert to %s: %s",
                chat_id,
                exc,
            )

    SENT_ALERTS.add(alert_id)


async def send_release_alert(
    application,
    event,
):
    key = event_key(event)

    alert_id = f"{key}:RELEASED"

    if alert_id in SENT_ALERTS:
        return

    if not event.get("actual"):
        return

    if not SUBSCRIBERS:
        return

    analysis = build_analysis(
        event,
        released=True,
    )

    message = (
        f"🔴 <b>FUNDAMENTALX RELEASE</b>\n\n"
        f"<b>{event_title(event)}</b>\n\n"
        f"<b>Actual:</b> "
        f"{format_value(event.get('actual'))}\n"
        f"<b>Forecast:</b> "
        f"{format_value(event.get('forecast'))}\n"
        f"<b>Previous:</b> "
        f"{format_value(event.get('previous'))}\n\n"
        f"{analysis}"
    )

    for chat_id in list(SUBSCRIBERS):
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error(
                "Failed release alert to %s: %s",
                chat_id,
                exc,
            )

    SENT_ALERTS.add(alert_id)


async def monitor_events(application):
    """
    Background monitor.

    Runs continuously while the Render service is alive.
    """

    logger.info("FundamentalX alert monitor started.")

    while True:

        try:
            events = get_upcoming_events(days=7)

            for raw_event in events:

                event = get_event_detail(raw_event)

                release_time = parse_datetime(
                    event.get("release_time")
                )

                if not release_time:
                    continue

                remaining = minutes_until(release_time)

                if remaining is None:
                    continue

                # Future alerts
                if remaining > 0:

                    for label, target_minutes in ALERT_WINDOWS:

                        # Give a 2-minute tolerance window
                        if (
                            target_minutes - 1
                            <= remaining
                            <= target_minutes + 1
                        ):
                            await send_alert(
                                application,
                                event,
                                label,
                            )

                # Release alert
                elif remaining <= 0:

                    # Only inspect events that are around release time.
                    if remaining >= -10:

                        released_event = get_event_detail(event)

                        if released_event.get("actual") is not None:
                            await send_release_alert(
                                application,
                                released_event,
                            )

        except Exception as exc:
            logger.exception(
                "Alert monitor error: %s",
                exc,
            )

        await asyncio_sleep(CHECK_INTERVAL_SECONDS)


# ------------------------------------------------------------
# SIMPLE ASYNC SLEEP
# ------------------------------------------------------------

async def asyncio_sleep(seconds):
    import asyncio
    await asyncio.sleep(seconds)


# ------------------------------------------------------------
# RENDER HEALTH SERVER
# ------------------------------------------------------------

def start_health_server():
    """
    Render Web Services expect an HTTP listener.
    """

    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        port = int(os.getenv("PORT", "10000"))

        class Handler(BaseHTTPRequestHandler):

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

        server = HTTPServer(
            ("0.0.0.0", port),
            Handler,
        )

        logger.info(
            "Health server listening on port %s",
            port,
        )

        server.serve_forever()

    except Exception as exc:
        logger.error(
            "Health server error: %s",
            exc,
        )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():

    # Start Render health server
    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("today", today_command)
    )

    application.add_handler(
        CommandHandler("next", next_command)
    )

    application.add_handler(
        CommandHandler("analysis", analysis_command)
    )

    application.add_handler(
        CommandHandler("alerts", alerts_command)
    )

    application.add_handler(
        CommandHandler(
            "stopalerts",
            stopalerts_command,
        )
    )

    async def post_init(app):
        app.create_task(
            monitor_events(app)
        )

    application.post_init = post_init

    logger.info("FundamentalX starting...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
