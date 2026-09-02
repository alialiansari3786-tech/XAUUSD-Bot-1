"""
smc.py
Python port of the market-structure, order-block, FVG, EQH/EQL, and
premium/discount-zone logic from LuxAlgo's "Smart Money Concepts" indicator.

ATTRIBUTION: The original Pine Script source is published by LuxAlgo as an
open-source script on TradingView, licensed under CC BY-NC-SA 4.0 and the
Mozilla Public License 2.0 (https://mozilla.org/MPL/2.0/). This is a
non-commercial, personal-use adaptation of that public logic into Python,
as permitted by the license. Original: "Smart Money Concepts [LuxAlgo]".

This does NOT include LuxAlgo's separate closed/protected "Guardeer"-style
indicator - that one was not ported, per copyright constraints discussed
earlier.
"""

import pandas as pd
import numpy as np

BULLISH = 1
BEARISH = -1


def _leg(df: pd.DataFrame, size: int) -> pd.Series:
    """
    Determine the current 'leg' direction at each bar: BULLISH if a new
    swing low just formed (price broke below the rolling low), BEARISH if
    a new swing high just formed (price broke above the rolling high).
    Mirrors Pine's leg() function - carries forward the last value otherwise.

    Pine's ta.highest(size)/ta.lowest(size) at bar i covers the CURRENT
    window of `size` bars ending at i (no extra shift) - compared against
    high[size]/low[size], the value from `size` bars before that window.
    """
    highest = df["High"].rolling(size).max()
    lowest = df["Low"].rolling(size).min()

    new_leg_high = df["High"].shift(size) > highest
    new_leg_low = df["Low"].shift(size) < lowest

    leg = pd.Series(index=df.index, dtype="float64")
    current = 0
    legs = []
    for i in range(len(df)):
        if bool(new_leg_high.iloc[i]) if not pd.isna(new_leg_high.iloc[i]) else False:
            current = BEARISH
        elif bool(new_leg_low.iloc[i]) if not pd.isna(new_leg_low.iloc[i]) else False:
            current = BULLISH
        legs.append(current)
    leg[:] = legs
    return leg


def detect_structure(df: pd.DataFrame, swing_size: int = 50, internal_size: int = 5) -> dict:
    """
    Detects swing and internal market structure: pivots, BOS (break of
    structure) and CHoCH (change of character) events, for both the
    'swing' (major) and 'internal' (minor) structure.

    Fixed version: tracks the high-pivot and low-pivot crossed-state
    SEPARATELY (as the original Pine does with distinct swingHigh/swingLow
    pivot objects), rather than a single shared 'crossed' flag - that was
    the bug in the first pass that produced zero events.

    Returns a dict with:
      - swing_pivots: list of {index, price, type ('high'/'low')}
      - swing_events: list of {index, price, type ('BOS'/'CHoCH'), bias ('bullish'/'bearish')}
      - internal_events: same shape, for internal structure
      - swing_trend / internal_trend: current bias ('bullish'/'bearish'/None)
    """
    result = {
        "swing_pivots": [],
        "swing_events": [],
        "internal_events": [],
        "swing_trend": None,
        "internal_trend": None,
    }

    for size, key_events, key_trend, is_internal in [
        (swing_size, "swing_events", "swing_trend", False),
        (internal_size, "internal_events", "internal_trend", True),
    ]:
        if len(df) <= size + 1:
            continue

        leg = _leg(df, size)
        new_pivot = leg.diff().fillna(0) != 0
        pivot_low = new_pivot & (leg == BULLISH)
        pivot_high = new_pivot & (leg == BEARISH)

        high_level, high_crossed = None, True   # True until a pivot sets a real level
        low_level, low_crossed = None, True
        trend_bias = None

        for i in range(len(df)):
            idx = i - size
            close = df["Close"].iloc[i]

            if pivot_high.iloc[i] and idx >= 0:
                high_level = df["High"].iloc[idx]
                high_crossed = False
                if not is_internal:
                    result["swing_pivots"].append({"index": df.index[idx], "price": high_level, "type": "high"})

            if pivot_low.iloc[i] and idx >= 0:
                low_level = df["Low"].iloc[idx]
                low_crossed = False
                if not is_internal:
                    result["swing_pivots"].append({"index": df.index[idx], "price": low_level, "type": "low"})

            # Bullish break: close crosses above the current high pivot level
            if high_level is not None and not high_crossed and close > high_level:
                tag = "CHoCH" if trend_bias == "bearish" else "BOS"
                trend_bias = "bullish"
                high_crossed = True
                result[key_events].append({"index": df.index[i], "price": high_level, "type": tag, "bias": "bullish"})

            # Bearish break: close crosses below the current low pivot level
            if low_level is not None and not low_crossed and close < low_level:
                tag = "CHoCH" if trend_bias == "bullish" else "BOS"
                trend_bias = "bearish"
                low_crossed = True
                result[key_events].append({"index": df.index[i], "price": low_level, "type": tag, "bias": "bearish"})

        result[key_trend] = trend_bias

    result["swing_events"].sort(key=lambda e: e["index"])
    result["internal_events"].sort(key=lambda e: e["index"])
    return result


def detect_order_blocks(df: pd.DataFrame, events: list, atr_period: int = 200) -> list:
    """
    For each structure event (BOS/CHoCH), finds the order block: the last
    opposing candle before the impulsive move that broke structure.
    Bullish OB -> the down candle right before an up-move that broke a high.
    Bearish OB -> the up candle right before a down-move that broke a low.

    Filters out "high volatility" bars (range >= 2x ATR) same as the
    original, to avoid marking blocks on abnormal candles.
    """
    atr = (df["High"] - df["Low"]).rolling(atr_period).mean()
    blocks = []

    for ev in events:
        try:
            end_idx = df.index.get_loc(ev["index"])
        except KeyError:
            continue

        window = df.iloc[max(0, end_idx - 100):end_idx + 1]
        if window.empty:
            continue

        if ev["bias"] == "bullish":
            # anchor = candle with the lowest low in the window (the down candle before the up move)
            anchor_pos = window["Low"].values.argmin()
        else:
            anchor_pos = window["High"].values.argmax()

        anchor = window.iloc[anchor_pos]
        blocks.append({
            "bias": ev["bias"],
            "top": float(anchor["High"]),
            "bottom": float(anchor["Low"]),
            "time": anchor.name,
        })

    return blocks


def detect_equal_highs_lows(df: pd.DataFrame, length: int = 3, threshold_atr_mult: float = 0.1, atr_period: int = 200) -> dict:
    """Detects EQH (equal highs) and EQL (equal lows) - pairs of pivots within threshold of each other."""
    atr = (df["High"] - df["Low"]).rolling(atr_period).mean()

    highs = df["High"].rolling(length * 2 + 1, center=True).apply(lambda x: x[length] == max(x), raw=True).fillna(0).astype(bool)
    lows = df["Low"].rolling(length * 2 + 1, center=True).apply(lambda x: x[length] == min(x), raw=True).fillna(0).astype(bool)

    eqh, eql = [], []
    last_high, last_high_atr = None, None
    last_low, last_low_atr = None, None

    for i in range(len(df)):
        if highs.iloc[i]:
            h = df["High"].iloc[i]
            a = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 0
            if last_high is not None and abs(h - last_high) < threshold_atr_mult * a:
                eqh.append({"index": df.index[i], "price": h})
            last_high = h
        if lows.iloc[i]:
            l = df["Low"].iloc[i]
            a = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 0
            if last_low is not None and abs(l - last_low) < threshold_atr_mult * a:
                eql.append({"index": df.index[i], "price": l})
            last_low = l

    return {"equal_highs": eqh, "equal_lows": eql}


def detect_fvg(df: pd.DataFrame, auto_threshold: bool = True) -> list:
    """
    Detects Fair Value Gaps (3-candle imbalance):
    Bullish FVG: current low > high[2 bars ago], with a strong up candle in between.
    Bearish FVG: current high < low[2 bars ago], with a strong down candle in between.
    """
    gaps = []
    body_delta_pct = ((df["Close"].shift(1) - df["Open"].shift(1)) / (df["Open"].shift(1) * 100))
    cum_threshold = body_delta_pct.abs().cumsum() / (pd.Series(range(1, len(df) + 1), index=df.index)) * 2 if auto_threshold else pd.Series(0, index=df.index)

    for i in range(2, len(df)):
        low_now = df["Low"].iloc[i]
        high_now = df["High"].iloc[i]
        high_2ago = df["High"].iloc[i - 2]
        low_2ago = df["Low"].iloc[i - 2]
        close_prev = df["Close"].iloc[i - 1]
        threshold = cum_threshold.iloc[i] if auto_threshold else 0

        bull = low_now > high_2ago and close_prev > high_2ago and body_delta_pct.iloc[i] > threshold
        bear = high_now < low_2ago and close_prev < low_2ago and -body_delta_pct.iloc[i] > threshold

        if bull:
            gaps.append({"index": df.index[i], "bias": "bullish", "top": low_now, "bottom": high_2ago})
        if bear:
            gaps.append({"index": df.index[i], "bias": "bearish", "top": low_2ago, "bottom": high_now})

    return gaps


def premium_discount_zones(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Splits the recent trading range (highest high / lowest low over `lookback`
    bars) into Premium (top 5%), Discount (bottom 5%), and Equilibrium
    (middle ~5% band) zones, same proportions as the original indicator.
    """
    recent = df.tail(lookback)
    top = recent["High"].max()
    bottom = recent["Low"].min()

    return {
        "premium_zone": (0.95 * top + 0.05 * bottom, top),
        "equilibrium_zone": (0.525 * bottom + 0.475 * top, 0.525 * top + 0.475 * bottom),
        "discount_zone": (bottom, 0.95 * bottom + 0.05 * top),
        "range_top": top,
        "range_bottom": bottom,
    }
