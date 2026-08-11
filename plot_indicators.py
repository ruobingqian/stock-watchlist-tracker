#!/usr/bin/env python3
"""Render a validation chart (candles + Keltner Channel, RSI Divergence, KDJ)
for one symbol, to visually check the indicator math against TradingView.

Usage: python3 plot_indicators.py [SYMBOL] [output_path.png]
"""

import math
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from indicators import (
    KC_ATR_LENGTH,
    KC_LENGTH,
    KC_MULTIPLIER,
    KDJ_PERIOD,
    KDJ_SIGNAL,
    RSI_PERIOD,
    drop_incomplete_last_bar,
    ema,
    fetch_history,
    kdj_series,
    rma,
    rsi,
    rsi_divergence_pairs,
    true_range,
)


def divergence_pairs(rsi_vals: list[float | None], bars: list[dict]):
    pairs = rsi_divergence_pairs(rsi_vals, bars)
    bull_pairs = [(i1, rsi_vals[i1], i2, rsi_vals[i2]) for i1, i2, kind in pairs if kind == "bullish"]
    bear_pairs = [(i1, rsi_vals[i1], i2, rsi_vals[i2]) for i1, i2, kind in pairs if kind == "bearish"]
    return bull_pairs, bear_pairs


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "ISRG"
    out_path = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/{symbol.lower()}_indicators.png"

    meta, bars = fetch_history(symbol, rng="2y", interval="1d")
    bars = drop_incomplete_last_bar(meta, bars)
    n = len(bars)
    closes = [b["close"] for b in bars]
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    dates = [datetime.fromtimestamp(b["time"], tz) for b in bars]
    x = list(range(n))

    basis_raw = ema(closes, KC_LENGTH)
    atr_raw = rma(true_range(bars), KC_ATR_LENGTH)
    nan = math.nan
    basis = [v if v is not None else nan for v in basis_raw]
    upper = [b + KC_MULTIPLIER * a if b is not None and a is not None else nan for b, a in zip(basis_raw, atr_raw)]
    lower = [b - KC_MULTIPLIER * a if b is not None and a is not None else nan for b, a in zip(basis_raw, atr_raw)]

    rsi_vals = rsi(closes, RSI_PERIOD)
    bull_pairs, bear_pairs = divergence_pairs(rsi_vals, bars)
    k_raw, d_raw, j_raw = kdj_series(bars)
    k_series = [v if v is not None else nan for v in k_raw]
    d_series = [v if v is not None else nan for v in d_raw]
    j_series = [v if v is not None else nan for v in j_raw]

    fig, (ax_price, ax_rsi, ax_kdj) = plt.subplots(
        3, 1, figsize=(16, 12), sharex=True, gridspec_kw={"height_ratios": [3, 1.3, 1.3]}
    )

    for i, b in enumerate(bars):
        color = "#26a69a" if b["close"] >= b["open"] else "#ef5350"
        ax_price.vlines(i, b["low"], b["high"], color=color, linewidth=0.8)
        lo = min(b["open"], b["close"])
        height = abs(b["close"] - b["open"]) or 0.01
        ax_price.add_patch(plt.Rectangle((i - 0.3, lo), 0.6, height, color=color))

    ax_price.plot(x, upper, color="#2962ff", linewidth=1)
    ax_price.plot(x, basis, color="#2962ff", linewidth=1)
    ax_price.plot(x, lower, color="#2962ff", linewidth=1)
    ax_price.fill_between(x, lower, upper, color="#2962ff", alpha=0.07)
    ax_price.set_title(f"{symbol} — Keltner Channel ({KC_LENGTH}, {KC_MULTIPLIER}, EMA basis, ATR{KC_ATR_LENGTH})")
    ax_price.set_ylabel("Price")

    valid = [(i, v) for i, v in enumerate(rsi_vals) if v is not None]
    ax_rsi.plot([i for i, _ in valid], [v for _, v in valid], color="#2962ff", linewidth=1)
    for i1, r1, i2, r2 in bull_pairs:
        ax_rsi.plot([i1, i2], [r1, r2], color="green", linewidth=1.5)
        ax_rsi.annotate(
            "Bull", (i2, r2), color="white", backgroundcolor="green", fontsize=7,
            xytext=(0, -12), textcoords="offset points", ha="center",
        )
    for i1, r1, i2, r2 in bear_pairs:
        ax_rsi.plot([i1, i2], [r1, r2], color="red", linewidth=1.5)
        ax_rsi.annotate(
            "Bear", (i2, r2), color="white", backgroundcolor="red", fontsize=7,
            xytext=(0, 10), textcoords="offset points", ha="center",
        )
    ax_rsi.set_ylabel(f"RSI({RSI_PERIOD})")
    ax_rsi.set_title("RSI Divergence")

    ax_kdj.plot(x, k_series, color="black", linewidth=1, label="K")
    ax_kdj.plot(x, d_series, color="orange", linewidth=1, label="D")
    ax_kdj.plot(x, j_series, color="purple", linewidth=1, label="J")
    ax_kdj.axhline(80, color="gray", linewidth=0.5, linestyle="--")
    ax_kdj.axhline(20, color="gray", linewidth=0.5, linestyle="--")
    ax_kdj.legend(loc="upper left", fontsize=7)
    ax_kdj.set_ylabel(f"KDJ({KDJ_PERIOD},{KDJ_SIGNAL})")

    display_start = next(
        i for i, d in enumerate(dates) if (dates[-1] - d).days <= 365
    )

    tick_idx, tick_labels, last_month = [], [], None
    for i, d in enumerate(dates):
        if i < display_start:
            continue
        key = (d.year, d.month)
        if key != last_month:
            tick_idx.append(i)
            tick_labels.append(d.strftime("%Y" if d.month == 1 else "%b"))
            last_month = key
    ax_kdj.set_xticks(tick_idx)
    ax_kdj.set_xticklabels(tick_labels)
    ax_price.set_xlim(display_start - 1, n)

    fig.suptitle(f"{symbol} daily — indicator validation chart")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(out_path)


if __name__ == "__main__":
    main()
