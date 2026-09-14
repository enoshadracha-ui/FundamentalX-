import os
import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUANTGIST_API_KEY = os.getenv("QUANTGIST_API_KEY")

QUANTGIST_BASE_URL = "https://api.quantgist.com/v1"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

if not QUANTGIST_API_KEY:
    raise RuntimeError("Missing QUANTGIST_API_KEY")


# ============================================================
# EVENT EDUCATIONAL ANALYSIS
# ============================================================

EVENT_ANALYSIS = {
    "FOMC": {
        "title": "FOMC Rate Decision",
        "context": (
            "The Federal Reserve's rate decision is important because it "
            "affects expectations for US interest rates, liquidity and the US dollar."
        ),
        "before": (
            "Before the release, compare the expected decision with the current "
            "rate environment and recent inflation/employment conditions. "
            "The statement and press conference can matter as much as the rate itself."
        ),
        "reaction": (
            "A larger-than-expected tightening signal can support the dollar, "
            "while a more dovish-than-expected outcome can weaken dollar expectations. "
            "Market reaction depends on how the result differs from what was already priced in."
        ),
    },

    "CPI": {
        "title": "US CPI",
        "context": (
            "Consumer Price Index data measures changes in consumer prices and is "
            "one of the major inflation indicators watched by the Federal Reserve."
        ),
        "before": (
            "Compare the forecast with recent inflation trends and the Fed's stated "
            "inflation concerns. Core inflation can also be important because it excludes food and energy."
        ),
        "reaction": (
            "Inflation materially above expectations can increase expectations for "
            "tighter monetary policy, while a weaker result can reduce those expectations. "
            "The size of the surprise matters."
        ),
    },

    "PCE": {
        "title": "US PCE Price Index",
        "context": (
            "PCE inflation is one of the Federal Reserve's preferred inflation measures."
        ),
        "before": (
            "Compare the expected reading with recent inflation data and the Fed's "
            "current policy stance."
        ),
        "reaction": (
            "A stronger-than-expected inflation reading can raise expectations for "
            "tighter policy. A weaker reading can have the opposite effect, depending on the broader data."
        ),
    },

    "NFP": {
        "title": "US Nonfarm Payrolls",
        "context": (
            "Nonfarm Payrolls measures the monthly change in employment outside the farm sector "
            "and is one of the most closely watched US labour-market releases."
        ),
        "before": (
            "Compare the employment forecast with recent labour-market trends. "
            "The unemployment rate and wage growth can provide additional context."
        ),
        "reaction": (
            "A significant upside employment surprise can strengthen expectations for a resilient "
            "economy and potentially firmer monetary policy. A downside surprise can shift expectations "
            "in the opposite direction."
        ),
    },

    "UNEMPLOYMENT": {
        "title": "US Unemployment Rate",
        "context": (
            "The unemployment rate provides a broad measure of labour-market conditions."
        ),
        "before": (
            "Compare the forecast with recent unemployment trends and other labour-market indicators."
        ),
        "reaction": (
            "A lower-than-expected unemployment rate can indicate stronger labour conditions, "
            "while a higher-than-expected rate can indicate weakening conditions. "
            "The wider employment report should be considered as well."
        ),
    },

    "GDP": {
        "title": "US GDP",
        "context": (
            "Gross Domestic Product measures the growth of economic activity."
        ),
        "before": (
            "Compare the expected growth rate with recent economic activity and revisions."
        ),
        "reaction": (
            "Stronger-than-expected growth can support expectations for a resilient economy. "
            "Weaker growth can increase concerns about economic slowdown."
        ),
    },

    "RETAIL_SALES": {
        "title": "US Retail Sales",
        "context": (
            "Retail Sales provides information about consumer spending."
        ),
        "before": (
            "Compare the forecast with recent consumer-spending trends and economic conditions."
        ),
        "reaction": (
            "Stronger consumer spending can suggest resilient demand, while weaker spending "
            "can point toward softer economic activity."
        ),
    },

    "PPI": {
        "title": "US PPI",
        "context": (
            "Producer Price Index measures changes in prices received by producers "
            "and can provide information about upstream inflation pressure."
        ),
        "before": (
            "Compare the expected result with recent producer and consumer inflation trends."
        ),
        "reaction": (
            "A stronger inflation reading can increase inflation concerns, while a weaker "
            "reading can reduce some inflation pressure expectations."
        ),
    },

    "ISM": {
        "title": "US ISM",
        "context": (
            "ISM surveys provide information about business activity and economic conditions."
        ),
        "before": (
            "Compare the forecast with recent business surveys and broader growth trends."
        ),
        "reaction": (
            "A stronger reading can suggest improving economic activity, while a weaker "
            "reading can indicate softer conditions."
        ),
    },
}


# ============================================================
# HELPERS
# ============================================================

def safe_float(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    text = (
        text.replace(",", "")
        .replace("%", "")
        .replace("K", "")
        .replace("k", "")
        .replace("M", "")
        .replace("m", "")
    )

    try:
        return float(text)
    except ValueError:
        return None


def display_value(value):
    if value is None or value == "":
        return "Not available"

    return str(value)


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


def event_key(event):
    return str(
        event.get("canonical_id")
        or event.get("event_id")
        or event.get("id")
        or event.get("title")
        or ""
    )


def identify_profile(event):
    title = str(
        event.get("title")
        or event.get("name")
        or event.get("event")
        or ""
    ).lower()

    canonical = str(event.get("canonical_id") or "").upper()

    if "FOMC" in canonical or "fomc" in title:
        if "minute" in title:
            return EVENT_ANALYSIS.get("FOMC")
        return EVENT_ANALYSIS.get("FOMC")

    if "NFP" in canonical or "nonfarm" in title or "non-farm" in title:
        return EVENT_ANALYSIS.get("NFP")

    if "CPI" in canonical or "consumer price" in title:
        return EVENT_ANALYSIS.get("CPI")

    if "PCE" in canonical or "personal consumption" in title:
        return EVENT_ANALYSIS.get("PCE")

    if "UNEMPLOYMENT" in canonical or "unemployment" in title:
        return EVENT_ANALYSIS.get("UNEMPLOYMENT")

    if "GDP" in canonical or "gross domestic product" in title:
        return EVENT_ANALYSIS.get("GDP")

    if "RETAIL" in canonical or "retail sales" in title:
        return EVENT_ANALYSIS.get("RETAIL_SALES")

    if "PPI" in canonical or "producer price" in title:
        return EVENT_ANALYSIS.get("PPI")

    if "ISM" in canonical or "ism" in title:
        return EVENT_ANALYSIS.get("ISM")

    return None


# ============================================================
# QUANTGIST API
# ============================================================

def quantgist_request(path, params=None):
    url = QUANTGIST_BASE_URL + path

    headers = {
        "X-API-Key": QUANTGIST_API_KEY,
        "Accept": "application/json",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params or {},
        timeout=20,
    )

    response.raise_for_status()

    return response.json()


def extract_events(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in ("data", "events", "results", "items"):
        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


def get_calendar(days=14):
    data = quantgist_request(
        "/macro/calendar",
        {
            "events": "NFP,CPI,PCE,FOMC,GDP,UNEMPLOYMENT,"
                      "RETAIL_SALES,PPI,ISM",
            "days": days,
        },
    )

    return extract_events(data)


# ============================================================
# EVENT ANALYSIS
# ============================================================

def build_analysis(event):
    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    profile = identify_profile(event)

    lines = []

    lines.append("📊 FUNDAMENTAL ANALYSIS")

    lines.append(
        f"Previous: {display_value(previous)}\n"
        f"Forecast: {display_value(forecast)}\n"
        f"Actual: {display_value(actual)}"
    )

    actual_num = safe_float(actual)
    forecast_num = safe_float(forecast)

    if actual_num is not None and forecast_num is not None:
        difference = actual_num - forecast_num

        if difference > 0:
            comparison = "ABOVE FORECAST"
        elif difference < 0:
            comparison = "BELOW FORECAST"
        else:
            comparison = "IN LINE WITH FORECAST"

        lines.append(
            f"\n📌 Result: {comparison}\n"
            f"Surprise: {difference:+g}"
        )

    if profile:
        lines.append(f"\n🧠 Context\n{profile['context']}")

        if actual_num is None:
            lines.append(
                f"\n⏳ Before release\n{profile['before']}"
            )
        else:
            lines.append(
                f"\n📈 Possible market interpretation\n"
                f"{profile['reaction']}"
            )
    else:
        if actual_num is None:
            lines.append(
                "\n⏳ Before release\n"
                "Compare the forecast with recent economic conditions "
                "and previous readings."
            )
        else:
            lines.append(
                "\n📈 Interpretation\n"
                "Compare the actual result with the forecast and previous "
                "reading. The size of the surprise and broader macro context "
                "can influence market reaction."
            )

    lines.append(
        "\n⚠️ Educational information only. "
        "Market reactions are not guaranteed and can differ from the "
        "initial economic interpretation."
    )

    return "\n".join(lines)


# ============================================================
# EVENT FORMAT
# ============================================================

def format_event(event, detailed=False):
    title = (
        event.get("title")
        or event.get("name")
        or event.get("event")
        or "Unknown Event"
    )

    currency = event.get("currency") or "N/A"
    impact = event.get("impact") or "N/A"

    release_time = (
        event.get("release_time")
        or event.get("scheduled_at")
        or event.get("date")
    )

    dt = parse_datetime(release_time)

    if dt:
        time_text = dt.strftime("%d %b %Y %H:%M UTC")
    else:
        time_text = str(release_time or "Unknown")

    actual = event.get("actual")
    forecast = event.get("forecast")
    previous = event.get("previous")

    text = (
        f"📰 {title}\n"
        f"💵 Currency: {currency}\n"
        f"🔴 Impact: {impact}\n"
        f"🕐 Release: {time_text}\n\n"
        f"Previous: {display_value(previous)}\n"
        f"Forecast: {display_value(forecast)}\n"
        f"Actual: {display_value(actual)}"
    )

    if detailed:
        text += "\n\n" + build_analysis(event)

    return text


# ============================================================
# COMMANDS
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🤖 FundamentalX\n\n"
        "Your economic-calendar and fundamental-analysis assistant.\n\n"
        "Commands:\n"
        "/today - Today's high-impact events\n"
        "/next - Upcoming major events\n\n"
        "FundamentalX provides macroeconomic context and scenario analysis. "
        "It does not execute trades."
    )

    await update.message.reply_text(message)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        events = get_calendar(days=1)

        now = datetime.now(timezone.utc)
        today = now.date()

        today_events = []

        for event in events:
            impact = str(event.get("impact") or "").lower()

            if impact != "high":
                continue

            release_time = (
                event.get("release_time")
                or event.get("scheduled_at")
                or event.get("date")
            )

            dt = parse_datetime(release_time)

            if dt and dt.date() == today:
                today_events.append(event)

        if not today_events:
            await update.message.reply_text(
                "📅 No high-impact events found for today."
            )
            return

        today_events.sort(
            key=lambda e: parse_datetime(
                e.get("release_time")
                or e.get("scheduled_at")
                or e.get("date")
            ) or datetime.max.replace(tzinfo=timezone.utc)
        )

        for event in today_events:
            await update.message.reply_text(
                format_event(event, detailed=True)
            )

    except Exception as exc:
        print("TODAY ERROR:", exc)

        await update.message.reply_text(
            "⚠️ I couldn't retrieve today's economic calendar right now."
        )


async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        events = get_calendar(days=14)

        upcoming = []

        now = datetime.now(timezone.utc)

        for event in events:
            impact = str(event.get("impact") or "").lower()

            if impact != "high":
                continue

            release_time = (
                event.get("release_time")
                or event.get("scheduled_at")
                or event.get("date")
            )

            dt = parse_datetime(release_time)

            if dt and dt >= now:
                upcoming.append((dt, event))

        upcoming.sort(key=lambda item: item[0])

        if not upcoming:
            await update.message.reply_text(
                "📅 No upcoming high-impact events found."
            )
            return

        # Avoid flooding Telegram.
        upcoming = upcoming[:8]

        for _, event in upcoming:
            await update.message.reply_text(
                format_event(event, detailed=True)
            )

    except Exception as exc:
        print("NEXT ERROR:", exc)

        await update.message.reply_text(
            "⚠️ I couldn't retrieve upcoming economic events right now."
        )


# ============================================================
# HEALTH SERVER FOR RENDER
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
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():
    threading.Thread(
        target=run_health_server,
        daemon=True,
    ).start()

    application = Application.builder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("today", today_command)
    )

    application.add_handler(
        CommandHandler("next", next_command)
    )

    print("FundamentalX started.")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
