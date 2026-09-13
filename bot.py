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
    "Accept": "application/json",
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
            f"SiftingIO HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    return data.get("events", data.get("data", []))


# =========================
# HELPERS
# =========================

def value_or_unavailable(value):

    if value is None or value == "":
        return "Not available"

    return str(value)


def event_time(event):

    scheduled = event.get("scheduled_at")

    if not scheduled:
        return "Time not available"

    try:
        dt = datetime.fromisoformat(
            scheduled.replace("Z", "+00:00")
        )

        return dt.strftime("%d %b %Y • %H:%M UTC")

    except Exception:
        return str(scheduled)


# =========================
# FUNDAMENTAL EXPLANATIONS
# =========================

def explain_event(name):

    name_lower = name.lower()

    if "fomc rate" in name_lower:

        return (
            "🏦 <b>Why it matters</b>\n"
            "The FOMC decision shows the Federal Reserve's view "
            "of interest rates and monetary policy.\n\n"
            "📌 <b>What markets watch</b>\n"
            "The rate decision, policy statement and guidance about "
            "future monetary policy.\n\n"
            "📈 <b>Possible market scenarios</b>\n"
            "• More hawkish than expected → USD and yields may strengthen.\n"
            "• More dovish than expected → USD and yields may weaken.\n"
            "• In-line decision → the statement and forward guidance "
            "can become the main driver."
        )

    if "fomc minutes" in name_lower:

        return (
            "🏦 <b>Why it matters</b>\n"
            "FOMC minutes provide additional detail about the Fed's "
            "discussion and policy outlook.\n\n"
            "📌 <b>What markets watch</b>\n"
            "Whether policymakers appear more hawkish or dovish "
            "than previously expected."
        )

    if "nonfarm payroll" in name_lower:

        return (
            "👷 <b>Why it matters</b>\n"
            "Nonfarm Payrolls measure changes in employment outside "
            "the farming sector and are one of the most closely "
            "watched US labour-market releases.\n\n"
            "📌 <b>What markets watch</b>\n"
            "Payroll growth, unemployment and wage-related data.\n\n"
            "📈 <b>Possible market scenarios</b>\n"
            "• Stronger labour data → may support USD and higher yields.\n"
            "• Weaker labour data → may pressure USD and yields.\n"
            "• Mixed data → market reaction can depend on the details."
        )

    if "unemployment rate" in name_lower:

        return (
            "👷 <b>Why it matters</b>\n"
            "The unemployment rate provides a key view of US labour "
            "market conditions.\n\n"
            "📌 <b>What markets watch</b>\n"
            "Whether employment conditions are improving or weakening "
            "relative to expectations."
        )

    if "cpi" in name_lower:

        return (
            "📊 <b>Why it matters</b>\n"
            "CPI measures changes in consumer prices and is an important "
            "indicator of inflation.\n\n"
            "📌 <b>What markets watch</b>\n"
            "Whether inflation is accelerating or cooling and what that "
            "could imply for monetary policy."
        )

    if "pce" in name_lower:

        return (
            "📊 <b>Why it matters</b>\n"
            "PCE is an important US inflation measure closely watched "
            "by the Federal Reserve.\n\n"
            "📌 <b>What markets watch</b>\n"
            "Whether inflation is moving toward or away from the Fed's "
            "preferred direction."
        )

    if "gdp" in name_lower:

        return (
            "🏭 <b>Why it matters</b>\n"
            "GDP measures the overall growth of economic activity.\n\n"
            "📌 <b>What markets watch</b>\n"
            "Whether economic growth is accelerating or slowing."
        )

    if "retail sales" in name_lower:

        return (
            "🛍 <b>Why it matters</b>\n"
            "Retail Sales provide information about consumer spending "
            "and economic activity.\n\n"
            "📌 <b>What markets watch</b>\n"
            "Strength or weakness in consumer demand."
        )

    return (
        "📊 <b>Why it matters</b>\n"
        "This is a high-impact economic release that can influence "
        "expectations for economic growth, inflation or monetary policy.\n\n"
        "📌 <b>What markets watch</b>\n"
        "The actual result, expectations and the wider macroeconomic "
        "context."
    )


def format_event(event):

    name = value_or_unavailable(event.get("name"))
    currency = value_or_unavailable(event.get("currency"))
    impact = value_or_unavailable(event.get("impact")).upper()
    agency = value_or_unavailable(event.get("agency"))

    previous = value_or_unavailable(event.get("previous"))
    forecast = value_or_unavailable(event.get("consensus"))
    actual = value_or_unavailable(event.get("actual"))

    message = (
        f"🔴 <b>{name}</b>\n\n"
        f"💵 Currency: {currency}\n"
        f"🔥 Impact: {impact}\n"
        f"🏛 Agency: {agency}\n"
        f"🕐 Scheduled: {event_time(event)}\n\n"
        f"<b>Previous:</b> {previous}\n"
        f"<b>Forecast:</b> {forecast}\n"
        f"<b>Actual:</b> {actual}\n\n"
    )

    message += explain_event(name)

    return message


# =========================
# /START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = (
        "👋 <b>Welcome to FundamentalX</b>\n\n"
        "Your economic-news and fundamental-analysis assistant.\n\n"
        "📅 /today — Today's high-impact events\n"
        "🔜 /next — Upcoming high-impact events\n\n"
        "FundamentalX currently monitors major US economic "
        "releases including FOMC, NFP, GDP, PCE and more.\n\n"
        "⚠️ FundamentalX provides educational market context "
        "and scenario analysis, not personalized financial advice."
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

        today_date = now.strftime("%Y-%m-%d")
        tomorrow_date = (
            now + timedelta(days=1)
        ).strftime("%Y-%m-%d")

        events = get_calendar(
            from_date=today_date,
            to_date=tomorrow_date,
            limit=100
        )

        today_events = []

        for event in events:

            scheduled = event.get("scheduled_at", "")

            if scheduled.startswith(today_date):
                today_events.append(event)

        if not today_events:

            await update.message.reply_text(
                "📅 No high-impact US economic events "
                "found for today."
            )

            return

        message = (
            f"🔴 <b>Today's High-Impact Events</b>\n"
            f"📅 {today_date}\n\n"
        )

        for event in today_events:

            message += format_event(event)
            message += "\n\n──────────────\n\n"

        await update.message.reply_text(
            message,
            parse_mode="HTML"
        )

    except Exception as e:

        print(f"/today error: {e}")

        await update.message.reply_text(
            "❌ I couldn't retrieve today's calendar.\n\n"
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

        events = get_calendar(limit=10)

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
            message += "\n\n──────────────\n\n"

        await update.message.reply_text(
            message,
            parse_mode="HTML"
        )

    except Exception as e:

        print(f"/next error: {e}")

        await update.message.reply_text(
            "❌ I couldn't retrieve upcoming events.\n\n"
            "Check the Render logs for the exact error."
        )


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain"
        )
        self.end_headers()

        self.wfile.write(
            b"FundamentalX is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.getenv("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server running on port {port}"
    )

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

    print(
        "FundamentalX Telegram bot is running."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
