import os
import threading
import requests

from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================
# ENVIRONMENT VARIABLES
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SIFTING_API_KEY = os.getenv("SIFTING_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

if not SIFTING_API_KEY:
    raise RuntimeError("SIFTING_API_KEY is missing")


# =========================
# SIFTINGIO
# =========================

SIFTING_URL = "https://api.sifting.io/v1/fnd/economic-calendar"

HEADERS = {
    "X-API-Key": SIFTING_API_KEY,
    "Accept-Encoding": "gzip",
}


def get_calendar(from_date=None, to_date=None, limit=100):
    params = {
        "impact": "high",
        "limit": limit,
    }

    if from_date:
        params["from"] = from_date

    if to_date:
        params["to"] = to_date

    response = requests.get(
        SIFTING_URL,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"SiftingIO returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    # Current API documentation describes the response
    # as an events array.
    return data.get("events", data.get("data", []))


# =========================
# FORMAT HELPERS
# =========================

def clean_value(value):
    if value is None or value == "":
        return "—"

    return str(value)


def format_event(event):
    name = clean_value(event.get("name"))
    currency = clean_value(event.get("currency"))
    impact = clean_value(event.get("impact")).upper()

    scheduled = clean_value(event.get("scheduled_at"))

    actual = clean_value(event.get("actual"))
    previous = clean_value(event.get("previous"))
    consensus = clean_value(event.get("consensus"))

    agency = clean_value(event.get("agency"))

    text = (
        f"🔴 <b>{name}</b>\n"
        f"💵 Currency: {currency}\n"
        f"📊 Impact: {impact}\n"
        f"🏛 Agency: {agency}\n"
        f"🕐 Scheduled: {scheduled}\n\n"
        f"Previous: {previous}\n"
        f"Forecast: {consensus}\n"
        f"Actual: {actual}\n"
    )

    return text


# =========================
# /START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = (
        "👋 <b>Welcome to FundamentalX</b>\n\n"
        "Your economic-calendar and fundamental-analysis assistant.\n\n"
        "📅 /today — Today's high-impact events\n"
        "🔜 /next — Upcoming high-impact events\n\n"
        "FundamentalX currently covers major US economic releases "
        "such as CPI, NFP, FOMC, GDP, PCE, Retail Sales and more."
    )

    await update.message.reply_text(
        message,
        parse_mode="HTML"
    )


# =========================
# /TODAY
# =========================

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔎 Checking today's high-impact economic events..."
    )

    try:
        now = datetime.now(timezone.utc)

        start_date = now.strftime("%Y-%m-%d")

        tomorrow = now + timedelta(days=1)
        end_date = tomorrow.strftime("%Y-%m-%d")

        events = get_calendar(
            from_date=start_date,
            to_date=end_date,
            limit=100,
        )

        # Keep only events scheduled for today.
        today_events = []

        for event in events:

            scheduled = event.get("scheduled_at", "")

            if scheduled.startswith(start_date):
                today_events.append(event)

        if not today_events:
            await update.message.reply_text(
                "📅 No high-impact US economic events found for today."
            )
            return

        message = (
            f"🔴 <b>Today's High-Impact Events</b>\n"
            f"📅 {start_date}\n\n"
        )

        for event in today_events:
            message += format_event(event)
            message += "\n──────────────\n\n"

        await update.message.reply_text(
            message,
            parse_mode="HTML"
        )

    except Exception as e:

        print(f"/today error: {e}")

        await update.message.reply_text(
            "❌ I couldn't retrieve the economic calendar right now.\n\n"
            "Check the Render logs for the exact error."
        )


# =========================
# /NEXT
# =========================

async def next_events(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🔎 Checking upcoming high-impact events..."
    )

    try:
        events = get_calendar(
            limit=10
        )

        if not events:
            await update.message.reply_text(
                "📅 No upcoming high-impact events found."
            )
            return

        message = (
            "🔜 <b>Upcoming High-Impact Events</b>\n\n"
        )

        for event in events[:10]:
            message += format_event(event)
            message += "\n──────────────\n\n"

        await update.message.reply_text(
            message,
            parse_mode="HTML"
        )

    except Exception as e:

        print(f"/next error: {e}")

        await update.message.reply_text(
            "❌ I couldn't retrieve upcoming events right now.\n\n"
            "Check the Render logs for the exact error."
        )


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

        self.wfile.write(
            b"FundamentalX is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"Health server running on port {port}")

    server.serve_forever()


# =========================
# MAIN
# =========================

def main():

    print("Starting FundamentalX...")

    threading.Thread(
        target=start_health_server,
        daemon=True
    ).start()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("today", today)
    )

    application.add_handler(
        CommandHandler("next", next_events)
    )

    print("FundamentalX Telegram bot is running.")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
