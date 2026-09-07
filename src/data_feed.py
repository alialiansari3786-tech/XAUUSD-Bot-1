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

# Primary + fallback tickers for gold. XAUUSD=X is the spot FX-style pair,
# which is what retail brokers (OANDA, etc.) actually quote. GC=F is COMEX
# gold FUTURES - it can diverge from spot by anywhere from a few dollars to
# $20-30+ depending on contango/backwardation and time to contract expiry,
# which will make every entry/SL/TP look "wrong" versus what your broker
# shows even though the bot's math is otherwise correct. Previously had
# these backwards (GC=F as primary) - fixed after real-world testing showed
# this exact divergence.
PRIMARY_TICKER = "XAUUSD=X"
FALLBACK_TICKER = "GC=F"

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

# How many bar-durations old the last candle is allowed to be before we
# treat the fetch as stale/unreliable rather than genuinely current. Real
# live testing found yfinance intermittently serving stale/wrong candles
# for XAUUSD=X - the SAME code path (last-close fallback) produced both
# perfectly reasonable entries and ones $40-60 away from the day's actual
# traded range on the same days, which only makes sense if the underlying
# fetch was sometimes stale. This catches that before it reaches a signal.
MAX_STALENESS_MULTIPLIER = 3


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


def sanity_check_against_daily_range(price: float, buffer_pct: float = 0.01) -> tuple:
    """
    Cross-checks a computed price (entry/SL/TP) against TODAY's actual
    daily candle High/Low, fetched independently. This catches a failure
    mode the timestamp-based is_data_fresh() check CANNOT catch: a candle
    with a fresh, current timestamp but a WRONG price value. Real live
    testing found exactly this - the same "last close" code path produced
    both normal entries and entries $40-60 outside the day's genuine
    traded range, on days where the timestamp was current, meaning
    staleness alone wasn't the (only) problem.

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


def get_candles(timeframe: str) -> pd.DataFrame:
    """
    Fetch candles for a given timeframe name (see TIMEFRAME_CONFIG).
    Tries PRIMARY_TICKER first (with retries), falls back to
    FALLBACK_TICKER (also with retries) only if the primary is completely
    exhausted OR its data comes back stale (see is_data_fresh). Handles
    the 4h resample manually since yfinance has no native 4h interval.

    Raises if BOTH tickers fail or return stale data after all retries -
    callers (main.py) should catch this per-method so one failed/stale
    timeframe fetch doesn't crash the entire scheduled run, and critically
    does NOT silently send a signal built on bad data.
    """
    if timeframe not in TIMEFRAME_CONFIG:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Options: {list(TIMEFRAME_CONFIG)}")

    cfg = TIMEFRAME_CONFIG[timeframe]

    try:
        df = _download(PRIMARY_TICKER, cfg["interval"], cfg["period"])
        if not is_data_fresh(df, timeframe):
            raise ValueError(f"{PRIMARY_TICKER} data for {timeframe} is stale (last candle too old)")
    except Exception as primary_error:
        print(f"  [data_feed] Primary ticker {PRIMARY_TICKER} failed/stale for {timeframe} ({primary_error}), trying fallback {FALLBACK_TICKER}...")
        df = _download(FALLBACK_TICKER, cfg["interval"], cfg["period"])
        if not is_data_fresh(df, timeframe):
            raise ValueError(f"Both {PRIMARY_TICKER} and {FALLBACK_TICKER} data for {timeframe} is stale - refusing to use it")

    if timeframe == "4h":
        df = resample_ohlc(df, "4h")

    return df


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
