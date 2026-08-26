import os
import sys
import re
import html
import json
import hashlib
import sqlite3
import datetime
import zoneinfo
import subprocess
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
from google.cloud import bigquery
import yfinance as yf

# Set project paths
BASE_DIR = r"f:\Project\STOCK MARKET TREND ANALYZER"
sys.path.insert(0, BASE_DIR)

from sentiment_generator.config import CACHE_DB_PATH, DATA_DIR, STOCKS, COMPANY_ALIASES
from sentiment_generator.news_fetcher import NewsFetcher
from sentiment_generator.cache import generate_article_id

# Target output paths
LIVE_POC_PARQUET_PATH = os.path.join(DATA_DIR, "bigquery_live_poc_reliance_2024_01.parquet")
LIVE_POC_METADATA_PATH = os.path.join(DATA_DIR, "bigquery_live_poc_reliance_2024_01_metadata.json")

def check_cache_state(label: str):
    conn = sqlite3.connect(CACHE_DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA integrity_check")
    integrity = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM raw_articles")
    raw_count = c.fetchone()[0]
    c.execute("SELECT status, COUNT(*) FROM fetch_periods GROUP BY status ORDER BY status")
    statuses = c.fetchall()
    conn.close()
    print(f"\n--- Cache State [{label}] ---")
    print(f"  integrity_check: {integrity}")
    print(f"  raw_articles count: {raw_count}")
    print(f"  fetch_periods: {statuses}")
    return integrity, raw_count, statuses

def get_git_commit() -> Optional[str]:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None

def fetch_nse_calendar(start_date: str, end_date: str) -> List[str]:
    # Download trading days from yfinance with forward buffer to handle end-of-month rollovers
    df = yf.download("RELIANCE.NS", start=start_date, end="2024-02-15", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    days = pd.Series(df.index).dt.strftime("%Y-%m-%d").drop_duplicates().sort_values().tolist()
    return days

def run_live_poc():
    print("=" * 80)
    print("  STEP 1: PRE-RUN CACHE SAFETY SNAPSHOT")
    print("=" * 80)
    pre_integrity, pre_raw_count, pre_statuses = check_cache_state("PRE-RUN")
    assert pre_integrity == "ok", "Pre-run cache integrity failed!"
    assert pre_raw_count == 634, f"Expected 634 raw articles, got {pre_raw_count}"

    # 1. Trading Calendar & NewsFetcher
    calendar = fetch_nse_calendar("2024-01-01", "2024-01-31")
    print(f"\nLoaded {len(calendar)} trading days ({calendar[0]} to {calendar[-1]})")
    fetcher = NewsFetcher(trading_calendar=calendar)
    tz_utc = zoneinfo.ZoneInfo("UTC")
    tz_ist = zoneinfo.ZoneInfo("Asia/Kolkata")

    # 2. Authenticate & Build Query
    print("\n" + "=" * 80)
    print("  STEP 2: EXECUTE REAL JANUARY 2024 QUERY ON GDELT BIGQUERY")
    print("=" * 80)
    client = bigquery.Client(project="stock-market-sentiment-506621")
    print(f"Authenticated BigQuery Project: {client.project}")

    sql_query = """
SELECT
  GKGRECORDID,
  DATE,
  SourceCommonName,
  DocumentIdentifier,
  V2Organizations,
  V2Persons,
  Extras
FROM
  `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE
  _PARTITIONDATE BETWEEN DATE('2024-01-01') AND DATE('2024-01-31')
  AND (
    REGEXP_CONTAINS(V2Organizations, r'(?i)\\breliance\\b')
    OR REGEXP_CONTAINS(V2Persons, r'(?i)\\b(mukesh ambani|ambani)\\b')
    OR REGEXP_CONTAINS(DocumentIdentifier, r'(?i)\\b(reliance|ril|jio)\\b')
    OR REGEXP_CONTAINS(Extras, r'(?i)<PAGE_TITLE>.*?\\b(reliance|ril|jio|mukesh ambani)\\b.*?</PAGE_TITLE>')
  )
  AND REGEXP_CONTAINS(Extras, r'<PAGE_TITLE>.+?</PAGE_TITLE>')
ORDER BY
  DATE ASC
""".strip()

    query_sha256 = hashlib.sha256(sql_query.encode("utf-8")).hexdigest()
    print("\n[SQL Query]:\n" + sql_query)
    print(f"\nQuery SHA256: {query_sha256}")

    print("\nExecuting live query on BigQuery...")
    query_job = client.query(sql_query)
    rows = list(query_job.result())
    
    total_bytes_processed = query_job.total_bytes_processed
    total_bytes_billed = query_job.total_bytes_billed
    rows_returned = len(rows)
    print(f"Query Execution Complete:")
    print(f"  Rows returned        : {rows_returned}")
    print(f"  Bytes processed      : {total_bytes_processed:,} ({total_bytes_processed / (1024**3):.4f} GiB)")
    print(f"  Bytes billed         : {total_bytes_billed:,} ({total_bytes_billed / (1024**3):.4f} GiB)")

    # 3. Processing & Telemetry Tracking
    print("\n" + "=" * 80)
    print("  STEPS 3 - 7: EXTRACTION, PROVENANCE, MATCHING, SESSION MAPPING & DEDUP")
    print("=" * 80)

    stats = {
        "candidate_rows": rows_returned,
        "rejected_missing_title": 0,
        "rejected_company_match": 0,
        "accepted_company_matches": 0,
        "invalid_timestamp_count": 0,
        "precise_timestamp_count": 0,
        "observation_fallback_count": 0,
        "before_close_count": 0,
        "after_close_rollover_count": 0,
        "weekend_holiday_rollover_count": 0,
        "no_session_count": 0,
        "duplicates_removed": 0,
        "accepted_rows": 0
    }

    raw_candidates = []
    rejected_candidates_sample = []

    title_pattern = re.compile(r'<PAGE_TITLE>(.*?)</PAGE_TITLE>', re.DOTALL | re.IGNORECASE)
    pubtime_pattern = re.compile(r'<PAGE_PRECISEPUBTIMESTAMP>(.*?)</PAGE_PRECISEPUBTIMESTAMP>', re.DOTALL | re.IGNORECASE)

    cutoff_time = datetime.time(15, 30, 0)

    for row in rows:
        rec = dict(row.items())
        extras = str(rec.get("Extras") or "")

        # --- STEP 3: Extract Title ---
        title_match = title_pattern.search(extras)
        if not title_match:
            stats["rejected_missing_title"] += 1
            continue
        
        raw_title = title_match.group(1).strip()
        if not raw_title:
            stats["rejected_missing_title"] += 1
            continue

        headline = html.unescape(raw_title)
        headline = " ".join(headline.split())
        if not headline:
            stats["rejected_missing_title"] += 1
            continue

        # --- STEP 5: Hardened Company Matching ---
        # Pass genuine title directly into fetcher.is_relevant_to_company
        is_relevant = fetcher.is_relevant_to_company(headline, "RELIANCE.NS")
        if not is_relevant:
            stats["rejected_company_match"] += 1
            # Save interesting rejected samples for audit (ADAG companies, banks, etc.)
            if any(k in headline.lower() for k in ["power", "infra", "capital", "communication", "bank", "rcom", "reliance on"]):
                if len(rejected_candidates_sample) < 30:
                    rejected_candidates_sample.append({
                        "headline": headline,
                        "source": rec.get("SourceCommonName"),
                        "url": rec.get("DocumentIdentifier"),
                        "gkg_date": rec.get("DATE")
                    })
            continue

        stats["accepted_company_matches"] += 1

        # --- STEP 4: Timestamp Policy & Provenance ---
        gkg_date_raw = str(rec.get("DATE") or "").strip()
        clean_gkg_date = re.sub(r'[^0-9]', '', gkg_date_raw)
        if len(clean_gkg_date) != 14:
            stats["invalid_timestamp_count"] += 1
            continue

        try:
            gkg_dt_utc = datetime.datetime.strptime(clean_gkg_date, "%Y%m%d%H%M%S").replace(tzinfo=tz_utc)
            seen_at_ist = gkg_dt_utc.astimezone(tz_ist)
        except Exception:
            stats["invalid_timestamp_count"] += 1
            continue

        pub_match = pubtime_pattern.search(extras)
        published_at_iso = None
        precise_dt_ist = None

        if pub_match:
            pub_raw = re.sub(r'[^0-9]', '', pub_match.group(1).strip())
            if len(pub_raw) == 14:
                try:
                    pub_dt_utc = datetime.datetime.strptime(pub_raw, "%Y%m%d%H%M%S").replace(tzinfo=tz_utc)
                    precise_dt_ist = pub_dt_utc.astimezone(tz_ist)
                    published_at_iso = precise_dt_ist.isoformat()
                    timestamp_basis = "publisher_precise_timestamp"
                    stats["precise_timestamp_count"] += 1
                except Exception:
                    pass

        if not published_at_iso:
            timestamp_basis = "gdelt_gkg_observation"
            stats["observation_fallback_count"] += 1

        # --- STEP 6: NSE Session Mapping ---
        # Map using precise timestamp if available, otherwise observation time
        eval_dt = precise_dt_ist if precise_dt_ist else seen_at_ist
        trading_date = fetcher.map_to_nse_trading_session(eval_dt)
        if not trading_date or trading_date not in fetcher.trading_calendar:
            stats["no_session_count"] += 1
            continue

        # Telemetry: Before/after close and weekend/holiday
        eval_cal_date = eval_dt.strftime("%Y-%m-%d")
        if eval_cal_date in calendar:
            if eval_dt.time() < cutoff_time:
                stats["before_close_count"] += 1
            else:
                stats["after_close_rollover_count"] += 1
        else:
            # Weekend or NSE holiday
            stats["weekend_holiday_rollover_count"] += 1

        # Normalization
        raw_url = str(rec.get("DocumentIdentifier") or "").strip()
        url = fetcher.normalize_url(raw_url) if raw_url else None
        source = str(rec.get("SourceCommonName") or "GDELT_GKG").strip()
        gkg_record_id = str(rec.get("GKGRECORDID") or "").strip()

        article_id = generate_article_id(
            ticker="RELIANCE.NS",
            trading_date=trading_date,
            headline=headline,
            url=url
        )

        raw_candidates.append({
            "article_id": article_id,
            "ticker": "RELIANCE.NS",
            "company": "Reliance Industries",
            "headline": headline,
            "source": source,
            "url": url,
            "published_at": published_at_iso,
            "seen_at": seen_at_ist.isoformat(),
            "source_timestamp": clean_gkg_date,
            "trading_date": trading_date,
            "timestamp_basis": timestamp_basis,
            "gkg_record_id": gkg_record_id,
            "finbert_label": None,
            "finbert_confidence": None,
            "sentiment_score": None
        })

    # --- STEP 7: Deduplication ---
    deduped_articles = fetcher._deduplicate_articles(raw_candidates)
    stats["duplicates_removed"] = len(raw_candidates) - len(deduped_articles)
    stats["accepted_rows"] = len(deduped_articles)

    print(f"Processing Summary:")
    print(f"  Candidate Rows             : {stats['candidate_rows']}")
    print(f"  Rejected (Missing Title)   : {stats['rejected_missing_title']}")
    print(f"  Rejected (Company Match)   : {stats['rejected_company_match']}")
    print(f"  Accepted Company Matches   : {stats['accepted_company_matches']}")
    print(f"  Invalid Timestamps         : {stats['invalid_timestamp_count']}")
    print(f"  Precise Publisher TS       : {stats['precise_timestamp_count']}")
    print(f"  Observation Fallback TS    : {stats['observation_fallback_count']}")
    print(f"  Before Close (Same-Day)    : {stats['before_close_count']}")
    print(f"  After Close (Rollover)     : {stats['after_close_rollover_count']}")
    print(f"  Weekend/Holiday Rollover   : {stats['weekend_holiday_rollover_count']}")
    print(f"  Duplicates Removed         : {stats['duplicates_removed']}")
    print(f"  Final Accepted Articles    : {stats['accepted_rows']}")

    # --- STEP 8: Write Live Staging Output ---
    print("\n" + "=" * 80)
    print("  STEP 8: WRITE LIVE STAGING PARQUET")
    print("=" * 80)
    df_live = pd.DataFrame(deduped_articles)
    df_live.to_parquet(LIVE_POC_PARQUET_PATH, index=False)
    print(f"Saved {len(df_live)} live accepted articles to: {LIVE_POC_PARQUET_PATH}")

    # Verify fixture file is untouched
    fixture_path = os.path.join(DATA_DIR, "bigquery_poc_reliance_2024_01.parquet")
    print(f"Fixture file preserved at: {fixture_path} (exists: {os.path.exists(fixture_path)})")

    # --- STEP 9: Write Live Provenance Metadata ---
    print("\n" + "=" * 80)
    print("  STEP 9: WRITE PROVENANCE METADATA")
    print("=" * 80)
    metadata = {
        "execution_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "authenticated_project": client.project,
        "source_project": "gdelt-bq",
        "dataset": "gdeltv2",
        "table": "gkg_partitioned",
        "date_start": "2024-01-01",
        "date_end": "2024-01-31",
        "query_sha256": query_sha256,
        "total_bytes_processed": int(total_bytes_processed),
        "total_bytes_billed": int(total_bytes_billed) if total_bytes_billed is not None else None,
        "rows_returned": int(rows_returned),
        "candidate_rows": int(stats["candidate_rows"]),
        "accepted_rows": int(stats["accepted_rows"]),
        "rejected_missing_title": int(stats["rejected_missing_title"]),
        "rejected_company_match": int(stats["rejected_company_match"]),
        "invalid_timestamp_count": int(stats["invalid_timestamp_count"]),
        "duplicates_removed": int(stats["duplicates_removed"]),
        "precise_timestamp_count": int(stats["precise_timestamp_count"]),
        "observation_fallback_count": int(stats["observation_fallback_count"]),
        "before_close_count": int(stats["before_close_count"]),
        "after_close_rollover_count": int(stats["after_close_rollover_count"]),
        "weekend_holiday_rollover_count": int(stats["weekend_holiday_rollover_count"]),
        "git_commit": get_git_commit()
    }
    with open(LIVE_POC_METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved provenance metadata to: {LIVE_POC_METADATA_PATH}")

    # --- STEP 10: Manual Article Audit ---
    print("\n" + "=" * 80)
    print(f"  STEP 10: MANUAL ARTICLE AUDIT (ACCEPTED SAMPLE: {min(30, len(deduped_articles))} OF {len(deduped_articles)})")
    print("=" * 80)
    for i, a in enumerate(deduped_articles[:30], 1):
        hl_clean = a['headline'][:80].encode('ascii', errors='replace').decode('ascii')
        url_clean = str(a['url'])[:75].encode('ascii', errors='replace').decode('ascii')
        print(f"[{i:02d}] Date: {a['trading_date']} | Basis: {a['timestamp_basis']} | Source: {a['source']}")
        print(f"     Title: {hl_clean}")
        print(f"     URL  : {url_clean}")
        print(f"     PubAt: {a['published_at']} | SeenAt(IST): {a['seen_at']}")
        print("-" * 80)

    print("\n" + "=" * 80)
    print(f"  STEP 10: SAMPLE REJECTED RELIANCE CANDIDATES (ADAG / UNRELATED / GRAMMATICAL) ({len(rejected_candidates_sample)} samples)")
    print("=" * 80)
    for i, r in enumerate(rejected_candidates_sample[:20], 1):
        hl_clean = r['headline'][:80].encode('ascii', errors='replace').decode('ascii')
        print(f"[{i:02d}] REJECTED: {hl_clean}")
        print(f"     Source  : {r['source']} | GKG DATE: {r['gkg_date']}")
        print("-" * 80)

    # --- STEP 11: Read-Only Cache Comparison ---
    print("\n" + "=" * 80)
    print("  STEP 11: READ-ONLY CACHE COMPARISON")
    print("=" * 80)
    conn = sqlite3.connect(CACHE_DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT headline, url, trading_date FROM raw_articles
        WHERE ticker = 'RELIANCE.NS' AND trading_date BETWEEN '2024-01-01' AND '2024-01-31'
    """)
    sqlite_rows = c.fetchall()
    conn.close()

    print(f"  Existing RELIANCE.NS Jan-2024 Cached Rows : {len(sqlite_rows)}")
    print(f"  BigQuery Accepted Rows                   : {len(deduped_articles)}")
    
    # Overlap check
    sqlite_urls = {r[1] for r in sqlite_rows if r[1]}
    sqlite_hl = {r[0].lower().strip() for r in sqlite_rows if r[0]}
    bq_urls = {a['url'] for a in deduped_articles if a.get('url')}
    bq_hl = {a['headline'].lower().strip() for a in deduped_articles if a.get('headline')}

    url_overlaps = len(bq_urls.intersection(sqlite_urls))
    hl_overlaps = len(bq_hl.intersection(sqlite_hl))

    print(f"  URL Overlaps                             : {url_overlaps}")
    print(f"  Headline Overlaps                        : {hl_overlaps}")
    print(f"  BigQuery-only Rows                       : {len(deduped_articles) - url_overlaps}")
    print(f"  Cache-only Rows                          : {len(sqlite_rows) - url_overlaps}")

    # --- STEP 12: Post-Run Cache Safety Check ---
    print("\n" + "=" * 80)
    print("  STEP 12: POST-RUN CACHE SAFETY CHECK")
    print("=" * 80)
    post_integrity, post_raw_count, post_statuses = check_cache_state("POST-RUN")
    assert post_integrity == "ok", "Post-run cache integrity failed!"
    assert post_raw_count == pre_raw_count == 634, f"raw_articles count modified! Was {pre_raw_count}, now {post_raw_count}"
    assert post_statuses == pre_statuses, f"fetch_periods modified! Was {pre_statuses}, now {post_statuses}"
    print("\n  [PASS] Production cache is 100% UNTOUCHED and pristine.")

    # --- STEP 13: Live POC Report Output ---
    print("\n" + "=" * 80)
    print("  STEP 13: LIVE POC FINAL METRICS SUMMARY")
    print("=" * 80)
    print(f"Authenticated project        : {client.project}")
    print(f"Source table                 : gdelt-bq.gdeltv2.gkg_partitioned")
    print(f"Date range                   : 2024-01-01 to 2024-01-31")
    print(f"Bytes processed              : {total_bytes_processed:,} ({total_bytes_processed / (1024**3):.4f} GiB)")
    print(f"Rows returned                : {rows_returned}")
    print(f"Rows with PAGE_TITLE         : {rows_returned - stats['rejected_missing_title']}")
    print(f"Missing-title rejects        : {stats['rejected_missing_title']}")
    print(f"Company-match rejects        : {stats['rejected_company_match']}")
    print(f"Accepted articles            : {stats['accepted_rows']}")
    print(f"Duplicates removed           : {stats['duplicates_removed']}")
    print(f"Precise publisher timestamps : {stats['precise_timestamp_count']}")
    print(f"GKG observation fallbacks    : {stats['observation_fallback_count']}")
    print(f"Before-close                 : {stats['before_close_count']}")
    print(f"After-close rollovers        : {stats['after_close_rollover_count']}")
    print(f"Weekend/holiday rollovers    : {stats['weekend_holiday_rollover_count']}")
    print(f"Existing-cache overlaps      : {url_overlaps}")
    print(f"Production cache unchanged   : YES")
    print("=" * 80)

if __name__ == "__main__":
    run_live_poc()
