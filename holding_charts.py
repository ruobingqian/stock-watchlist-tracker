#!/usr/bin/env python3
"""Render a 4-panel chart for each current holding, for the daily report:
price + Keltner Channel overlay (with the actual entry marked), RSI
Divergence, KDJ, and the combined weighted score vs. buy/sell thresholds
(the same score panel from strategy_chart.py).

Usage: python3 holding_charts.py [output_dir]
"""

import json
import math
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from backtest import build_rsi_raw, compute_raw_components
from indicators import (
    KC_LENGTH,
    KC_MULTIPLIER,
    KDJ_PERIOD,
    KDJ_SIGNAL,
    RSI_PERIOD,
    drop_incomplete_last_bar,
    fetch_history,
    kdj_series,
    keltner_series,
    rsi,
    rsi_divergence_pairs,
)

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_PATH = os.path.join(HERE, "oos_best_params.json")
HOLDINGS_PATH = os.path.join(HERE, "holdings.json")


def chart_one(symbol: str, entry_price: float, entry_date: str, weights, buy_th: float, sell_th: float, persist_days: int, out_path: str, marker_label: str = "BUY (entry)"):
    meta, bars = fetch_history(symbol, rng="2y", interval="1d")
    bars = drop_incomplete_last_bar(meta, bars)
    n = len(bars)
    closes = [b["close"] for b in bars]
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    dates = [datetime.fromtimestamp(b["time"], tz) for b in bars]
    x = list(range(n))

    display_start = next((i for i, d in enumerate(dates) if (dates[-1] - d).days <= 365), 0)

    nan = math.nan
    upper, basis, lower = keltner_series(bars)
    upper_d = [v if v is not None else nan for v in upper]
    basis_d = [v if v is not None else nan for v in basis]
    lower_d = [v if v is not None else nan for v in lower]

    rsi_vals = rsi(closes, RSI_PERIOD)
    div_pairs = rsi_divergence_pairs(rsi_vals, bars)
    bull_pairs = [(i1, rsi_vals[i1], i2, rsi_vals[i2]) for i1, i2, kind in div_pairs if kind == "bullish"]
    bear_pairs = [(i1, rsi_vals[i1], i2, rsi_vals[i2]) for i1, i2, kind in div_pairs if kind == "bearish"]

    k_series, d_series, j_series = kdj_series(bars)
    k_d = [v if v is not None else nan for v in k_series]
    d_d = [v if v is not None else nan for v in d_series]
    j_d = [v if v is not None else nan for v in j_series]

    raw = compute_raw_components(bars)
    rsi_raw = build_rsi_raw(raw["n"], raw["events"], persist_days)
    w_kc, w_rsi, w_kdj = weights
    score = [w_kc * raw["kc_raw"][i] + w_rsi * rsi_raw[i] + w_kdj * raw["kdj_raw"][i] for i in range(n)]

    fig, (ax_price, ax_rsi, ax_kdj, ax_score) = plt.subplots(
        4, 1, figsize=(16, 15), sharex=True, gridspec_kw={"height_ratios": [3, 1.1, 1.1, 1.1]}
    )

    for i, b in enumerate(bars):
        color = "#26a69a" if b["close"] >= b["open"] else "#ef5350"
        ax_price.vlines(i, b["low"], b["high"], color=color, linewidth=0.8)
        lo = min(b["open"], b["close"])
        height = abs(b["close"] - b["open"]) or 0.01
        ax_price.add_patch(plt.Rectangle((i - 0.3, lo), 0.6, height, color=color))
    ax_price.plot(x, upper_d, color="#2962ff", linewidth=1)
    ax_price.plot(x, basis_d, color="#2962ff", linewidth=1)
    ax_price.plot(x, lower_d, color="#2962ff", linewidth=1)
    ax_price.fill_between(x, lower_d, upper_d, color="#2962ff", alpha=0.06)

    entry_idx = next((i for i, d in enumerate(dates) if d.strftime("%Y-%m-%d") == entry_date), None)
    if entry_idx is not None:
        ax_price.scatter(entry_idx, entry_price, marker="^", color="green", s=170, zorder=5, edgecolors="black")
        ax_price.annotate(
            marker_label, (entry_idx, entry_price), xytext=(0, -18), textcoords="offset points",
            ha="center", fontsize=8, color="green", fontweight="bold",
        )

    ax_price.set_title(f"{symbol} — Keltner Channel ({KC_LENGTH}, {KC_MULTIPLIER})  |  {marker_label}: {entry_date} @ ${entry_price:.2f}")
    ax_price.set_ylabel("Price")

    valid = [(i, v) for i, v in enumerate(rsi_vals) if v is not None]
    ax_rsi.plot([i for i, _ in valid], [v for _, v in valid], color="#2962ff", linewidth=1)
    for i1, r1, i2, r2 in bull_pairs:
        ax_rsi.plot([i1, i2], [r1, r2], color="green", linewidth=1.5)
        ax_rsi.annotate("Bull", (i2, r2), color="white", backgroundcolor="green", fontsize=7,
                         xytext=(0, -12), textcoords="offset points", ha="center")
    for i1, r1, i2, r2 in bear_pairs:
        ax_rsi.plot([i1, i2], [r1, r2], color="red", linewidth=1.5)
        ax_rsi.annotate("Bear", (i2, r2), color="white", backgroundcolor="red", fontsize=7,
                         xytext=(0, 10), textcoords="offset points", ha="center")
    ax_rsi.set_ylabel(f"RSI({RSI_PERIOD})")
    ax_rsi.set_title("RSI Divergence")

    ax_kdj.plot(x, k_d, color="black", linewidth=1, label="K")
    ax_kdj.plot(x, d_d, color="orange", linewidth=1, label="D")
    ax_kdj.plot(x, j_d, color="purple", linewidth=1, label="J")
    ax_kdj.axhline(80, color="gray", linewidth=0.5, linestyle="--")
    ax_kdj.axhline(20, color="gray", linewidth=0.5, linestyle="--")
    ax_kdj.legend(loc="upper left", fontsize=7)
    ax_kdj.set_ylabel(f"KDJ({KDJ_PERIOD},{KDJ_SIGNAL})")

    ax_score.plot(x, score, color="black", linewidth=1.2, label="combined score")
    ax_score.axhline(buy_th, color="green", linewidth=1, linestyle="--", label="buy threshold")
    ax_score.axhline(sell_th, color="red", linewidth=1, linestyle="--", label="sell threshold")
    ax_score.fill_between(x, score, buy_th, where=[s >= buy_th for s in score], color="green", alpha=0.2)
    ax_score.fill_between(x, score, sell_th, where=[s <= sell_th for s in score], color="red", alpha=0.2)
    ax_score.legend(loc="upper left", fontsize=7)
    ax_score.set_ylabel("Score")
    ax_score.set_title("Combined buy/sell signal")

    tick_idx, tick_labels, last_month = [], [], None
    for i, d in enumerate(dates):
        if i < display_start:
            continue
        key = (d.year, d.month)
        if key != last_month:
            tick_idx.append(i)
            tick_labels.append(d.strftime("%Y" if d.month == 1 else "%b"))
            last_month = key
    ax_score.set_xticks(tick_idx)
    ax_score.set_xticklabels(tick_labels)
    ax_price.set_xlim(display_start - 1, n)

    if marker_label == "BUY (entry)":
        unrealized_pct = (closes[-1] / entry_price - 1) * 100
        fig.suptitle(f"{symbol} — current holding, {unrealized_pct:+.1f}% unrealized")
    else:
        fig.suptitle(f"{symbol} — {marker_label}")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/holding_charts"
    os.makedirs(out_dir, exist_ok=True)

    with open(PARAMS_PATH) as f:
        params = json.load(f)
    weights = (params["weights"]["kc"], params["weights"]["rsi_div"], params["weights"]["kdj"])
    buy_th, sell_th, persist_days = params["buy_threshold"], params["sell_threshold"], params["persist_days"]

    with open(HOLDINGS_PATH) as f:
        holdings = json.load(f)["positions"]

    for sym, pos in sorted(holdings.items()):
        out_path = os.path.join(out_dir, f"{sym.lower()}_holding.png")
        try:
            chart_one(sym, pos["entry_price"], pos["entry_date"], weights, buy_th, sell_th, persist_days, out_path)
            print(out_path)
        except Exception as exc:
            print(f"WARNING: failed to chart {sym}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
