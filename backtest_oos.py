#!/usr/bin/env python3
"""Out-of-sample validation: optimize the KC/RSI-divergence/KDJ strategy's
weights and thresholds on 2021-2024 data only, then evaluate that SAME
parameter set (no re-fitting) on 2025-present data, across the full
WATCHLIST universe. This is the honest test of whether the strategy found
in backtest.py generalizes or was just fit to noise in one year.

Usage:
    python3 backtest_oos.py --iters 3000
    python3 backtest_oos.py --show   # report using saved oos_best_params.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backtest import (
    MIN_TRADES_FOR_SCORE,
    TRANSACTION_COST,
    compute_metrics,
    compute_raw_components,
    objective,
    simulate,
)
from indicators import drop_incomplete_last_bar, fetch_history
from stock_watchlist import WATCHLIST

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, ".data_cache_oos.json")
OOS_PARAMS_PATH = os.path.join(HERE, "oos_best_params.json")

FETCH_START = datetime(2019, 11, 1, tzinfo=timezone.utc)  # buffer before train for warmup
TRAIN_START = datetime(2021, 1, 1, tzinfo=timezone.utc).date()
TEST_START = datetime(2025, 1, 1, tzinfo=timezone.utc).date()


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
    period1 = int(FETCH_START.timestamp())
    period2 = int(datetime.now(timezone.utc).timestamp())
    for i, sym in enumerate(tickers):
        key = sym
        if key not in cache:
            try:
                meta, bars = fetch_history(sym, period1=period1, period2=period2)
            except Exception as exc:
                print(f"WARNING: failed to fetch {sym}: {exc}", file=sys.stderr)
                continue
            cache[key] = {"meta": meta, "bars": bars}
            dirty = True
            if dirty and i % 20 == 0:
                save_cache(cache)  # checkpoint periodically - this fetch takes a while
        out[sym] = (cache[key]["meta"], cache[key]["bars"])
    if dirty:
        save_cache(cache)
    return out


def compute_split_components(meta: dict, bars: list[dict]) -> dict | None:
    bars = drop_incomplete_last_bar(meta, bars)
    if len(bars) < 400:
        return None
    raw = compute_raw_components(bars)

    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    dates = [datetime.fromtimestamp(b["time"], tz).date() for b in bars]

    try:
        train_start_idx = next(i for i, d in enumerate(dates) if d >= TRAIN_START)
        train_end_idx = next(i for i, d in enumerate(dates) if d >= TEST_START)
        test_start_idx = train_end_idx
    except StopIteration:
        return None

    n = raw["n"]
    if train_end_idx - train_start_idx < 200 or n - test_start_idx < 30:
        return None

    return {**raw, "train_start_idx": train_start_idx, "train_end_idx": train_end_idx, "test_start_idx": test_start_idx}


def train_comp(comp: dict) -> dict:
    return {**comp, "start_idx": comp["train_start_idx"], "n": comp["train_end_idx"]}


def test_comp(comp: dict) -> dict:
    return {**comp, "start_idx": comp["test_start_idx"]}  # n stays full length


def random_search_oos(all_components: dict[str, dict], iters: int, seed: int = 7, min_coverage_frac: float = 1 / 3):
    import random

    rng = random.Random(seed)
    train_comps = {sym: train_comp(c) for sym, c in all_components.items()}
    best_score = -1e9
    best_params = None
    for _ in range(iters):
        weights = (rng.uniform(0, 2), rng.uniform(0, 2), rng.uniform(0, 2))
        buy_th = rng.uniform(0.2, 1.6)
        sell_th = rng.uniform(-1.6, -0.1)
        persist_days = rng.randint(1, 10)

        results = {}
        for sym, comp in train_comps.items():
            equity_curve, trades, _, _ = simulate(comp, weights, buy_th, sell_th, persist_days)
            results[sym] = compute_metrics(equity_curve, trades)
        score = objective(results, min_coverage_frac)
        if score > best_score:
            best_score = score
            best_params = {
                "weights": {"kc": weights[0], "rsi_div": weights[1], "kdj": weights[2]},
                "buy_threshold": buy_th,
                "sell_threshold": sell_th,
                "persist_days": persist_days,
                "train_median_sharpe": best_score,
            }
    return best_params


def evaluate(all_components: dict[str, dict], params: dict, which: str) -> dict[str, dict]:
    weights = (params["weights"]["kc"], params["weights"]["rsi_div"], params["weights"]["kdj"])
    comps = {sym: (train_comp(c) if which == "train" else test_comp(c)) for sym, c in all_components.items()}
    results = {}
    all_trades = []
    for sym, comp in comps.items():
        equity_curve, trades, _, _ = simulate(comp, weights, params["buy_threshold"], params["sell_threshold"], params["persist_days"])
        results[sym] = compute_metrics(equity_curve, trades)
        all_trades.extend(trades)
    return results, all_trades


def print_period_report(label: str, results: dict[str, dict], all_trades: list[float]) -> None:
    traded = [(s, r) for s, r in results.items() if r["num_trades"] >= MIN_TRADES_FOR_SCORE]
    traded.sort(key=lambda x: x[1]["sharpe"], reverse=True)
    print(f"\n=== {label} ===")
    print(f"{'Symbol':<8}{'Return':>10}{'Sharpe':>10}{'WinRate':>10}{'MaxDD':>10}{'Trades':>8}")
    for sym, r in traded[:15]:
        print(
            f"{sym:<8}{r['total_return']*100:>9.1f}%{r['sharpe']:>10.2f}"
            f"{r['win_rate']*100:>9.1f}%{r['max_dd']*100:>9.1f}%{r['num_trades']:>8}"
        )
    if len(traded) > 15:
        print(f"... and {len(traded)-15} more traded tickers")

    sharpes = sorted(r["sharpe"] for _, r in traded)
    returns = [r["total_return"] for _, r in traded]
    print(
        f"\n{len(traded)}/{len(results)} tickers traded ({MIN_TRADES_FOR_SCORE}+ trades). "
        f"Median Sharpe: {sharpes[len(sharpes)//2]:.2f}  Mean return: {sum(returns)/len(returns)*100 if returns else 0:.1f}%"
    )
    if all_trades:
        wins = [t for t in all_trades if t > 0]
        losses = [t for t in all_trades if t <= 0]
        gross_gain, gross_loss = sum(wins), -sum(losses)
        pf = gross_gain / gross_loss if gross_loss > 0 else float("inf")
        print(
            f"Pooled {len(all_trades)} trades: win rate {len(wins)/len(all_trades)*100:.1f}%, "
            f"avg trade {sum(all_trades)/len(all_trades)*100:.1f}%, profit factor {pf:.2f}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iters", type=int, default=3000)
    parser.add_argument("--min-coverage", type=float, default=1 / 3)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    raw = load_all_bars(WATCHLIST)
    print(f"Fetched {len(raw)}/{len(WATCHLIST)} tickers in {time.time()-t0:.0f}s", file=sys.stderr)

    all_components = {}
    for sym, (meta, bars) in raw.items():
        comp = compute_split_components(meta, bars)
        if comp is not None:
            all_components[sym] = comp
    print(f"{len(all_components)} tickers have enough history for both train and test windows\n")

    if args.show:
        if not os.path.exists(OOS_PARAMS_PATH):
            print("No saved oos_best_params.json yet - run without --show first.")
            return
        with open(OOS_PARAMS_PATH) as f:
            params = json.load(f)
    else:
        t1 = time.time()
        params = random_search_oos(all_components, args.iters, min_coverage_frac=args.min_coverage)
        print(f"Optimized on train window in {time.time()-t1:.0f}s", file=sys.stderr)
        if params is None:
            print("No parameter combination produced enough trades on the train window.")
            return
        with open(OOS_PARAMS_PATH, "w") as f:
            json.dump(params, f, indent=2)

    w = params["weights"]
    print("Parameters (fit on 2021-01-01 to 2024-12-31 only):")
    print(f"  weights: KC={w['kc']:.2f}  RSI_div={w['rsi_div']:.2f}  KDJ={w['kdj']:.2f}")
    print(f"  buy_threshold={params['buy_threshold']:.2f}  sell_threshold={params['sell_threshold']:.2f}")
    print(f"  divergence persistence: {params['persist_days']} days")

    train_results, train_trades = evaluate(all_components, params, "train")
    print_period_report("TRAIN: 2021-01-01 to 2024-12-31 (in-sample, used to fit params)", train_results, train_trades)

    test_results, test_trades = evaluate(all_components, params, "test")
    print_period_report("TEST: 2025-01-01 to present (out-of-sample, NOT used to fit params)", test_results, test_trades)


if __name__ == "__main__":
    main()
