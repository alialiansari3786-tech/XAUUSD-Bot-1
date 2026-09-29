"""
bias_state.py
The Liquidity MSNR Method (new Method 3) uses a combined bias built from
Method 1's and Method 2's own 1H direction, recalculated only twice a day
at fixed New York-time checkpoints (17:45 and 01:00 NY) and held fixed
between checkpoints regardless of what Method 1/2's 1H direction does in
the meantime. Persisted the same way as alert_state.json/ob_memory.json -
a JSON file in the repo, committed back by the workflow.

Combination rule: if Method 1's and Method 2's 1H direction agree, use
that. If they disagree, Method 1's 1H direction wins.
"""

import json
import os
import datetime
from zoneinfo import ZoneInfo

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "bias_state.json")
NY_TZ = ZoneInfo("America/New_York")
CHECKPOINTS = [(1, 0), (17, 45)]  # (hour, minute) in NY time


def _load_state() -> dict | None:
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _latest_checkpoint(now_ny: datetime.datetime) -> datetime.datetime:
    """The most recent of today's/yesterday's checkpoint times that is <= now."""
    candidates = []
    for h, m in CHECKPOINTS:
        candidates.append(now_ny.replace(hour=h, minute=m, second=0, microsecond=0))
        yesterday = now_ny - datetime.timedelta(days=1)
        candidates.append(yesterday.replace(hour=h, minute=m, second=0, microsecond=0))
    valid = [c for c in candidates if c <= now_ny]
    return max(valid)


def get_method3_bias(m1_1h_direction: str | None, m2_1h_direction: str | None) -> dict:
    """
    Returns {"bias": 'bullish'/'bearish'/None, "checkpoint": iso string,
    "freshly_computed": bool}. Reuses the cached bias untouched if we're
    still within the same checkpoint window; only recomputes at/after a
    new checkpoint boundary.
    """
    now_ny = datetime.datetime.now(NY_TZ)
    checkpoint = _latest_checkpoint(now_ny)
    checkpoint_iso = checkpoint.isoformat()

    state = _load_state()
    if state and state.get("checkpoint") == checkpoint_iso:
        return {"bias": state.get("bias"), "checkpoint": checkpoint_iso, "freshly_computed": False}

    if m1_1h_direction and m2_1h_direction:
        bias = m1_1h_direction if m1_1h_direction == m2_1h_direction else m1_1h_direction
    else:
        bias = m1_1h_direction or m2_1h_direction

    _save_state({"bias": bias, "checkpoint": checkpoint_iso,
                 "computed_from": {"m1_1h": m1_1h_direction, "m2_1h": m2_1h_direction}})
    return {"bias": bias, "checkpoint": checkpoint_iso, "freshly_computed": True}
