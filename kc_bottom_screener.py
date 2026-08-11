#!/usr/bin/env python3
"""Standalone screener: scan WATCHLIST for tickers whose latest confirmed
close is at or below their Keltner Channel lower band, and render a 4-panel
chart (same as holding_charts.py) for each hit. Does this one thing only -
no strategy state, no holdings.json, no buy/sell logic.

Usage: python3 kc_bottom_screener.py [output_dir]
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from holding_charts import chart_one
from indicators import drop_incomplete_last_bar, fetch_history, keltner_channel
from stock_watchlist import WATCHLIST

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_PATH = os.path.join(HERE, "oos_best_params.json")


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/kc_bottom_hits"
    os.makedirs(out_dir, exist_ok=True)

    with open(PARAMS_PATH) as f:
        params = json.load(f)
    weights = (params["weights"]["kc"], params["weights"]["rsi_div"], params["weights"]["kdj"])
    buy_th, sell_th, persist_days = params["buy_threshold"], params["sell_threshold"], params["persist_days"]

    hits, errors = [], []
    for symbol in WATCHLIST:
        try:
            meta, bars = fetch_history(symbol, rng="1y")
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            continue
        bars = drop_incomplete_last_bar(meta, bars)
        if len(bars) < 60:
            continue
        kc = keltner_channel(bars)
        if kc is None:
            continue
        close = bars[-1]["close"]
        if close <= kc["lower"]:
            tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
            bar_date = datetime.fromtimestamp(bars[-1]["time"], tz).strftime("%Y-%m-%d")
            hits.append({
                "symbol": symbol, "close": close, "lower": kc["lower"],
                "basis": kc["basis"], "upper": kc["upper"], "date": bar_date,
            })

    hits.sort(key=lambda h: (h["close"] - h["lower"]) / h["lower"])

    now = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")
    print(f"=== Keltner Channel bottom-band screen — {now} ===\n")
    print(f"{len(hits)} of {len(WATCHLIST)} tickers at/below the lower Keltner band:\n")
    for h in hits:
        pct_vs_band = (h["close"] - h["lower"]) / h["lower"] * 100
        print(f"  {h['symbol']:<6} close ${h['close']:.2f}  lower band ${h['lower']:.2f}  ({pct_vs_band:+.2f}% vs band)")

    print()
    for h in hits:
        out_path = os.path.join(out_dir, f"{h['symbol'].lower()}_kc_bottom.png")
        try:
            chart_one(
                h["symbol"], h["close"], h["date"], weights, buy_th, sell_th, persist_days,
                out_path, marker_label="KC bottom hit",
            )
            print(out_path)
        except Exception as exc:
            print(f"WARNING: failed to chart {h['symbol']}: {exc}", file=sys.stderr)

    if errors:
        print(f"\nErrors ({len(errors)}): " + "; ".join(errors[:10]))


if __name__ == "__main__":
    main()
