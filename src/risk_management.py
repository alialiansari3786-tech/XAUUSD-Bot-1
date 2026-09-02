"""
risk_management.py
Percentage-of-equity position sizing for XAUUSD. Reads account balance and
risk % from config/account.json fresh on every call, so the bot always
uses your CURRENT balance - as your account grows from $1,000 to $2,000
and beyond, the same risk % automatically produces a larger position size
with zero code changes. You only ever need to update the balance figure
in the config file.

XAUUSD contract convention: 1.00 standard lot = 100 troy ounces, so a
$1.00 price move = $100 profit/loss per standard lot (this matches most
retail brokers' gold CFD/forex-style contracts - confirm yours matches
before relying on this for real position sizing, since a few brokers use
different contract sizes).
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "account.json")
CONTRACT_SIZE_OZ_PER_LOT = 100  # standard lot = 100 oz for most XAUUSD brokers
MIN_LOT_STEP = 0.01  # most brokers allow 0.01 lot increments (micro lots)


def load_account_config() -> dict:
    """Reads the current account balance and risk % fresh from the config file."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"config/account.json not found. Create it with your account_balance_usd "
            f"and risk_percent_per_trade before position sizing can work."
        )
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    if "account_balance_usd" not in config or "risk_percent_per_trade" not in config:
        raise ValueError("config/account.json must have 'account_balance_usd' and 'risk_percent_per_trade'")
    return config


def calculate_position_size(entry: float, sl: float, balance: float = None, risk_percent: float = None) -> dict:
    """
    Calculates the lot size that risks exactly risk_percent% of the current
    account balance, given the entry and stop-loss prices.

    If balance/risk_percent aren't passed explicitly, reads them fresh from
    config/account.json - this is the normal path, so a balance update in
    the config file is picked up automatically on the very next alert with
    no other changes needed.

    Returns {lot_size, dollar_risk, price_distance, balance_used, risk_percent_used}.
    """
    if balance is None or risk_percent is None:
        config = load_account_config()
        balance = config["account_balance_usd"] if balance is None else balance
        risk_percent = config["risk_percent_per_trade"] if risk_percent is None else risk_percent

    price_distance = abs(entry - sl)
    if price_distance <= 0:
        raise ValueError("entry and sl must differ to calculate a position size")

    dollar_risk = balance * (risk_percent / 100)
    dollar_per_point_per_lot = CONTRACT_SIZE_OZ_PER_LOT  # $1 move = $100 per 1.0 lot

    raw_lot_size = dollar_risk / (price_distance * dollar_per_point_per_lot)
    # round DOWN to the nearest lot step - rounding up would silently risk
    # more than the intended %, which defeats the purpose of this feature
    lot_size = (raw_lot_size // MIN_LOT_STEP) * MIN_LOT_STEP
    lot_size = round(lot_size, 2)

    viable = lot_size >= MIN_LOT_STEP
    if not viable:
        # the stop is too wide for even the minimum lot size to stay within
        # the risk %, given the current balance - this is a real "don't
        # take this trade at this size" signal, not a rounding quirk
        min_lot_risk = round(MIN_LOT_STEP * price_distance * dollar_per_point_per_lot, 2)

    return {
        "lot_size": lot_size,
        "dollar_risk": round(dollar_risk, 2),
        "price_distance": round(price_distance, 2),
        "balance_used": balance,
        "risk_percent_used": risk_percent,
        "viable": viable,
        "min_lot_risk": min_lot_risk if not viable else None,
    }
