"""
data_feed.py
Pulls multi-timeframe XAUUSD (gold) price data using yfinance.

Notes on yfinance limits (as of 2026):
- 1m data: only last ~7 days available
- 2m-90m data: only last ~60 days available
- 1h and above: long history available
So for our live-polling use case (checking the *current* setup), this is fine —
we always pull a rolling recent window, not deep history.
"""

import yfinance as yf
import pandas as pd
import datetime
from src.retry_utils import retry_with_backoff

# GC=F (COMEX gold futures) is now the ONLY viable ticker for gold on
# Yahoo Finance - XAUUSD=X (the spot FX-style pair) was fully removed by
# Yahoo (confirmed by the user directly, and by live GitHub Actions logs
# showing "Quote not found / possibly delisted" for every fetch attempt).
# There is currently no working spot-gold fallback ticker on Yahoo Finance
# at all, so there's no "primary vs fallback" cascade anymore - GC=F is
# used for everything, always.
#
# Since GC=F is futures, not spot, it carries a variable basis premium
# over what retail brokers (OANDA, etc.) actually quote - real live
# testing found this gap ran ~$40+ during one incident, and it drifts
# over time with contango/backwardation, so a hardcoded correction would
# go stale. Instead, a LIVE basis adjustment is computed every fetch
# against an independent gold-pegged reference (SPOT_REFERENCE_TICKER)
# and applied to the returned OHLC before it ever reaches the strategy
# logic. If that reference itself is unavailable, raw (unadjusted) GC=F
# data is used and every alert built from it visibly says so
# (see get_last_fetch_info() / main.py's _fallback_warning_line()).
PRIMARY_TICKER = "GC=F"

# PAXG (Pax Gold) and XAUT (Tether Gold) are both crypto tokens redeemable
# 1:1 for physical gold and arbitraged tightly to spot gold price - used
# only to compute the live futures-vs-spot basis adjustment above, NOT as
# a primary data source (crypto liquidity/exchange quirks make either one
# individually less reliable as the main feed, but averaging the two
# smooths out single-source noise/quirks). If only one is available on a
# given run, that one alone is used rather than failing the whole
# adjustment - see _compute_live_basis().
SPOT_REFERENCE_TICKERS = ["PAXG-USD", "XAUT-USD"]

# Map our timeframe names to yfinance interval strings + how much history to pull
TIMEFRAME_CONFIG = {
    "monthly": {"interval": "1mo", "period": "5y"},
    "weekly": {"interval": "1wk", "period": "2y"},
    "daily": {"interval": "1d", "period": "6mo"},
    "4h": {"interval": "1h", "period": "60d"},   # yfinance has no native 4h; we resample 1h -> 4h
    "1h": {"interval": "1h", "period": "60d"},
    "15m": {"interval": "15m", "period": "60d"},
    "30m": {"interval": "30m", "period": "60d"},
    "5m": {"interval": "5m", "period": "60d"},
    "3m": {"interval": "2m", "period": "7d"},    # yfinance has no native 3m; closest is 2m
}

# Expected bar duration per timeframe, used for the staleness check below.
BAR_DURATION_MINUTES = {
    "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240,
    "daily": 24 * 60, "weekly": 7 * 24 * 60, "monthly": 30 * 24 * 60, "3m": 3,
}

MAX_STALENESS_MULTIPLIER = 3

# --- Basis-correction safety limits ---
BASIS_CACHE_SECONDS = 600        # work out the basis once per run, reuse it for every timeframe
MAX_BASIS_BAR_AGE_MINUTES = 15   # prices used for the basis must be this fresh
MAX_REF_DISAGREEMENT = 8.0       # if PAXG and XAUT differ by more than this ($), distrust both
MAX_PLAUSIBLE_BASIS = 80.0       # a basis bigger than +/- this ($) is treated as bad data
_basis_cache = {"time": 0.0, "basis": 0.0, "source": None}

# Module-level record of the most recent fetch's basis-adjustment status,
# so main.py can surface a visible warning in the alert whenever the raw
# futures price had to be used unadjusted (rather than that happening
# invisibly). Reset/updated on every get_candles() call.
_last_fetch_info = {"basis_applied": False, "basis_adjustment": 0.0, "basis_source": None}


def get_last_fetch_info() -> dict:
    """Returns info about whether the most recent get_candles() call could apply a live futures-to-spot basis adjustment, and what it was."""
    return dict(_last_fetch_info)


def is_data_fresh(df: pd.DataFrame, timeframe: str) -> bool:
    """
    Checks whether the most recent candle's timestamp is recent enough to
    trust, relative to the timeframe's expected bar duration. Weekends
    aren't accounted for here (a Monday morning fetch of 1h data will
    correctly show the last bar as being from Friday close, appropriately
    "stale" by this check during the closed weekend) - callers should only
    be running this on market-open weekdays anyway (see main.py's
    is_market_weekday), so this mainly catches genuine feed problems
    during active trading hours, not expected weekend gaps.
    """
    if df is None or df.empty:
        return False

    last_ts = df.index[-1]
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize("UTC")
    else:
        last_ts = last_ts.tz_convert("UTC")

    now = datetime.datetime.now(datetime.timezone.utc)
    age_minutes = (now - last_ts).total_seconds() / 60

    bar_minutes = BAR_DURATION_MINUTES.get(timeframe, 60)
    max_allowed = bar_minutes * MAX_STALENESS_MULTIPLIER

    return age_minutes <= max_allowed


@retry_with_backoff(max_attempts=3, base_delay=3.0)
def _download(ticker: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.download(
        tickers=ticker,
        interval=interval,
        period=period,
        progress=False,
        auto_adjust=True,
    )
    if df is None or df.empty:
        raise ValueError(f"No data returned for {ticker} @ {interval}")
    # yfinance sometimes returns MultiIndex columns for single tickers; flatten
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index.name = "datetime"
    return df


def _close_series_utc(ticker: str) -> pd.Series:
    """Latest 5m closes for a ticker, indexed in UTC so different tickers can be matched bar-for-bar."""
    df = _download(ticker, "5m", "1d")
    series = df["Close"].astype(float).copy()
    series.index = pd.to_datetime(series.index)
    if series.index.tz is None:
        series.index = series.index.tz_localize("UTC")
    else:
        series.index = series.index.tz_convert("UTC")
    return series


def _compute_live_basis() -> tuple:
    """
    Futures-vs-spot basis, computed ONCE per run (cached) from the newest 5m bar
    that futures and the reference tokens share, and only if that bar is fresh,
    the two tokens agree, and the result is plausible. Returns (basis, source);
    (0.0, None) means no trustworthy basis was available.
    """
    now_ts = datetime.datetime.now().timestamp()
    if _basis_cache["time"] and now_ts - _basis_cache["time"] < BASIS_CACHE_SECONDS:
        return _basis_cache["basis"], _basis_cache["source"]

    basis, source = 0.0, None
    try:
        futures = _close_series_utc(PRIMARY_TICKER)
        refs = {}
        for ticker in SPOT_REFERENCE_TICKERS:
            try:
                refs[ticker] = _close_series_utc(ticker)
            except Exception as e:
                print(f"  [data_feed] Reference ticker {ticker} unavailable for basis computation ({e})")

        if not refs:
            print("  [data_feed] Both reference tickers unavailable - no basis this run")
        else:
            joined = pd.concat([futures.rename("fut")] + [s.rename(t) for t, s in refs.items()],
                               axis=1, join="inner").dropna()
            if joined.empty:
                print("  [data_feed] No 5m bar shared by futures and reference tickers - no basis this run")
            else:
                last_ts = joined.index[-1]
                age = (datetime.datetime.now(datetime.timezone.utc) - last_ts).total_seconds() / 60
                ref_cols = [c for c in joined.columns if c != "fut"]
                last_row = joined.iloc[-1]
                if age > MAX_BASIS_BAR_AGE_MINUTES:
                    print(f"  [data_feed] Newest shared futures/reference bar is {age:.0f} min old - no basis this run")
                elif len(ref_cols) == 2 and abs(last_row[ref_cols[0]] - last_row[ref_cols[1]]) > MAX_REF_DISAGREEMENT:
                    print(f"  [data_feed] PAXG and XAUT disagree by more than ${MAX_REF_DISAGREEMENT:.0f} - no basis this run")
                else:
                    ref_avg = joined[ref_cols].mean(axis=1)
                    candidate = float((joined["fut"] - ref_avg).tail(3).median())
                    if abs(candidate) > MAX_PLAUSIBLE_BASIS:
                        print(f"  [data_feed] Basis {candidate:.2f} is outside plausible range - no basis this run")
                    else:
                        basis = candidate
                        source = " + ".join(ref_cols) + " (avg)" if len(ref_cols) == 2 else ref_cols[0]
                        print(f"  [data_feed] Basis this run: {basis:.2f} from {source} (bar age {age:.0f} min)")
    except Exception as e:
        print(f"  [data_feed] Basis computation failed ({e}) - no basis this run")

    _basis_cache.update({"time": now_ts, "basis": basis, "source": source})
    return basis, source


def _compute_live_basis_old_unused() -> tuple:
    """
    Computes a LIVE futures-vs-spot basis (GC=F price minus a gold-pegged
    spot reference) to correct GC=F toward what a retail broker actually
    quotes, instead of a hardcoded offset that goes stale as
    contango/backwardation shifts over time (a user manually subtracted a
    fixed $45 after observing this gap once - reasonable as a one-off
    sanity check, but not stable day to day).

    Uses the AVERAGE of both SPOT_REFERENCE_TICKERS (PAXG-USD, XAUT-USD)
    when both are fetchable, to smooth out single-source noise/quirks
    (crypto exchange spreads, momentary liquidity gaps). If only one comes
    back, that one alone is used rather than failing the whole adjustment.

    Returns (basis: float, source: str) - source describes which
    reference(s) were actually used ("PAXG-USD + XAUT-USD (avg)", or just
    one name if only that one was available). basis is 0.0 with
    source=None only if BOTH references fail, in which case raw GC=F data
    is used un-adjusted (still better than crashing, but flagged in
    get_last_fetch_info so every alert built from it visibly says so).
    """
    try:
        futures_df = _download(PRIMARY_TICKER, "5m", "1d")
        futures_price = float(futures_df["Close"].iloc[-1])
    except Exception as e:
        print(f"  [data_feed] Could not fetch {PRIMARY_TICKER} itself for basis computation ({e})")
        return 0.0, None

    ref_prices = {}
    for ticker in SPOT_REFERENCE_TICKERS:
        try:
            ref_df = _download(ticker, "5m", "1d")
            ref_prices[ticker] = float(ref_df["Close"].iloc[-1])
        except Exception as e:
            print(f"  [data_feed] Reference ticker {ticker} unavailable for basis computation ({e})")

    if not ref_prices:
        print("  [data_feed] Both reference tickers unavailable - using raw GC=F price, unadjusted")
        return 0.0, None

    spot_ref_price = sum(ref_prices.values()) / len(ref_prices)
    basis = futures_price - spot_ref_price

    if len(ref_prices) == len(SPOT_REFERENCE_TICKERS):
        source = " + ".join(ref_prices.keys()) + " (avg)"
    else:
        source = list(ref_prices.keys())[0]

    return basis, source


def get_candles(timeframe: str) -> pd.DataFrame:
    """
    Fetch candles for a given timeframe name (see TIMEFRAME_CONFIG), using
    GC=F (COMEX gold futures) - the only working gold ticker left on Yahoo
    Finance since XAUUSD=X was removed, confirmed directly by the user and
    by live GitHub Actions error logs. There is no fallback ticker to try
    if this fails; retries with backoff (see _download) are the only
    resilience against transient failures.

    Since GC=F is futures, not spot, a LIVE basis adjustment is computed
    and applied on every call (see _compute_live_basis) so the returned
    prices track what a retail broker actually quotes rather than raw
    futures. If that adjustment can't be computed, raw GC=F data is used
    and _last_fetch_info records this so every alert built from it
    visibly flags the caveat rather than that happening invisibly.

    Handles the 4h resample manually since yfinance has no native 4h
    interval. Raises if GC=F fails or returns stale data after all
    retries - callers (main.py) should catch this per-method so one
    failed/stale timeframe fetch doesn't crash the entire scheduled run,
    and critically does NOT silently send a signal built on bad data.
    """
    global _last_fetch_info

    if timeframe not in TIMEFRAME_CONFIG:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Options: {list(TIMEFRAME_CONFIG)}")

    cfg = TIMEFRAME_CONFIG[timeframe]

    df = _download(PRIMARY_TICKER, cfg["interval"], cfg["period"])
    if not is_data_fresh(df, timeframe):
        raise ValueError(f"{PRIMARY_TICKER} data for {timeframe} is stale (last candle too old) - no fallback ticker available to try instead")

    basis, basis_source = _compute_live_basis()
    if basis != 0.0:
        for col in ["Open", "High", "Low", "Close"]:
            if col in df.columns:
                df[col] = df[col] - basis
        print(f"  [data_feed] Applied live basis adjustment of {basis:.2f} (source: {basis_source}) to {PRIMARY_TICKER} data for {timeframe}")
    _last_fetch_info = {"basis_applied": basis != 0.0, "basis_adjustment": basis, "basis_source": basis_source}

    if timeframe == "4h":
        df = resample_ohlc(df, "4h")

    return df


def sanity_check_against_daily_range(price: float, buffer_pct: float = 0.01) -> tuple:
    """
    Cross-checks a computed price (entry/SL/TP) against TODAY's actual
    daily candle High/Low, fetched independently. This catches a failure
    mode the timestamp-based is_data_fresh() check CANNOT catch: a candle
    with a fresh, current timestamp but a WRONG price value (e.g. a bad
    print from the data provider).

    Since GC=F is now the only gold ticker on Yahoo Finance and the same
    live basis adjustment (see get_candles) is applied everywhere it's
    used, this check's own "daily" fetch is corrected the same way as the
    entry-timeframe fetch it's validating - so both should land in the
    same (spot-corrected) price neighborhood when everything is working
    normally, keeping this check meaningful for catching genuine one-off
    bad candles rather than a structural basis mismatch.

    buffer_pct allows a little room beyond the day's recorded high/low,
    since the daily candle is still forming intraday and there can be a
    brief lag between it and the entry-timeframe's very latest tick.

    Returns (is_sane: bool, day_low: float, day_high: float) so callers
    can both gate on it and log the actual range for debugging.
    """
    try:
        daily_df = get_candles("daily")
    except Exception as e:
        # if we can't even fetch daily data to cross-check, we cannot
        # vouch for the price either - treat as failing the sanity check
        # rather than silently skipping the check
        print(f"  [sanity_check] Could not fetch daily data to cross-check: {e}")
        return False, None, None

    today_row = daily_df.iloc[-1]
    day_low = float(min(today_row["Low"], today_row["High"], today_row["Open"], today_row["Close"]))
    day_high = float(max(today_row["Low"], today_row["High"], today_row["Open"], today_row["Close"]))
    buffer = (day_high - day_low) * buffer_pct if day_high > day_low else price * buffer_pct

    is_sane = (day_low - buffer) <= price <= (day_high + buffer)
    return is_sane, day_low, day_high


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample a 1h (or finer) OHLC dataframe up to a coarser timeframe, e.g. '4h'."""
    agg = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }
    agg = {k: v for k, v in agg.items() if k in df.columns}
    out = df.resample(rule).agg(agg).dropna(how="any")
    return out


if __name__ == "__main__":
    # quick manual test
    for tf in ["daily", "1h", "4h", "15m"]:
        try:
            d = get_candles(tf)
            print(f"{tf}: {len(d)} candles, latest close = {d['Close'].iloc[-1]:.2f}")
        except Exception as e:
            print(f"{tf}: FAILED - {e}")
