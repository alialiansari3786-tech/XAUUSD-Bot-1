"""
sar.py
Implements the friend's "SAR" (Support and Resistance) strategy:
- Marking S/R via two-candle V/A patterns (or two same-color candles), using
  closing prices.
- Fresh vs. Unfresh level tracking, with re-freshing when a same-timeframe
  candle fully closes beyond an unfresh level again.
- Rejection / breakout / pullback detection for the trade workflow.

This feeds into Method 3 as an additional confluence layer alongside
liquidity.py and structure.py.
"""

import pandas as pd


def mark_sr_levels(df: pd.DataFrame) -> list:
    """
    Marks support levels (two-candle 'V', using closing prices) and
    resistance levels (two-candle 'A', using closing prices), plus the
    two-same-color-candle variant.

    Returns a list of {index, price, type ('support'/'resistance')}.
    """
    levels = []
    closes = df["Close"]
    opens = df["Open"]

    for i in range(2, len(df) - 1):
        c0, c1, c2 = closes.iloc[i - 2], closes.iloc[i - 1], closes.iloc[i]
        is_green = lambda idx: closes.iloc[idx] > opens.iloc[idx]

        # Two-candle V (support): close dips then recovers - c1 is the low point
        if c1 < c0 and c1 < c2:
            levels.append({"index": df.index[i - 1], "price": c1, "type": "support"})

        # Two-candle A (resistance): close rises then falls - c1 is the high point
        if c1 > c0 and c1 > c2:
            levels.append({"index": df.index[i - 1], "price": c1, "type": "resistance"})

        # Two same-color candles forming a level at their shared close
        if is_green(i - 2) and is_green(i - 1) and c1 > c0:
            levels.append({"index": df.index[i - 1], "price": c1, "type": "support"})
        if (not is_green(i - 2)) and (not is_green(i - 1)) and c1 < c0:
            levels.append({"index": df.index[i - 1], "price": c1, "type": "resistance"})

    return levels


def track_fresh_unfresh(df: pd.DataFrame, levels: list) -> list:
    """
    For each marked level, walks forward through the candles after it formed
    to determine fresh/unfresh status as of the latest bar, tracking
    re-freshing (a full candle close back beyond the level after a tap).

    Returns the same level dicts with an added 'status' field:
    'fresh' or 'unfresh', plus 'last_status_change' (index).
    """
    results = []
    for level in levels:
        try:
            start_pos = df.index.get_loc(level["index"])
        except KeyError:
            continue

        status = "fresh"
        last_change = level["index"]
        price = level["price"]
        is_support = level["type"] == "support"

        for i in range(start_pos + 1, len(df)):
            high, low, close = df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]

            if status == "fresh":
                touched = (low <= price <= high)
                if touched:
                    status = "unfresh"
                    last_change = df.index[i]
            else:  # unfresh -> can re-fresh on a full close back beyond the level
                refreshed = (close > price) if is_support else (close < price)
                # "back beyond" for re-freshing means closing on the far side again,
                # i.e. price fully broke through and closed past the level
                broke_through = (close < price) if is_support else (close > price)
                if broke_through:
                    status = "fresh"
                    last_change = df.index[i]

        results.append({**level, "status": status, "last_status_change": last_change})

    return results


def detect_rejection(df: pd.DataFrame, level_price: float, is_support: bool) -> bool:
    """
    Checks if the most recently CLOSED candle shows a rejection at the given
    level: wicks into/through the level but closes back on the origin side.
    """
    if len(df) < 1:
        return False
    last = df.iloc[-1]
    if is_support:
        wicked_in = last["Low"] <= level_price
        closed_above = last["Close"] > level_price
        return wicked_in and closed_above
    else:
        wicked_in = last["High"] >= level_price
        closed_below = last["Close"] < level_price
        return wicked_in and closed_below


def detect_breakout(df: pd.DataFrame, level_price: float, is_support: bool) -> bool:
    """Checks if the most recently CLOSED candle fully closed beyond the level (a breakout)."""
    if len(df) < 1:
        return False
    last_close = df["Close"].iloc[-1]
    return (last_close < level_price) if is_support else (last_close > level_price)


def next_fresh_target(levels_with_status: list, current_price: float, direction: str) -> dict | None:
    """
    Finds the next fresh level in the given direction ('up' or 'down') from
    the current price - used as the target per the SAR "fresh to fresh" rule.
    """
    fresh = [l for l in levels_with_status if l["status"] == "fresh"]
    if direction == "up":
        candidates = [l for l in fresh if l["price"] > current_price]
        return min(candidates, key=lambda l: l["price"]) if candidates else None
    else:
        candidates = [l for l in fresh if l["price"] < current_price]
        return max(candidates, key=lambda l: l["price"]) if candidates else None
