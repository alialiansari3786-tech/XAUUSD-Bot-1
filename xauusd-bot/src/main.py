"""
main.py
Entry point run by GitHub Actions on a schedule. Runs Methods 1, 2, and 3,
and sends a Telegram alert (with chart) for any setup found.

SL/TP MODEL (Method 1): approximates your real rules using the STL/IDM
structure state, since full OB-drawing logic isn't built yet -
  Entry  = current 15m close
  SL     = Recent STL/STH on the 15m structure, in the trade direction
  TP     = the higher timeframe's (Daily) New Confirmation Point if the
           trade is a pullback play, else the 15m's own Confirmation Point
This is an approximation of "entry = 15m OB/mid-FVG, SL = Recent STH/STL,
TP = HTF OB start or 15m Confirmation Point" - once OB-drawing is built,
swap the entry/TP anchors for actual OB/FVG levels.

Method 2 still uses the placeholder alignment-based risk model until its
real Fib-pullback logic is built (see methods.py TODO).
"""

from src.methods import run_method_1, run_method_2
from src.method3 import run_method_3
from src.data_feed import get_candles
from src.telegram_sender import send_message, format_setup_message
from src.order_blocks import order_block_in_range, find_fvg_in_range, valid_pullback_entry
from src.alert_state import already_alerted, mark_alerted
import datetime


from src.structure import calculate_atr


def _safe_sl(candidate_sl, entry: float, is_bullish: bool, recent_df, lookback_bars: int = 20) -> float:
    """
    Hard sanity check: SL must be below entry for a bullish trade, above
    entry for a bearish trade. A structure-derived SL (Recent STL/STH) can
    go stale - price can fall through it without the state machine
    resetting yet (it only resets on a confirmation-point break, not on
    "did price already invalidate the recorded low/high") - which can
    silently put a "stop" on the WRONG side of entry. Real-world testing
    surfaced exactly this on a live alert.

    Falls back to the actual recent price extreme (lowest low / highest
    high over lookback_bars) if the structural candidate fails the check;
    falls back further to a flat % buffer off entry if even that extreme
    is somehow still on the wrong side.

    Buffer beyond the extreme is 1x ATR(14), not a fixed % of price - live
    testing showed a fixed 0.08%-of-price buffer was smaller than a single
    typical 15m candle's range, causing normal market noise to trigger
    stops before any real move could develop (a run of losing trades on
    real data traced directly to this).
    """
    if candidate_sl is not None:
        if is_bullish and candidate_sl < entry:
            return candidate_sl
        if not is_bullish and candidate_sl > entry:
            return candidate_sl

    # structural SL was stale/wrong-sided (or missing) - use the real extreme instead
    window = recent_df.tail(lookback_bars)
    atr = calculate_atr(recent_df, period=14)
    if is_bullish:
        extreme = window["Low"].min()
        sl = extreme - atr
        if sl >= entry:
            sl = entry - atr
    else:
        extreme = window["High"].max()
        sl = extreme + atr
        if sl <= entry:
            sl = entry + atr
    return sl


def handle_method_1(result: dict) -> None:
    major_trend = result["major_trend"]
    if major_trend is None:
        return

    full_alignment = result["h4_agrees"] and result["h1_agrees"]
    is_pullback_play = result["pullback_in_progress"]

    if not (full_alignment or is_pullback_play):
        return  # no clean setup either way

    # Trend-continuation trade goes with major_trend; a pullback play trades
    # the opposite direction while it taps the HTF confirmation point.
    direction = major_trend if full_alignment else ("bearish" if major_trend == "bullish" else "bullish")

    m15_structure = result["idm_structure_by_timeframe"].get("15m", {})
    daily_structure = result["idm_structure_by_timeframe"].get("daily", {})

    is_bullish = direction == "bullish"
    m15_state = m15_structure.get("uptrend_state" if is_bullish else "downtrend_state", {})
    daily_state = daily_structure.get("uptrend_state" if is_bullish else "downtrend_state", {})

    m15_df = get_candles("15m")
    last_close = m15_df["Close"].iloc[-1]

    # Entry: per the walkthrough, "entry at the start of the OB or at the
    # Mid of FVG" - look inside the 15m trading range for an OB first, FVG
    # as fallback. If a Daily+4H combined confluence zone exists, prefer
    # that as a stronger reference for where the 15m entry should be
    # weighted toward (per "a combined 4H+Daily OB confirms the strength
    # of that zone").
    trading_range = m15_state.get("trading_range")
    ob = order_block_in_range(m15_df, trading_range, direction) if trading_range else None
    fvg = find_fvg_in_range(m15_df, trading_range) if trading_range else None
    combined_htf_zone = result.get("daily_h4_combined_zone")

    ob_entry = (ob["bottom"] if is_bullish else ob["top"]) if ob else None
    fvg_entry = fvg["mid"] if fvg else None
    combined_entry = combined_htf_zone["mid"] if combined_htf_zone else None

    entry_source = None
    entry = None
    if valid_pullback_entry(ob_entry, last_close, is_bullish):
        entry = ob_entry
        entry_source = "OB start"
        if combined_htf_zone and combined_htf_zone["bottom"] <= entry <= combined_htf_zone["top"]:
            entry_source = "OB start (confirmed by Daily+4H confluence zone)"
    elif valid_pullback_entry(fvg_entry, last_close, is_bullish):
        entry = fvg_entry
        entry_source = "FVG mid"
    elif valid_pullback_entry(combined_entry, last_close, is_bullish):
        entry = combined_entry
        entry_source = "Daily+4H combined confluence zone (no valid 15m OB/FVG found)"
    else:
        entry = last_close
        entry_source = "last close (no valid pullback OB/FVG found - approximation)"

    sl = _safe_sl(m15_state.get("recent_stl") if is_bullish else m15_state.get("recent_sth"), entry, is_bullish, m15_df)

    tp = None
    if is_pullback_play:
        tp = daily_state.get("confirmation_point")
    if tp is None:
        tp = m15_state.get("confirmation_point")
    risk = abs(entry - sl)
    # TP sanity check: must be on the correct side of entry AND far enough
    # to be meaningful (at least 1.5x the risk) - a stale confirmation
    # point can fail both, same root cause as the SL staleness above.
    tp_valid = tp is not None and (
        (is_bullish and tp > entry and (tp - entry) >= risk * 1.5) or
        (not is_bullish and tp < entry and (entry - tp) >= risk * 1.5)
    )
    if not tp_valid:
        tp = entry + risk * 2 if is_bullish else entry - risk * 2

    play_type = "Trend Continuation" if full_alignment else "Pullback (counter-trend, targeting HTF confirmation)"
    combos = result.get("ob_confluence_combos", [])
    combo_line = f"  OB Confluence: {', '.join(combos)}" if combos else "  OB Confluence: none currently"
    entry_align = result.get("entry_ob_aligns_with_htf")
    align_line = f"\n  Entry OB aligns with HTF OB: {entry_align} (higher success chance)" if entry_align is not None else ""
    summary = (
        f"  Major trend (Daily): {major_trend}\n"
        f"  4H agrees: {result['h4_agrees']} | 1H agrees: {result['h1_agrees']}\n"
        f"  Play type: {play_type}\n"
        f"  Entry source: {entry_source}\n"
        f"{combo_line}{align_line}"
    )

    method_key = "Method 1 (Combined)"
    if already_alerted(method_key, direction, entry, sl, tp):
        print(f"  Skipping {method_key} - duplicate of a recent alert")
        return

    msg = format_setup_message(
        method_name=result["method"],
        direction=direction,
        entry=entry, sl=sl, tp=tp,
        alignment_summary=summary,
        structure_summary=f"15m Recent STL/STH: {sl:.2f} | Target confirmation point: {tp:.2f}",
    )
    send_message(msg)
    mark_alerted(method_key, direction, entry, sl, tp)


def handle_method_2(result: dict) -> None:
    major_trend = result["major_trend"]
    if major_trend is None:
        return

    is_pullback_play = result["pullback_in_progress"]
    full_alignment = result["h1_agrees"] and not is_pullback_play

    if not (full_alignment or is_pullback_play):
        return

    direction = major_trend if full_alignment else ("bearish" if major_trend == "bullish" else "bullish")
    is_bullish = direction == "bullish"

    m5_fib = result["fib_structure_by_timeframe"].get("5m", {})
    daily_fib = result["fib_structure_by_timeframe"].get("daily", {})
    m5_state = m5_fib.get("uptrend_state" if is_bullish else "downtrend_state", {}) if not isinstance(m5_fib, dict) or "uptrend_state" in m5_fib else {}
    daily_state = daily_fib.get("uptrend_state" if is_bullish else "downtrend_state", {}) if not isinstance(daily_fib, dict) or "uptrend_state" in daily_fib else {}

    m5_df = get_candles("5m")
    last_close = m5_df["Close"].iloc[-1]

    # Entry: same OB-start / FVG-mid rule as Method 1, applied to the 5m
    # trading range (5m is Method 2's entry-trigger timeframe). Validated
    # against the pullback direction rule the same way.
    trading_range = m5_state.get("trading_range")
    ob = order_block_in_range(m5_df, trading_range, direction) if trading_range else None
    fvg = find_fvg_in_range(m5_df, trading_range) if trading_range else None

    ob_entry = (ob["bottom"] if is_bullish else ob["top"]) if ob else None
    fvg_entry = fvg["mid"] if fvg else None

    if valid_pullback_entry(ob_entry, last_close, is_bullish):
        entry = ob_entry
        entry_source = "OB start"
    elif valid_pullback_entry(fvg_entry, last_close, is_bullish):
        entry = fvg_entry
        entry_source = "FVG mid"
    else:
        entry = last_close
        entry_source = "last close (no valid pullback OB/FVG found - approximation)"

    sl = _safe_sl(m5_state.get("recent_stl") if is_bullish else m5_state.get("recent_sth"), entry, is_bullish, m5_df)

    tp = None
    if is_pullback_play:
        tp = daily_state.get("confirmation_point")
    if tp is None:
        tp = m5_state.get("confirmation_point")
    risk = abs(entry - sl)
    tp_valid = tp is not None and (
        (is_bullish and tp > entry and (tp - entry) >= risk * 1.5) or
        (not is_bullish and tp < entry and (entry - tp) >= risk * 1.5)
    )
    if not tp_valid:
        tp = entry + risk * 2 if is_bullish else entry - risk * 2

    play_type = "Trend Continuation" if full_alignment else "Pullback (counter-trend, targeting HTF confirmation)"
    summary = (
        f"  Major trend (Daily, Simple MSS): {major_trend}\n"
        f"  1H agrees: {result['h1_agrees']}\n"
        f"  Play type: {play_type}\n"
        f"  Entry source: {entry_source}\n"
        f"  Note: {result.get('note', '')}"
    )

    method_key = "Method 2 (Monthly-Daily-Hourly-5m)"
    if already_alerted(method_key, direction, entry, sl, tp):
        print(f"  Skipping {method_key} - duplicate of a recent alert")
        return

    msg = format_setup_message(
        method_name=result["method"],
        direction=direction,
        entry=entry, sl=sl, tp=tp,
        alignment_summary=summary,
        structure_summary=f"5m Recent STL/STH: {sl:.2f} | Target confirmation point: {tp:.2f}",
    )
    send_message(msg)
    mark_alerted(method_key, direction, entry, sl, tp)


def handle_method_3(result: dict) -> None:
    if not result["setup_found"]:
        return

    entry_zone = result["entry_zone"]
    if not entry_zone["confirming_timeframes"]:
        return  # liquidity swept and structure agrees, but no entry-zone confirmation yet

    direction = result["setup_direction"]
    entry_info = entry_zone["entry_info"]
    entry = entry_info["entry"] if entry_info else result["current_price"]
    entry_source = f"{entry_info['type']} on {entry_info['timeframe']}" if entry_info else "current price (no OB/FVG found - approximation)"

    sl = result["sl"]
    tp = result["tp"]
    if sl is None:
        return  # no structural stop available - skip rather than guess

    if tp is None:
        risk = abs(entry - sl)
        tp = entry + risk * 2 if direction == "bullish" else entry - risk * 2

    swept_summary = "\n".join(
        f"  {s['level_name']}: {s['level_price']:.2f} ({s['side']} swept)" for s in result["swept_levels"]
    )
    sar_line = f"\n  SAR confluence agrees: {result['sar_agrees']}" if result["sar_agrees"] is not None else ""
    note = f"\n\n_Note: {result['note']}_" if result.get("note") else ""

    method_key = "Method 3 (Liquidity + Structure)"
    if already_alerted(method_key, direction, entry, sl, tp):
        print(f"  Skipping {method_key} - duplicate of a recent alert")
        return

    confluence_detail = ", ".join(f"{tf}:{entry_zone['confirming_models'].get(tf, '?')}" for tf in entry_zone["confirming_timeframes"])
    msg = format_setup_message(
        method_name="Liquidity + Structure Method",
        direction=direction,
        entry=entry, sl=sl, tp=tp,
        alignment_summary=(
            "Liquidity levels swept:\n" + swept_summary +
            f"\n  Entry-zone confluence: {entry_zone['confluence_strength']} ({confluence_detail})" +
            f"\n  Entry source: {entry_source}" +
            sar_line + note
        ),
        structure_summary=f"Daily trend: {result['structure'].get('daily', {}).get('trend')}",
    )
    send_message(msg)
    mark_alerted(method_key, direction, entry, sl, tp)


def is_market_weekday() -> bool:
    """
    Skip Saturday and Sunday (UTC) entirely - gold markets are closed then,
    so any data pulled would be stale and any "setup" would be meaningless.
    Note: this is a simple weekday check, not exact market hours - gold
    actually reopens Sunday evening UTC and closes Friday evening UTC, so
    the very first/last few hours of the trading week are also skipped
    here. Fine for a 15-min-interval scanner; tighten if that edge matters
    to you.
    """
    today = datetime.datetime.now(datetime.timezone.utc).weekday()
    return today < 5  # Monday=0 ... Friday=4; Saturday=5, Sunday=6 are skipped


def main():
    if not is_market_weekday():
        print("Weekend (UTC) - gold markets closed, skipping this run entirely.")
        return

    # Each method is wrapped independently: if yfinance/Telegram fail even
    # after retries for one method, that method's check is skipped for this
    # run rather than crashing the entire scheduled job (which would also
    # skip the other two methods and the alert-state commit).
    print("Running Method 1 (Combined)...")
    try:
        m1 = run_method_1()
        handle_method_1(m1)
    except Exception as e:
        print(f"  Method 1 failed this run, skipping: {e}")

    print("Running Method 2 (Monthly-Daily-Hourly-5m)...")
    try:
        m2 = run_method_2()
        handle_method_2(m2)
    except Exception as e:
        print(f"  Method 2 failed this run, skipping: {e}")

    print("Running Method 3 (Liquidity + Structure)...")
    try:
        m3 = run_method_3()
        handle_method_3(m3)
    except Exception as e:
        print(f"  Method 3 failed this run, skipping: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
