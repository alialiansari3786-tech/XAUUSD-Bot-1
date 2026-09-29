"""
fibo2.py
Computes the "Fibo2" zones used by the Liquidity MSNR Method: two
independent shallow-retracement zones measured from an MSS event's grab
point (0, the point price is retracing FROM) to the extreme reached by the
impulsive move that confirmed the MSS (1).

price(pct) = price0 + pct * (price1 - price0)   [linear interpolation]

For a bearish MSS: price0 = the grabbed swing HIGH (before the drop),
price1 = the lowest low reached by the impulsive drop. Small pct (0.109-
0.25) sits close to price0 (the high) - a shallow bounce/retest zone
right after the impulsive move, before continuation lower.

For a bullish MSS (mirror): price0 = the grabbed swing LOW, price1 = the
highest high reached by the impulsive rally.

Two independent zones:
  zone_a: entry at 0.145, outer/SL-fallback line at 0.109
  zone_b: entry at 0.25,  outer/SL-fallback line at 0.214
"""

import pandas as pd

ZONE_A_ENTRY, ZONE_A_OUTER = 0.145, 0.109
ZONE_B_ENTRY, ZONE_B_OUTER = 0.25, 0.214


def compute_fibo2_zone(df: pd.DataFrame, mss_event: dict, lookahead_bars: int = 5) -> dict | None:
    """
    mss_event: an event dict from mss.py's detect_mss()/latest_mss()
    (needs 'direction' and 'grab_swing_index'; 'index' is the confirming
    candle). Returns None if the indices can't be located in df.
    """
    direction = mss_event["direction"]
    try:
        grab_pos = df.index.get_loc(mss_event["grab_swing_index"])
        confirm_pos = df.index.get_loc(mss_event["index"])
    except KeyError:
        return None

    window_end = min(confirm_pos + lookahead_bars, len(df) - 1)
    if window_end < grab_pos:
        return None
    window = df.iloc[grab_pos:window_end + 1]
    if window.empty:
        return None

    if direction == "bearish":
        price0 = float(df["High"].iloc[grab_pos])
        price1 = float(window["Low"].min())
    else:
        price0 = float(df["Low"].iloc[grab_pos])
        price1 = float(window["High"].max())

    def level(pct):
        return price0 + pct * (price1 - price0)

    return {
        "direction": direction,
        "price0": price0,
        "price1": price1,
        "zone_a": {"entry": level(ZONE_A_ENTRY), "outer": level(ZONE_A_OUTER)},
        "zone_b": {"entry": level(ZONE_B_ENTRY), "outer": level(ZONE_B_OUTER)},
    }
