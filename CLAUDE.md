# Stock Watchlist Tracker — Run Instructions

## What this repo does
Pulls near-real-time quote data and 1 year of daily bars (via Yahoo Finance's
public chart API, `query1.finance.yahoo.com/v8/finance/chart/{symbol}`) for a
fixed watchlist of tickers and prints a daily summary: last price, $ and %
change vs. previous close, day range, volume, and three TradingView-style
indicators (Keltner Channel, RSI Divergence, KDJ). Flags any ticker whose
price move since previous close exceeds the alert threshold — indicator
values are informational only for now, no alert logic is wired to them yet.

## How to run
```bash
pip install -q -r /path/to/stock-watchlist-tracker/requirements.txt
python3 /path/to/stock-watchlist-tracker/stock_watchlist.py
```

## Expected output
Console summary, one ticker block per line group, e.g.:
```
AAPL   $   227.15    +3.42 (+1.53%)  range 224.10-228.40  vol 54,231,900
       Keltner(26,2.7): upper 338.94  basis 316.05  lower 293.16
       RSI(24): 48.9  divergence: bearish
       KDJ(9,3): K 25.9  D 32.4  J 12.8
```
Tickers moving >= ALERT_THRESHOLD_PCT (default 2.0%) since previous close are
marked `[ALERT]`.

## Script behavior (do not change)
- `stock_watchlist.py` contains all fetch/format/indicator logic — run as-is
- Data source: Yahoo Finance's public chart API, called directly via `requests`
  with `range=1y&interval=1d` (NOT the `yfinance` package — its
  `curl_cffi`-based browser-TLS-fingerprint spoofing gets reset by this
  environment's TLS-intercepting proxy; plain `requests` works fine against
  the same API)
- The most recent daily bar is dropped from indicator calculations while the
  market is still open (before 16:00 in the ticker's exchange timezone), to
  mirror TradingView's "wait for timeframe closes" — indicators only use
  confirmed closes
- `previousClose`/`chartPreviousClose` are not reliably present on this
  endpoint for all range values, so previous close is derived from the bar
  history instead (the close immediately before the bar matching
  `meta.regularMarketTime`'s date)
- Watchlist: `WATCHLIST` constant at the top of the script
- Alert threshold: `ALERT_THRESHOLD_PCT` constant (default 2.0%, price-move
  based only — no alert logic wired to the indicators yet)

### Indicator parameters (mirror the TradingView settings they were configured from)
- **Keltner Channel**: length 26, multiplier 2.7, EMA basis (source close),
  ATR-style bands, ATR length 26 (`KC_LENGTH`, `KC_MULTIPLIER`, `KC_ATR_LENGTH`)
- **RSI Divergence**: RSI period 24 (source close), pivot lookback left/right
  5/5, pivot comparison range 5–60 bars; flags regular bullish divergence
  (RSI higher low + price lower low) and regular bearish divergence (RSI
  lower high + price higher high) at the most recently confirmed pivot pair
  (`RSI_PERIOD`, `RSI_PIVOT_LEFT`, `RSI_PIVOT_RIGHT`, `RSI_MIN_RANGE`, `RSI_MAX_RANGE`)
- **KDJ**: period 9, signal 3, seeded at K=D=50 (`KDJ_PERIOD`, `KDJ_SIGNAL`)

## After running
Report the summary directly in chat (no file output needed): price and %
change per ticker, and call out anything that crossed the alert threshold.

## Watchlist
176 tickers from the user's TradingView watchlist, grouped by sector as
commented in `stock_watchlist.py` (software/growth, semis, healthcare,
airlines/consumer/China, financials/energy) — edit `WATCHLIST` there to add
or remove tickers. SpaceX ("SPCX" in the user's TradingView UI) is
intentionally excluded — it's a private company with no public market data.

## Backtesting & strategy (backtest.py)
Combines the three indicators into one weighted long-only mean-reversion
score: `score = w_kc*kc + w_rsi_div*rsi_div + w_kdj*kdj`, each component
roughly in [-1.5, 1.5] (KC/KDJ: distance from band/50 as %B-style oversold
vs. overbought; RSI divergence: +1/-1 for `persist_days` days after a
confirmed pivot). Buys when flat and score >= buy_threshold; sells to cash
when long and score <= sell_threshold. Trades fill at the next bar's open
(no lookahead) and a divergence only enters the score `RSI_PIVOT_RIGHT`
days after its pivot bar, matching when it's actually confirmable.

```bash
python3 backtest.py                                    # optimize (median-Sharpe objective), save to best_params.json
python3 backtest.py --iters 10000 --min-coverage 0.6 --out best_params_active.json  # broader-coverage variant
python3 backtest.py --show --out best_params_selective.json   # report on a saved params file without re-optimizing
```

Two saved variants from the 34-ticker/2-year backtest as of this writing:
- `best_params_selective.json` (default `--min-coverage 1/3`): few, high-conviction
  trades (11/34 tickers traded, 46 pooled trades, 67% pooled win rate,
  median Sharpe 1.28). Requires strong agreement across all three indicators.
- `best_params_active.json` (`--min-coverage 0.6`): trades more of the
  watchlist (25/34 tickers, 75 pooled trades, 69% pooled win rate, median
  Sharpe 0.87) with lower thresholds and near-zero weight on Keltner Channel.

**Caveat**: this is optimized on ~1 year of data per ticker with as few as
2-3 trades per ticker — nowhere near enough to be statistically robust on a
per-ticker basis. Treat the results as a starting point to paper-trade or
monitor forward, not a validated strategy. The pooled trade-level stats and
median-Sharpe objective (both robust to single-ticker outliers) are a
sturdier read than any individual ticker's numbers.

Fetched bars are cached in `.data_cache.json` (gitignored) so repeated
optimizer runs don't re-hit Yahoo Finance; delete it to force a refresh.

## Out-of-sample validation (backtest_oos.py) — the strategy that's actually in use
`backtest_oos.py` fits weights/thresholds on 2021-01-01 to 2024-12-31 only
(across the full 176-ticker WATCHLIST, ~6 years of daily bars per ticker
fetched via exact `period1`/`period2` date bounds), then evaluates that same
fitted parameter set - no re-fitting - on 2025-01-01-to-present data. This is
the honest check of whether a strategy found by `backtest.py`'s optimizer
generalizes or was just fit to noise in one year.

```bash
python3 backtest_oos.py --iters 3000       # fetch + optimize on train, evaluate on both windows, save oos_best_params.json
python3 backtest_oos.py --show             # re-report oos_best_params.json without re-optimizing
```

**Result as of this writing, and important interpretation**: `oos_best_params.json`
holds up out-of-sample by the headline numbers (train: 63/167 tickers traded,
232 pooled trades, 67.7% win rate, profit factor 5.74; test: 156 pooled trades,
68.6% win rate, profit factor 5.45 - no collapse). But checking the actual
holding periods revealed **median holding period of 460 trading days on train
(~1.8 years) and 209 days on test (~10 months)**. This is NOT a short-term
swing-trading strategy despite being built from indicators normally used that
way - the optimizer, free to pick any threshold against a median-Sharpe
objective, converged on "buy once on a moderate KC/RSI-divergence/KDJ
confluence signal, then hold for months to years until a rare, deep score
collapse (sell_threshold=-1.54) triggers an exit." Given this watchlist is
full of names with huge multi-year runs (NVDA +898%, IONQ +759%, CRWD +432%
on train), a lot of the apparent edge is really "buy quality growth stocks
near a dip and hold through a strong bull market" - beta exposure from the
watchlist selection, not proven short-term predictive power from the three
indicators. **The user has explicitly chosen to keep and use it as a
dip-buying/long-hold strategy** (not a swing-trading one) - do not describe
it as short-term or re-optimize toward shorter holds unless the user asks.

Fetched bars for this script are cached separately in `.data_cache_oos.json`
(gitignored, ~40MB, 176 tickers × ~6 years) so repeat runs don't re-fetch.

## Daily signal check (daily_signal.py) — runs automatically at 1pm PT
Runs the live strategy (`oos_best_params.json` weights/thresholds) against
today's data for the full WATCHLIST and reports what changed:

```bash
python3 daily_signal.py
```

- **First run ever** (no `holdings.json`): bootstraps current positions from
  the 2025-present backtest - i.e. for every ticker, "is the strategy
  currently holding it as of today per the historical signal history, and
  since when/at what price." This establishes a realistic starting point
  instead of pretending the strategy starts from all-cash today. A
  bootstrap run reports the starting positions, not new buy/sell signals.
- **Every run after that**: fetches each ticker's latest 2 years of daily
  bars (a fresh fetch, not `.data_cache_oos.json`, since it needs today's
  bar), computes today's confirmed score, and compares it against the
  persisted position in `holdings.json`:
  - flat + score >= buy_threshold -> **BUY** (recorded at today's close;
    unlike backtest.py's next-bar-open fill, this is a live daily tool with
    no "tomorrow" to fill at, so today's close is the practical fill price)
  - long + score <= sell_threshold -> **SELL** (return computed vs. entry
    price, appended to `trade_log`)
  - long + no sell signal -> reported as an unrealized-return holding
  - flat + no buy signal -> not reported (nothing happened)
- **State persistence is critical**: `holdings.json` must be committed and
  pushed to the repo after every run. A scheduled trigger may fire into a
  fresh session/container each time - anything not in git is lost. Do not
  add `holdings.json` to `.gitignore`.
- Given how rarely this strategy sells (see the holding-period finding
  above), expect most daily runs to show few or zero new BUY/SELL signals
  and a large, slow-changing holdings table - that's the strategy working
  as intended, not a bug.
- Scheduled via a daily Routine at 1pm PT / 4pm ET (US market close), so the
  day's final bar is available. Report the day's BUY/SELL signals and the
  current holdings table (sorted by unrealized return) directly in chat.

## Validation chart
`plot_indicators.py [SYMBOL] [output.png]` renders a candlestick chart with
Keltner Channel overlay plus RSI Divergence and KDJ subpanels, styled after
the TradingView layout, for visually cross-checking the indicator math
against TradingView. It fetches 2 years of history (vs. 1 year for the daily
summary) so indicators are fully warmed up before the displayed 1-year
window — this only affects the chart's early history, not the latest values
`stock_watchlist.py` reports, since EMA/RMA-based smoothing converges within
a year of daily bars regardless of warm-up length.

## Known environment constraint
`query1.finance.yahoo.com` must be reachable from wherever this runs. In a
Claude Code on the web environment with a restricted network policy, this
host needs to be added to the environment's Custom network access allowlist
(with "Also include default list of common package managers" checked) —
see https://code.claude.com/docs/en/cloud-environments#network-access.
