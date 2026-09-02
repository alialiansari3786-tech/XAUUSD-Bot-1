"""
retry_utils.py
Retry-with-backoff decorator for live API calls (yfinance, Telegram).
GitHub Actions runs the bot unattended - a single transient network hiccup
shouldn't crash the whole scheduled run and skip every method's check.
"""

import time
import functools


def retry_with_backoff(max_attempts: int = 3, base_delay: float = 2.0, exceptions=(Exception,)):
    """
    Decorator that retries a function on failure with exponential backoff
    (base_delay, base_delay*2, base_delay*4, ...). Re-raises the last
    exception if all attempts fail, so the caller can still decide how to
    handle a total failure (e.g. skip that one method's check rather than
    crashing the entire scheduled run).
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        print(f"  [retry] {func.__name__} failed (attempt {attempt}/{max_attempts}): {e}. Retrying in {delay:.0f}s...")
                        time.sleep(delay)
                    else:
                        print(f"  [retry] {func.__name__} failed after {max_attempts} attempts: {e}")
            raise last_exception
        return wrapper
    return decorator
