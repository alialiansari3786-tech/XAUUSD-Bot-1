"""
alert_state.py
Prevents duplicate Telegram alerts across scheduled runs. Since GitHub
Actions runs are stateless (fresh checkout every time), state is persisted
to a JSON file IN THE REPO, which the workflow commits back after each run.

A setup is considered "the same" as a previous alert if it matches on
method + direction + entry/SL/TP all within a small tolerance (prices can
drift slightly between runs while still being "the same" setup). Once
sent, a setup won't re-alert unless its levels move meaningfully, or until
it expires (default 24h) - after which a still-valid setup can alert again
since enough time has passed that it may represent a fresh opportunity.
"""

import json
import os
import time

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "alert_state.json")
PRICE_TOLERANCE_PCT = 0.001   # 0.1% - setups within this are "the same" setup
EXPIRY_SECONDS = 24 * 60 * 60  # re-allow alerting the same setup after 24h


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


def _prices_match(a: float, b: float, tolerance_pct: float = PRICE_TOLERANCE_PCT) -> bool:
    if a == 0:
        return b == 0
    return abs(a - b) / abs(a) <= tolerance_pct


def _prune_expired(state: list) -> list:
    now = time.time()
    return [s for s in state if now - s["timestamp"] < EXPIRY_SECONDS]


def already_alerted(method: str, direction: str, entry: float, sl: float, tp: float) -> bool:
    """Checks whether a matching setup was already alerted recently."""
    state = _prune_expired(_load_state())
    for s in state:
        if s["method"] != method or s["direction"] != direction:
            continue
        if _prices_match(s["entry"], entry) and _prices_match(s["sl"], sl) and _prices_match(s["tp"], tp):
            return True
    return False


def mark_alerted(method: str, direction: str, entry: float, sl: float, tp: float) -> None:
    """Records a setup as alerted, so it won't fire again until it expires or changes."""
    state = _prune_expired(_load_state())
    state.append({
        "method": method,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "timestamp": time.time(),
    })
    _save_state(state)
