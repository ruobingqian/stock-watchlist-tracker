# Stock Watchlist Tracker — Run Instructions

## What this repo does
Pulls near-real-time quote data (via Yahoo Finance's public chart API,
`query1.finance.yahoo.com/v8/finance/chart/{symbol}`) for a fixed watchlist
of tickers and prints a daily summary: last price, $ and % change vs.
previous close, day range, and volume. Flags any ticker whose move since
previous close exceeds the alert threshold.

## How to run
```bash
pip install -q -r /path/to/stock-watchlist-tracker/requirements.txt
python3 /path/to/stock-watchlist-tracker/stock_watchlist.py
```

## Expected output
Console summary, one line per ticker, e.g.:
```
AAPL   $   227.15    +3.42 (+1.53%)  range 224.10-228.40  vol 54,231,900
```
Tickers moving >= ALERT_THRESHOLD_PCT (default 2.0%) since previous close are
marked `[ALERT]`.

## Script behavior (do not change)
- `stock_watchlist.py` contains all fetch/format logic — run as-is
- Data source: Yahoo Finance's public chart API, called directly via `requests`
  (NOT the `yfinance` package — its `curl_cffi`-based browser-TLS-fingerprint
  spoofing gets reset by this environment's TLS-intercepting proxy; plain
  `requests` works fine against the same API)
- Watchlist: `WATCHLIST` constant at the top of the script
- Alert threshold: `ALERT_THRESHOLD_PCT` constant (default 2.0%)

## After running
Report the summary directly in chat (no file output needed): price and %
change per ticker, and call out anything that crossed the alert threshold.

## Watchlist
META, ISRG, GOOGL, AAPL, SPY, QQQ — edit `WATCHLIST` in `stock_watchlist.py`
to add or remove tickers.

## Known environment constraint
`query1.finance.yahoo.com` must be reachable from wherever this runs. In a
Claude Code on the web environment with a restricted network policy, this
host needs to be added to the environment's Custom network access allowlist
(with "Also include default list of common package managers" checked) —
see https://code.claude.com/docs/en/cloud-environments#network-access.
