"""
method3_msnr.py
The "Liquidity MSNR Method" - the rebuilt Method 3, replacing the old
liquidity-sweep + SAR model entirely with the Malaysian SNR (MSNR)
framework:

  1. Bias comes from OUTSIDE this method: a twice-daily (17:45/01:00 NY)
     combination of Method 1's and Method 2's own 1H direction
     (bias_state.py). This method does not compute its own bias.
  2. Key Levels (A/V, OC/Gap, OML) are found on 4H/1H only (msnr_levels.py).
  3. The MSS trigger and entry are found on 15m/5m only (mss.py).
  4. Confluence: after an MSS matching the bias, look inside that leg
     (between the MSS's grab point and its impulsive extreme) for a fresh
     Key Level PLUS either a POI (OB/FVG/iFVG) or a Fibo2 zone
     (fibo2.py). Entry = the POI if present, else whichever Fibo2 zone
     gives a valid pullback entry.
  5. SL = beyond the leg's own extreme (ATR-buffered), unless that
     distance exceeds ~50 pips, in which case fall back to the paired
     Fibo2 zone's outer line (0.109/0.214).
     ASSUMPTION: 1 pip = $0.10 for XAUUSD - adjust PIP_SIZE below if your
     broker quotes differently.
  6. TP = nearest fresh opposite-side Key Level on 4H/1H, sanity-checked
     to be >= 1.5x the risk distance, else a 1:2 R:R fallback.

Carries forward the real-incident protections already proven for the old
Method 3 / Methods 1 & 2: a Method-3-specific 60-min hard staleness gate
on the entry-timeframe candle (the same class of bug that produced
mismatched entry-vs-live-price alerts before), msnr_memory's used-level
guard (same class of fix as ob_memory.py), valid_pullback_entry() so an
entry is never on the wrong side of current price, and an ATR-based SL
floor/sanity check so SL never lands on the wrong side of entry.
"""

import datetime

from src.data_feed import get_candles
from src.structure import calculate_atr
from src.mss import detect_mss
from src.msnr_levels import collect_key_levels
from src.fibo2 import compute_fibo2_zone
from src.order_blocks import order_block_in_range, find_fvg_in_range, find_ifvg_in_range, valid_pullback_entry
from src import msnr_memory

KEY_LEVEL_TIMEFRAMES = ["4h", "1h"]
ENTRY_TIMEFRAMES = ["15m", "5m"]
MAX_METHOD3_PRICE_AGE_MINUTES = 60
PIP_SIZE = 0.10          # ASSUMPTION for XAUUSD - confirm/adjust to your broker's pip convention
MAX_SL_PIPS = 50
MIN_TP_RISK_MULTIPLE = 1.5


def _find_poi_in_leg(df, leg_bounds, direction: str) -> dict | None:
    """Prefers an OB, then an inversion FVG (higher-conviction per ICT), then a plain FVG."""
    ob = order_block_in_range(df, leg_bounds, direction)
    if ob:
        return {**ob, "poi_type": "OB"}

    ifvg = find_ifvg_in_range(df, leg_bounds)
    if ifvg and ifvg["bias"] == direction:
        return {**ifvg, "poi_type": "iFVG"}

    fvg = find_fvg_in_range(df, leg_bounds)
    if fvg and fvg["bias"] == direction:
        return {**fvg, "poi_type": "FVG"}

    return None


def run_liquidity_msnr_method(bias_info: dict) -> dict:
    bias = bias_info.get("bias")
    if bias is None:
        return {"method": "Liquidity MSNR Method", "setup_found": False,
                "note": "No bias available yet (waiting on Method 1/2's 1H direction)."}

    candles = {tf: get_candles(tf) for tf in KEY_LEVEL_TIMEFRAMES + ENTRY_TIMEFRAMES}

    # Method-3-specific hard staleness gate on the fastest entry timeframe
    price_timestamp = candles["5m"].index[-1]
    price_timestamp_utc = price_timestamp.tz_localize("UTC") if price_timestamp.tzinfo is None else price_timestamp.tz_convert("UTC")
    price_age_minutes = (datetime.datetime.now(datetime.timezone.utc) - price_timestamp_utc).total_seconds() / 60
    if price_age_minutes > MAX_METHOD3_PRICE_AGE_MINUTES:
        return {
            "method": "Liquidity MSNR Method", "setup_found": False,
            "price_timestamp": str(price_timestamp), "price_age_minutes": round(price_age_minutes, 1),
            "note": f"REFUSED - price data is {price_age_minutes:.0f} min old (max {MAX_METHOD3_PRICE_AGE_MINUTES} min).",
        }

    key_levels = collect_key_levels(candles["4h"], "4h") + collect_key_levels(candles["1h"], "1h")
    key_levels = [lvl for lvl in key_levels if lvl["status"] == "fresh"]

    mss_event = None
    entry_tf = None
    for tf in ENTRY_TIMEFRAMES:
        events = [e for e in detect_mss(candles[tf]) if e["direction"] == bias]
        if events:
            mss_event = events[-1]
            entry_tf = tf
            break

    if mss_event is None:
        return {"method": "Liquidity MSNR Method", "bias": bias, "setup_found": False,
                "note": f"No MSS matching the {bias} bias on 15m/5m."}

    fibo = compute_fibo2_zone(candles[entry_tf], mss_event)
    if fibo is None:
        return {"method": "Liquidity MSNR Method", "bias": bias, "setup_found": False,
                "note": "Could not compute Fibo2 zone for the latest MSS event."}

    leg_low, leg_high = sorted([fibo["price0"], fibo["price1"]])
    levels_in_leg = [lvl for lvl in key_levels if leg_low <= lvl["price"] <= leg_high]
    if not levels_in_leg:
        return {"method": "Liquidity MSNR Method", "bias": bias, "setup_found": False,
                "note": "No fresh 4H/1H Key Level sits inside the current MSS leg."}

    is_bullish = bias == "bullish"
    current_price = candles[entry_tf]["Close"].iloc[-1]
    leg_bounds = (fibo["price0"], fibo["price1"])

    poi = _find_poi_in_leg(candles[entry_tf], leg_bounds, bias)
    entry, entry_source, confluence_type, zone_used = None, None, None, None

    if poi and valid_pullback_entry(poi["mid"], current_price, is_bullish):
        entry = poi["mid"]
        entry_source = f"{poi['poi_type']} POI"
        confluence_type = "Key Level + POI"
    else:
        for zname, zone in [("zone_a", fibo["zone_a"]), ("zone_b", fibo["zone_b"])]:
            if valid_pullback_entry(zone["entry"], current_price, is_bullish):
                entry = zone["entry"]
                entry_source = f"Fibo2 {zname} ({'0.145' if zname == 'zone_a' else '0.25'})"
                confluence_type = "Key Level + Fibo2"
                zone_used = zname
                break

    if entry is None:
        return {"method": "Liquidity MSNR Method", "bias": bias, "setup_found": False,
                "note": "Key Level present in the leg, but no valid POI or Fibo2 pullback entry found."}

    anchor_level = min(levels_in_leg, key=lambda lvl: abs(lvl["price"] - entry))
    if msnr_memory.was_already_used(anchor_level["timeframe"], anchor_level["type"], anchor_level["direction"], anchor_level["price"]):
        return {"method": "Liquidity MSNR Method", "bias": bias, "setup_found": False,
                "note": f"Anchor Key Level ({anchor_level['type']} on {anchor_level['timeframe']}) already used for a prior entry."}

    # --- SL ---
    atr = calculate_atr(candles[entry_tf], period=14)
    extreme = fibo["price0"]
    candidate_sl = extreme - atr if is_bullish else extreme + atr

    sl_distance_pips = abs(entry - candidate_sl) / PIP_SIZE
    if sl_distance_pips > MAX_SL_PIPS and zone_used:
        candidate_sl = fibo[zone_used]["outer"]

    # hard sanity: SL must be on the correct side of entry
    if is_bullish and candidate_sl >= entry:
        candidate_sl = entry - atr
    if (not is_bullish) and candidate_sl <= entry:
        candidate_sl = entry + atr

    sl = candidate_sl
    risk = abs(entry - sl)

    # --- TP: nearest fresh opposite-side Key Level ---
    opposite = [lvl["price"] for lvl in key_levels if (is_bullish and lvl["price"] > entry) or ((not is_bullish) and lvl["price"] < entry)]
    tp = None
    if opposite:
        candidate_tp = min(opposite) if is_bullish else max(opposite)
        if abs(candidate_tp - entry) >= risk * MIN_TP_RISK_MULTIPLE:
            tp = candidate_tp
    if tp is None:
        tp = entry + risk * 2 if is_bullish else entry - risk * 2

    return {
        "method": "Liquidity MSNR Method",
        "bias": bias,
        "bias_checkpoint": bias_info.get("checkpoint"),
        "setup_found": True,
        "direction": bias,
        "entry_timeframe": entry_tf,
        "entry": entry,
        "entry_source": entry_source,
        "confluence_type": confluence_type,
        "anchor_key_level": {"type": anchor_level["type"], "timeframe": anchor_level["timeframe"], "price": anchor_level["price"]},
        "sl": sl,
        "tp": tp,
        "price_timestamp": str(price_timestamp),
        "price_age_minutes": round(price_age_minutes, 1),
        "levels_in_leg_count": len(levels_in_leg),
        "_mark_used": {"timeframe": anchor_level["timeframe"], "level_type": anchor_level["type"],
                        "direction": anchor_level["direction"], "price": anchor_level["price"]},
    }
