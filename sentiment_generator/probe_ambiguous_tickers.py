"""
Live GDELT validation probe for ambiguous tickers:
ITC.NS, LT.NS, TITAN.NS, SBIN.NS, TCS.NS

Audits:
- Company relevance filtering on real headlines
- Rejection of acronym collisions (e.g. Lt Governor, international trade, Titan moon)
- Acceptance of legitimate company corporate/financial news
- 15:30 IST session mapping across multiple stocks
"""

import datetime
import sys
import zoneinfo
from collections import Counter


def _build_trading_calendar():
    try:
        import pandas_market_calendars as mcal
        cal = mcal.get_calendar("NSE")
        schedule = cal.schedule(start_date="2024-12-01", end_date="2025-02-28")
        return [str(d.date()) for d in schedule.index]
    except ImportError:
        pass

    holidays = {"2024-12-25", "2025-01-26", "2025-02-26"}
    days = []
    d = datetime.date(2024, 12, 1)
    end = datetime.date(2025, 2, 28)
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            days.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return days


def main():
    from sentiment_generator.news_fetcher import NewsFetcher

    tz_utc = zoneinfo.ZoneInfo("UTC")
    tz_ist = zoneinfo.ZoneInfo("Asia/Kolkata")

    calendar = _build_trading_calendar()
    print("=" * 70)
    print("  AMBIGUOUS TICKERS -- Live GDELT Probe")
    print("=" * 70)

    fetcher = NewsFetcher(trading_calendar=calendar)

    start_dt = datetime.datetime(2025, 1, 6, 0, 0, 0, tzinfo=tz_utc)
    end_dt = datetime.datetime(2025, 1, 10, 23, 59, 59, tzinfo=tz_utc)

    tickers = ["ITC.NS", "LT.NS", "TITAN.NS", "SBIN.NS", "TCS.NS"]

    for ticker in tickers:
        print("\n" + "=" * 70, flush=True)
        print(f"  TESTING TICKER: {ticker}", flush=True)
        print("=" * 70, flush=True)

        try:
            articles = fetcher.fetch_gdelt_window(ticker, start_dt, end_dt)
        except RuntimeError as exc:
            print(f"  *** ERROR fetching {ticker}: {exc}", flush=True)
            continue

        print(f"  Accepted articles: {len(articles)}", flush=True)

        td_counts = Counter(a.get("trading_date", "MISSING") for a in articles)
        print(f"  Trading session breakdown: {dict(sorted(td_counts.items()))}", flush=True)

        print("\n  Sample accepted headlines (up to 8):", flush=True)
        for i, a in enumerate(articles[:8], 1):
            hl = str(a.get("headline", "")).encode("ascii", errors="replace").decode("ascii")[:90]
            td = a.get("trading_date")
            ts = a.get("seen_at", "")[11:19]
            print(f"    [{i:02d}] ({td} {ts} IST) : {hl}", flush=True)

    print("\n" + "=" * 70, flush=True)
    print("  OVERALL MULTI-TICKER DIAGNOSTICS", flush=True)
    print("=" * 70, flush=True)
    diag = fetcher.get_diagnostics()
    for k, v in sorted(diag.items()):
        if k != "truncated_ranges":
            print(f"    {k:<45} : {v}", flush=True)

    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
