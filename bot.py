import os
import threading
import requests

from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SIFTING_API_KEY = os.getenv("SIFTING_API_KEY")

SIFTING_URL = "https://api.sifting.io/v1/fnd/economic-calendar"

HEADERS = {
    "X-API-Key": SIFTING_API_KEY,
    "Accept": "application/json",
}


# ============================================================
# EVENT ANALYSIS KNOWLEDGE
# ============================================================

EVENT_ANALYSIS = {

    "FOMC Rate Decision": {
        "what": (
            "The Federal Open Market Committee decides the target range "
            "for the US federal funds rate and communicates its view on "
            "economic conditions and monetary policy."
        ),
        "why": (
            "Interest-rate decisions can significantly change expectations "
            "for US monetary policy and therefore influence the US dollar, "
            "Treasury yields and broader financial markets."
        ),
        "watch": (
            "The market watches the rate decision, policy statement, "
            "economic projections and the tone of the Federal Reserve."
        ),
        "above": (
            "A more hawkish-than-expected decision or guidance can increase "
            "expectations for tighter monetary policy."
        ),
        "below": (
            "A more dovish-than-expected decision or guidance can increase "
            "expectations for easier monetary policy."
        ),
        "inline": (
            "If the decision broadly matches expectations, the reaction may "
            "depend more heavily on the statement and forward guidance."
        ),
    },

    "FOMC Minutes": {
        "what": (
            "The FOMC Minutes provide additional detail about the discussion "
            "and reasoning behind a previous Federal Reserve policy meeting."
        ),
        "why": (
            "They can reveal how policymakers viewed inflation, employment, "
            "growth and the future path of interest rates."
        ),
        "watch": (
            "Markets focus on changes in the balance between hawkish and "
            "dovish views among policymakers."
        ),
        "above": (
            "More hawkish discussion than previously expected can strengthen "
            "expectations for tighter policy."
        ),
        "below": (
            "More dovish discussion can strengthen expectations for easier "
            "policy."
        ),
        "inline": (
            "If the minutes largely confirm what markets already expected, "
            "the reaction may be limited."
        ),
    },

    "CPI": {
        "what": (
            "The Consumer Price Index measures changes in the prices paid "
            "by consumers for a basket of goods and services."
        ),
        "why": (
            "CPI is an important measure of inflation and can influence "
            "expectations about central-bank policy."
        ),
        "watch": (
            "Markets compare the actual inflation reading with the previous "
            "reading and the expected/consensus reading."
        ),
        "above": (
            "Higher-than-expected inflation generally indicates stronger "
            "price pressure and may increase expectations for tighter policy."
        ),
        "below": (
            "Lower-than-expected inflation generally indicates weaker price "
            "pressure and may increase expectations for easier policy."
        ),
        "inline": (
            "A result close to expectations may produce a smaller reaction "
            "unless other details are significant."
        ),
    },

    "PCE": {
        "what": (
            "The Personal Consumption Expenditures Price Index measures "
            "changes in prices paid for goods and services consumed by "
            "households."
        ),
        "why": (
            "PCE inflation is closely watched by the Federal Reserve when "
            "assessing inflation conditions."
        ),
        "watch": (
            "Markets pay particular attention to the core PCE measure, "
            "which excludes food and energy."
        ),
        "above": (
            "Stronger-than-expected PCE inflation can increase expectations "
            "for tighter monetary policy."
        ),
        "below": (
            "Softer-than-expected PCE inflation can increase expectations "
            "for easier monetary policy."
        ),
        "inline": (
            "A result close to expectations may cause a more limited reaction "
            "unless the details change the policy outlook."
        ),
    },

    "Nonfarm Payrolls": {
        "what": (
            "Nonfarm Payrolls measures the monthly change in employment "
            "across much of the US economy, excluding certain categories "
            "such as farm workers."
        ),
        "why": (
            "It is one of the most closely watched indicators of US labor "
            "market conditions."
        ),
        "watch": (
            "Markets compare the employment change with expectations and "
            "also examine wages, unemployment and revisions."
        ),
        "above": (
            "Stronger-than-expected payroll growth generally signals a "
            "stronger labor market and can increase expectations for tighter "
            "monetary policy."
        ),
        "below": (
            "Weaker-than-expected payroll growth generally signals softer "
            "labor-market conditions and can increase expectations for "
            "easier monetary policy."
        ),
        "inline": (
            "If payrolls are close to expectations, wages, unemployment and "
            "revisions can determine the broader interpretation."
        ),
    },

    "Unemployment Rate": {
        "what": (
            "The unemployment rate measures the percentage of the labor "
            "force that is unemployed and actively seeking work."
        ),
        "why": (
            "It provides an important indication of labor-market strength "
            "and economic conditions."
        ),
        "watch": (
            "Markets compare the actual unemployment rate with the previous "
            "reading and expectations."
        ),
        "above": (
            "A higher-than-expected unemployment rate generally signals "
            "weaker labor-market conditions."
        ),
        "below": (
            "A lower-than-expected unemployment rate generally signals "
            "stronger labor-market conditions."
        ),
        "inline": (
            "A reading close to expectations may leave the broader policy "
            "outlook largely unchanged."
        ),
    },

    "GDP": {
        "what": (
            "Gross Domestic Product measures the value of goods and services "
            "produced by an economy."
        ),
        "why": (
            "GDP provides a broad measure of economic growth and activity."
        ),
        "watch": (
            "Markets compare the growth rate with previous readings and "
            "economic expectations."
        ),
        "above": (
            "Stronger-than-expected growth generally indicates stronger "
            "economic activity."
        ),
        "below": (
            "Weaker-than-expected growth generally indicates softer "
            "economic activity."
        ),
        "inline": (
            "Growth close to expectations may have a smaller immediate "
            "effect unless the underlying components are surprising."
        ),
    },

    "Retail Sales": {
        "what": (
            "Retail Sales measures changes in the value of sales made by "
            "retail businesses."
        ),
        "why": (
            "Consumer spending is an important component of economic "
            "activity, making retail sales a useful growth indicator."
        ),
        "watch": (
            "Markets compare the actual result with expectations and examine "
            "the strength of consumer demand."
        ),
        "above": (
            "Stronger-than-expected retail sales generally indicate stronger "
            "consumer spending."
        ),
        "below": (
            "Weaker-than-expected retail sales generally indicate softer "
            "consumer spending."
        ),
        "inline": (
            "A result close to expectations may produce a more limited "
            "reaction."
        ),
    },

    "PPI": {
        "what": (
            "The Producer Price Index measures changes in prices received "
            "by producers for goods and services."
        ),
        "why": (
            "Producer-price changes can provide information about inflation "
            "pressures earlier in the supply chain."
        ),
        "watch": (
            "Markets compare the result with expectations and examine "
            "whether producer-price pressures are accelerating or easing."
        ),
        "above": (
            "Higher-than-expected producer inflation can indicate stronger "
            "upstream price pressure."
        ),
        "below": (
            "Lower-than-expected producer inflation can indicate easing "
            "upstream price pressure."
        ),
        "inline": (
            "A result near expectations may have a smaller immediate impact."
        ),
    },
}


# ============================================================
# HELPERS
# ============================================================

def get_event_analysis(event_name):
    """
    Finds the best analysis profile for an event.
    """

    if event_name in EVENT_ANALYSIS:
        return EVENT_ANALYSIS[event_name]

    name = event_name.lower()

    keyword_map = [
        ("cpi", "CPI"),
        ("consumer price", "CPI"),
        ("pce", "PCE"),
        ("personal consumption", "PCE"),
        ("nonfarm", "Nonfarm Payrolls"),
        ("payroll", "Nonfarm Payrolls"),
        ("unemployment", "Unemployment Rate"),
        ("jobless rate", "Unemployment Rate"),
        ("gdp", "GDP"),
        ("gross domestic", "GDP"),
        ("retail sales", "Retail Sales"),
        ("ppi", "PPI"),
        ("producer price", "PPI"),
        ("fomc minutes", "FOMC Minutes"),
        ("fomc", "FOMC Rate Decision"),
    ]

    for keyword, profile in keyword_map:
        if keyword in name:
            return EVENT_ANALYSIS[profile]

    return {
        "what": (
            "This is an economic indicator or policy-related event that "
            "can provide information about economic conditions."
        ),
        "why": (
            "Markets monitor the release because changes in economic "
            "conditions can influence expectations for monetary policy."
        ),
        "watch": (
            "Markets generally compare the actual release with the previous "
            "reading and the expected/consensus value."
        ),
        "above": (
            "A stronger-than-expected result may change expectations "
            "depending on what the indicator measures."
        ),
        "below": (
            "A weaker-than-expected result may change expectations "
            "depending on what the indicator measures."
        ),
        "inline": (
            "If the result is close to expectations, the market may focus "
            "more heavily on the details and wider economic context."
        ),
    }


def format_value(value):
    """
    Safely formats economic values without inventing missing data.
    """

    if value is None:
        return "Not available"

    if isinstance(value, str) and not value.strip():
        return "Not available"

    return str(value)


def parse_datetime(value):
    """
    Converts SiftingIO timestamps into a readable UTC time.
    """

    if not value:
        return "Time unavailable"

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        dt = dt.astimezone(timezone.utc)

        return dt.strftime("%d %b %Y, %H:%M UTC")

    except Exception:
        return str(value)


def build_analysis(event):
    """
    Builds the educational fundamental analysis for one event.
    """

    name = event.get("name", "Unknown event")
    analysis = get_event_analysis(name)

    previous = event.get("previous")
    forecast = event.get("consensus")
    actual = event.get("actual")

    previous_text = format_value(previous)
    forecast_text = format_value(forecast)
    actual_text = format_value(actual)

    result_section = ""

    # --------------------------------------------------------
    # RELEASED DATA ANALYSIS
    # --------------------------------------------------------

    if actual is not None and forecast is not None:

        try:
            actual_num = float(actual)
            forecast_num = float(forecast)

            if actual_num > forecast_num:
                result_section = (
                    "📊 **Result:** Above expectations\n\n"
                    f"{analysis['above']}"
                )

            elif actual_num < forecast_num:
                result_section = (
                    "📊 **Result:** Below expectations\n\n"
                    f"{analysis['below']}"
                )

            else:
                result_section = (
                    "📊 **Result:** In line with expectations\n\n"
                    f"{analysis['inline']}"
                )

        except (ValueError, TypeError):

            result_section = (
                "📊 **Result:** Actual and forecast are available, "
                "but they could not be compared automatically.\n\n"
                f"{analysis['inline']}"
            )

    # --------------------------------------------------------
    # PRE-RELEASE ANALYSIS
    # --------------------------------------------------------

    else:

        result_section = (
            "📊 **Before the release**\n\n"
            "The actual result is not available yet. "
            "The key comparison after release will be the Actual result "
            "versus the Forecast/Consensus, alongside the Previous reading."
        )

    return (
        f"📌 **{name}**\n\n"

        f"📚 **What it is**\n"
        f"{analysis['what']}\n\n"

        f"🎯 **Why it matters**\n"
        f"{analysis['why']}\n\n"

        f"👀 **What markets watch**\n"
        f"{analysis['watch']}\n\n"

        f"📈 **Previous:** {previous_text}\n"
        f"🔮 **Forecast:** {forecast_text}\n"
        f"📊 **Actual:** {actual_text}\n\n"

        f"{result_section}\n\n"

        "⚠️ **Context matters:** Economic releases do not guarantee a "
        "specific market reaction. Markets can also react to positioning, "
        "other economic data, central-bank communication and information "
        "already priced into the market."
    )


# ============================================================
# SIFTINGIO CALENDAR
# ============================================================

def get_calendar(start_date=None, end_date=None, impact="high", limit=50):

    if not SIFTING_API_KEY:
        raise RuntimeError("SIFTING_API_KEY is not configured.")

    params = {
        "impact": impact,
        "limit": limit,
    }

    if start_date:
        params["from"] = start_date

    if end_date:
        params["to"] = end_date

    response = requests.get(
        SIFTING_URL,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):
        if "data" in data:
            return data["data"]

        if "events" in data:
            return data["events"]

    if isinstance(data, list):
        return data

    return []


# ============================================================
# EVENT FORMATTER
# ============================================================

def format_event(event, include_analysis=False):

    name = event.get("name", "Unknown event")
    currency = event.get("currency") or "N/A"
    impact = event.get("impact") or "N/A"
    agency = event.get("agency") or "N/A"
    scheduled = parse_datetime(event.get("scheduled_at"))

    text = (
        f"🔴 **{name}**\n"
        f"💵 Currency: {currency}\n"
        f"⚠️ Impact: {impact}\n"
        f"🏛 Agency: {agency}\n"
        f"🕒 Scheduled: {scheduled}\n\n"
        f"Previous: {format_value(event.get('previous'))}\n"
        f"Forecast: {format_value(event.get('consensus'))}\n"
        f"Actual: {format_value(event.get('actual'))}"
    )

    if include_analysis:
        text += "\n\n" + build_analysis(event)

    return text


# ============================================================
# /START
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = (
        "👋 **Welcome to FundamentalX**\n\n"

        "FundamentalX monitors major economic events and explains "
        "what they mean from a fundamental perspective.\n\n"

        "📅 /today — Today's high-impact events\n"
        "⏭ /next — Upcoming high-impact events\n\n"

        "The analysis is educational and focuses on economic data, "
        "expectations and possible market implications."
    )

    await update.message.reply_text(
        message,
        parse_mode="Markdown"
    )


# ============================================================
# /TODAY
# ============================================================

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        now = datetime.now(timezone.utc)

        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        events = get_calendar(
            start_date=today,
            end_date=tomorrow,
            impact="high",
            limit=50,
        )

        if not events:

            await update.message.reply_text(
                "📅 No high-impact US economic events found for today."
            )

            return

        message_parts = [
            "📅 **TODAY'S HIGH-IMPACT EVENTS**\n"
        ]

        for event in events:

            message_parts.append(
                format_event(
                    event,
                    include_analysis=True
                )
            )

        message = "\n\n━━━━━━━━━━━━━━━━━━\n\n".join(
            message_parts
        )

        await update.message.reply_text(
            message[:4000],
            parse_mode="Markdown"
        )

    except Exception as e:

        print("TODAY ERROR:", e)

        await update.message.reply_text(
            "❌ I couldn't retrieve today's economic calendar right now."
        )


# ============================================================
# /NEXT
# ============================================================

async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        now = datetime.now(timezone.utc)

        start_date = now.strftime("%Y-%m-%d")
        end_date = (now + timedelta(days=30)).strftime("%Y-%m-%d")

        events = get_calendar(
            start_date=start_date,
            end_date=end_date,
            impact="high",
            limit=50,
        )

        if not events:

            await update.message.reply_text(
                "📅 No upcoming high-impact US economic events were found."
            )

            return

        # Sort by scheduled release time
        events = sorted(
            events,
            key=lambda x: x.get("scheduled_at") or ""
        )

        message_parts = [
            "⏭ **UPCOMING HIGH-IMPACT EVENTS**\n"
        ]

        for event in events:

            message_parts.append(
                format_event(
                    event,
                    include_analysis=True
                )
            )

        message = "\n\n━━━━━━━━━━━━━━━━━━\n\n".join(
            message_parts
        )

        # Telegram messages have a size limit.
        # Send in chunks.
        chunks = []

        while len(message) > 3900:

            split_at = message.rfind(
                "\n\n━━━━━━━━━━━━━━━━━━\n\n",
                0,
                3900
            )

            if split_at == -1:
                split_at = 3900

            chunks.append(message[:split_at])
            message = message[split_at:]

        chunks.append(message)

        for chunk in chunks:

            await update.message.reply_text(
                chunk,
                parse_mode="Markdown"
            )

    except Exception as e:

        print("NEXT ERROR:", e)

        await update.message.reply_text(
            "❌ I couldn't retrieve the upcoming economic calendar right now."
        )


# ============================================================
# HEALTH CHECK FOR RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.end_headers()

        self.wfile.write(
            b"FundamentalX is running."
        )

    def log_message(self, format, *args):
        return


def run_health_server():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    if not SIFTING_API_KEY:

        raise RuntimeError(
            "SIFTING_API_KEY is not configured."
        )

    # Start Render health server
    health_thread = threading.Thread(
        target=run_health_server,
        daemon=True
    )

    health_thread.start()

    # Build Telegram application
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "today",
            today_command
        )
    )

    application.add_handler(
        CommandHandler(
            "next",
            next_command
        )
    )

    print("FundamentalX starting...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
