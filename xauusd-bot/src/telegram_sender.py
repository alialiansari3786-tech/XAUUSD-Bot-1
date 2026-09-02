"""
telegram_sender.py
Sends formatted setup alerts to your Telegram bot, including chart screenshots.

Requires env vars (set as GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
from src.retry_utils import retry_with_backoff
from src.risk_management import calculate_position_size

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

API_BASE = "https://api.telegram.org/bot{token}"
REQUEST_TIMEOUT_SECONDS = 15


@retry_with_backoff(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def send_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    url = API_BASE.format(token=BOT_TOKEN) + "/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()


@retry_with_backoff(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def send_photo(image_path: str, caption: str = "") -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    url = API_BASE.format(token=BOT_TOKEN) + "/sendPhoto"
    with open(image_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
            files={"photo": f},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    resp.raise_for_status()


def render_chart(df, levels: dict, out_path: str = "/tmp/setup_chart.png", title: str = "XAUUSD Setup") -> str:
    """Renders a candlestick chart with key levels marked as horizontal lines."""
    add_plots = []
    hlines = {"hlines": [], "colors": [], "linestyle": "--"}
    for name, price in levels.items():
        if price is not None:
            hlines["hlines"].append(price)
            hlines["colors"].append("orange")

    mpf.plot(
        df.tail(150),
        type="candle",
        style="charles",
        title=title,
        hlines=hlines if hlines["hlines"] else None,
        savefig=out_path,
    )
    return out_path


def format_setup_message(method_name: str, direction: str, entry: float, sl: float, tp: float,
                          alignment_summary: str, structure_summary: str) -> str:
    try:
        sizing = calculate_position_size(entry, sl)
        if sizing["viable"]:
            sizing_block = (
                f"*Position Size:* {sizing['lot_size']} lots "
                f"(risking ${sizing['dollar_risk']} = {sizing['risk_percent_used']}% of ${sizing['balance_used']})\n\n"
            )
        else:
            sizing_block = (
                f"*Position Size:* ⚠️ NOT VIABLE at current settings - "
                f"even the minimum lot size (0.01) would risk ${sizing['min_lot_risk']}, "
                f"more than your {sizing['risk_percent_used']}% (${sizing['dollar_risk']}) limit on ${sizing['balance_used']}. "
                f"Stop is too wide for this account size at this risk %.\n\n"
            )
    except Exception as e:
        # never let a missing/broken config file block the alert itself -
        # the setup info is still useful even without a sizing suggestion
        sizing_block = f"*Position Size:* unavailable ({e})\n\n"

    return (
        f"🚨 *{method_name} Setup Detected*\n\n"
        f"*Direction:* {direction.upper()}\n"
        f"*Entry:* {entry:.2f}\n"
        f"*Stop Loss:* {sl:.2f}\n"
        f"*Take Profit:* {tp:.2f}\n\n"
        f"{sizing_block}"
        f"*Timeframe Alignment:*\n{alignment_summary}\n\n"
        f"*Structure:*\n{structure_summary}"
    )
