"""
confirmation_models.py
Implements the 4 alternative confirmation models Method 3 allows swapping
in for MSS: CISD, Unicorn Model, Turtle Soup, SCOB (Single Candle Order
Block). These are generic, publicly-documented smart-money-concept
patterns (not tied to any commercial indicator), built independently from
their standard definitions.

MSS remains the preferred default (see mss.py) - these are the documented
alternatives, used interchangeably in method3.py's entry-zone confirmation
check.
"""

import pandas as pd
from src.structure import find_swings
from src.order_blocks import find_order_block


def detect_cisd(df: pd.DataFrame, min_run: int = 3) -> list:
    """
    CISD (Change in State of Delivery): a shift confirmed when price closes
    back through the OPEN of the last opposing-color candle before an
    impulsive move - a body-based structure shift, distinct from MSS's
    swing-level break.

    Requires at least `min_run` consecutive same-color candles forming the
    move being reversed - without this, a plain candle-to-candle color
    flip (which happens constantly in normal volatility) would falsely
    count as a "shift" on nearly every bar.

    Returns a list of {index, price, direction}.
    """
    events = []
    is_green = (df["Close"] > df["Open"]).values

    i = 1
    n = len(df)
    while i < n:
        run_color = is_green[i]
        run_start = i
        while i < n and is_green[i] == run_color:
            i += 1
        run_length = i - run_start

        if run_length >= min_run and run_start > 0:
            origin_pos = run_start - 1  # last opposing-color candle before the run
            origin_open = df["Open"].iloc[origin_pos]
            direction = "bearish" if run_color else "bullish"  # reversal direction

            for j in range(i, min(i + 20, n)):
                if direction == "bullish" and df["Close"].iloc[j] > origin_open:
                    events.append({"index": df.index[j], "price": origin_open, "direction": "bullish"})
                    break
                if direction == "bearish" and df["Close"].iloc[j] < origin_open:
                    events.append({"index": df.index[j], "price": origin_open, "direction": "bearish"})
                    break

    events.sort(key=lambda e: e["index"])
    return events


def detect_unicorn(df: pd.DataFrame, lookback: int = 2, max_bars_after_mss: int = 15) -> list:
    """
    Unicorn Model: a Fair Value Gap that overlaps with a breaker block (an
    order block that failed and got flipped - i.e. the opposite-direction
    OB from just before a structure break in the current leg). The overlap
    zone is treated as unusually high-probability confluence.

    Only pairs an MSS/breaker with FVGs that formed within
    `max_bars_after_mss` candles of that MSS - without this restriction,
    pairing every MSS with every FVG anywhere in the dataset produces a
    combinatorial explosion of spurious "confluence" that isn't really
    from the same move.

    Returns a list of {index, top, bottom, mid, direction}.
    """
    from src.mss import detect_mss
    from src.smc import detect_fvg

    events = []
    mss_events = detect_mss(df, lookback=lookback)
    fvgs = detect_fvg(df)

    for mss in mss_events:
        breaker = find_order_block(df, mss["index"], mss["direction"])
        if not breaker:
            continue
        try:
            mss_pos = df.index.get_loc(mss["index"])
        except KeyError:
            continue

        for fvg in fvgs:
            if fvg["bias"] != mss["direction"]:
                continue
            try:
                fvg_pos = df.index.get_loc(fvg["index"])
            except KeyError:
                continue
            if not (mss_pos <= fvg_pos <= mss_pos + max_bars_after_mss):
                continue  # FVG must belong to the same leg, shortly after the MSS

            fvg_top, fvg_bottom = fvg["top"], fvg["bottom"]
            overlap = not (breaker["top"] < min(fvg_top, fvg_bottom) or max(fvg_top, fvg_bottom) < breaker["bottom"])
            if overlap:
                top = min(breaker["top"], max(fvg_top, fvg_bottom))
                bottom = max(breaker["bottom"], min(fvg_top, fvg_bottom))
                events.append({
                    "index": fvg["index"],
                    "top": top,
                    "bottom": bottom,
                    "mid": (top + bottom) / 2,
                    "direction": mss["direction"],
                })
                break  # one match per MSS is enough

    events.sort(key=lambda e: e["index"])
    return events


def detect_turtle_soup(df: pd.DataFrame, lookback_period: int = 20, reversal_window: int = 3) -> list:
    """
    Turtle Soup: a false-breakout reversal. Price sweeps beyond a prior
    N-period swing high/low (a stop hunt), then closes back INSIDE that
    prior range within a few candles - signaling the breakout was a trap
    and price is reversing.

    Returns a list of {index, price, direction}.
    """
    events = []
    rolling_high = df["High"].rolling(lookback_period).max().shift(1)
    rolling_low = df["Low"].rolling(lookback_period).min().shift(1)

    for i in range(lookback_period, len(df)):
        prior_high = rolling_high.iloc[i]
        prior_low = rolling_low.iloc[i]
        if pd.isna(prior_high) or pd.isna(prior_low):
            continue

        # bearish turtle soup: sweeps above prior high, then closes back below it
        if df["High"].iloc[i] > prior_high:
            for j in range(i + 1, min(i + 1 + reversal_window, len(df))):
                if df["Close"].iloc[j] < prior_high:
                    events.append({"index": df.index[j], "price": prior_high, "direction": "bearish"})
                    break

        # bullish turtle soup: sweeps below prior low, then closes back above it
        if df["Low"].iloc[i] < prior_low:
            for j in range(i + 1, min(i + 1 + reversal_window, len(df))):
                if df["Close"].iloc[j] > prior_low:
                    events.append({"index": df.index[j], "price": prior_low, "direction": "bullish"})
                    break

    events.sort(key=lambda e: e["index"])
    return events


def detect_scob(df: pd.DataFrame, body_ratio_threshold: float = 0.7, lookback: int = 2) -> list:
    """
    SCOB (Single Candle Order Block): a single decisive, strong-bodied
    candle (body >= body_ratio_threshold of its total range) immediately
    preceding a structure break - used as the order block anchor instead
    of the more complex "last opposing candle" definition.

    Returns a list of {index, top, bottom, mid, direction}.
    """
    from src.mss import detect_mss

    events = []
    mss_events = detect_mss(df, lookback=lookback)

    body = (df["Close"] - df["Open"]).abs()
    total_range = (df["High"] - df["Low"]).replace(0, pd.NA)
    body_ratio = body / total_range

    for mss in mss_events:
        try:
            end_pos = df.index.get_loc(mss["index"])
        except KeyError:
            continue

        # scan backward from the MSS event for the nearest strong-bodied
        # candle in the opposing direction (the decisive single candle)
        for i in range(end_pos - 1, max(0, end_pos - 20), -1):
            if pd.isna(body_ratio.iloc[i]):
                continue
            is_green = df["Close"].iloc[i] > df["Open"].iloc[i]
            opposing = (not is_green) if mss["direction"] == "bullish" else is_green
            if opposing and body_ratio.iloc[i] >= body_ratio_threshold:
                top = float(df["High"].iloc[i])
                bottom = float(df["Low"].iloc[i])
                events.append({
                    "index": df.index[i],
                    "top": top,
                    "bottom": bottom,
                    "mid": (top + bottom) / 2,
                    "direction": mss["direction"],
                })
                break

    events.sort(key=lambda e: e["index"])
    return events
