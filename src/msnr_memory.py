"""
msnr_memory.py
Persists which MSNR Key Levels (A/V, OC_GAP, OML) the Liquidity MSNR
Method has already fired a trade from, across scheduled runs - same
persistence pattern as ob_memory.py/alert_state.json (a JSON file in the
repo, committed back by the workflow).

Freshness/SBR/RBS status itself is recomputed fresh every run by walking
the full candle history (msnr_levels.track_freshness) - this file only
guards against firing a SECOND continuation trade off the same level
while it's still within memory, the same real-incident class that
ob_memory.py already protects Methods 1 & 2 against.
"""

import json
import os
import time

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "msnr_memory.json")
PRICE_MATCH_TOLERANCE_PCT = 0.0015
EXPIRY_SECONDS = 14 * 24 * 60 * 60


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
    return [lvl for lvl in state if now - lvl["first_seen"] < EXPIRY_SECONDS]


def _prices_match(a: float, b: float, tolerance_pct: float = PRICE_MATCH_TOLERANCE_PCT) -> bool:
    ref = max(abs(a), 1.0)
    return abs(a - b) / ref <= tolerance_pct


def _find_matching(state: list, timeframe: str, level_type: str, direction: str, price: float):
    for lvl in state:
        if lvl["timeframe"] != timeframe or lvl["level_type"] != level_type or lvl["direction"] != direction:
            continue
        if _prices_match(price, lvl["price"]):
            return lvl
    return None


def was_already_used(timeframe: str, level_type: str, direction: str, price: float) -> bool:
    state = _prune_expired(_load_state())
    existing = _find_matching(state, timeframe, level_type, direction, price)
    return existing is not None and existing["used_count"] > 0


def mark_used(timeframe: str, level_type: str, direction: str, price: float) -> None:
    state = _prune_expired(_load_state())
    existing = _find_matching(state, timeframe, level_type, direction, price)
    if existing is None:
        existing = {
            "timeframe": timeframe, "level_type": level_type, "direction": direction,
            "price": price, "first_seen": time.time(), "used_count": 0, "last_used": None,
        }
        state.append(existing)
    existing["used_count"] += 1
    existing["last_used"] = time.time()
    _save_state(state)
