"""
Live GDELT validation probe -- RELIANCE.NS, January 2025.

Purpose
-------
Verify that the production NewsFetcher pipeline works correctly against
the real GDELT DOC API before committing to the full 20-stock historical run.

What is checked
---------------
1. GDELT actually responds and returns articles.
2. Timestamp parsing works on real GDELT seendate values.
3. Articles map to the correct NSE trading session (15:30 IST cutoff).
4. No unexpected 250-record truncation (pagination splits if needed).
5. Recursive pagination and shared budget behave correctly.
6. Deduplication removes genuine duplicates without collapsing distinct articles.
7. Company-relevance filtering keeps Reliance articles and rejects unrelated ones.
8. All diagnostic counters are plausible and internally consistent.

Run
---
From the project root (STOCK MARKET TREND ANALYZER/):

    python -m sentiment_generator.probe_live_gdelt

Expected output (approximate)
------------------------------
  - api_requests                  : a small positive integer
  - pagination_budget_exhausted   : 0
  - query_failures                : 0
  - truncated_windows             : 0 (or small, with listed ranges)
  - articles_retrieved            : positive
  - articles_mapped_to_trading_sessions : positive
  - articles_rejected_*           : reasonable proportions (not 100%)
  - Sample articles show genuine Reliance / RIL headlines
  - IST timestamps and trading_date assignments look correct
"""

import datetime
import sys
import zoneinfo
from collections import Counter


# ---------------------------------------------------------------------------
# Build a minimal NSE trading calendar for 2024-12 through 2025-02.
# We use pandas_market_calendars if available; otherwise fall back to a
# hardcoded weekday list that excludes known NSE holidays for that window.
# ---------------------------------------------------------------------------

def _build_trading_calendar() -> list:
    try:
        import pandas_market_calendars as mcal  # type: ignore
        cal = mcal.get_calendar("NSE")
        schedule = cal.schedule(start_date="2024-12-01", end_date="2025-02-28")
        return [str(d.date()) for d in schedule.index]
    except ImportError:
        pass

    # Fallback: business days minus known NSE holidays (approximate, sufficient for probe)
    holidays = {"2024-12-25", "2025-01-26", "2025-02-26"}
    days = []
    d = datetime.date(2024, 12, 1)
    end = datetime.date(2025, 2, 28)
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            days.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Main probe
# ---------------------------------------------------------------------------

def main() -> None:
    from sentiment_generator.news_fetcher import NewsFetcher

    tz_utc = zoneinfo.ZoneInfo("UTC")
    tz_ist = zoneinfo.ZoneInfo("Asia/Kolkata")

    calendar = _build_trading_calendar()
    print(f"\n{'='*70}")
    print("  RELIANCE.NS  --  Live GDELT Probe  (January 2025)")
    print(f"{'='*70}")
    print(f"  Trading calendar loaded : {len(calendar)} days "
          f"({calendar[0]} -> {calendar[-1]})")

    fetcher = NewsFetcher(trading_calendar=calendar)

    # One trading week: Mon 6-Jan-2025 UTC 00:00 through Fri 10-Jan-2025 UTC 23:59:59
    start_dt = datetime.datetime(2025, 1, 6, 0, 0, 0, tzinfo=tz_utc)
    end_dt   = datetime.datetime(2025, 1, 10, 23, 59, 59, tzinfo=tz_utc)

    ticker = "RELIANCE.NS"
    print(f"\n  Ticker  : {ticker}")
    print(f"  Window  : {start_dt.isoformat()}  ->  {end_dt.isoformat()}")
    print(f"  (IST)   : {start_dt.astimezone(tz_ist).isoformat()}"
          f"  ->  {end_dt.astimezone(tz_ist).isoformat()}")
    print()

    try:
        articles = fetcher.fetch_gdelt_window(ticker, start_dt, end_dt)
    except RuntimeError as exc:
        print(f"\n  *** RUNTIME ERROR during fetch ***\n  {exc}")
        sys.exit(1)

    # -- Diagnostics ----------------------------------------------------------
    diag = fetcher.get_diagnostics()
    stats = diag
    truncated = diag.get("truncated_ranges", [])

    print(f"\n{'-'*70}")
    print("  DIAGNOSTIC COUNTERS")
    print(f"{'-'*70}")

    GROUPS = {
        "HTTP": [
            "api_requests", "successful_requests", "failed_requests",
            "rate_limit_responses", "query_failures",
        ],
        "Articles": [
            "articles_retrieved", "articles_mapped_to_trading_sessions",
            "articles_rejected_company_match",
            "articles_rejected_invalid_timestamp",
            "articles_rejected_low_precision_timestamp",
            "articles_rejected_out_of_range",
            "articles_skipped_no_trading_session",
            "articles_missing_published_at",
            "duplicates_removed",
        ],
        "Pagination": [
            "pagination_splits", "complete_windows",
            "truncated_windows", "incomplete_windows",
            "pagination_budget_exhausted",
        ],
    }
    WARN_IF_NONZERO = {
        "failed_requests", "query_failures", "pagination_budget_exhausted",
    }

    for group_name, keys in GROUPS.items():
        print(f"\n  [{group_name}]")
        for k in keys:
            v = stats.get(k, "N/A")
            flag = ""
            if k in WARN_IF_NONZERO and isinstance(v, int) and v > 0:
                flag = "  !!  NON-ZERO"
            if k == "truncated_windows" and isinstance(v, int) and v > 0:
                flag = "  !!  CHECK RANGES BELOW"
            print(f"    {k:<48} {v}{flag}")

    # -- Internal consistency checks ------------------------------------------
    print(f"\n{'-'*70}")
    print("  INTERNAL CONSISTENCY CHECKS")
    print(f"{'-'*70}")

    retrieved   = stats.get("articles_retrieved", 0) or 0
    mapped      = stats.get("articles_mapped_to_trading_sessions", 0) or 0
    rej_company = stats.get("articles_rejected_company_match", 0) or 0
    rej_ts      = stats.get("articles_rejected_invalid_timestamp", 0) or 0
    rej_lp      = stats.get("articles_rejected_low_precision_timestamp", 0) or 0
    rej_range   = stats.get("articles_rejected_out_of_range", 0) or 0
    skipped_no  = stats.get("articles_skipped_no_trading_session", 0) or 0
    final_count = len(articles)

    accounted  = mapped + rej_company + rej_ts + rej_lp + rej_range + skipped_no
    balance_ok = retrieved >= accounted
    dedup_ok   = final_count <= mapped or mapped == 0
    budget_ok  = (stats.get("pagination_budget_exhausted") or 0) == 0
    http_ok    = (stats.get("failed_requests") or 0) == 0 and \
                 (stats.get("query_failures") or 0) == 0

    print(f"  retrieved ({retrieved}) >= accounted ({accounted}) : "
          f"{'OK' if balance_ok else 'X MISMATCH'}")
    print(f"  final_count ({final_count}) <= mapped ({mapped}) : "
          f"{'OK' if dedup_ok else 'X MISMATCH'}")
    print(f"  pagination_budget_exhausted == 0 : {'OK' if budget_ok else 'X BUDGET HIT'}")
    print(f"  no HTTP failures / query_failures : {'OK' if http_ok else 'X FAILURES'}")

    if retrieved > 0:
        rej_pct = 100.0 * rej_company / retrieved
        flag = "  !!  HIGH (possible over-filtering)" if rej_pct > 80 else ""
        print(f"  company-match rejection rate : {rej_pct:.1f}%{flag}")

    # -- Truncated ranges -----------------------------------------------------
    if truncated:
        print(f"\n{'-'*70}")
        print(f"  TRUNCATED WINDOWS ({len(truncated)}) -- 250-cap hit, unsplittable")
        print(f"{'-'*70}")
        for t_ticker, t_start, t_end in truncated:
            print(f"    {t_ticker}  {t_start} -> {t_end}")
    else:
        print(f"\n  Truncated ranges : none OK")

    # -- Trading-date distribution --------------------------------------------
    print(f"\n{'-'*70}")
    print(f"  TRADING-DATE DISTRIBUTION  ({final_count} articles total)")
    print(f"{'-'*70}")
    td_counts = Counter(a.get("trading_date", "MISSING") for a in articles)
    for td in sorted(td_counts):
        print(f"    {td}  :  {td_counts[td]:3d}")

    # -- 15:30 IST boundary spot-check ----------------------------------------
    print(f"\n{'-'*70}")
    print("  15:30 IST BOUNDARY SPOT-CHECK")
    print(f"{'-'*70}")

    before = [a for a in articles
              if a.get("seen_at") and "+05:30" in (a["seen_at"] or "")
              and a["seen_at"][11:19] < "15:30:00"]
    at_after = [a for a in articles
                if a.get("seen_at") and "+05:30" in (a["seen_at"] or "")
                and a["seen_at"][11:19] >= "15:30:00"]

    print(f"  Articles seen before 15:30 IST  : {len(before)}")
    print(f"  Articles seen at/after 15:30 IST: {len(at_after)}")

    for label, group in [
        ("BEFORE 15:30 (expected: same-day trading_date)", before),
        ("AT/AFTER 15:30 (expected: next trading_date)", at_after),
    ]:
        if group:
            a = group[0]
            hl_safe = str(a.get('headline','')).encode('ascii', errors='replace').decode('ascii')[:88]
            print(f"\n  Sample [{label}]")
            print(f"    seen_at      : {a.get('seen_at', 'N/A')}")
            print(f"    trading_date : {a.get('trading_date', 'N/A')}")
            print(f"    headline     : {hl_safe}")

    # -- Sample accepted articles ----------------------------------------------
    SAMPLE = 15
    print(f"\n{'-'*70}")
    print(f"  SAMPLE ACCEPTED ARTICLES (first {min(SAMPLE, final_count)} of {final_count})")
    print(f"{'-'*70}")
    for i, a in enumerate(articles[:SAMPLE], 1):
        hl_safe = str(a.get('headline', '')).encode('ascii', errors='replace').decode('ascii')[:95]
        url_safe = str(a.get('url', '')).encode('ascii', errors='replace').decode('ascii')[:88]
        print(f"\n  [{i:02d}]")
        print(f"    trading_date  : {a.get('trading_date', 'N/A')}")
        print(f"    seen_at (IST) : {a.get('seen_at', 'N/A')}")
        print(f"    source_ts     : {a.get('source_timestamp', 'N/A')}")
        print(f"    ts_basis      : {a.get('timestamp_basis', 'N/A')}")
        print(f"    headline      : {hl_safe}")
        print(f"    url           : {url_safe}")

    # -- Verdict ---------------------------------------------------------------
    all_ok = balance_ok and dedup_ok and budget_ok and http_ok and final_count > 0
    print(f"\n{'='*70}")
    if all_ok:
        verdict = "PASS  -- pipeline looks correct for this window"
    else:
        verdict = "!!  REVIEW -- one or more checks failed; see above"
    print(f"  VERDICT         : {verdict}")
    print(f"  Final articles  : {final_count}")
    print(f"  API requests    : {stats.get('api_requests', 0)}")
    print(f"  Budget remaining: {64 - (stats.get('api_requests', 0))}"
          " (of 64 default)")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
