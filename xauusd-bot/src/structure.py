"""
structure.py
Detects swing highs/lows and labels market structure as HH/HL/LH/LL,
plus overall trend direction per timeframe. Used by all 3 of your methods.
"""

import pandas as pd


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Average True Range over the given period - measures real recent
    volatility, used to size stop-loss buffers appropriately instead of an
    arbitrary fixed % of price (which was too tight for gold's actual
    volatility and caused a run of normal-noise stop-outs in live testing).
    Returns the most recent ATR value, or a simple high-low range average
    if there isn't enough data for a full ATR calculation.
    """
    if len(df) < 2:
        return float((df["High"] - df["Low"]).iloc[-1]) if len(df) == 1 else 0.0

    high_low = df["High"] - df["Low"]
    high_close_prev = (df["High"] - df["Close"].shift(1)).abs()
    low_close_prev = (df["Low"] - df["Close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)

    atr = true_range.rolling(min(period, len(df))).mean().iloc[-1]
    if pd.isna(atr):
        atr = true_range.mean()
    return float(atr)


def find_swings(df: pd.DataFrame, lookback: int = 2) -> pd.DataFrame:
    """
    Marks swing highs and swing lows using a simple fractal method:
    a candle is a swing high if its High is higher than `lookback` candles
    on both sides; a swing low if its Low is lower than `lookback` candles
    on both sides.

    Returns the dataframe with two new boolean columns: swing_high, swing_low.
    """
    df = df.copy()
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)

    swing_high = [False] * n
    swing_low = [False] * n

    for i in range(lookback, n - lookback):
        window_high = highs[i - lookback: i + lookback + 1]
        window_low = lows[i - lookback: i + lookback + 1]
        if highs[i] == max(window_high) and list(window_high).count(highs[i]) == 1:
            swing_high[i] = True
        if lows[i] == min(window_low) and list(window_low).count(lows[i]) == 1:
            swing_low[i] = True

    df["swing_high"] = swing_high
    df["swing_low"] = swing_low
    return df


def label_structure(df: pd.DataFrame, lookback: int = 2) -> dict:
    """
    Runs find_swings, then walks the sequence of swing highs/lows to label
    each as HH, HL, LH, or LL relative to the prior swing of the same type.

    Returns a dict with:
      - 'swings': list of {index, datetime, price, type ('high'/'low'), label}
      - 'trend': 'uptrend' / 'downtrend' / 'ranging' (based on the most recent
                 confirmed structure)
      - 'last_broken_level': price of the most recent structure level that
                 price has broken through (a BOS - break of structure), or None
    """
    d = find_swings(df, lookback=lookback)

    swings = []
    for i in range(len(d)):
        if d["swing_high"].iloc[i]:
            swings.append({"index": i, "datetime": d.index[i], "price": d["High"].iloc[i], "type": "high"})
        elif d["swing_low"].iloc[i]:
            swings.append({"index": i, "datetime": d.index[i], "price": d["Low"].iloc[i], "type": "low"})

    last_high = None
    last_low = None
    for s in swings:
        if s["type"] == "high":
            s["label"] = "HH" if (last_high is not None and s["price"] > last_high) else \
                         ("LH" if last_high is not None else "H")
            last_high = s["price"]
        else:
            s["label"] = "HL" if (last_low is not None and s["price"] > last_low) else \
                         ("LL" if last_low is not None else "L")
            last_low = s["price"]

    # Determine trend from the last few labeled swings
    recent_labels = [s["label"] for s in swings[-4:]]
    if recent_labels.count("HH") + recent_labels.count("HL") >= 2 and "LL" not in recent_labels:
        trend = "uptrend"
    elif recent_labels.count("LH") + recent_labels.count("LL") >= 2 and "HH" not in recent_labels:
        trend = "downtrend"
    else:
        trend = "ranging"

    # Break of structure check: has price closed beyond the last opposite swing?
    last_broken_level = None
    if swings:
        last_close = d["Close"].iloc[-1]
        last_swing_high = next((s["price"] for s in reversed(swings) if s["type"] == "high"), None)
        last_swing_low = next((s["price"] for s in reversed(swings) if s["type"] == "low"), None)
        if trend == "uptrend" and last_swing_low is not None and last_close < last_swing_low:
            last_broken_level = last_swing_low
        elif trend == "downtrend" and last_swing_high is not None and last_close > last_swing_high:
            last_broken_level = last_swing_high

    return {
        "swings": swings,
        "trend": trend,
        "last_broken_level": last_broken_level,
    }
