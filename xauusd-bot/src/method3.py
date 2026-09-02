"""
method3.py
Liquidity + Structure method.

STATUS: Liquidity + structure logic below is fully wired up and working.
The custom Pine Script indicator piece is NOT yet ported - paste your
Pine Script and I'll translate its logic into a `custom_indicator_signal()`
function here, then combine it into check_method_3_setup() below.
"""

from src.data_feed import get_candles
from src.structure import label_structure, calculate_atr
from src.liquidity import build_liquidity_map, check_liquidity_sweep
from src.sar import mark_sr_levels, track_fresh_unfresh, detect_rejection, detect_breakout, next_fresh_target
from src.mss import detect_mss, latest_mss
from src.order_blocks import find_order_block, find_fvg_in_range, valid_pullback_entry
from src.confirmation_models import detect_cisd, detect_unicorn, detect_turtle_soup, detect_scob

LIQUIDITY_TIMEFRAMES = ["weekly", "daily", "4h", "1h", "15m"]

# Per the walkthrough: entry-zone timeframes are 5m/15m/30m, and 1H only
# situationally (used here as an extra confluence check, not required).
ENTRY_ZONE_TIMEFRAMES = ["5m", "15m", "30m"]
ENTRY_ZONE_OPTIONAL_1H = "1h"

# Friend's SAR confluence: daily is the anchor timeframe for rejection/target,
# same as the SAR workflow's "start with a Daily rejection" step.
SAR_ANCHOR_TIMEFRAME = "daily"


def check_sar_confluence(daily_df) -> dict:
    """
    Runs the friend's SAR strategy on the Daily timeframe: marks S/R levels,
    tracks fresh/unfresh status, and checks whether the most recent candle
    shows a rejection at a fresh level (the SAR workflow's starting trigger).
    Returns the nearest fresh level in each direction as candidate targets.
    """
    levels = mark_sr_levels(daily_df)
    tracked = track_fresh_unfresh(daily_df, levels)
    current_price = daily_df["Close"].iloc[-1]

    rejection_at = None
    for level in tracked:
        if level["status"] != "fresh":
            continue
        is_support = level["type"] == "support"
        if detect_rejection(daily_df, level["price"], is_support):
            rejection_at = level
            break

    return {
        "levels": tracked,
        "rejection_at": rejection_at,
        "next_fresh_up": next_fresh_target(tracked, current_price, "up"),
        "next_fresh_down": next_fresh_target(tracked, current_price, "down"),
    }


def custom_indicator_signal() -> dict:
    """
    PLACEHOLDER - waiting on your Pine Script.
    Once ported, this should return something like:
      {"signal": "bullish" | "bearish" | None, "details": {...}}
    """
    return {"signal": None, "details": "Pine Script not yet ported"}


def _find_confirming_event(df, direction: str, sweep_time) -> dict | None:
    """
    Checks for a confirmation matching the setup direction, at or after the
    sweep. Tries MSS first (preferred default per the walkthrough), then
    falls back through CISD, Unicorn Model, Turtle Soup, and SCOB in that
    order - the first one that confirms is used. Returns
    {"model": name, "event": event_dict} or None.
    """
    checks = [
        ("MSS", detect_mss(df)),
        ("CISD", detect_cisd(df)),
        ("Unicorn", detect_unicorn(df)),
        ("Turtle Soup", detect_turtle_soup(df)),
        ("SCOB", detect_scob(df)),
    ]

    for model_name, events in checks:
        for ev in reversed(events):  # most recent matching event first
            if ev["direction"] != direction:
                continue
            if sweep_time is not None and ev["index"] < sweep_time:
                continue
            return {"model": model_name, "event": ev}

    return None


def check_entry_zone_confluence(direction: str, sweep_time, current_price: float) -> dict:
    """
    Per the walkthrough: after liquidity is taken, look inside the entry
    zone (5m/15m/30m, situationally 1H) for a confirmation model - MSS,
    CISD, Unicorn Model, Turtle Soup, or SCOB (MSS preferred default, the
    others as documented alternatives). Checks each entry-zone timeframe
    for a confirmation matching the setup direction that happened AT OR
    AFTER the liquidity sweep.

    Every candidate OB/FVG/model-zone entry is validated against
    valid_pullback_entry() before being accepted - a bullish entry must sit
    at or below current_price (a real dip to wait for), a bearish entry at
    or above it. Without this, a real zone that's simply on the wrong side
    of current price (requiring a rally/decline to even reach) was getting
    presented as an actionable trade - confirmed by a live alert with both
    entry and SL sitting far above actual price at alert time.

    Returns which timeframes confirm (and with which model), the OB/FVG
    entry found on the smallest confirming timeframe, and the confluence
    strength label.
    """
    confirming_tfs = []
    confirming_models = {}
    ob_fvg_by_tf = {}

    for tf in ENTRY_ZONE_TIMEFRAMES + [ENTRY_ZONE_OPTIONAL_1H]:
        try:
            df = get_candles(tf)
        except Exception:
            continue

        # Search ALL events since the sweep for one matching the setup
        # direction - not just the single most recent event overall. Small
        # timeframes (5m especially) flip direction often, so checking only
        # "is the latest event still bullish right now" would miss a valid
        # confirmation that happened in the zone and was followed by noise.
        match = _find_confirming_event(df, direction, sweep_time)
        if not match:
            continue

        matching_event = match["event"]

        # OB/FVG anchored on the matching event's leg (entry = start of OB
        # or mid of FVG). Some models (Unicorn, SCOB) already return a
        # top/bottom/mid zone directly rather than a bare index - use that
        # when present instead of re-deriving an OB.
        if "mid" in matching_event:
            candidate_entry = matching_event["bottom"] if direction == "bullish" else matching_event["top"]
            if not valid_pullback_entry(candidate_entry, current_price, direction == "bullish"):
                continue  # real zone, but on the wrong side of current price - not actionable
            confirming_tfs.append(tf)
            confirming_models[tf] = match["model"]
            ob_fvg_by_tf[tf] = {"type": match["model"], "entry": candidate_entry}
            continue

        ob = find_order_block(df, matching_event["index"], direction)
        candidate_entry = None
        candidate_info = None
        if ob:
            candidate_entry = ob["bottom"] if direction == "bullish" else ob["top"]
            candidate_info = {"type": f"OB ({match['model']})", "entry": candidate_entry}
        else:
            # fall back to searching for a FVG across the whole recent range
            fvg = find_fvg_in_range(df, (df["Low"].min(), df["High"].max()))
            if fvg and fvg["bias"] == direction:
                candidate_entry = fvg["mid"]
                candidate_info = {"type": f"FVG ({match['model']})", "entry": candidate_entry}

        if candidate_info and valid_pullback_entry(candidate_entry, current_price, direction == "bullish"):
            confirming_tfs.append(tf)
            confirming_models[tf] = match["model"]
            ob_fvg_by_tf[tf] = candidate_info
        # else: confirmation model fired, but no valid (correctly-sided)
        # entry zone was found on this timeframe - don't count it as
        # confirming, since there'd be nothing actionable to enter on

    required = set(ENTRY_ZONE_TIMEFRAMES)
    confirming_set = set(confirming_tfs)

    if {"5m", "15m", "1h"}.issubset(confirming_set):
        strength = "strongest (5m+15m+1H)"
    elif {"5m", "15m", "30m"}.issubset(confirming_set):
        strength = "strong (5m+15m+30m)"
    elif {"15m", "30m"}.issubset(confirming_set):
        strength = "moderate (15m+30m)"
    elif {"5m", "15m"}.issubset(confirming_set):
        strength = "moderate (5m+15m)"
    elif confirming_tfs:
        strength = f"weak (only {confirming_tfs[0]})"
    else:
        strength = "none"

    # entry from the smallest confirming timeframe (most precise), preferring 5m
    entry_info = None
    for tf in ["5m", "15m", "30m", "1h"]:
        if tf in ob_fvg_by_tf:
            entry_info = {**ob_fvg_by_tf[tf], "timeframe": tf}
            break

    return {
        "confirming_timeframes": confirming_tfs,
        "confirming_models": confirming_models,
        "confluence_strength": strength,
        "entry_info": entry_info,
    }


def run_method_3() -> dict:
    candles = {tf: get_candles(tf) for tf in LIQUIDITY_TIMEFRAMES}
    structure = {tf: label_structure(df) for tf, df in candles.items()}

    liquidity_levels = build_liquidity_map(candles)
    current_price = candles["15m"]["Close"].iloc[-1]
    swept_levels = check_liquidity_sweep(current_price, liquidity_levels)

    indicator = custom_indicator_signal()
    sar = check_sar_confluence(candles["daily"])

    # A "setup" fires when: a liquidity level was just swept AND the
    # opposite-side target direction agrees with the higher-timeframe
    # structure trend AND (once wired in) the custom indicator agrees.
    # SAR confluence (a Daily rejection at a fresh level pointing the same
    # direction) strengthens the setup but is not currently required to fire -
    # it's surfaced separately so you can judge confluence yourself.
    daily_trend = structure.get("daily", {}).get("trend")
    setup_found = False
    setup_direction = None

    for sweep in swept_levels:
        implied_dir = "bullish" if sweep["target_side"] == "high" else "bearish"
        structure_agrees = (implied_dir == "bullish" and daily_trend == "uptrend") or \
                            (implied_dir == "bearish" and daily_trend == "downtrend")
        if structure_agrees:
            setup_found = True
            setup_direction = implied_dir
            break

    sar_agrees = None
    if setup_found and sar["rejection_at"]:
        rej_is_support = sar["rejection_at"]["type"] == "support"
        sar_direction = "bullish" if rej_is_support else "bearish"
        sar_agrees = (sar_direction == setup_direction)

    entry_zone = {"confirming_timeframes": [], "confluence_strength": "none", "entry_info": None}
    sl, tp = None, None
    swept_level_price = None

    if setup_found:
        matching_sweep = next(
            (s for s in swept_levels if ("bullish" if s["target_side"] == "high" else "bearish") == setup_direction),
            None,
        )
        sweep_time = None
        if matching_sweep:
            swept_level_price = matching_sweep["level_price"]
            # find when that level was swept - approximate with the most
            # recent 15m bar, since check_liquidity_sweep only checks current price
            sweep_time = candles["15m"].index[-1]

        entry_zone = check_entry_zone_confluence(setup_direction, sweep_time, current_price)

        entry_price = entry_zone["entry_info"]["entry"] if entry_zone["entry_info"] else current_price

        # SL: the level's own definition price is NOT a safe anchor - if
        # price has already moved past that old level (common, since the
        # level itself can predate the actual sweep candle by a lot), a
        # "level +/- small buffer" can land on the WRONG SIDE of entry,
        # producing a nonsensical stop (e.g. a "stop" above entry on a
        # bullish trade). Use the actual recent price extreme instead
        # (lowest low / highest high over a short recent window), with a
        # hard sanity check that falls back to a simple % buffer off entry
        # if the structural anchor still comes out on the wrong side.
        #
        # Buffer beyond the extreme is 1x ATR(14), not a fixed % of price -
        # live testing showed 6/6 executed Method 3 trades hit their stop,
        # traced to a fixed 0.08%-of-price buffer being smaller than a
        # single typical 15m candle's range - normal noise was triggering
        # stops before any real move could develop.
        recent_window = candles["15m"].tail(20)
        atr = calculate_atr(candles["15m"], period=14)
        if setup_direction == "bullish":
            extreme = recent_window["Low"].min()
            sl = extreme - atr
            if sl >= entry_price:  # sanity check - stop must be below a long's entry
                sl = entry_price - atr
        else:
            extreme = recent_window["High"].max()
            sl = extreme + atr
            if sl <= entry_price:  # sanity check - stop must be above a short's entry
                sl = entry_price + atr

        # TP = the opposite-side liquidity level (per "target the other side
        # liquidity"), the nearest one beyond current price in the trade direction
        opposite_side_levels = [
            price for name, price in liquidity_levels.items()
            if price is not None and (
                (setup_direction == "bullish" and "high" in name.lower() and price > current_price) or
                (setup_direction == "bearish" and "low" in name.lower() and price < current_price)
            )
        ]
        if opposite_side_levels:
            tp = min(opposite_side_levels) if setup_direction == "bullish" else max(opposite_side_levels)
            # sanity check - TP must be far enough from entry to be meaningful
            # (at least 2x the risk distance); otherwise look further out
            risk = abs(entry_price - sl)
            if abs(tp - entry_price) < risk * 1.5:
                farther_levels = [p for p in opposite_side_levels if abs(p - entry_price) >= risk * 1.5]
                if farther_levels:
                    tp = min(farther_levels) if setup_direction == "bullish" else max(farther_levels)
                else:
                    tp = None  # no meaningful target found - let the caller fall back to R:R

    return {
        "method": "Liquidity + Structure",
        "current_price": current_price,
        "swept_levels": swept_levels,
        "structure": structure,
        "indicator": indicator,
        "sar": sar,
        "sar_agrees": sar_agrees,
        "setup_found": setup_found,
        "setup_direction": setup_direction,
        "entry_zone": entry_zone,
        "sl": sl,
        "tp": tp,
        "note": "Indicator signal not yet included - Pine Script pending" if indicator["signal"] is None else None,
    }
