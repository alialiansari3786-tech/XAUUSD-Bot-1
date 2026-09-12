"""
ob_memory.py
Persists memory of order blocks the bot has already acted on or seen
invalidated, across scheduled runs (same persistence pattern as
alert_state.py - a JSON file in the repo, committed back by the workflow).

REAL INCIDENT this addresses: Method 2 fired 3 consecutive bullish
continuation trades, all losing, in a zone where price had already tapped
a 1H OB from above and was heading down to take out liquidity below (per
the user's own multi-timeframe chart analysis). The bot had no memory that
it had already used that OB region for a prior signal, or that price had
already interacted with/moved through it - it just found "the current OB
in range" fresh every run with zero awareness of history.

Two protections:
1. mark_used() / was_already_used() - once an OB has been used as the
   basis for a fired continuation entry, the SAME zone won't be used again
   for another continuation trade in the same direction while it's still
   in memory. This directly stops "repeat the same losing trade off the
   same zone" behavior.
2. check_and_mark_invalidated() - if price has since closed fully through
   an OB's far boundary (the zone failed structurally), it's flagged
   invalidated and never used again even if it was never actually fired
   on, since a broken OB shouldn't be treated as a valid continuation zone.

This is a conservative first step: it PREVENTS re-using a stale/broken
zone, rather than attempting to auto-flip direction into a reversal trade
off a mitigated OB (a stronger claim that needs more validation before
being trusted) - see conversation notes for that as a possible next step.
"""

import json
import os
import time

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "ob_memory.json")
PRICE_MATCH_TOLERANCE_PCT = 0.0015   # 0.15% - OBs within this are "the same" zone across runs
EXPIRY_SECONDS = 14 * 24 * 60 * 60   # forget an OB after 14 days, regardless of status


def _load_state() -> list:
    if not os.path.exists(STATE_PATH):
        return []
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_state(state: list) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _prune_expired(state: list) -> list:
    now = time.time()
    return [ob for ob in state if now - ob["first_seen"] < EXPIRY_SECONDS]


def _ranges_match(a_top, a_bottom, b_top, b_bottom, tolerance_pct: float = PRICE_MATCH_TOLERANCE_PCT) -> bool:
    """Two OB ranges count as 'the same zone' if their tops and bottoms are each within tolerance, or their ranges overlap substantially."""
    ref = max(abs(a_top), 1.0)
    top_close = abs(a_top - b_top) / ref <= tolerance_pct
    bottom_close = abs(a_bottom - b_bottom) / ref <= tolerance_pct
    if top_close and bottom_close:
        return True
    # also count a clear overlap (one range mostly inside the other) as a match
    overlap_top = min(a_top, b_top)
    overlap_bottom = max(a_bottom, b_bottom)
    return overlap_top > overlap_bottom


def _find_matching(state: list, timeframe: str, direction: str, top: float, bottom: float):
    for ob in state:
        if ob["timeframe"] != timeframe or ob["direction"] != direction:
            continue
        if _ranges_match(top, bottom, ob["top"], ob["bottom"]):
            return ob
    return None


def _get_or_register(timeframe: str, direction: str, top: float, bottom: float) -> dict:
    """Finds the existing memory record for this OB zone, or creates a fresh one if it's genuinely new."""
    state = _prune_expired(_load_state())
    existing = _find_matching(state, timeframe, direction, top, bottom)
    if existing:
        return existing

    record = {
        "timeframe": timeframe,
        "direction": direction,
        "top": top,
        "bottom": bottom,
        "first_seen": time.time(),
        "used_count": 0,
        "last_used": None,
        "invalidated": False,
    }
    state.append(record)
    _save_state(state)
    return record


def was_already_used(timeframe: str, direction: str, top: float, bottom: float) -> bool:
    """Checks whether this OB zone has already been used as the basis for a fired entry."""
    record = _get_or_register(timeframe, direction, top, bottom)
    return record["used_count"] > 0


def is_invalidated(timeframe: str, direction: str, top: float, bottom: float) -> bool:
    """Checks whether this OB zone has already been flagged as structurally broken."""
    record = _get_or_register(timeframe, direction, top, bottom)
    return record["invalidated"]


def check_and_mark_invalidated(timeframe: str, direction: str, top: float, bottom: float, current_price: float) -> bool:
    """
    Checks whether current price has closed fully through this OB's far
    boundary (the zone has failed structurally) and marks it invalidated
    if so. A bullish OB (support/demand zone) is invalidated if price is
    now below its bottom; a bearish OB (resistance/supply zone) is
    invalidated if price is now above its top.

    Returns the up-to-date invalidated status (True/False) after this check.
    """
    state = _prune_expired(_load_state())
    existing = _find_matching(state, timeframe, direction, top, bottom)
    if existing is None:
        # never seen before - register fresh, can't be invalidated on first sight
        state.append({
            "timeframe": timeframe, "direction": direction, "top": top, "bottom": bottom,
            "first_seen": time.time(), "used_count": 0, "last_used": None, "invalidated": False,
        })
        _save_state(state)
        return False

    if not existing["invalidated"]:
        broke_down = direction == "bullish" and current_price < existing["bottom"]
        broke_up = direction == "bearish" and current_price > existing["top"]
        if broke_down or broke_up:
            existing["invalidated"] = True
            _save_state(state)

    return existing["invalidated"]


def mark_used(timeframe: str, direction: str, top: float, bottom: float) -> None:
    """Records that this OB zone was just used as the basis for a fired entry."""
    state = _prune_expired(_load_state())
    existing = _find_matching(state, timeframe, direction, top, bottom)
    if existing is None:
        existing = {
            "timeframe": timeframe, "direction": direction, "top": top, "bottom": bottom,
            "first_seen": time.time(), "used_count": 0, "last_used": None, "invalidated": False,
        }
        state.append(existing)
    existing["used_count"] += 1
    existing["last_used"] = time.time()
    _save_state(state)


def is_ob_still_valid_for_entry(timeframe: str, direction: str, top: float, bottom: float, current_price: float) -> bool:
    """
    Convenience combined check: an OB is safe to use for a fresh
    continuation entry only if it hasn't already been used AND hasn't
    since been structurally invalidated by price. Callers should call
    this before using any OB for entry, and call mark_used() right when
    an entry actually fires from it.
    """
    if check_and_mark_invalidated(timeframe, direction, top, bottom, current_price):
        return False
    if was_already_used(timeframe, direction, top, bottom):
        return False
    return True
