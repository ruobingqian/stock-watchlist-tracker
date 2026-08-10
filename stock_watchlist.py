#!/usr/bin/env python3
"""Daily watchlist summary using Yahoo Finance (via yfinance)."""

import sys
from datetime import datetime

import yfinance as yf

WATCHLIST = ["META", "ISRG", "GOOGL", "AAPL", "SPY", "QQQ"]
ALERT_THRESHOLD_PCT = 2.0


def fetch_quote(symbol: str) -> dict:
    info = yf.Ticker(symbol).fast_info
    last_price = info["lastPrice"]
    prev_close = info["previousClose"]
    change = last_price - prev_close
    pct_change = (change / prev_close) * 100 if prev_close else 0.0
    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "change": change,
        "pct_change": pct_change,
        "day_low": info.get("dayLow"),
        "day_high": info.get("dayHigh"),
        "volume": info.get("lastVolume"),
    }


def format_summary(quotes: list[dict]) -> str:
    lines = [f"Stock Watchlist Summary — {datetime.now():%Y-%m-%d %H:%M}", ""]
    for q in quotes:
        flag = " [ALERT]" if abs(q["pct_change"]) >= ALERT_THRESHOLD_PCT else ""
        lines.append(
            f"{q['symbol']:<6} ${q['last_price']:>9.2f}  "
            f"{q['change']:+7.2f} ({q['pct_change']:+.2f}%)  "
            f"range {q['day_low']:.2f}-{q['day_high']:.2f}  "
            f"vol {q['volume']:,}{flag}"
        )
    return "\n".join(lines)


def main():
    quotes = []
    for symbol in WATCHLIST:
        try:
            quotes.append(fetch_quote(symbol))
        except Exception as exc:
            print(f"WARNING: failed to fetch {symbol}: {exc}", file=sys.stderr)
    print(format_summary(quotes))


if __name__ == "__main__":
    main()
