#!/usr/bin/env python3
"""Daily watchlist summary using Yahoo Finance's public chart API.

Adds three TradingView-style indicators, each computed from confirmed daily
bars only (the in-progress bar for today is dropped before market close, to
match TradingView's "wait for timeframe closes" behavior):

- Keltner Channel: EMA basis + Wilder ATR bands
- RSI Divergence: regular bullish/bearish divergence between RSI pivots and price pivots
- KDJ: stochastic K/D/J
"""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from indicators import (
    RSI_PERIOD,
    drop_incomplete_last_bar,
    fetch_history,
    keltner_channel,
    kdj,
    rsi,
    rsi_divergence,
)

WATCHLIST = [
    # original tech/growth watchlist
    "ISRG", "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NET", "NFLX",
    "SNOW", "TSLA", "ZM", "ABNB", "ADBE", "ADSK", "DOCU", "CRM",
    "DASH", "HOOD", "MDB", "NTNX", "OKTA", "RBLX", "PYPL", "RDDT",
    "SE", "SPOT", "XYZ", "TWLO", "TTWO",
    # software / internet
    "DDOG", "CRWD", "UBER", "PLTR", "APP", "SHOP", "CART", "CRCL",
    "U", "ORCL", "AUR", "COIN", "EBAY", "NOW", "PANW", "CPNG",
    "IONQ",
    # semiconductors / hardware
    "AMD", "INTC", "KEYS", "KLAC", "MU", "NVDA", "SMCI", "TSM",
    "AMAT", "ASML", "AVGO", "ARM", "QCOM", "MRVL", "ALAB", "SNDK",
    "LITE", "WDC", "COHR", "LRCX", "GLW", "SKM", "UMC", "CIEN",
    "STX", "CRDO", "DELL",
    # healthcare / pharma
    "ABBV", "JNJ", "LLY", "MRK", "MRNA", "NVO", "PFE", "GILD",
    "UNH", "AMGN", "GH", "RVMD", "INCY", "BNTX",
    # airlines / consumer / china
    "BA", "CCL", "DAL", "UAL", "WMT", "CMG", "COST", "DIS",
    "KO", "MCD", "T", "LULU", "ULTA", "NKE", "SBUX", "TGT",
    "RSG", "BABA", "BIDU", "NTES", "PDD", "XPEV", "NIO",
    # financials / energy & minerals
    "AXP", "JPM", "BAC", "FSLR", "FCX", "OXY", "RIO", "BHP",
    "TRGP", "VST", "DUK", "GEV", "AEP", "LYSDY", "BKR", "CAT",
    "B", "XOM", "NEM", "ALB", "BE", "CEG", "NEE", "SCCO",
    # space / defense / industrials (added 2026-08-11, market-cap filtered)
    "NBIS", "CRWV", "ASTS", "IREN", "LMT", "RKLB", "AMRZ", "CLS",
    "ATI", "AES",
    # Dow 30 + Nasdaq-100 additions (added 2026-08-11, market-cap filtered)
    "ADI", "ADP", "ALNY", "AZN", "BKNG", "CCEP", "CDNS", "CMCSA",
    "CPRT", "CSCO", "CSGP", "CSX", "CTAS", "CVX", "DXCM", "EA",
    "EXC", "FANG", "FAST", "FER", "FTNT", "GEHC", "GOOG", "GS",
    "HD", "HON", "IBM", "IDXX", "ILMN", "INTU", "KDP", "KHC",
    "LIN", "MAR", "MCHP", "MDLZ", "MELI", "MMM", "MNST", "MPWR",
    "MSTR", "NXPI", "ODFL", "ORLY", "PAYX", "PCAR", "PEP", "PG",
    "REGN", "ROP", "ROST", "SHW", "SNPS", "TEAM", "TER", "TMUS",
    "TRV", "TXN", "V", "VRTX", "WBD", "WDAY", "XEL",
]
ALERT_THRESHOLD_PCT = 2.0


def analyze(symbol: str) -> dict:
    meta, bars = fetch_history(symbol)
    confirmed = drop_incomplete_last_bar(meta, bars)

    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    regular_market_date = datetime.fromtimestamp(meta["regularMarketTime"], tz).date()
    last_bar_date = datetime.fromtimestamp(bars[-1]["time"], tz).date()
    if last_bar_date == regular_market_date and len(bars) >= 2:
        prev_close = bars[-2]["close"]
    else:
        prev_close = bars[-1]["close"]

    last_price = meta["regularMarketPrice"]
    change = last_price - prev_close
    pct_change = (change / prev_close) * 100 if prev_close else 0.0

    closes = [b["close"] for b in confirmed]
    rsi_vals = rsi(closes, RSI_PERIOD)

    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "change": change,
        "pct_change": pct_change,
        "day_low": meta.get("regularMarketDayLow"),
        "day_high": meta.get("regularMarketDayHigh"),
        "volume": meta.get("regularMarketVolume"),
        "keltner": keltner_channel(confirmed),
        "rsi": rsi_vals[-1],
        "divergence": rsi_divergence(rsi_vals, confirmed),
        "kdj": kdj(confirmed),
    }


def format_summary(rows: list[dict]) -> str:
    lines = [f"Stock Watchlist Summary — {datetime.now():%Y-%m-%d %H:%M}", ""]
    for q in rows:
        flag = " [ALERT]" if abs(q["pct_change"]) >= ALERT_THRESHOLD_PCT else ""
        lines.append(
            f"{q['symbol']:<6} ${q['last_price']:>9.2f}  "
            f"{q['change']:+7.2f} ({q['pct_change']:+.2f}%)  "
            f"range {q['day_low']:.2f}-{q['day_high']:.2f}  "
            f"vol {q['volume']:,}{flag}"
        )

        kc = q["keltner"]
        if kc:
            lines.append(
                f"       Keltner(26,2.7): upper {kc['upper']:.2f}  basis {kc['basis']:.2f}  lower {kc['lower']:.2f}"
            )

        rsi_val = q["rsi"]
        if rsi_val is not None:
            div = q["divergence"]
            div_str = "bullish" if div["bullish"] else "bearish" if div["bearish"] else "none"
            lines.append(f"       RSI(24): {rsi_val:.1f}  divergence: {div_str}")

        kdj_val = q["kdj"]
        if kdj_val:
            lines.append(
                f"       KDJ(9,3): K {kdj_val['k']:.1f}  D {kdj_val['d']:.1f}  J {kdj_val['j']:.1f}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def main():
    rows = []
    for symbol in WATCHLIST:
        try:
            rows.append(analyze(symbol))
        except Exception as exc:
            print(f"WARNING: failed to fetch {symbol}: {exc}", file=sys.stderr)
    print(format_summary(rows))


if __name__ == "__main__":
    main()
