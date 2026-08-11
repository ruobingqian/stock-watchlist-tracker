#!/usr/bin/env python3
"""Render a chart illustrating the backtested strategy on one symbol: price
with Keltner Channel and buy/sell markers, plus the combined weighted score
that drives those decisions against its buy/sell thresholds.

Usage: python3 strategy_chart.py SYMBOL [--params best_params_selective.json] [output.png]
"""

import argparse
import json
import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backtest import build_rsi_raw, compute_components, simulate
from indicators import drop_incomplete_last_bar, fetch_history, keltner_series

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--params", default=os.path.join(HERE, "best_params_selective.json"))
    parser.add_argument("output", nargs="?", default=None)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    out_path = args.output or f"/tmp/{symbol.lower()}_strategy.png"

    with open(args.params) as f:
        params = json.load(f)
    weights = (params["weights"]["kc"], params["weights"]["rsi_div"], params["weights"]["kdj"])
    buy_th, sell_th, persist_days = params["buy_threshold"], params["sell_threshold"], params["persist_days"]
    w_kc, w_rsi, w_kdj = weights

    meta, bars = fetch_history(symbol, rng="2y", interval="1d")
    comp = compute_components(meta, bars)
    if comp is None:
        raise SystemExit(f"Not enough history for {symbol}")

    equity_curve, trades, buy_bars, sell_bars = simulate(comp, weights, buy_th, sell_th, persist_days)

    start = comp["start_idx"]
    full_bars = drop_incomplete_last_bar(meta, bars)
    disp_bars = full_bars[start:]
    n_disp = len(disp_bars)
    x = list(range(n_disp))
    buy_x = [i - start for i in buy_bars]
    sell_x = [i - start for i in sell_bars]

    rsi_raw = build_rsi_raw(comp["n"], comp["events"], persist_days)[start:]
    kc_raw = comp["kc_raw"][start:]
    kdj_raw = comp["kdj_raw"][start:]
    score = [w_kc * kc_raw[i] + w_rsi * rsi_raw[i] + w_kdj * kdj_raw[i] for i in range(n_disp)]

    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    dates = [datetime.fromtimestamp(b["time"], tz) for b in disp_bars]

    upper, basis, lower_band = keltner_series(full_bars)
    nan = math.nan
    upper_d = [v if v is not None else nan for v in upper[start:]]
    basis_d = [v if v is not None else nan for v in basis[start:]]
    lower_d = [v if v is not None else nan for v in lower_band[start:]]

    fig, (ax_price, ax_score) = plt.subplots(
        2, 1, figsize=(16, 9), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]}
    )

    for i, b in enumerate(disp_bars):
        color = "#26a69a" if b["close"] >= b["open"] else "#ef5350"
        ax_price.vlines(i, b["low"], b["high"], color=color, linewidth=0.8)
        lo = min(b["open"], b["close"])
        height = abs(b["close"] - b["open"]) or 0.01
        ax_price.add_patch(plt.Rectangle((i - 0.3, lo), 0.6, height, color=color))

    ax_price.plot(x, upper_d, color="#2962ff", linewidth=1)
    ax_price.plot(x, basis_d, color="#2962ff", linewidth=1)
    ax_price.plot(x, lower_d, color="#2962ff", linewidth=1)
    ax_price.fill_between(x, lower_d, upper_d, color="#2962ff", alpha=0.06)

    for bx in buy_x:
        ax_price.scatter(bx, disp_bars[bx]["open"], marker="^", color="green", s=140, zorder=5, edgecolors="black")
        ax_price.annotate("BUY", (bx, disp_bars[bx]["open"]), xytext=(0, -16), textcoords="offset points",
                           ha="center", fontsize=8, color="green", fontweight="bold")
    for sx in sell_x:
        price = disp_bars[sx]["open"]
        ax_price.scatter(sx, price, marker="v", color="red", s=140, zorder=5, edgecolors="black")
        ax_price.annotate("SELL", (sx, price), xytext=(0, 10), textcoords="offset points",
                           ha="center", fontsize=8, color="red", fontweight="bold")

    for bx, sx in zip(buy_x, sell_x):
        ax_price.axvspan(bx, sx, color="green", alpha=0.05)

    ax_price.set_title(
        f"{symbol} — strategy trades (weights KC={w_kc:.2f} RSI_div={w_rsi:.2f} KDJ={w_kdj:.2f}, "
        f"buy>={buy_th:.2f}, sell<={sell_th:.2f})"
    )
    ax_price.set_ylabel("Price")

    ax_score.plot(x, score, color="black", linewidth=1.2, label="combined score")
    ax_score.axhline(buy_th, color="green", linewidth=1, linestyle="--", label="buy threshold")
    ax_score.axhline(sell_th, color="red", linewidth=1, linestyle="--", label="sell threshold")
    ax_score.fill_between(x, score, buy_th, where=[s >= buy_th for s in score], color="green", alpha=0.2)
    ax_score.fill_between(x, score, sell_th, where=[s <= sell_th for s in score], color="red", alpha=0.2)
    ax_score.legend(loc="upper left", fontsize=8)
    ax_score.set_ylabel("Score")

    tick_idx, tick_labels, last_month = [], [], None
    for i, d in enumerate(dates):
        key = (d.year, d.month)
        if key != last_month:
            tick_idx.append(i)
            tick_labels.append(d.strftime("%Y" if d.month == 1 else "%b"))
            last_month = key
    ax_score.set_xticks(tick_idx)
    ax_score.set_xticklabels(tick_labels)
    ax_price.set_xlim(-1, n_disp)

    total_return = equity_curve[-1] / equity_curve[0] - 1 if equity_curve else 0.0
    fig.suptitle(f"{symbol} — {len(trades)} trades, total return {total_return*100:+.1f}%")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(out_path)


if __name__ == "__main__":
    main()
