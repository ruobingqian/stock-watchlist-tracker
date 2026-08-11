#!/usr/bin/env python3
"""Daily buy/sell signal check + holdings tracking for the KC/RSI-divergence/
KDJ dip-buy-and-hold strategy (oos_best_params.json - the params the user
chose to keep and use, see CLAUDE.md). Meant to run once per trading day at
market close (1pm PT / 4pm ET).

Maintains persistent state in holdings.json: which tickers are currently
"long" (with entry price/date) vs flat, and a running trade log. On first
run (no holdings.json yet), bootstraps state from what the strategy would
already be holding today per the 2025-present out-of-sample backtest -
after that, each run only advances by the newest bar.

IMPORTANT: holdings.json must be committed and pushed after every run - a
scheduled trigger may fire into a fresh session/container each time, and
local-only state would be lost.

Usage: python3 daily_signal.py
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from backtest import build_rsi_raw, compute_raw_components
from backtest_oos import compute_split_components, load_all_bars, test_comp
from indicators import drop_incomplete_last_bar, fetch_history
from stock_watchlist import WATCHLIST

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_PATH = os.path.join(HERE, "oos_best_params.json")
HOLDINGS_PATH = os.path.join(HERE, "holdings.json")


def load_holdings() -> dict:
    if os.path.exists(HOLDINGS_PATH):
        with open(HOLDINGS_PATH) as f:
            return json.load(f)
    return {"positions": {}, "trade_log": [], "last_run": None}


def save_holdings(state: dict) -> None:
    with open(HOLDINGS_PATH, "w") as f:
        json.dump(state, f, indent=2)


def determine_current_position(comp: dict, weights, buy_th: float, sell_th: float, persist_days: int, start_idx: int):
    """Same signal state machine as backtest.simulate(), but does NOT force a
    close at the end of the data - used only to bootstrap live state from
    history: are we currently long as of the last available bar, and since
    when/at what price?"""
    w_kc, w_rsi, w_kdj = weights
    closes, opens = comp["closes"], comp["opens"]
    kc_raw, kdj_raw = comp["kc_raw"], comp["kdj_raw"]
    rsi_raw = build_rsi_raw(comp["n"], comp["events"], persist_days)

    position = False
    entry_price = entry_bar = None
    pending = None
    for i in range(start_idx, comp["n"]):
        if pending == "buy" and not position:
            position, entry_price, entry_bar = True, opens[i], i
        elif pending == "sell" and position:
            position, entry_price, entry_bar = False, None, None
        pending = None

        score = w_kc * kc_raw[i] + w_rsi * rsi_raw[i] + w_kdj * kdj_raw[i]
        if not position and score >= buy_th:
            pending = "buy"
        elif position and score <= sell_th:
            pending = "sell"

    return {"entry_price": entry_price, "entry_bar": entry_bar} if position else None


def bootstrap_positions(weights, buy_th: float, sell_th: float, persist_days: int) -> dict:
    print("No holdings.json found - bootstrapping from the 2025-present backtest...", file=sys.stderr)
    raw = load_all_bars(WATCHLIST)  # reuses .data_cache_oos.json if present
    positions = {}
    for sym, (meta, bars) in raw.items():
        comp = compute_split_components(meta, bars)
        if comp is None:
            continue
        tc = test_comp(comp)
        pos = determine_current_position(comp, weights, buy_th, sell_th, persist_days, tc["start_idx"])
        if pos is None:
            continue
        entry_bars = drop_incomplete_last_bar(meta, bars)
        tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
        entry_date = datetime.fromtimestamp(entry_bars[pos["entry_bar"]]["time"], tz).strftime("%Y-%m-%d")
        positions[sym] = {"entry_price": pos["entry_price"], "entry_date": entry_date}
    return positions


def main():
    with open(PARAMS_PATH) as f:
        params = json.load(f)
    weights = (params["weights"]["kc"], params["weights"]["rsi_div"], params["weights"]["kdj"])
    buy_th, sell_th, persist_days = params["buy_threshold"], params["sell_threshold"], params["persist_days"]

    state = load_holdings()
    bootstrapped = False
    if not state["positions"] and state["last_run"] is None:
        state["positions"] = bootstrap_positions(weights, buy_th, sell_th, persist_days)
        bootstrapped = True

    positions = state["positions"]
    buys, sells, holding_updates, errors = [], [], [], []

    for symbol in WATCHLIST:
        try:
            meta, bars = fetch_history(symbol, rng="2y")
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            continue
        bars = drop_incomplete_last_bar(meta, bars)
        if len(bars) < 60:
            continue
        raw = compute_raw_components(bars)
        rsi_raw = build_rsi_raw(raw["n"], raw["events"], persist_days)
        i = raw["n"] - 1
        score = weights[0] * raw["kc_raw"][i] + weights[1] * rsi_raw[i] + weights[2] * raw["kdj_raw"][i]
        price = bars[-1]["close"]
        tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
        bar_date = datetime.fromtimestamp(bars[-1]["time"], tz).strftime("%Y-%m-%d")

        pos = positions.get(symbol)
        if pos is None:
            if not bootstrapped and score >= buy_th:
                positions[symbol] = {"entry_price": price, "entry_date": bar_date}
                buys.append({"symbol": symbol, "price": price, "score": round(score, 3), "date": bar_date})
        else:
            unrealized_pct = (price / pos["entry_price"] - 1) * 100
            if not bootstrapped and score <= sell_th:
                sells.append({
                    "symbol": symbol, "entry_price": pos["entry_price"], "entry_date": pos["entry_date"],
                    "exit_price": price, "exit_date": bar_date, "return_pct": round(unrealized_pct, 2),
                })
                state["trade_log"].append({
                    "symbol": symbol, "entry_price": pos["entry_price"], "entry_date": pos["entry_date"],
                    "exit_price": price, "exit_date": bar_date, "return_pct": round(unrealized_pct, 2),
                })
                del positions[symbol]
            else:
                holding_updates.append({
                    "symbol": symbol, "entry_price": pos["entry_price"], "entry_date": pos["entry_date"],
                    "current_price": price, "unrealized_pct": round(unrealized_pct, 2),
                })

    state["last_run"] = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")
    save_holdings(state)

    print(f"=== Daily signal check — {state['last_run']} ===\n")
    if bootstrapped:
        print(f"Bootstrapped {len(positions)} open position(s) from the 2025-present backtest (no buy/sell fired today - today only establishes the starting state):\n")
        for sym, p in sorted(positions.items()):
            print(f"  {sym:<6} entered {p['entry_date']} @ ${p['entry_price']:.2f}")
        print()
    else:
        print(f"BUY signals today ({len(buys)}):")
        for b in buys:
            print(f"  {b['symbol']:<6} entered @ ${b['price']:.2f}  score={b['score']}")
        print(f"\nSELL signals today ({len(sells)}):")
        for s in sells:
            print(f"  {s['symbol']:<6} exited @ ${s['exit_price']:.2f}  return={s['return_pct']:+.2f}%  (held since {s['entry_date']})")

    holding_updates.sort(key=lambda h: h["unrealized_pct"], reverse=True)
    print(f"\nCurrent holdings ({len(holding_updates)}):")
    for h in holding_updates:
        print(f"  {h['symbol']:<6} ${h['entry_price']:.2f} -> ${h['current_price']:.2f}  {h['unrealized_pct']:+.2f}%  (since {h['entry_date']})")

    if errors:
        print(f"\nErrors ({len(errors)}): " + "; ".join(errors[:10]))


if __name__ == "__main__":
    main()
