"""
idm_structure.py
Implements the user's STL/IDM/New STL Confirmation Point trend-and-range
mapping logic (Combined Method, Method 1), used on Daily/4H/1H/15m.

Uptrend mechanics (downtrend/STH is the exact mirror):
1. Start with a Recent STL (swing low) and an IDM (the next swing high,
   acting as inducement).
2. If price takes out (sweeps) the IDM high -> that high becomes the
   "New STL Confirmation Point".
   If price instead makes a NEW swing high WITHOUT first taking the IDM,
   that new high simply replaces the IDM (no confirmation point yet).
3. Once IDM is taken and a confirmation point exists: when price breaks
   above it with a body close, the low that formed between the
   confirmation point's formation and the moment it got broken becomes the
   new Recent STL.
4. The zone between Recent STL and New STL Confirmation Point is the
   trading range to trade in the trend direction. This cascades forward.
"""

import pandas as pd
from src.structure import find_swings


def track_stl_idm_structure(df: pd.DataFrame, lookback: int = 2) -> dict:
    """
    Walks forward through swing points bar-by-bar, maintaining the
    Recent STL / IDM / New STL Confirmation Point state machine for an
    uptrend read, and the mirrored STH version for a downtrend read.

    Returns a dict with:
      - 'uptrend_state': {recent_stl, idm, confirmation_point, trading_range}
      - 'downtrend_state': same shape, mirrored (STH-based)
      - 'history': list of state transitions with their bar index, for
        debugging/visualization
    """
    d = find_swings(df, lookback=lookback)
    swings = []
    for i in range(len(d)):
        if d["swing_high"].iloc[i]:
            swings.append({"index": i, "price": d["High"].iloc[i], "type": "high"})
        elif d["swing_low"].iloc[i]:
            swings.append({"index": i, "price": d["Low"].iloc[i], "type": "low"})

    up = {"recent_stl": None, "idm": None, "confirmation_point": None, "confirmed": False}
    down = {"recent_sth": None, "idm": None, "confirmation_point": None, "confirmed": False}
    history = []

    for s in swings:
        idx, price, typ = s["index"], s["price"], s["type"]

        # --- Uptrend (STL) side ---
        if typ == "low":
            if up["recent_stl"] is None:
                up["recent_stl"] = price
            elif up["confirmed"] and up["confirmation_point"] is not None:
                # a new low after confirmation could become the fresh Recent STL
                # (handled on break-confirmation below; here we just track candidate)
                pass
            elif up["idm"] is None:
                up["idm"] = None  # lows don't set IDM in uptrend read; highs do
        if typ == "high":
            if up["idm"] is None and up["recent_stl"] is not None:
                up["idm"] = price
                history.append({"index": idx, "event": "IDM set (uptrend)", "price": price})
            elif up["idm"] is not None and not up["confirmed"]:
                if price > up["idm"]:
                    # taking out the IDM high -> becomes confirmation point
                    up["confirmation_point"] = price
                    up["confirmed"] = True
                    history.append({"index": idx, "event": "New STL Confirmation Point set", "price": price})
                else:
                    # new swing high without taking IDM -> replaces IDM
                    up["idm"] = price
                    history.append({"index": idx, "event": "IDM replaced (uptrend)", "price": price})

        # Check for body-close break of confirmation point -> new Recent STL
        if up["confirmed"] and up["confirmation_point"] is not None:
            close_price = df["Close"].iloc[idx]
            if close_price > up["confirmation_point"]:
                # find the low formed between confirmation point formation and this break
                lows_between = [sw["price"] for sw in swings if sw["type"] == "low" and sw["index"] <= idx]
                new_stl = lows_between[-1] if lows_between else up["recent_stl"]
                history.append({"index": idx, "event": "Confirmation Point broken -> new Recent STL", "price": new_stl})
                up = {"recent_stl": new_stl, "idm": None, "confirmation_point": None, "confirmed": False}

        # --- Downtrend (STH) side - mirror ---
        if typ == "high":
            if down["recent_sth"] is None:
                down["recent_sth"] = price
        if typ == "low":
            if down["idm"] is None and down["recent_sth"] is not None:
                down["idm"] = price
                history.append({"index": idx, "event": "IDM set (downtrend)", "price": price})
            elif down["idm"] is not None and not down["confirmed"]:
                if price < down["idm"]:
                    down["confirmation_point"] = price
                    down["confirmed"] = True
                    history.append({"index": idx, "event": "New STH Confirmation Point set", "price": price})
                else:
                    down["idm"] = price
                    history.append({"index": idx, "event": "IDM replaced (downtrend)", "price": price})

        if down["confirmed"] and down["confirmation_point"] is not None:
            close_price = df["Close"].iloc[idx]
            if close_price < down["confirmation_point"]:
                highs_between = [sw["price"] for sw in swings if sw["type"] == "high" and sw["index"] <= idx]
                new_sth = highs_between[-1] if highs_between else down["recent_sth"]
                history.append({"index": idx, "event": "Confirmation Point broken -> new Recent STH", "price": new_sth})
                down = {"recent_sth": new_sth, "idm": None, "confirmation_point": None, "confirmed": False}

    up_range = None
    if up["recent_stl"] is not None and up["confirmation_point"] is not None:
        up_range = (up["recent_stl"], up["confirmation_point"])

    down_range = None
    if down["recent_sth"] is not None and down["confirmation_point"] is not None:
        down_range = (down["confirmation_point"], down["recent_sth"])

    return {
        "uptrend_state": {**up, "trading_range": up_range},
        "downtrend_state": {**down, "trading_range": down_range},
        "history": history,
    }
