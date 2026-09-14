import os
import threading
import requests

from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUANTGIST_API_KEY = os.getenv("QUANTGIST_API_KEY")

QUANTGIST_BASE_URL = "https://api.quantgist.com/v1"


if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

if not QUANTGIST_API_KEY:
    raise RuntimeError("QUANTGIST_API_KEY is missing")


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


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "FundamentalX is online.\n\n"
        "Use /debug to inspect QuantGist."
    )


async def debug_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        data = get_calendar(14)

        groups = data.get("data", [])

        lines = [
            "🔎 QUANTGIST DEBUG 3",
            "",
            f"Groups returned: {len(groups)}",
            "",
        ]

        for group in groups:
            alias = group.get("alias", "UNKNOWN")
            label = group.get("label", "Unknown")
            country = group.get("country", "Unknown")
            count = group.get("count", 0)

            nested_events = group.get("data", [])

            lines.append(
                f"{alias} | {label} | {country}"
            )

            lines.append(
                f"Count: {count} | Nested: {len(nested_events)}"
            )

            if nested_events:
                event = nested_events[0]

                lines.append(
                    f"First event keys: {list(event.keys())}"
                )

                for key, value in event.items():
                    if key.lower() not in {
                        "api_key",
                        "key",
                        "token",
                        "authorization",
                        "password",
                        "secret",
                    }:
                        lines.append(
                            f"{key}: {value}"
                        )

                lines.append("")

        message = "\n".join(lines)

        await update.message.reply_text(
            message[:4000]
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
