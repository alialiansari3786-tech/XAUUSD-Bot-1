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


def get_candles(timeframe: str) -> pd.DataFrame:
    """
    Fetch candles for a given timeframe name (see TIMEFRAME_CONFIG).
    Tries PRIMARY_TICKER first (with retries), falls back to
    FALLBACK_TICKER (also with retries) only if the primary is completely
    exhausted. Handles the 4h resample manually since yfinance has no
    native 4h interval.

    Raises the fallback ticker's exception if BOTH tickers fail after all
    retries - callers (main.py) should catch this per-method so one failed
    timeframe fetch doesn't crash the entire scheduled run.
    """
    if timeframe not in TIMEFRAME_CONFIG:
        raise ValueError(f"Unknown timeframe '{timeframe}'. Options: {list(TIMEFRAME_CONFIG)}")

    cfg = TIMEFRAME_CONFIG[timeframe]

    try:
        df = _download(PRIMARY_TICKER, cfg["interval"], cfg["period"])
    except Exception as primary_error:
        print(f"  [data_feed] Primary ticker {PRIMARY_TICKER} failed for {timeframe} after retries, trying fallback {FALLBACK_TICKER}...")
        df = _download(FALLBACK_TICKER, cfg["interval"], cfg["period"])

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
