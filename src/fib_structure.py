"""
fib_structure.py
Implements Method 2's (Monthly-Daily-Hourly-5m) Fib-pullback-based
STL/New STL Confirmation Point structure, distinct from Method 1's
IDM-based mechanism (idm_structure.py).

Uptrend mechanics (downtrend/STH is the mirror):
- Track the current swing's Recent STL (low, 100% / fib level 1) and the
  running high (candidate New STL Confirmation Point, fib level 0).
- A high only becomes a CONFIRMED "New STL Confirmation Point" once price
  pulls back at least the minimum % of that up-leg (25% on Daily, 37.5% on
  1H - configurable per timeframe; other timeframes default to 30% pending
  the user specifying exact thresholds).
- If price makes a NEW high before hitting the minimum pullback %, the
  pullback measurement resets from the new high.
- Once confirmed, pullback can continue further (up to 99%, or even sweep
  the Recent STL) - then when price breaks back below the confirmation
  point with a body close, the low formed between the confirmation point's
  formation and that break becomes the new Recent STL.
"""

import pandas as pd

# Minimum pullback % (as a fraction of the leg) required to confirm a high
# as a New STL Confirmation Point, per timeframe. User-confirmed values.
# Monthly is context-only in Method 2 (Monthly OB reversal zone) and never
# runs through this pullback structure at all - not included here.
PULLBACK_THRESHOLDS = {
    "daily": 0.25,
    "1h": 0.375,
    "5m": 0.375,
}


def _pullback_pct(leg_high: float, leg_low: float, current_price: float) -> float:
    """Fraction of the (leg_low -> leg_high) leg that current_price has retraced."""
    leg_size = leg_high - leg_low
    if leg_size <= 0:
        return 0.0
    return (leg_high - current_price) / leg_size


def track_fib_structure(df: pd.DataFrame, timeframe: str, lookback: int = 2) -> dict:
    """
    Walks forward bar-by-bar maintaining the Fib-pullback STL/Confirmation
    Point state for an uptrend read, and the mirrored STH version for a
    downtrend read.

    Returns the same shape as idm_structure.track_stl_idm_structure():
      - uptrend_state / downtrend_state: {recent_stl/sth, running_extreme,
        confirmation_point, confirmed, trading_range}
      - history: list of state transitions
    """
    from src.structure import find_swings
    threshold = PULLBACK_THRESHOLDS.get(timeframe, 0.30)

    d = find_swings(df, lookback=lookback)
    swings = []
    for i in range(len(d)):
        if d["swing_high"].iloc[i]:
            swings.append({"index": i, "price": d["High"].iloc[i], "type": "high"})
        elif d["swing_low"].iloc[i]:
            swings.append({"index": i, "price": d["Low"].iloc[i], "type": "low"})

    up = {"recent_stl": None, "running_high": None, "confirmation_point": None, "confirmed": False}
    down = {"recent_sth": None, "running_low": None, "confirmation_point": None, "confirmed": False}
    history = []

    for s in swings:
        idx, price, typ = s["index"], s["price"], s["type"]
        close_price = df["Close"].iloc[idx]

        # --- Uptrend (STL) side ---
        if typ == "low" and up["recent_stl"] is None:
            up["recent_stl"] = price
            up["running_high"] = price

        if typ == "high" and up["recent_stl"] is not None and not up["confirmed"]:
            if up["running_high"] is None or price > up["running_high"]:
                up["running_high"] = price
                history.append({"index": idx, "event": f"New running high ({timeframe})", "price": price})

        # Check pullback confirmation on every bar once we have a running high
        if up["recent_stl"] is not None and up["running_high"] is not None and not up["confirmed"]:
            pullback = _pullback_pct(up["running_high"], up["recent_stl"], close_price)
            if pullback >= threshold:
                up["confirmation_point"] = up["running_high"]
                up["confirmed"] = True
                history.append({"index": idx, "event": f"New STL Confirmation Point set ({timeframe}, {pullback:.1%} pullback)", "price": up["running_high"]})

        # Check for body-close break of confirmation point -> new Recent STL
        if up["confirmed"] and up["confirmation_point"] is not None:
            if close_price < up["confirmation_point"]:
                lows_between = [sw["price"] for sw in swings if sw["type"] == "low" and sw["index"] <= idx]
                new_stl = lows_between[-1] if lows_between else up["recent_stl"]
                history.append({"index": idx, "event": "STL Confirmation Point broken -> new Recent STL", "price": new_stl})
                up = {"recent_stl": new_stl, "running_high": new_stl, "confirmation_point": None, "confirmed": False}

        # --- Downtrend (STH) side - mirror ---
        if typ == "high" and down["recent_sth"] is None:
            down["recent_sth"] = price
            down["running_low"] = price

        if typ == "low" and down["recent_sth"] is not None and not down["confirmed"]:
            if down["running_low"] is None or price < down["running_low"]:
                down["running_low"] = price
                history.append({"index": idx, "event": f"New running low ({timeframe})", "price": price})

        if down["recent_sth"] is not None and down["running_low"] is not None and not down["confirmed"]:
            leg_size = down["recent_sth"] - down["running_low"]
            pullback = (close_price - down["running_low"]) / leg_size if leg_size > 0 else 0
            if pullback >= threshold:
                down["confirmation_point"] = down["running_low"]
                down["confirmed"] = True
                history.append({"index": idx, "event": f"New STH Confirmation Point set ({timeframe}, {pullback:.1%} pullback)", "price": down["running_low"]})

        if down["confirmed"] and down["confirmation_point"] is not None:
            if close_price > down["confirmation_point"]:
                highs_between = [sw["price"] for sw in swings if sw["type"] == "high" and sw["index"] <= idx]
                new_sth = highs_between[-1] if highs_between else down["recent_sth"]
                history.append({"index": idx, "event": "STH Confirmation Point broken -> new Recent STH", "price": new_sth})
                down = {"recent_sth": new_sth, "running_low": new_sth, "confirmation_point": None, "confirmed": False}

    up_range = None
    if up["recent_stl"] is not None and up["confirmation_point"] is not None:
        up_range = (up["recent_stl"], up["confirmation_point"])

    down_range = None
    if down["recent_sth"] is not None and down["confirmation_point"] is not None:
        down_range = (down["confirmation_point"], down["recent_sth"])

    return {
        "uptrend_state": {**up, "trading_range": up_range},
        "downtrend_state": {**down, "trading_range": down_range},
        "history": history,
        "pullback_threshold_used": threshold,
    }
