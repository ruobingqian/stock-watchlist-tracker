#!/usr/bin/env python3
"""Daily watchlist summary using Yahoo Finance's public chart API."""

import sys
from datetime import datetime

import requests

WATCHLIST = ["META", "ISRG", "GOOGL", "AAPL", "SPY", "QQQ"]
ALERT_THRESHOLD_PCT = 2.0

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_quote(symbol: str) -> dict:
    resp = requests.get(CHART_URL.format(symbol=symbol), headers=HEADERS, timeout=15)
    resp.raise_for_status()
    meta = resp.json()["chart"]["result"][0]["meta"]

    last_price = meta["regularMarketPrice"]
    prev_close = meta["previousClose"]
    change = last_price - prev_close
    pct_change = (change / prev_close) * 100 if prev_close else 0.0
    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "change": change,
        "pct_change": pct_change,
        "day_low": meta.get("regularMarketDayLow"),
        "day_high": meta.get("regularMarketDayHigh"),
        "volume": meta.get("regularMarketVolume"),
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
