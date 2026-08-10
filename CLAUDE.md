# Stock Watchlist Tracker — Run Instructions

## What this repo does
Pulls near-real-time quote data (via Yahoo Finance / `yfinance`) for a fixed
watchlist of tickers and prints a daily summary: last price, $ and % change
vs. previous close, day range, and volume. Flags any ticker whose move since
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
- Data source: Yahoo Finance via the `yfinance` package (`Ticker.fast_info`)
- Watchlist: `WATCHLIST` constant at the top of the script
- Alert threshold: `ALERT_THRESHOLD_PCT` constant (default 2.0%)

## After running
Report the summary directly in chat (no file output needed): price and %
change per ticker, and call out anything that crossed the alert threshold.

## Watchlist
META, ISRG, GOOGL, AAPL, SPY, QQQ — edit `WATCHLIST` in `stock_watchlist.py`
to add or remove tickers.

## Known environment constraint
Yahoo Finance (`fc.yahoo.com`, `query1/query2.finance.yahoo.com`) must be
reachable from wherever this runs. In a Claude Code on the web environment
with a restricted network policy, these hosts need to be added to the
environment's egress allowlist before this script will work — see
https://code.claude.com/docs/en/claude-code-on-the-web for how environment
network policies are configured.
