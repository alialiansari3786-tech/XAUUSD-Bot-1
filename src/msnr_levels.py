"""
msnr_levels.py
Detects the Malaysian Support & Resistance (MSNR) "Key Levels" used by the
Liquidity MSNR Method (new Method 3), per the user's MSNR reference PDF
(Yanu Emmanuel F, "The Alchemist" - MSNR x SMC x ICT).

Four level types:
  - A/V levels   (ch 2.1)  - close-to-open line between two adjacent
    candles, NO gap required. 'A' (bearish candle follows bullish) =
    resistance. 'V' (bullish candle follows bearish) = support.
  - OC/Gap levels (ch 2.2.1) - a genuine price gap (no range overlap)
    between two adjacent candles. Distinct from A/V - requires an actual
    gap, not just a close/open mismatch.
  - OML levels (Head & Shoulders neckline-at-shoulder-level) - a swing
    (Left Shoulder) exceeded by a more extreme swing (Head), then price
    returns to re-touch the LS's own price level forming a Right
    Shoulder - entry is AT that re-touch (the RS candle's extreme),
    SL taken from the lower timeframe's own structure around the RS.
  - Freshness / SBR / RBS: per ch 2.2.3-2.2.4 - untouched = fresh; any
    wick/body touch = unfresh; a full-body CLOSE through it flips the
    level's role (SBR: resistance->support, RBS: support->resistance)
    and it becomes fresh again in the new role, able to keep re-flipping.
"""

import pandas as pd
from src.structure import find_swings

OML_TOLERANCE_PCT = 0.001       # how close RS must return to LS price to count as a re-touch
TOUCH_TOLERANCE_PCT = 0.0005    # wick/body "touch" tolerance for freshness tracking


def find_av_levels(df: pd.DataFrame) -> list:
    """
    Classic 'A' (resistance) / 'V' (support) SNR levels: draw a line from
    one candle's CLOSE to the next candle's OPEN (ignore wicks). No gap
    required - this is distinct from find_oc_gap_levels().
    """
    levels = []
    opens, closes = df["Open"], df["Close"]

    for i in range(len(df) - 1):
        bullish0 = closes.iloc[i] > opens.iloc[i]
        bullish1 = closes.iloc[i + 1] > opens.iloc[i + 1]
        price = (closes.iloc[i] + opens.iloc[i + 1]) / 2

        if bullish0 and not bullish1:
            levels.append({"type": "A", "direction": "resistance", "price": float(price), "index": df.index[i + 1]})
        elif (not bullish0) and bullish1:
            levels.append({"type": "V", "direction": "support", "price": float(price), "index": df.index[i + 1]})

    return levels


def find_oc_gap_levels(df: pd.DataFrame) -> list:
    """
    OC/Gap SNR level (ch 2.2.1): a genuine price gap between two adjacent
    candles - candle i+1's range does not overlap candle i's range at
    all. Bullish gap (price gapped up) acts as a support zone if
    revisited; bearish gap (price gapped down) acts as a resistance zone.
    """
    levels = []
    for i in range(len(df) - 1):
        c0_high, c0_low = df["High"].iloc[i], df["Low"].iloc[i]
        c1_high, c1_low = df["High"].iloc[i + 1], df["Low"].iloc[i + 1]

        if c1_low > c0_high:
            top, bottom = float(c1_low), float(c0_high)
            levels.append({"type": "OC_GAP", "direction": "support", "top": top, "bottom": bottom,
                            "price": (top + bottom) / 2, "index": df.index[i + 1]})
        elif c1_high < c0_low:
            top, bottom = float(c0_low), float(c1_high)
            levels.append({"type": "OC_GAP", "direction": "resistance", "top": top, "bottom": bottom,
                            "price": (top + bottom) / 2, "index": df.index[i + 1]})

    return levels


def find_oml_levels(df: pd.DataFrame, lookback: int = 2, tolerance_pct: float = OML_TOLERANCE_PCT) -> list:
    """
    OML level: Left Shoulder (LS) -> Head (a more extreme swing) -> Right
    Shoulder (RS) returning to the LS's own price. The OML level price IS
    the LS's price; the entry is at the RS re-touch (top/bottom of the RS
    candle), SL to be taken from the LTF's own structure around the RS.

    Returns a list of {type:'OML', direction, price, ls_index, head_index,
    rs_index} - direction 'bearish' = topping pattern (highs), 'bullish' =
    bottoming pattern (lows).
    """
    d = find_swings(df, lookback=lookback)
    highs = [(i, float(d["High"].iloc[i])) for i in range(len(d)) if d["swing_high"].iloc[i]]
    lows = [(i, float(d["Low"].iloc[i])) for i in range(len(d)) if d["swing_low"].iloc[i]]

    levels = []

    def _scan(points, direction, more_extreme):
        for a in range(len(points) - 2):
            ls_i, ls_p = points[a]
            for b in range(a + 1, len(points) - 1):
                head_i, head_p = points[b]
                if not more_extreme(head_p, ls_p):
                    continue
                for c in range(b + 1, len(points)):
                    rs_i, rs_p = points[c]
                    if abs(rs_p - ls_p) / ls_p <= tolerance_pct:
                        levels.append({
                            "type": "OML", "direction": direction, "price": ls_p,
                            "ls_index": df.index[ls_i], "head_index": df.index[head_i],
                            "rs_index": df.index[rs_i], "index": df.index[rs_i],
                        })
                    break  # only the first RS candidate after this head
                break  # only the first head candidate after this LS

    _scan(highs, "bearish", lambda head, ls: head > ls)
    _scan(lows, "bullish", lambda head, ls: head < ls)

    return levels


def track_freshness(df: pd.DataFrame, level: dict, touch_tolerance_pct: float = TOUCH_TOLERANCE_PCT) -> dict:
    """
    Walks forward from a level's formation bar tracking fresh/unfresh and
    SBR/RBS flips (ch 2.2.3-2.2.4): untouched = fresh; any wick/body touch
    = unfresh; a full body CLOSE through flips the level's role and it
    becomes fresh again in the new role, and can keep re-flipping.

    Works for any level dict with a 'price' and 'index' (OML, A/V, or the
    OC_GAP midpoint - OC_GAP's actual top/bottom band is used for the
    touch check when present, midpoint for the flip-direction check).
    """
    try:
        start_pos = df.index.get_loc(level["index"])
    except KeyError:
        return {**level, "status": "fresh", "flipped": False}

    price = level["price"]
    direction = level["direction"]
    top = level.get("top", price)
    bottom = level.get("bottom", price)
    status = "fresh"
    flipped = False
    last_change = level["index"]

    for i in range(start_pos + 1, len(df)):
        high, low, close = df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
        band_top = top + top * touch_tolerance_pct
        band_bottom = bottom - bottom * touch_tolerance_pct
        touched = not (high < band_bottom or low > band_top)

        # direction here means the level's role: 'support' or 'resistance'
        # (OML/A/V use 'support'/'resistance' too - normalize elsewhere)
        role = "support" if direction in ("support", "bullish") else "resistance"

        if status == "fresh":
            if touched:
                status = "unfresh"
                last_change = df.index[i]
        else:
            broke_through = (close < bottom) if role == "resistance" else (close > top)
            if broke_through:
                direction = "support" if role == "resistance" else "resistance"
                flipped = not flipped
                status = "fresh"
                last_change = df.index[i]

    return {**level, "status": status, "direction": direction, "flipped": flipped, "last_status_change": last_change}


def collect_key_levels(df: pd.DataFrame, timeframe: str) -> list:
    """
    Runs all three detectors on one timeframe's candles, tracks freshness
    on each, and tags every level with its source timeframe. This is the
    Layer-1 "Key Levels" set the Liquidity MSNR Method looks for
    confluence with.
    """
    raw = find_av_levels(df) + find_oc_gap_levels(df) + find_oml_levels(df)
    tracked = [track_freshness(df, lvl) for lvl in raw]
    for lvl in tracked:
        lvl["timeframe"] = timeframe
    return tracked
