import os
import sys
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
torch_lib_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'torch', 'lib')
if os.path.exists(torch_lib_dir):
    os.environ['PATH'] = torch_lib_dir + os.pathsep + os.environ.get('PATH', '')
    if hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(torch_lib_dir)
        except Exception:
            pass

import time
import argparse
import datetime
import calendar
import shutil
import logging
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import torch
from tqdm import tqdm
import pandas as pd
import numpy as np
import yfinance as yf

from .config import (
    START_DATE, END_DATE, STOCKS, COMPANY_ALIASES,
    DATA_DIR, CACHE_DIR, CACHE_DB_PATH,
    DAILY_SENTIMENT_CSV, SENTIMENT_METADATA_CSV, SENTIMENT_COVERAGE_CSV,
    ARTICLES_PARQUET, QUALITY_REPORT_TXT, QUALITY_REPORT_CSV,
    ROOT_DAILY_SENTIMENT_CSV, FETCH_WORKERS, LOW_COVERAGE_THRESHOLD
)
from .cache import (
    init_db, get_period_status, record_fetch_period,
    save_raw_articles, get_unscored_articles, update_article_sentiments,
    load_all_articles_df, export_articles_parquet
)
from .news_fetcher import NewsFetcher
from .finbert_sentiment import FinBertAnalyzer
from .aggregation import aggregate_daily_sentiment, generate_coverage_report
from .validation import validate_production_dataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sentiment_generator")


def fetch_nse_trading_calendar(start_date: str, end_date: str) -> List[str]:
    """Retrieves verified NSE trading days within the requested date window."""
    print(f"  [Calendar] Fetching official NSE trading calendar ({start_date} to {end_date})...")
    try:
        df = yf.download("RELIANCE.NS", start=start_date, end=end_date, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        trading_dates = (
            pd.Series(df.index)
            .dt.strftime("%Y-%m-%d")
            .drop_duplicates()
            .sort_values()
            .tolist()
        )
        if not trading_dates:
            raise RuntimeError(f"Yahoo Finance returned 0 trading days for RELIANCE.NS between {start_date} and {end_date}.")
        print(f"  [Calendar] {len(trading_dates)} verified NSE trading days loaded.")
        return trading_dates
    except Exception as e:
        raise RuntimeError(
            f"Failed to download verified NSE trading calendar from Yahoo Finance ({e}). "
            "Halting execution to prevent generating incorrect trading dates."
        )


def load_existing_macro_sentiment(root_csv: str) -> Dict[str, float]:
    """Loads existing genuine macro Sentiment_Score values from root daily_sentiment.csv."""
    macro_map = {}
    if os.path.exists(root_csv):
        try:
            df_old = pd.read_csv(root_csv)
            df_old.columns = [c.strip() for c in df_old.columns]
            if "Date" in df_old.columns and "Sentiment_Score" in df_old.columns:
                df_old["_d"] = pd.to_datetime(df_old["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
                for _, row in df_old.iterrows():
                    val = row["Sentiment_Score"]
                    if pd.notna(val) and pd.notna(row["_d"]):
                        macro_map[row["_d"]] = round(float(val), 4)
            print(f"  [Macro] Preserved {len(macro_map)} existing macro Sentiment_Score values (general market sentiment preserved, not regenerated).")
        except Exception as e:
            print(f"  [Macro] Notice: Could not load existing macro sentiment ({e}).")
    return macro_map


def generate_month_ranges(start_date: str, end_date: str) -> List[Tuple[datetime.datetime, datetime.datetime, str, str]]:
    """Generates timezone-aware UTC month boundaries (start_dt, end_dt, start_str, end_str) covering the range."""
    import zoneinfo
    tz_utc = zoneinfo.ZoneInfo("UTC")
    start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=tz_utc)
    end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=tz_utc)
    
    ranges = []
    curr = datetime.datetime(start_dt.year, start_dt.month, 1, tzinfo=tz_utc)
    while curr <= end_dt:
        last_day = calendar.monthrange(curr.year, curr.month)[1]
        m_start = max(curr, start_dt)
        m_end = min(
            datetime.datetime(curr.year, curr.month, last_day, 23, 59, 59, tzinfo=tz_utc),
            end_dt.replace(hour=23, minute=59, second=59, tzinfo=tz_utc)
        )
        
        start_s = m_start.strftime("%Y-%m-%d")
        end_s = m_end.strftime("%Y-%m-%d")
        ranges.append((m_start, m_end, start_s, end_s))
        
        # Advance to next month
        if curr.month == 12:
            curr = datetime.datetime(curr.year + 1, 1, 1, tzinfo=tz_utc)
        else:
            curr = datetime.datetime(curr.year, curr.month + 1, 1, tzinfo=tz_utc)
    return ranges


def process_ticker_news_fetch(
    ticker: str,
    month_ranges: List[Tuple[datetime.datetime, datetime.datetime, str, str]],
    fetcher: NewsFetcher,
    force_refetch: bool = False
) -> Dict[str, Any]:
    """Fetches all period windows for a single ticker."""
    new_articles_count = 0
    periods_fetched = 0
    periods_skipped = 0
    failed_periods = 0

    for m_start_dt, m_end_dt, p_start, p_end in month_ranges:
        if not force_refetch:
            status_rec = get_period_status(ticker, p_start, p_end)
            if status_rec and status_rec["status"] in ("success", "empty"):
                periods_skipped += 1
                continue

        try:
            articles = fetcher.fetch_gdelt_window(ticker, m_start_dt, m_end_dt)
            inserted = save_raw_articles(articles)
            new_articles_count += inserted
            periods_fetched += 1
            
            if len(articles) > 0:
                record_fetch_period(ticker, p_start, p_end, status="success", article_count=len(articles))
            else:
                record_fetch_period(ticker, p_start, p_end, status="empty", article_count=0)
                
            # Polite rate-limiting between period queries
            time.sleep(0.3)
        except Exception as e:
            failed_periods += 1
            record_fetch_period(ticker, p_start, p_end, status="failed", article_count=0, error_message=str(e))
            logger.warning(f"Fetch failed for {ticker} ({p_start} to {p_end}): {e}")

    return {
        "ticker": ticker,
        "new_articles": new_articles_count,
        "periods_fetched": periods_fetched,
        "periods_skipped": periods_skipped,
        "failed_periods": failed_periods
    }


def build_data_quality_report(
    df_articles: pd.DataFrame,
    df_sent: pd.DataFrame,
    df_meta: pd.DataFrame,
    ticker_keys: List[str],
    trading_dates: List[str],
    finbert_failures: int,
    fetcher_diagnostics: Optional[Dict[str, int]] = None
) -> str:
    """Constructs the comprehensive Data Quality Report."""
    lines = []
    sep = "=" * 70
    def add(t=""): lines.append(t)

    add(sep)
    add("  STOCK MARKET TREND ANALYZER  -  DATA QUALITY REPORT")
    add(f"  Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    add(sep)

    total_td = len(trading_dates)
    add(f"\n  Trading Sessions in Scope : {total_td}")
    add(f"  Calendar Range            : {trading_dates[0] if trading_dates else 'N/A'} to {trading_dates[-1] if trading_dates else 'N/A'}")

    total_articles = len(df_articles)
    add(f"  Total Raw Articles in DB  : {total_articles}")
    add(f"  FinBERT Inference Failures: {finbert_failures}")

    if fetcher_diagnostics:
        add("\n  --- GDELT Retrieval Diagnostics ---")
        add(f"    API Requests Made              : {fetcher_diagnostics.get('api_requests', 0)}")
        add(f"    Successful Requests (200)      : {fetcher_diagnostics.get('successful_requests', 0)}")
        add(f"    Rate Limit Hits (429)          : {fetcher_diagnostics.get('rate_limit_responses', 0)}")
        add(f"    Failed Requests                : {fetcher_diagnostics.get('failed_requests', 0)}")
        add(f"    Query Failures                 : {fetcher_diagnostics.get('query_failures', 0)}")
        add(f"    Total Articles Retrieved       : {fetcher_diagnostics.get('articles_retrieved', 0)}")
        add(f"    Rejected (Missing Title)       : {fetcher_diagnostics.get('articles_rejected_missing_title', 0)}")
        add(f"    Rejected (Company Match)       : {fetcher_diagnostics.get('articles_rejected_company_match', 0)}")
        add(f"    Rejected (Invalid Timestamp)   : {fetcher_diagnostics.get('articles_rejected_invalid_timestamp', 0)}")
        add(f"    Rejected (Low Precision TS)    : {fetcher_diagnostics.get('articles_rejected_low_precision_timestamp', 0)}")
        add(f"    Missing Published Timestamp    : {fetcher_diagnostics.get('articles_missing_published_at', 0)}")
        add(f"    Missing URL                    : {fetcher_diagnostics.get('articles_missing_url', 0)}")
        add(f"    Duplicates Removed             : {fetcher_diagnostics.get('duplicates_removed', 0)}")
        add(f"    Skipped (No Trading Day)       : {fetcher_diagnostics.get('articles_skipped_no_trading_session', 0)}")
        add(f"    Mapped to Trading Sessions     : {fetcher_diagnostics.get('articles_mapped_to_trading_sessions', 0)}")
        add(f"  --- Pagination Window Statistics ---")
        add(f"    Pagination Splits              : {fetcher_diagnostics.get('pagination_splits', 0)}")
        add(f"    Complete Windows               : {fetcher_diagnostics.get('complete_windows', 0)}")
        add(f"    Truncated Windows              : {fetcher_diagnostics.get('truncated_windows', 0)}")
        add(f"    Incomplete Windows             : {fetcher_diagnostics.get('incomplete_windows', 0)}")
        add(f"    Budget Exhausted Errors        : {fetcher_diagnostics.get('pagination_budget_exhausted', 0)}")

        truncated_ranges = fetcher_diagnostics.get("truncated_ranges", [])
        if truncated_ranges:
            add(f"\n  [!] WARNING: {len(truncated_ranges)} TRUNCATED WINDOW(S) DETECTED")
            add(f"      These windows hit the GDELT 250-record cap and could NOT be")
            add(f"      subdivided further. Coverage in these periods is INCOMPLETE.")
            add(f"      Do NOT treat these periods as fully fetched.")
            for (tk, ws, we) in truncated_ranges:
                add(f"        Ticker={tk}  Window=[{ws}, {we}]")
        elif fetcher_diagnostics.get('truncated_windows', 0) == 0 and fetcher_diagnostics.get('incomplete_windows', 0) == 0:
            add(f"    Coverage Completeness          : All fetched windows complete (no truncation)")


    if not df_articles.empty:
        add(f"  Earliest Article Seen     : {df_articles['seen_at'].dropna().min()[:10] if 'seen_at' in df_articles else 'N/A'}")
        add(f"  Latest Article Seen       : {df_articles['seen_at'].dropna().max()[:10] if 'seen_at' in df_articles else 'N/A'}")

        add("\n  --- Source Distribution ---")
        for src, count in df_articles["source"].value_counts().items():
            pct = round(100.0 * count / total_articles, 1)
            add(f"    {src:<25} {count:>7}  ({pct}%)")

        add("\n  --- Articles per Ticker ---")
        for t in ticker_keys:
            cnt = int((df_articles["ticker"] == t).sum())
            add(f"    {t:<20} {cnt:>7}")

        # Articles per year
        df_art_copy = df_articles.copy()
        if "trading_date" in df_art_copy.columns:
            df_art_copy["_year"] = pd.to_datetime(df_art_copy["trading_date"]).dt.year
            add("\n  --- Articles per Year ---")
            for yr, count in df_art_copy["_year"].value_counts().sort_index().items():
                add(f"    {yr}  {count:>7}")

    # Trading-day coverage per ticker
    add("\n  --- Trading-Day Coverage per Ticker ---")
    add(f"  {'Ticker':<20} {'Days w/ News':>13} {'Coverage%':>10} {'0.0 Sent%':>10} {'Flag'}")
    add("  " + "-" * 60)

    low_cov_tickers = []
    for t in ticker_keys:
        if t in df_sent.columns:
            col = df_sent[t]
            t_meta = df_meta[df_meta["Ticker"] == t] if not df_meta.empty else pd.DataFrame()
            days_news = int(t_meta["News_Available"].sum()) if not t_meta.empty else 0
            cov_pct = round(100.0 * days_news / total_td, 1) if total_td > 0 else 0.0
            zero_pct = round(100.0 * (col == 0.0).sum() / total_td, 1) if total_td > 0 else 0.0
            
            flag = ""
            if cov_pct < (LOW_COVERAGE_THRESHOLD * 100):
                flag = "[LOW COVERAGE]"
                low_cov_tickers.append(t)
            add(f"  {t:<20} {days_news:>13} {cov_pct:>9.1f}% {zero_pct:>9.1f}%  {flag}")

    # Yearly coverage reliability
    add("\n  --- Yearly Coverage Reliability ---")
    yearly_flagged = []
    if not df_meta.empty:
        df_meta_copy = df_meta.copy()
        df_meta_copy["_year"] = pd.to_datetime(df_meta_copy["Date"]).dt.year
        for yr, grp in df_meta_copy.groupby("_year"):
            n_td = grp["Date"].nunique()
            n_art = grp["Article_Count"].sum()
            slots = n_td * len(ticker_keys)
            cov_pct = round(100.0 * len(grp[grp["Article_Count"] > 0]) / slots, 1) if slots > 0 else 0.0
            flag = ""
            if cov_pct < (LOW_COVERAGE_THRESHOLD * 100):
                flag = " [!] LOW COVERAGE YEAR"
                yearly_flagged.append(yr)
            add(f"    {yr}  trading_days={n_td:>3}  articles={n_art:>5}  ticker_day_coverage={cov_pct:>5.1f}%{flag}")

    add(f"\n{sep}")
    add("  RELIABILITY & DATA INTEGRITY SUMMARY")
    add(sep)
    if yearly_flagged:
        add(f"  [!] Flagged Low-Coverage Years (treat as sparse news):")
        for y in yearly_flagged:
            add(f"      Year {y}")
    else:
        add("  All years meet or exceed the coverage threshold.")

    if low_cov_tickers:
        add(f"  [!] Tickers with <{LOW_COVERAGE_THRESHOLD * 100:.0f}% trading-day news coverage:")
        for t in low_cov_tickers:
            add(f"      {t}")
    add(sep)
    add()
    return "\n".join(lines)


def run_pipeline(
    tickers: Optional[List[str]] = None,
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    dry_run: bool = False,
    force_refetch: bool = False
):
    """Executes the full modular sentiment generation pipeline."""
    target_tickers = tickers if tickers else list(STOCKS.keys())

    print("\n" + "=" * 70)
    print("  STOCK MARKET TREND ANALYZER -- SENTIMENT GENERATOR")
    print(f"  Target Tickers : {len(target_tickers)} stocks")
    print(f"  Date Window    : {start_date} to {end_date}")
    print(f"  Execution Mode : {'DRY-RUN (Validation only)' if dry_run else 'PRODUCTION'}")
    print("=" * 70 + "\n")

    # Step 1: Initialize Cache & Load Calendars
    init_db()
    trading_dates = fetch_nse_trading_calendar(start_date, end_date)
    if not trading_dates:
        raise RuntimeError("No trading dates found in the specified range.")

    macro_map = load_existing_macro_sentiment(ROOT_DAILY_SENTIMENT_CSV)
    month_ranges = generate_month_ranges(start_date, end_date)
    fetcher = NewsFetcher(trading_calendar=trading_dates)

    print(f"  [Plan] {len(target_tickers)} tickers x {len(month_ranges)} period windows.")

    # Step 2: Phase 1 -- Parallel News Fetching
    print("\n-- Phase 1: Parallel News Fetching (GDELT + Contextual Filtering) --")
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {
            pool.submit(process_ticker_news_fetch, t, month_ranges, fetcher, force_refetch): t
            for t in target_tickers
        }
        for fut in tqdm(as_completed(futures), total=len(futures), desc="  Fetching news"):
            res = fut.result()
            print(f"    {res['ticker']:<18} new_articles={res['new_articles']:>4}  periods_fetched={res['periods_fetched']:>3}  skipped={res['periods_skipped']:>3}")

    # Step 3: Phase 2 -- Batch FinBERT Sentiment Inference
    print("\n-- Phase 2: Batch FinBERT Sentiment Inference (ProsusAI/finbert) --")
    analyzer = FinBertAnalyzer()
    
    unscored = get_unscored_articles()
    if unscored:
        print(f"  [FinBERT] Found {len(unscored)} unscored articles in cache. Running batch inference...")
        batch_size = 32
        for i in tqdm(range(0, len(unscored), batch_size), desc="  FinBERT Scoring"):
            batch = unscored[i:i + batch_size]
            texts = [item["headline"] for item in batch]
            scores = analyzer.analyze_batch(texts)
            
            scored_records = []
            for item, s in zip(batch, scores):
                if s is not None:
                    scored_records.append({
                        "article_id": item["article_id"],
                        "finbert_label": s["finbert_label"],
                        "finbert_confidence": s["finbert_confidence"],
                        "sentiment_score": s["sentiment_score"]
                    })
            update_article_sentiments(scored_records)
        print(f"  [FinBERT] Inference complete. Failures: {analyzer.inference_failures}")
    else:
        print("  [FinBERT] All cached articles already have FinBERT sentiment scores.")

    # Step 4: Phase 3 -- Export Parquet Audit Trail
    print("\n-- Phase 3: Exporting Immutable news_articles.parquet Audit Trail --")
    export_articles_parquet(ARTICLES_PARQUET)

    # Step 5: Phase 4 -- Daily Sentiment Aggregation
    print("\n-- Phase 4: Assembling Daily Sentiment & Metadata Datasets --")
    df_all_articles = load_all_articles_df()
    
    # Group cached articles by (ticker, trading_date)
    articles_by_ticker_date = defaultdict(lambda: defaultdict(list))
    if not df_all_articles.empty:
        for _, row in df_all_articles.iterrows():
            t = row["ticker"]
            td = row["trading_date"]
            articles_by_ticker_date[t][td].append({
                "headline": row["headline"],
                "finbert_label": row.get("finbert_label"),
                "finbert_confidence": row.get("finbert_confidence"),
                "sentiment_score": row.get("sentiment_score")
            })

    daily_rows = []
    metadata_rows = []

    for d in tqdm(trading_dates, desc="  Aggregating Days"):
        row = {
            "Date": d,
            "Sentiment_Score": macro_map.get(d, 0.0)
        }
        for t in target_tickers:
            arts = articles_by_ticker_date[t].get(d, [])
            agg = aggregate_daily_sentiment(arts)
            
            row[t] = agg["daily_sentiment"]
            metadata_rows.append({
                "Date": d,
                "Ticker": t,
                "Article_Count": agg["article_count"],
                "Positive_Count": agg["pos_count"],
                "Neutral_Count": agg["neu_count"],
                "Negative_Count": agg["neg_count"],
                "Avg_Sentiment": agg["avg_sentiment"],
                "Sentiment_Score": agg["daily_sentiment"],
                "News_Available": agg["news_available"]
            })
        daily_rows.append(row)

    # Save to staging CSVs
    expected_cols = ["Date", "Sentiment_Score"] + target_tickers
    df_sent = pd.DataFrame(daily_rows)[expected_cols]
    
    # Ensure strictly numeric types without silent NaN conversion
    for t in target_tickers:
        df_sent[t] = pd.to_numeric(df_sent[t], errors="raise").clip(-1.0, 1.0).round(4)
    df_sent["Sentiment_Score"] = pd.to_numeric(df_sent["Sentiment_Score"], errors="raise").round(4)
    df_sent.sort_values("Date", inplace=True)
    df_sent.drop_duplicates(subset=["Date"], inplace=True)
    df_sent.to_csv(DAILY_SENTIMENT_CSV, index=False)
    print(f"  [Output] Staging daily_sentiment.csv saved ({len(df_sent)} rows) -> {DAILY_SENTIMENT_CSV}")

    df_meta = pd.DataFrame(metadata_rows)
    df_meta.to_csv(SENTIMENT_METADATA_CSV, index=False)
    print(f"  [Output] sentiment_metadata.csv saved ({len(df_meta)} records) -> {SENTIMENT_METADATA_CSV}")

    df_ticker_cov, df_yearly_cov = generate_coverage_report(trading_dates, df_meta, target_tickers)
    df_ticker_cov.to_csv(SENTIMENT_COVERAGE_CSV, index=False)
    print(f"  [Output] sentiment_coverage.csv saved -> {SENTIMENT_COVERAGE_CSV}")

    # Step 6: Phase 5 -- Data Quality Report
    print("\n-- Phase 5: Generating Data Quality Report --")
    quality_report = build_data_quality_report(
        df_articles=df_all_articles,
        df_sent=df_sent,
        df_meta=df_meta,
        ticker_keys=target_tickers,
        trading_dates=trading_dates,
        finbert_failures=analyzer.inference_failures,
        fetcher_diagnostics=fetcher.get_diagnostics()
    )
    print(quality_report)
    with open(QUALITY_REPORT_TXT, "w", encoding="utf-8") as f:
        f.write(quality_report)

    df_ticker_cov.to_csv(QUALITY_REPORT_CSV, index=False)
    print(f"  [Report] Quality report text -> {QUALITY_REPORT_TXT}")
    print(f"  [Report] Quality report CSV  -> {QUALITY_REPORT_CSV}")

    # Step 7: Phase 6 -- Production Validation Gate
    print("\n-- Phase 6: Production Validation Gate --")
    is_valid, errors, warnings, metrics = validate_production_dataset(
        daily_csv_path=DAILY_SENTIMENT_CSV,
        metadata_csv_path=SENTIMENT_METADATA_CSV,
        parquet_audit_path=ARTICLES_PARQUET,
        ticker_cols=target_tickers,
        expected_trading_dates=trading_dates
    )

    if warnings:
        print("\n  [Validation Warnings]:")
        for w in warnings:
            print(f"    - {w}")

    if not is_valid:
        print("\n" + "!" * 70)
        print("  VALIDATION GATE FAILED -- PRODUCTION FILE WAS NOT MODIFIED!")
        for err in errors:
            print(f"    - {err}")
        print("!" * 70 + "\n")
        raise RuntimeError("Production dataset validation failed. See errors above.")

    print("\n  [Validation] All checks PASSED successfully.")

    if dry_run:
        print("\n  [DRY-RUN] Validation gate passed. Root production CSV was NOT modified.")
    else:
        # If all 20 stocks are being updated, copy to root
        if set(target_tickers) == set(STOCKS.keys()):
            shutil.copy(DAILY_SENTIMENT_CSV, ROOT_DAILY_SENTIMENT_CSV)
            print(f"  [DEPLOY] Validation passed. Copied staging dataset -> {ROOT_DAILY_SENTIMENT_CSV}")
        else:
            print(f"  [DEPLOY] Partial run ({len(target_tickers)} stocks). Staging files ready; full 20-stock run required for root replacement.")

    print("\nPipeline execution complete.\n")


def main():
    parser = argparse.ArgumentParser(description="Stock Market Trend Analyzer — FinBERT Sentiment Generator")
    parser.add_argument("--ticker", type=str, default=None, help="Specific ticker (e.g. RELIANCE.NS) or comma-separated list.")
    parser.add_argument("--start-date", type=str, default=START_DATE, help="Start date (YYYY-MM-DD).")
    parser.add_argument("--end-date", type=str, default=END_DATE, help="End date (YYYY-MM-DD).")
    parser.add_argument("--dry-run", action="store_true", help="Run without overwriting root production daily_sentiment.csv.")
    parser.add_argument("--force-refetch", action="store_true", help="Force re-fetching from GDELT ignoring cached period status.")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.ticker.split(",")] if args.ticker else None

    run_pipeline(
        tickers=tickers,
        start_date=args.start_date,
        end_date=args.end_date,
        dry_run=args.dry_run,
        force_refetch=args.force_refetch
    )


if __name__ == "__main__":
    main()
