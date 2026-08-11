#!/usr/bin/env python3
"""Backtest and optimize a long-only strategy combining Keltner Channel,
RSI Divergence, and KDJ into one weighted mean-reversion score.

Each indicator contributes a signed component in roughly [-1.5, 1.5]:
- Keltner: high near/above the upper band is "extended" (negative/sell
  pressure), low near/below the lower band is "stretched" (positive/buy
  pressure) - %B of the band, inverted.
- RSI Divergence: +1 for a window of days after a confirmed bullish
  divergence, -1 after a confirmed bearish one, 0 otherwise.
- KDJ: K near 0 is oversold (positive/buy pressure), K near 100 is
  overbought (negative/sell pressure).

score = w_kc*kc + w_rsi*rsi_div + w_kdj*kdj
Buy (go long, all-in) when flat and score >= buy_threshold.
Sell (exit to cash) when long and score <= sell_threshold.
Trades execute at the next bar's open (the score is only known as of
today's confirmed close), so there's no lookahead. A divergence signal
itself isn't knowable until RSI_PIVOT_RIGHT bars after the pivot bar
(that's when the pivot is confirmed), which is accounted for before it
ever enters the score.

Usage:
    python3 backtest.py                 # optimize, print + save best params
    python3 backtest.py --iters 3000    # more random-search iterations
    python3 backtest.py --show          # show detail for saved best params
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from indicators import (
    RSI_PERIOD,
    RSI_PIVOT_RIGHT,
    drop_incomplete_last_bar,
    fetch_history,
    keltner_series,
    kdj_series,
    rsi,
    rsi_divergence_pairs,
)
from stock_watchlist import WATCHLIST

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, ".data_cache.json")
BEST_PARAMS_PATH = os.path.join(HERE, "best_params.json")

FETCH_RANGE = "2y"       # extra history so indicators are warmed up before the backtest window
BACKTEST_DAYS = 365      # calendar days actually traded/evaluated
TRANSACTION_COST = 0.001  # 10 bps per fill, one-way
MIN_TRADES_FOR_SCORE = 2  # tickers with fewer trades don't count toward the objective


# ---------------------------------------------------------------- data -----

def load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


def load_all_bars(tickers: list[str]) -> dict[str, tuple[dict, list[dict]]]:
    cache = load_cache()
    out = {}
    dirty = False
    for sym in tickers:
        key = f"{sym}:{FETCH_RANGE}"
        if key not in cache:
            try:
                meta, bars = fetch_history(sym, rng=FETCH_RANGE)
            except Exception as exc:
                print(f"WARNING: failed to fetch {sym}: {exc}", file=sys.stderr)
                continue
            cache[key] = {"meta": meta, "bars": bars}
            dirty = True
        entry = cache[key]
        out[sym] = (entry["meta"], entry["bars"])
    if dirty:
        save_cache(cache)
    return out


# ------------------------------------------------------- signal components -

def compute_raw_components(bars: list[dict]) -> dict:
    """Signal arrays over the FULL bar series (no train/test or rolling-window
    slicing) - shared by compute_components() (rolling 1y live window) and
    backtest_oos.py (fixed train/test date ranges)."""
    n = len(bars)
    closes = [b["close"] for b in bars]
    opens = [b["open"] for b in bars]
    upper, basis, lower = keltner_series(bars)
    rsi_vals = rsi(closes, RSI_PERIOD)
    k_series, _, _ = kdj_series(bars)
    div_pairs = rsi_divergence_pairs(rsi_vals, bars)

    kc_raw = [0.0] * n
    for i in range(n):
        if upper[i] is not None and lower[i] is not None and upper[i] != lower[i]:
            pct_b = (closes[i] - lower[i]) / (upper[i] - lower[i])
            kc_raw[i] = max(-1.5, min(1.5, 1 - 2 * pct_b))

    kdj_raw = [0.0] * n
    for i in range(n):
        if k_series[i] is not None:
            kdj_raw[i] = max(-1.5, min(1.5, (50 - k_series[i]) / 50))

    events = []
    for i1, i2, kind in div_pairs:
        confirm_bar = i2 + RSI_PIVOT_RIGHT
        if confirm_bar < n:
            events.append((confirm_bar, 1.0 if kind == "bullish" else -1.0))

    return {"closes": closes, "opens": opens, "kc_raw": kc_raw, "kdj_raw": kdj_raw, "events": events, "n": n}


def compute_components(meta: dict, bars: list[dict]) -> dict | None:
    bars = drop_incomplete_last_bar(meta, bars)
    n = len(bars)
    if n < 120:
        return None

    raw = compute_raw_components(bars)

    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    dates = [datetime.fromtimestamp(b["time"], tz) for b in bars]
    start_idx = next(i for i, d in enumerate(dates) if (dates[-1] - d).days <= BACKTEST_DAYS)

    return {**raw, "start_idx": start_idx}


def build_rsi_raw(n: int, events: list[tuple[int, float]], persist_days: int) -> list[float]:
    raw = [0.0] * n
    for confirm_bar, direction in events:
        for j in range(confirm_bar, min(n, confirm_bar + persist_days)):
            raw[j] = direction
    return raw


# ----------------------------------------------------------- simulation ----

def simulate(comp: dict, weights: tuple[float, float, float], buy_th: float, sell_th: float, persist_days: int):
    w_kc, w_rsi, w_kdj = weights
    closes, opens = comp["closes"], comp["opens"]
    kc_raw, kdj_raw = comp["kc_raw"], comp["kdj_raw"]
    rsi_raw = build_rsi_raw(comp["n"], comp["events"], persist_days)

    cash, shares = 1.0, 0.0
    position = False
    entry_price = None
    trades = []
    equity_curve = []
    buy_bars, sell_bars = [], []
    pending = None

    for i in range(comp["start_idx"], comp["n"]):
        if pending == "buy" and not position:
            shares = cash * (1 - TRANSACTION_COST) / opens[i]
            cash = 0.0
            position = True
            entry_price = opens[i]
            buy_bars.append(i)
        elif pending == "sell" and position:
            cash = shares * opens[i] * (1 - TRANSACTION_COST)
            trades.append(opens[i] / entry_price - 1)
            shares = 0.0
            position = False
            sell_bars.append(i)
        pending = None

        equity_curve.append(cash + shares * closes[i])

        score = w_kc * kc_raw[i] + w_rsi * rsi_raw[i] + w_kdj * kdj_raw[i]
        if not position and score >= buy_th:
            pending = "buy"
        elif position and score <= sell_th:
            pending = "sell"

    if position:
        final_cash = shares * closes[-1] * (1 - TRANSACTION_COST)
        trades.append(closes[-1] / entry_price - 1)
        equity_curve[-1] = final_cash
        sell_bars.append(comp["n"] - 1)

    return equity_curve, trades, buy_bars, sell_bars


def compute_metrics(equity_curve: list[float], trades: list[float]) -> dict:
    if len(equity_curve) < 2:
        return {"total_return": 0.0, "sharpe": 0.0, "win_rate": 0.0, "max_dd": 0.0, "num_trades": 0}

    rets = [equity_curve[i] / equity_curve[i - 1] - 1 for i in range(1, len(equity_curve))]
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / max(1, len(rets) - 1)
    std = math.sqrt(var)
    sharpe = (mean_r / std) * math.sqrt(252) if std > 0 else 0.0

    peak, max_dd = equity_curve[0], 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak)

    win_rate = (sum(1 for t in trades if t > 0) / len(trades)) if trades else 0.0
    return {
        "total_return": equity_curve[-1] / equity_curve[0] - 1,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "max_dd": max_dd,
        "num_trades": len(trades),
    }


def backtest_all(all_components: dict[str, dict], weights, buy_th, sell_th, persist_days) -> dict[str, dict]:
    results = {}
    for sym, comp in all_components.items():
        equity_curve, trades, _, _ = simulate(comp, weights, buy_th, sell_th, persist_days)
        results[sym] = compute_metrics(equity_curve, trades)
    return results


def objective(results: dict[str, dict], min_coverage_frac: float = 1 / 3) -> float:
    scored = [r["sharpe"] for r in results.values() if r["num_trades"] >= MIN_TRADES_FOR_SCORE]
    if len(scored) < max(3, round(len(results) * min_coverage_frac)):
        return -999.0
    scored.sort()
    mid = len(scored) // 2
    median = scored[mid] if len(scored) % 2 else (scored[mid - 1] + scored[mid]) / 2
    return median


# ------------------------------------------------------------- search ------

def random_search(all_components: dict[str, dict], iters: int, seed: int = 7, min_coverage_frac: float = 1 / 3):
    rng = random.Random(seed)
    best_score = -1e9
    best_params = None
    for _ in range(iters):
        weights = (rng.uniform(0, 2), rng.uniform(0, 2), rng.uniform(0, 2))
        buy_th = rng.uniform(0.2, 1.6)
        sell_th = rng.uniform(-1.6, -0.1)
        persist_days = rng.randint(1, 10)

        results = backtest_all(all_components, weights, buy_th, sell_th, persist_days)
        score = objective(results, min_coverage_frac)
        if score > best_score:
            best_score = score
            best_params = {
                "weights": {"kc": weights[0], "rsi_div": weights[1], "kdj": weights[2]},
                "buy_threshold": buy_th,
                "sell_threshold": sell_th,
                "persist_days": persist_days,
                "median_sharpe": best_score,
            }
    return best_params


def print_report(all_components: dict[str, dict], params: dict) -> dict[str, dict]:
    weights = (params["weights"]["kc"], params["weights"]["rsi_div"], params["weights"]["kdj"])
    results = {}
    all_trades = []
    for sym, comp in all_components.items():
        equity_curve, trades, _, _ = simulate(comp, weights, params["buy_threshold"], params["sell_threshold"], params["persist_days"])
        results[sym] = compute_metrics(equity_curve, trades)
        all_trades.extend(trades)

    print("Best parameters:")
    print(f"  weights: KC={weights[0]:.2f}  RSI_div={weights[1]:.2f}  KDJ={weights[2]:.2f}")
    print(f"  buy_threshold={params['buy_threshold']:.2f}  sell_threshold={params['sell_threshold']:.2f}")
    print(f"  divergence persistence: {params['persist_days']} days")
    print()
    print(f"{'Symbol':<8}{'Return':>10}{'Sharpe':>10}{'WinRate':>10}{'MaxDD':>10}{'Trades':>8}")
    traded = [(s, r) for s, r in results.items() if r["num_trades"] >= MIN_TRADES_FOR_SCORE]
    traded.sort(key=lambda x: x[1]["sharpe"], reverse=True)
    for sym, r in traded:
        print(
            f"{sym:<8}{r['total_return']*100:>9.1f}%{r['sharpe']:>10.2f}"
            f"{r['win_rate']*100:>9.1f}%{r['max_dd']*100:>9.1f}%{r['num_trades']:>8}"
        )

    untraded = [s for s, r in results.items() if r["num_trades"] < MIN_TRADES_FOR_SCORE]
    if untraded:
        print(f"\nNo/too few trades ({MIN_TRADES_FOR_SCORE}+ required): {', '.join(untraded)}")

    sharpes = [r["sharpe"] for _, r in traded]
    returns = [r["total_return"] for _, r in traded]
    win_rates = [r["win_rate"] for _, r in traded]
    if traded:
        print(
            f"\nAggregate over {len(traded)} traded tickers: "
            f"median Sharpe {sorted(sharpes)[len(sharpes)//2]:.2f}, "
            f"mean return {sum(returns)/len(returns)*100:.1f}%, "
            f"mean win rate {sum(win_rates)/len(win_rates)*100:.1f}%"
        )

    if all_trades:
        wins = [t for t in all_trades if t > 0]
        losses = [t for t in all_trades if t <= 0]
        gross_gain = sum(wins)
        gross_loss = -sum(losses)
        profit_factor = gross_gain / gross_loss if gross_loss > 0 else float("inf")
        print(
            f"\nPooled across all {len(all_trades)} individual trades (more statistically meaningful "
            f"than per-ticker stats given how few trades each ticker sees):\n"
            f"  win rate {len(wins)/len(all_trades)*100:.1f}%, "
            f"avg trade return {sum(all_trades)/len(all_trades)*100:.1f}%, "
            f"profit factor {profit_factor:.2f}"
        )
        print(
            "\nCAUTION: with ~1 year of data, most tickers only produce 2-3 trades. That's too few "
            "to trust in isolation - the per-ticker Sharpe/return numbers above are noisy and prone "
            "to overfitting on this particular year. The pooled trade stats and median Sharpe (robust "
            "to single-ticker outliers) are a sturdier read, but treat all of this as a starting point "
            "to monitor going forward, not a validated strategy."
        )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iters", type=int, default=2000)
    parser.add_argument("--show", action="store_true", help="Just report on the saved best_params.json")
    parser.add_argument("--min-coverage", type=float, default=1 / 3, help="Min fraction of tickers that must trade")
    parser.add_argument("--out", default=BEST_PARAMS_PATH, help="Where to save the best params JSON")
    args = parser.parse_args()

    raw = load_all_bars(WATCHLIST)
    all_components = {}
    for sym, (meta, bars) in raw.items():
        comp = compute_components(meta, bars)
        if comp is not None:
            all_components[sym] = comp
    print(f"Loaded {len(all_components)}/{len(WATCHLIST)} tickers with enough history\n")

    if args.show:
        if not os.path.exists(args.out):
            print(f"No saved params at {args.out} yet — run without --show first.")
            return
        with open(args.out) as f:
            params = json.load(f)
        print_report(all_components, params)
        return

    best_params = random_search(all_components, args.iters, min_coverage_frac=args.min_coverage)
    if best_params is None:
        print("No parameter combination produced enough trades to score. Try more iterations.")
        return

    with open(args.out, "w") as f:
        json.dump(best_params, f, indent=2)

    print_report(all_components, best_params)


if __name__ == "__main__":
    main()
