import os
import requests
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"


def get_calendar():
    """Get today's economic calendar from Finnhub."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    params = {
        "from": today,
        "to": today,
        "token": FINNHUB_API_KEY
    }

    response = requests.get(FINNHUB_URL, params=params, timeout=20)
    response.raise_for_status()

    return response.json()


def format_event(event):
    currency = event.get("currency", "N/A")
    name = event.get("event", "Unknown event")
    impact = event.get("impact", "N/A")
    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("prev")

    return (
        f"🔴 {currency} — {name}\n"
        f"Impact: {impact}\n"
        f"Previous: {previous}\n"
        f"Forecast: {forecast}\n"
        f"Actual: {actual}\n"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to FundamentalX.\n\n"
        "I monitor important economic events and provide "
        "fundamental market analysis.\n\n"
        "Commands:\n"
        "/today — Today's economic events\n"
        "/next — Next upcoming events"
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_calendar()
        events = data.get("economicCalendar", [])

        if not events:
            await update.message.reply_text(
                "📅 No economic events found for today."
            )
            return

        # Start with high-impact events only.
        high_impact = [
            event for event in events
            if str(event.get("impact", "")).lower() in
            ("high", "3", "red")
        ]

        if not high_impact:
            await update.message.reply_text(
                "📅 No high-impact economic events found today."
            )
            return

        message = "📊 FUNDAMENTALX — HIGH IMPACT\n\n"

        for event in high_impact[:15]:
            message += format_event(event) + "\n"

        await update.message.reply_text(message)

    except Exception as e:
        print("Calendar error:", e)
        await update.message.reply_text(
            "⚠️ I couldn't retrieve the economic calendar right now."
        )


async def next_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏳ Next-event monitoring will be added after we confirm "
        "the Finnhub calendar connection."
    )


def main():
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY is missing.")

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("next", next_events))

    print("FundamentalX is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
