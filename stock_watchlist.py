#!/usr/bin/env python3
"""Daily watchlist summary using Yahoo Finance's public chart API.

Adds three TradingView-style indicators, each computed from confirmed daily
bars only (the in-progress bar for today is dropped before market close, to
match TradingView's "wait for timeframe closes" behavior):

- Keltner Channel: EMA basis + Wilder ATR bands
- RSI Divergence: regular bullish/bearish divergence between RSI pivots and price pivots
- KDJ: stochastic K/D/J
"""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

WATCHLIST = ["META", "ISRG", "GOOGL", "AAPL", "SPY", "QQQ"]
ALERT_THRESHOLD_PCT = 2.0

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


def fetch_history(symbol: str, rng: str = "1y", interval: str = "1d"):
    resp = requests.get(
        CHART_URL.format(symbol=symbol),
        headers=HEADERS,
        params={"range": rng, "interval": interval},
        timeout=15,
    )
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


def keltner_channel(bars: list[dict]) -> dict | None:
    closes = [b["close"] for b in bars]
    basis = ema(closes, KC_LENGTH)
    atr = rma(true_range(bars), KC_ATR_LENGTH)
    if basis[-1] is None or atr[-1] is None:
        return None
    return {
        "upper": basis[-1] + KC_MULTIPLIER * atr[-1],
        "basis": basis[-1],
        "lower": basis[-1] - KC_MULTIPLIER * atr[-1],
    }


def rsi(values: list[float], period: int) -> list[float | None]:
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


def rsi_divergence(rsi_vals: list[float | None], bars: list[dict]) -> dict:
    pivots = find_pivots(rsi_vals, RSI_PIVOT_LEFT, RSI_PIVOT_RIGHT)
    lows = [p for p in pivots if p[2] == "L"]
    highs = [p for p in pivots if p[2] == "H"]
    signal = {"bullish": False, "bearish": False}

    if len(lows) >= 2:
        (i2, rsi2, _), (i1, rsi1, _) = lows[-1], lows[-2]
        if RSI_MIN_RANGE <= (i2 - i1) <= RSI_MAX_RANGE:
            if rsi2 > rsi1 and bars[i2]["low"] < bars[i1]["low"]:
                signal["bullish"] = True

    if len(highs) >= 2:
        (i2, rsi2, _), (i1, rsi1, _) = highs[-1], highs[-2]
        if RSI_MIN_RANGE <= (i2 - i1) <= RSI_MAX_RANGE:
            if rsi2 < rsi1 and bars[i2]["high"] > bars[i1]["high"]:
                signal["bearish"] = True

    return signal


def kdj(bars: list[dict]) -> dict | None:
    if len(bars) < KDJ_PERIOD:
        return None
    k_val = d_val = 50.0
    for i in range(KDJ_PERIOD - 1, len(bars)):
        window = bars[i - KDJ_PERIOD + 1 : i + 1]
        hh = max(b["high"] for b in window)
        ll = min(b["low"] for b in window)
        rsv = 50.0 if hh == ll else (bars[i]["close"] - ll) / (hh - ll) * 100
        k_val += (rsv - k_val) / KDJ_SIGNAL
        d_val += (k_val - d_val) / KDJ_SIGNAL
    return {"k": k_val, "d": d_val, "j": 3 * k_val - 2 * d_val}


def analyze(symbol: str) -> dict:
    meta, bars = fetch_history(symbol)
    confirmed = drop_incomplete_last_bar(meta, bars)

    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    regular_market_date = datetime.fromtimestamp(meta["regularMarketTime"], tz).date()
    last_bar_date = datetime.fromtimestamp(bars[-1]["time"], tz).date()
    if last_bar_date == regular_market_date and len(bars) >= 2:
        prev_close = bars[-2]["close"]
    else:
        prev_close = bars[-1]["close"]

    last_price = meta["regularMarketPrice"]
    change = last_price - prev_close
    pct_change = (change / prev_close) * 100 if prev_close else 0.0

    closes = [b["close"] for b in confirmed]

    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "change": change,
        "pct_change": pct_change,
        "day_low": meta.get("regularMarketDayLow"),
        "day_high": meta.get("regularMarketDayHigh"),
        "volume": meta.get("regularMarketVolume"),
        "keltner": keltner_channel(confirmed),
        "rsi": rsi(closes, RSI_PERIOD)[-1],
        "divergence": rsi_divergence(rsi(closes, RSI_PERIOD), confirmed),
        "kdj": kdj(confirmed),
    }


def format_summary(rows: list[dict]) -> str:
    lines = [f"Stock Watchlist Summary — {datetime.now():%Y-%m-%d %H:%M}", ""]
    for q in rows:
        flag = " [ALERT]" if abs(q["pct_change"]) >= ALERT_THRESHOLD_PCT else ""
        lines.append(
            f"{q['symbol']:<6} ${q['last_price']:>9.2f}  "
            f"{q['change']:+7.2f} ({q['pct_change']:+.2f}%)  "
            f"range {q['day_low']:.2f}-{q['day_high']:.2f}  "
            f"vol {q['volume']:,}{flag}"
        )

        kc = q["keltner"]
        if kc:
            lines.append(
                f"       Keltner(26,2.7): upper {kc['upper']:.2f}  basis {kc['basis']:.2f}  lower {kc['lower']:.2f}"
            )

        rsi_val = q["rsi"]
        if rsi_val is not None:
            div = q["divergence"]
            div_str = "bullish" if div["bullish"] else "bearish" if div["bearish"] else "none"
            lines.append(f"       RSI(24): {rsi_val:.1f}  divergence: {div_str}")

        kdj_val = q["kdj"]
        if kdj_val:
            lines.append(
                f"       KDJ(9,3): K {kdj_val['k']:.1f}  D {kdj_val['d']:.1f}  J {kdj_val['j']:.1f}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def main():
    rows = []
    for symbol in WATCHLIST:
        try:
            rows.append(analyze(symbol))
        except Exception as exc:
            print(f"WARNING: failed to fetch {symbol}: {exc}", file=sys.stderr)
    print(format_summary(rows))


if __name__ == "__main__":
    main()
