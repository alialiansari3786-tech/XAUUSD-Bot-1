"""
news_awareness.py
Reads a manually-maintained calendar of high-impact ("red folder") USD
economic events from config/high_impact_events.json, and provides two
protections against news-driven volatility that structure-based logic
alone can't anticipate:

1. is_within_blackout_window() - don't fire brand-new signals right
   before/after a known high-impact release, since the resulting spike
   is unpredictable and isn't a real structural setup.
2. get_nearby_event() - if a release is coming up while a new trade would
   likely still be open, widen the SL buffer rather than using a plain
   ATR distance, which isn't sized for a scheduled volatility event.

REAL INCIDENT this addresses: a live trade's SL "barely survived" a 6PM
CPI release - the bot had zero awareness that high-impact news was
imminent when it opened that trade.

No external API dependency (Finnhub's free-tier calendar access could not
be confirmed from documentation, and Forex Factory/Investing.com have no
public API and scraping either raises ToS/fragility concerns - see
conversation notes). High-impact USD event dates are published by
BLS/the Federal Reserve months in advance on a fixed schedule, so a
manually-updated file is both free and genuinely reliable.
"""

import json
import os
import datetime

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "high_impact_events.json")

# How long before/after a listed event to refuse firing brand-new signals.
BLACKOUT_BEFORE_MINUTES = 30
BLACKOUT_AFTER_MINUTES = 15

# How far ahead to look when deciding whether to widen a new trade's SL
# because a release might land while the trade is still open.
SL_WIDEN_LOOKAHEAD_MINUTES = 240   # 4 hours - covers most intraday holding periods
SL_WIDEN_MULTIPLIER = 1.75         # extra room on top of the normal ATR-based buffer

# Ignore events more than this far in the past - keeps a stale config
# (the user forgot to prune old entries) from ever causing problems.
IGNORE_PAST_HOURS = 24


def _load_events() -> list:
    if not os.path.exists(CONFIG_PATH):
        return []
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        events = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for e in data.get("events", []):
            if e.get("name", "").startswith("Example:"):
                continue  # skip the placeholder examples shipped in the template
            try:
                dt = datetime.datetime.fromisoformat(e["datetime_utc"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if (now - dt).total_seconds() > IGNORE_PAST_HOURS * 3600:
                continue  # long past, ignore rather than let a stale entry misbehave
            events.append({"name": e.get("name", "Unnamed event"), "datetime_utc": dt})
        return events
    except (json.JSONDecodeError, IOError):
        print("  [news_awareness] Could not read high_impact_events.json - proceeding as if no events are scheduled")
        return []


def is_within_blackout_window() -> tuple:
    """
    Checks whether right now falls within BLACKOUT_BEFORE_MINUTES before or
    BLACKOUT_AFTER_MINUTES after any listed event. Returns (bool, event_name_or_None).
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    for event in _load_events():
        minutes_to_event = (event["datetime_utc"] - now).total_seconds() / 60
        if -BLACKOUT_AFTER_MINUTES <= minutes_to_event <= BLACKOUT_BEFORE_MINUTES:
            return True, event["name"]
    return False, None


def get_nearby_event() -> dict:
    """
    Returns the nearest upcoming event within SL_WIDEN_LOOKAHEAD_MINUTES,
    or None if nothing's coming up soon enough to matter for SL sizing.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    upcoming = [
        e for e in _load_events()
        if 0 <= (e["datetime_utc"] - now).total_seconds() / 60 <= SL_WIDEN_LOOKAHEAD_MINUTES
    ]
    if not upcoming:
        return None
    return min(upcoming, key=lambda e: e["datetime_utc"])


def sl_widen_multiplier_if_news_pending() -> float:
    """
    Returns SL_WIDEN_MULTIPLIER if a high-impact event is coming up soon
    enough to plausibly land while a new trade is still open, else 1.0
    (no widening). Callers multiply their normal ATR-based buffer by this.
    """
    return SL_WIDEN_MULTIPLIER if get_nearby_event() else 1.0
