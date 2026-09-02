"""
liquidity.py
Implements the liquidity side of your Method 3:
- Weekly / Daily High-Low
- Daily / 4H / 1H / 15m swing High-Low
- Equal Highs / Equal Lows
- Old Highs / Old Lows
- Detects when a liquidity level gets "taken" (swept) and flags the
  opposite-side target, to combine with structure.py's HH/HL/LH/LL read.
"""

import pandas as pd
from src.structure import find_swings

EQUAL_LEVEL_TOLERANCE_PCT = 0.0007  # ~0.07% - tune this against real gold volatility


def key_range_levels(df: pd.DataFrame) -> dict:
    """Simple high/low of the given dataframe's full range (e.g. this week, this day)."""
    return {
        "high": df["High"].max(),
        "low": df["Low"].min(),
    }


def equal_highs_lows(df: pd.DataFrame, lookback: int = 2, tolerance_pct: float = EQUAL_LEVEL_TOLERANCE_PCT) -> dict:
    """
    Finds clusters of swing highs (or lows) that sit within tolerance_pct of each
    other - these are your "equal highs" / "equal lows" liquidity pools.
    """
    d = find_swings(df, lookback=lookback)
    swing_highs = d.loc[d["swing_high"], "High"]
    swing_lows = d.loc[d["swing_low"], "Low"]

    def cluster(values: pd.Series):
        vals = sorted(values.tolist())
        clusters = []
        for v in vals:
            placed = False
            for c in clusters:
                if abs(v - c[-1]) / c[-1] <= tolerance_pct:
                    c.append(v)
                    placed = True
                    break
            if not placed:
                clusters.append([v])
        # only clusters with 2+ touches count as "equal" liquidity
        return [sum(c) / len(c) for c in clusters if len(c) >= 2]

    return {
        "equal_highs": cluster(swing_highs),
        "equal_lows": cluster(swing_lows),
    }


def old_high_low(df: pd.DataFrame, lookback_bars: int = 50) -> dict:
    """Old (untested-in-recent-history) high/low, useful as a further liquidity target."""
    older = df.iloc[:-lookback_bars] if len(df) > lookback_bars else df
    if older.empty:
        return {"old_high": None, "old_low": None}
    return {"old_high": older["High"].max(), "old_low": older["Low"].min()}


def check_liquidity_sweep(current_price: float, levels: dict, max_distance_pct: float = 0.01) -> list:
    """
    Given the current price and a dict of named liquidity levels
    (e.g. {'weekly_high': 2415.3, 'equal_high_1': 2410.1, ...}),
    returns a list of levels that price has just taken out (swept),
    each paired with the implied opposite-side target direction.

    max_distance_pct limits this to levels within a reasonable proximity
    of current price (default 1%). Without this, a level from months or
    years ago that price simply ended up above/below (in a long trend)
    would incorrectly count as "just swept" forever - a real sweep means
    price recently interacted with that specific level, not that it's
    eventually beyond some old historical price.
    """
    swept = []
    for name, price in levels.items():
        if price is None:
            continue
        distance_pct = abs(current_price - price) / current_price
        if distance_pct > max_distance_pct:
            continue  # too far away to be a meaningful "just swept" level

        is_high_level = "high" in name.lower()
        if is_high_level and current_price > price:
            swept.append({"level_name": name, "level_price": price, "side": "high", "target_side": "low"})
        elif (not is_high_level) and current_price < price:
            swept.append({"level_name": name, "level_price": price, "side": "low", "target_side": "high"})
    return swept


def build_liquidity_map(candles_by_timeframe: dict) -> dict:
    """
    candles_by_timeframe: dict like {'weekly': df, 'daily': df, '4h': df, '1h': df, '15m': df}
    Returns a flat dict of named liquidity levels ready for check_liquidity_sweep().
    """
    levels = {}

    if "weekly" in candles_by_timeframe:
        rng = key_range_levels(candles_by_timeframe["weekly"].iloc[-1:])
        levels["weekly_high"] = rng["high"]
        levels["weekly_low"] = rng["low"]

    if "daily" in candles_by_timeframe:
        rng = key_range_levels(candles_by_timeframe["daily"].iloc[-1:])
        levels["daily_high"] = rng["high"]
        levels["daily_low"] = rng["low"]
        old = old_high_low(candles_by_timeframe["daily"])
        levels["old_daily_high"] = old["old_high"]
        levels["old_daily_low"] = old["old_low"]

    for tf in ["4h", "1h", "15m"]:
        if tf in candles_by_timeframe:
            eq = equal_highs_lows(candles_by_timeframe[tf])
            for i, price in enumerate(eq["equal_highs"]):
                levels[f"{tf}_equal_high_{i}"] = price
            for i, price in enumerate(eq["equal_lows"]):
                levels[f"{tf}_equal_low_{i}"] = price

    return levels
