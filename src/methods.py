"""
methods.py
Method 1 (Combined) and Method 2 (Monthly-Daily-Hourly-5m) setup detection.

Method 1 now uses the user's actual MSS (mss.py) and STL/IDM/New STL
Confirmation Point structure (idm_structure.py) on Daily/4H/1H/15m, per the
walkthrough - NOT generic HH/HL/LH/LL. Method 2 still uses generic structure
as a placeholder pending its Fib-pullback confirmation logic (TODO).

Method 3 lives in liquidity.py + method3.py + sar.py + smc.py.
"""

from src.data_feed import get_candles
from src.structure import label_structure
from src.mss import latest_mss
from src.idm_structure import track_stl_idm_structure
from src.fib_structure import track_fib_structure
from src.mss_simple import detect_simple_mss
from src.order_blocks import order_block_in_range, obs_overlap, combined_zone

METHOD_1_TIMEFRAMES = ["weekly", "daily", "4h", "1h", "15m", "5m"]
METHOD_1_STRUCTURE_TIMEFRAMES = ["daily", "4h", "1h", "15m"]  # where STL/IDM applies
METHOD_2_STRUCTURE_TIMEFRAMES = ["daily", "1h", "5m"]  # where Fib-pullback structure applies


def analyze_timeframes(timeframes: list) -> dict:
    """Fetches candles and runs generic structure labeling for each timeframe given."""
    result = {}
    for tf in timeframes:
        try:
            df = get_candles(tf)
            result[tf] = label_structure(df)
        except Exception as e:
            result[tf] = {"error": str(e)}
    return result


def check_alignment(structure_by_tf: dict) -> dict:
    """
    Checks whether all timeframes agree on trend direction.
    Returns whether it's aligned, in which direction, and which (if any)
    timeframes disagree.
    """
    trends = {tf: s.get("trend") for tf, s in structure_by_tf.items() if "trend" in s}
    if not trends:
        return {"aligned": False, "direction": None, "disagreeing": list(structure_by_tf.keys())}

    values = list(trends.values())
    up_count = values.count("uptrend")
    down_count = values.count("downtrend")

    if up_count == len(values):
        return {"aligned": True, "direction": "bullish", "disagreeing": []}
    if down_count == len(values):
        return {"aligned": True, "direction": "bearish", "disagreeing": []}

    majority_dir = "uptrend" if up_count >= down_count else "downtrend"
    disagreeing = [tf for tf, t in trends.items() if t != majority_dir]
    return {"aligned": False, "direction": None, "disagreeing": disagreeing}


def _ob_for_state(df, state: dict, direction: str) -> dict | None:
    """Gets the order block inside a given STL/IDM trading range, for the given direction."""
    trading_range = state.get("trading_range")
    if trading_range is None:
        return None
    return order_block_in_range(df, trading_range, direction)


def check_ob_confluence(daily_ob, h4_ob, h1_ob) -> list:
    """
    Checks all 4 documented confluence combinations: Daily+4H, Daily+1H,
    4H+1H, and Daily+4H+1H (all three overlapping together). Returns the
    list of combo names that currently hold - can be more than one
    (e.g. Daily+4H holding doesn't preclude 4H+1H also holding separately).
    """
    combos = []
    if obs_overlap(daily_ob, h4_ob):
        combos.append("Daily+4H")
    if obs_overlap(daily_ob, h1_ob):
        combos.append("Daily+1H")
    if obs_overlap(h4_ob, h1_ob):
        combos.append("4H+1H")
    if obs_overlap(daily_ob, h4_ob) and obs_overlap(h4_ob, h1_ob) and obs_overlap(daily_ob, h1_ob):
        combos.append("Daily+4H+1H")
    return combos


def run_method_1() -> dict:
    """
    Combined Method: for each of Daily/4H/1H/15m, gets the latest MSS
    (liquidity grab + body-close break) and the current STL/IDM structure
    state. Weekly/5m are fetched for context (Weekly OB context is handled
    separately; 5m is the entry-trigger timeframe, not analyzed for
    structure here).

    Also checks real OB-to-OB confluence across timeframes per the
    walkthrough's documented combos (Daily+4H, Daily+1H, 4H+1H,
    Daily+4H+1H), and whether the 15m entry OB aligns with a 1H or 4H OB
    (which the walkthrough says raises the setup's chances of success).
    """
    mss_by_tf = {}
    idm_by_tf = {}
    candles_by_tf = {}

    for tf in METHOD_1_STRUCTURE_TIMEFRAMES:
        try:
            df = get_candles(tf)
            candles_by_tf[tf] = df
            mss_by_tf[tf] = latest_mss(df)
            idm_by_tf[tf] = track_stl_idm_structure(df)
        except Exception as e:
            mss_by_tf[tf] = {"error": str(e)}
            idm_by_tf[tf] = {"error": str(e)}

    # Major trend = Daily MSS direction (per the walkthrough: Daily captures
    # the major trend). 1H showing the opposite MSS direction from Daily/4H
    # is read as a pullback, not a reversal - surfaced here, not auto-traded.
    daily_mss = mss_by_tf.get("daily")
    h4_mss = mss_by_tf.get("4h")
    h1_mss = mss_by_tf.get("1h")

    major_trend = daily_mss["direction"] if daily_mss and "direction" in daily_mss else None
    h4_agrees = h4_mss and "direction" in h4_mss and h4_mss["direction"] == major_trend
    h1_agrees = h1_mss and "direction" in h1_mss and h1_mss["direction"] == major_trend
    pullback_in_progress = major_trend is not None and h4_agrees and not h1_agrees

    # OB confluence across Daily/4H/1H, using each timeframe's own trading
    # range and the major trend direction as the OB side to look for.
    ob_confluence_combos = []
    entry_ob_aligns_with_htf = None
    daily_h4_combined_zone = None

    if major_trend is not None:
        is_bullish = major_trend == "bullish"
        state_key = "uptrend_state" if is_bullish else "downtrend_state"

        daily_state = idm_by_tf.get("daily", {}).get(state_key, {}) if isinstance(idm_by_tf.get("daily"), dict) else {}
        h4_state = idm_by_tf.get("4h", {}).get(state_key, {}) if isinstance(idm_by_tf.get("4h"), dict) else {}
        h1_state = idm_by_tf.get("1h", {}).get(state_key, {}) if isinstance(idm_by_tf.get("1h"), dict) else {}
        m15_state = idm_by_tf.get("15m", {}).get(state_key, {}) if isinstance(idm_by_tf.get("15m"), dict) else {}

        daily_ob = _ob_for_state(candles_by_tf.get("daily"), daily_state, major_trend) if "daily" in candles_by_tf else None
        h4_ob = _ob_for_state(candles_by_tf.get("4h"), h4_state, major_trend) if "4h" in candles_by_tf else None
        h1_ob = _ob_for_state(candles_by_tf.get("1h"), h1_state, major_trend) if "1h" in candles_by_tf else None
        m15_ob = _ob_for_state(candles_by_tf.get("15m"), m15_state, major_trend) if "15m" in candles_by_tf else None

        ob_confluence_combos = check_ob_confluence(daily_ob, h4_ob, h1_ob)

        if daily_ob and h4_ob and obs_overlap(daily_ob, h4_ob):
            daily_h4_combined_zone = combined_zone(daily_ob, h4_ob)

        if m15_ob:
            entry_ob_aligns_with_htf = obs_overlap(m15_ob, h1_ob) or obs_overlap(m15_ob, h4_ob)

    return {
        "method": "Combined Method",
        "mss_by_timeframe": mss_by_tf,
        "idm_structure_by_timeframe": idm_by_tf,
        "major_trend": major_trend,
        "h4_agrees": h4_agrees,
        "h1_agrees": h1_agrees,
        "pullback_in_progress": pullback_in_progress,
        "ob_confluence_combos": ob_confluence_combos,
        "daily_h4_combined_zone": daily_h4_combined_zone,
        "entry_ob_aligns_with_htf": entry_ob_aligns_with_htf,
    }


def run_method_2() -> dict:
    """
    Monthly-Daily-Hourly-5m Method: uses Simple MSS (mss_simple.py, valid-
    swing filtered) and the Fib-pullback STL/New STL Confirmation Point
    structure (fib_structure.py) on Daily/1H/5m. Monthly is context-only
    (Monthly OB for Daily-trend-reversal zones) - not yet wired since OB
    detection on Monthly isn't built; flagged below.
    """
    mss_by_tf = {}
    fib_by_tf = {}

    for tf in METHOD_2_STRUCTURE_TIMEFRAMES:
        try:
            df = get_candles(tf)
            mss_by_tf[tf] = detect_simple_mss(df)
            fib_by_tf[tf] = track_fib_structure(df, timeframe=tf)
        except Exception as e:
            mss_by_tf[tf] = {"error": str(e)}
            fib_by_tf[tf] = {"error": str(e)}

    daily_mss_events = mss_by_tf.get("daily")
    daily_direction = daily_mss_events[-1]["direction"] if daily_mss_events and not isinstance(daily_mss_events, dict) and daily_mss_events else None

    h1_mss_events = mss_by_tf.get("1h")
    h1_direction = h1_mss_events[-1]["direction"] if h1_mss_events and not isinstance(h1_mss_events, dict) and h1_mss_events else None

    h1_agrees = h1_direction is not None and h1_direction == daily_direction
    pullback_in_progress = daily_direction is not None and not h1_agrees and h1_direction is not None

    return {
        "method": "Monthly-Daily-Hourly-5m Method",
        "mss_by_timeframe": mss_by_tf,
        "fib_structure_by_timeframe": fib_by_tf,
        "major_trend": daily_direction,
        "h1_agrees": h1_agrees,
        "pullback_in_progress": pullback_in_progress,
        "note": "Monthly OB context not yet wired in (needs OB detection on Monthly TF)",
    }
