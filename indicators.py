#!/usr/bin/env python3
"""Shared data fetch + indicator math: Keltner Channel, RSI Divergence, KDJ.

Used by stock_watchlist.py (daily summary), plot_indicators.py (validation
charts), and backtest.py (strategy backtesting/optimization).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

KC_LENGTH = 26
KC_MULTIPLIER = 2.7
KC_ATR_LENGTH = 26

RSI_PERIOD = 24
RSI_PIVOT_LEFT = 5
RSI_PIVOT_RIGHT = 5
RSI_MIN_RANGE = 5
RSI_MAX_RANGE = 60

KDJ_PERIOD = 9
KDJ_SIGNAL = 3

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_history(
    symbol: str,
    rng: str = "1y",
    interval: str = "1d",
    period1: int | None = None,
    period2: int | None = None,
):
    """period1/period2 (unix seconds) give an exact date range and take
    precedence over rng when both would otherwise apply."""
    params = {"period1": period1, "period2": period2, "interval": interval} if period1 is not None else {
        "range": rng,
        "interval": interval,
    }
    resp = requests.get(CHART_URL.format(symbol=symbol), headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]
    quote = result["indicators"]["quote"][0]
    bars = []
    for t, o, h, l, c, v in zip(
        result["timestamp"], quote["open"], quote["high"], quote["low"], quote["close"], quote["volume"]
    ):
        if None in (o, h, l, c):
            continue
        bars.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
    return meta, bars


def drop_incomplete_last_bar(meta: dict, bars: list[dict]) -> list[dict]:
    if not bars:
        return bars
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    last_bar_date = datetime.fromtimestamp(bars[-1]["time"], tz).date()
    now_local = datetime.now(tz)
    if last_bar_date == now_local.date() and now_local.hour < 16:
        return bars[:-1]
    return bars


def ema(values: list[float], length: int) -> list[float | None]:
    if len(values) < length:
        return [None] * len(values)
    out: list[float | None] = [None] * (length - 1)
    prev = sum(values[:length]) / length
    out.append(prev)
    k = 2 / (length + 1)
    for v in values[length:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rma(values: list[float], length: int) -> list[float | None]:
    if len(values) < length:
        return [None] * len(values)
    out: list[float | None] = [None] * (length - 1)
    prev = sum(values[:length]) / length
    out.append(prev)
    for v in values[length:]:
        prev = (prev * (length - 1) + v) / length
        out.append(prev)
    return out


def true_range(bars: list[dict]) -> list[float]:
    tr = [bars[0]["high"] - bars[0]["low"]]
    for i in range(1, len(bars)):
        h, l, prev_close = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        tr.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
    return tr


def keltner_series(bars: list[dict]):
    closes = [b["close"] for b in bars]
    basis = ema(closes, KC_LENGTH)
    atr = rma(true_range(bars), KC_ATR_LENGTH)
    upper = [b + KC_MULTIPLIER * a if b is not None and a is not None else None for b, a in zip(basis, atr)]
    lower = [b - KC_MULTIPLIER * a if b is not None and a is not None else None for b, a in zip(basis, atr)]
    return upper, basis, lower


def keltner_channel(bars: list[dict]) -> dict | None:
    upper, basis, lower = keltner_series(bars)
    if basis[-1] is None:
        return None
    return {"upper": upper[-1], "basis": basis[-1], "lower": lower[-1]}


def rsi(values: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    n = len(values)
    if n < period + 1:
        return [None] * n
    diffs = [values[i] - values[i - 1] for i in range(1, n)]
    avg_gain = rma([max(d, 0.0) for d in diffs], period)
    avg_loss = rma([max(-d, 0.0) for d in diffs], period)
    out: list[float | None] = [None]
    for ag, al in zip(avg_gain, avg_loss):
        if ag is None:
            out.append(None)
        elif al == 0:
            out.append(100.0)
        else:
            out.append(100 - 100 / (1 + ag / al))
    return out


def find_pivots(series: list[float | None], left: int, right: int) -> list[tuple[int, float, str]]:
    pivots = []
    for i in range(left, len(series) - right):
        if series[i] is None:
            continue
        window = series[i - left : i + right + 1]
        if any(x is None for x in window):
            continue
        if series[i] == max(window) and window.count(series[i]) == 1:
            pivots.append((i, series[i], "H"))
        elif series[i] == min(window) and window.count(series[i]) == 1:
            pivots.append((i, series[i], "L"))
    return pivots


def rsi_divergence_pairs(rsi_vals: list[float | None], bars: list[dict]):
    """All regular divergence pairs across the series: (i1, i2, "bullish"/"bearish")."""
    pivots = find_pivots(rsi_vals, RSI_PIVOT_LEFT, RSI_PIVOT_RIGHT)
    lows = [p for p in pivots if p[2] == "L"]
    highs = [p for p in pivots if p[2] == "H"]
    signals = []
    for (i1, r1, _), (i2, r2, _) in zip(lows, lows[1:]):
        if RSI_MIN_RANGE <= (i2 - i1) <= RSI_MAX_RANGE and r2 > r1 and bars[i2]["low"] < bars[i1]["low"]:
            signals.append((i1, i2, "bullish"))
    for (i1, r1, _), (i2, r2, _) in zip(highs, highs[1:]):
        if RSI_MIN_RANGE <= (i2 - i1) <= RSI_MAX_RANGE and r2 < r1 and bars[i2]["high"] > bars[i1]["high"]:
            signals.append((i1, i2, "bearish"))
    return signals


def rsi_divergence(rsi_vals: list[float | None], bars: list[dict]) -> dict:
    """Whether the most recently confirmed RSI pivot itself forms a divergence
    (i.e. a signal that just fired), for the daily summary. Not to be confused
    with rsi_divergence_pairs(), which returns every historical occurrence."""
    pivots = find_pivots(rsi_vals, RSI_PIVOT_LEFT, RSI_PIVOT_RIGHT)
    lows = [p for p in pivots if p[2] == "L"]
    highs = [p for p in pivots if p[2] == "H"]
    pairs = rsi_divergence_pairs(rsi_vals, bars)
    newest_low_idx = lows[-1][0] if lows else -1
    newest_high_idx = highs[-1][0] if highs else -1
    bullish = any(i2 == newest_low_idx for i1, i2, kind in pairs if kind == "bullish")
    bearish = any(i2 == newest_high_idx for i1, i2, kind in pairs if kind == "bearish")
    return {"bullish": bullish, "bearish": bearish}


def kdj_series(bars: list[dict]):
    n = len(bars)
    k_series: list[float | None] = [None] * n
    d_series: list[float | None] = [None] * n
    j_series: list[float | None] = [None] * n
    k_val = d_val = 50.0
    for i in range(KDJ_PERIOD - 1, n):
        window = bars[i - KDJ_PERIOD + 1 : i + 1]
        hh = max(b["high"] for b in window)
        ll = min(b["low"] for b in window)
        rsv = 50.0 if hh == ll else (bars[i]["close"] - ll) / (hh - ll) * 100
        k_val += (rsv - k_val) / KDJ_SIGNAL
        d_val += (k_val - d_val) / KDJ_SIGNAL
        k_series[i], d_series[i], j_series[i] = k_val, d_val, 3 * k_val - 2 * d_val
    return k_series, d_series, j_series


def kdj(bars: list[dict]) -> dict | None:
    k_series, d_series, j_series = kdj_series(bars)
    if k_series[-1] is None:
        return None
    return {"k": k_series[-1], "d": d_series[-1], "j": j_series[-1]}
