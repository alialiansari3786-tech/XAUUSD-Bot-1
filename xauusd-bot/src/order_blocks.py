"""
order_blocks.py
Shared "mark the OB inside the trading range" logic, used by Method 1
(inside the STL/IDM trading range), and available for Method 2/3 too.

An order block, in this bot's simplified model: the last opposing candle
before the impulsive move that broke structure (same concept as
smc.py's detect_order_blocks, generalized to work off any {index, price,
direction} break event - e.g. an idm_structure.py confirmation-point break,
or an mss.py MSS event - not just smc.py's own structure events).
"""

import pandas as pd


def valid_pullback_entry(candidate_entry, current_price: float, is_bullish: bool) -> bool:
    """
    A real pullback entry must require price to move TOWARD it in the
    direction opposite the trade (i.e. you're waiting for a retracement),
    not require price to already have moved further in the trade's
    direction to reach it. For a bullish (buy) trade, the entry must be at
    or below current price - you're waiting for a dip into the zone. For a
    bearish (sell) trade, the entry must be at or above current price.

    Without this check, order_block_in_range()/find_fvg_in_range() can
    return a real zone that's simply on the wrong side of current price
    entirely (a "buy" entry above price, requiring a rally to even reach
    it) - live testing found exactly this: an alert with both entry and SL
    sitting well above where price actually was, making it a breakout-style
    order dressed up as a pullback entry, and explaining why most alerts
    were never realistically reachable that day. Used by both main.py
    (Methods 1 & 2) and method3.py so all three methods apply the same rule.
    """
    if candidate_entry is None:
        return False
    return candidate_entry <= current_price if is_bullish else candidate_entry >= current_price


def obs_overlap(ob_a: dict, ob_b: dict) -> bool:
    """Checks whether two order blocks' price ranges overlap (i.e. are confluent)."""
    if ob_a is None or ob_b is None:
        return False
    return not (ob_a["top"] < ob_b["bottom"] or ob_b["top"] < ob_a["bottom"])


def combined_zone(ob_a: dict, ob_b: dict) -> dict | None:
    """
    Returns the overlapping price zone shared by two confluent order blocks
    (the intersection of their ranges) - this combined zone is treated as a
    stronger target than either OB alone, per the Combined Method's
    confluence rules.
    """
    if not obs_overlap(ob_a, ob_b):
        return None
    top = min(ob_a["top"], ob_b["top"])
    bottom = max(ob_a["bottom"], ob_b["bottom"])
    return {"top": top, "bottom": bottom, "mid": (top + bottom) / 2}


def find_order_block(df: pd.DataFrame, break_index, direction: str, lookback_bars: int = 100) -> dict | None:
    """
    Given a dataframe and the bar index where a structure break/confirmation
    happened, finds the order block: the last opposing candle before the
    impulsive move.

    direction: 'bullish' (look for the last down candle before the up move)
               or 'bearish' (look for the last up candle before the down move)

    Returns {top, bottom, mid, time} or None if not enough data.
    """
    try:
        end_pos = df.index.get_loc(break_index)
    except KeyError:
        return None

    window = df.iloc[max(0, end_pos - lookback_bars):end_pos + 1]
    if window.empty:
        return None

    if direction == "bullish":
        anchor_pos = window["Low"].values.argmin()
    else:
        anchor_pos = window["High"].values.argmax()

    anchor = window.iloc[anchor_pos]
    top, bottom = float(anchor["High"]), float(anchor["Low"])
    return {
        "top": top,
        "bottom": bottom,
        "mid": (top + bottom) / 2,
        "time": anchor.name,
    }


def order_block_in_range(df: pd.DataFrame, range_bounds: tuple, direction: str) -> dict | None:
    """
    Finds an order block whose candle falls INSIDE a given trading range
    (e.g. the Recent STL -> New STL Confirmation Point range from
    idm_structure.py), which is how Method 1 wants OBs marked: "inside the
    trading range" on the respective timeframe.

    range_bounds: (low, high) price bounds of the trading range.
    direction: 'bullish' or 'bearish' - which side of candle to anchor on.

    Returns the order block dict (see find_order_block) for the most
    recent qualifying candle, or None if none found.
    """
    if range_bounds is None:
        return None
    low_bound, high_bound = min(range_bounds), max(range_bounds)

    in_range = df[(df["Low"] >= low_bound) & (df["High"] <= high_bound)]
    if in_range.empty:
        return None

    if direction == "bullish":
        anchor = in_range.loc[in_range["Low"].idxmin()]
    else:
        anchor = in_range.loc[in_range["High"].idxmax()]

    top, bottom = float(anchor["High"]), float(anchor["Low"])
    return {
        "top": top,
        "bottom": bottom,
        "mid": (top + bottom) / 2,
        "time": anchor.name,
    }


def find_fvg_in_range(df: pd.DataFrame, range_bounds: tuple) -> dict | None:
    """
    Finds a 3-candle Fair Value Gap whose gap falls inside a given trading
    range - the alternative entry zone to an OB, per your Combined Method
    workflow ("entry at the start of the OB or at the Mid of FVG").
    """
    if range_bounds is None or len(df) < 3:
        return None
    low_bound, high_bound = min(range_bounds), max(range_bounds)

    for i in range(2, len(df)):
        high_2ago = df["High"].iloc[i - 2]
        low_2ago = df["Low"].iloc[i - 2]
        low_now = df["Low"].iloc[i]
        high_now = df["High"].iloc[i]

        bull_gap = low_now > high_2ago
        bear_gap = high_now < low_2ago

        if bull_gap and low_bound <= high_2ago and low_now <= high_bound:
            mid = (low_now + high_2ago) / 2
            return {"top": low_now, "bottom": high_2ago, "mid": mid, "time": df.index[i], "bias": "bullish"}
        if bear_gap and low_bound <= high_now and low_2ago <= high_bound:
            mid = (high_now + low_2ago) / 2
            return {"top": low_2ago, "bottom": high_now, "mid": mid, "time": df.index[i], "bias": "bearish"}

    return None
