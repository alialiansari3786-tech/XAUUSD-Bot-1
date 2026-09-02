"""
mss.py
Implements the user's actual MSS (Market Structure Shift) definition:
a swing low/high forms where liquidity gets swept or grabbed (wick or body),
then price breaks back beyond the PRIOR OPPOSITE swing with a body close
(not just a wick) - that confirms the MSS. If that swing doesn't give a
clean signal, the second-last swing can be used as the reference instead.

This is distinct from:
- structure.py's generic HH/HL/LH/LL labeling (kept as a simpler fallback)
- Method 2's "Simple MSS" (see mss_simple.py) which requires the anchoring
  swing to have swept at least the last 2 candles' liquidity
- Method 3's MSS, which is the same grab+body-close mechanic (grab preferred
  over sweep) - this module serves both Method 1 and Method 3
"""

import pandas as pd
from src.structure import find_swings


def _is_body_close_beyond(candle: pd.Series, level: float, direction: str) -> bool:
    """direction: 'above' or 'below' - checks the candle's BODY close, not wick."""
    body_close = candle["Close"]
    if direction == "above":
        return body_close > level
    else:
        return body_close < level


def detect_mss(df: pd.DataFrame, lookback: int = 2, use_second_last_fallback: bool = True) -> list:
    """
    Scans for MSS events using the user's definition:
    1. A swing point forms (potential liquidity grab point).
    2. Price later breaks beyond the swing immediately PRIOR to that one
       (the opposite type), confirmed by a body close (not wick).
    3. If the most recent prior opposite swing doesn't get broken, falls
       back to checking the second-last opposite swing (if enabled).

    Returns a list of {index, price, direction ('bullish'/'bearish'),
    grab_swing_index, broken_swing_price, fallback_used}.
    """
    d = find_swings(df, lookback=lookback)
    swing_highs = [(i, d["High"].iloc[i]) for i in range(len(d)) if d["swing_high"].iloc[i]]
    swing_lows = [(i, d["Low"].iloc[i]) for i in range(len(d)) if d["swing_low"].iloc[i]]

    events = []

    # Bullish MSS: liquidity grab at a swing LOW, then body-close break above
    # a prior swing HIGH.
    for idx, (grab_i, grab_price) in enumerate(swing_lows):
        prior_highs = [(i, p) for i, p in swing_highs if i < grab_i]
        if not prior_highs:
            continue

        candidates = [prior_highs[-1]]
        if use_second_last_fallback and len(prior_highs) >= 2:
            candidates.append(prior_highs[-2])

        for target_i, target_price in candidates:
            # scan forward from the grab point for a body-close break above target_price
            for j in range(grab_i + 1, len(df)):
                if _is_body_close_beyond(df.iloc[j], target_price, "above"):
                    events.append({
                        "index": df.index[j],
                        "price": target_price,
                        "direction": "bullish",
                        "grab_swing_index": df.index[grab_i],
                        "broken_swing_price": target_price,
                        "fallback_used": target_i != prior_highs[-1][0],
                    })
                    break
            else:
                continue
            break  # stop after first candidate (primary or fallback) that worked

    # Bearish MSS: liquidity grab at a swing HIGH, then body-close break below
    # a prior swing LOW.
    for idx, (grab_i, grab_price) in enumerate(swing_highs):
        prior_lows = [(i, p) for i, p in swing_lows if i < grab_i]
        if not prior_lows:
            continue

        candidates = [prior_lows[-1]]
        if use_second_last_fallback and len(prior_lows) >= 2:
            candidates.append(prior_lows[-2])

        for target_i, target_price in candidates:
            for j in range(grab_i + 1, len(df)):
                if _is_body_close_beyond(df.iloc[j], target_price, "below"):
                    events.append({
                        "index": df.index[j],
                        "price": target_price,
                        "direction": "bearish",
                        "grab_swing_index": df.index[grab_i],
                        "broken_swing_price": target_price,
                        "fallback_used": target_i != prior_lows[-1][0],
                    })
                    break
            else:
                continue
            break

    events.sort(key=lambda e: e["index"])
    return events


def latest_mss(df: pd.DataFrame, lookback: int = 2) -> dict | None:
    """Returns the most recent MSS event, or None if none found."""
    events = detect_mss(df, lookback=lookback)
    return events[-1] if events else None
