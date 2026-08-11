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
META, ISRG, GOOGL, AAPL, SPY, QQQ — edit `WATCHLIST` in `stock_watchlist.py`
to add or remove tickers.

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
