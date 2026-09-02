"""
mss_simple.py
Implements Method 2's "Simple MSS" - swappable with the ICT MSS rule (NOT
with Method 1's MSS, per the user's correction). Distinct from mss.py.

Valid-swing filter:
- MSS DownSide: the anchoring swing low is only valid if it swept the low
  liquidity of at least the last two candles, then broke back upside with
  a body close.
- MSS UpSide: mirror - the swing high is only valid if it swept the high
  liquidity of the last two candles, then broke back downside with a body
  close.
- Each swing point in the leg needs its own body close.
"""

import pandas as pd
from src.structure import find_swings


def _swept_last_two_lows(df: pd.DataFrame, pos: int) -> bool:
    if pos < 2:
        return False
    candle_low = df["Low"].iloc[pos]
    prev_two_lows = df["Low"].iloc[pos - 2:pos]
    return bool((candle_low < prev_two_lows).all())


def _swept_last_two_highs(df: pd.DataFrame, pos: int) -> bool:
    if pos < 2:
        return False
    candle_high = df["High"].iloc[pos]
    prev_two_highs = df["High"].iloc[pos - 2:pos]
    return bool((candle_high > prev_two_highs).all())


def find_valid_swings(df: pd.DataFrame, lookback: int = 2) -> list:
    """
    Finds swing points that pass the Simple MSS valid-swing filter.

    Returns a list of {index, price, type ('high'/'low'), valid: True}.
    Only valid swings are returned - invalid candidates are dropped.
    """
    d = find_swings(df, lookback=lookback)
    valid = []

    for i in range(len(d)):
        if d["swing_low"].iloc[i]:
            pos = i - lookback
            if pos < 0:
                continue
            # valid low: swept last 2 candles' low liquidity, then broke
            # upside with a body close on a subsequent candle
            if _swept_last_two_lows(df, pos):
                for j in range(pos + 1, min(pos + 10, len(df))):
                    if df["Close"].iloc[j] > df["Low"].iloc[pos]:
                        valid.append({"index": df.index[pos], "price": df["Low"].iloc[pos], "type": "low", "valid": True})
                        break

        if d["swing_high"].iloc[i]:
            pos = i - lookback
            if pos < 0:
                continue
            if _swept_last_two_highs(df, pos):
                for j in range(pos + 1, min(pos + 10, len(df))):
                    if df["Close"].iloc[j] < df["High"].iloc[pos]:
                        valid.append({"index": df.index[pos], "price": df["High"].iloc[pos], "type": "high", "valid": True})
                        break

    return valid


def detect_simple_mss(df: pd.DataFrame, lookback: int = 2) -> list:
    """
    Detects Simple MSS events using only VALID swings (per the filter
    above) as anchors, then applies the same grab+body-close-break-of-prior-
    opposite-swing mechanic as mss.py's detect_mss.
    """
    valid_swings = find_valid_swings(df, lookback=lookback)
    valid_highs = [(df.index.get_loc(s["index"]), s["price"]) for s in valid_swings if s["type"] == "high"]
    valid_lows = [(df.index.get_loc(s["index"]), s["price"]) for s in valid_swings if s["type"] == "low"]

    events = []

    for grab_i, grab_price in valid_lows:
        prior_highs = [(i, p) for i, p in valid_highs if i < grab_i]
        if not prior_highs:
            continue
        target_i, target_price = prior_highs[-1]
        for j in range(grab_i + 1, len(df)):
            if df["Close"].iloc[j] > target_price:
                events.append({"index": df.index[j], "price": target_price, "direction": "bullish"})
                break

    for grab_i, grab_price in valid_highs:
        prior_lows = [(i, p) for i, p in valid_lows if i < grab_i]
        if not prior_lows:
            continue
        target_i, target_price = prior_lows[-1]
        for j in range(grab_i + 1, len(df)):
            if df["Close"].iloc[j] < target_price:
                events.append({"index": df.index[j], "price": target_price, "direction": "bearish"})
                break

    events.sort(key=lambda e: e["index"])
    return events
