import pandas as pd
import numpy as np
import os
from typing import List, Dict, Any, Tuple, Optional

def validate_production_dataset(
    daily_csv_path: str,
    metadata_csv_path: str,
    parquet_audit_path: str,
    ticker_cols: List[str],
    expected_trading_dates: Optional[List[str]] = None
) -> Tuple[bool, List[str], List[str], Dict[str, Any]]:
    """
    Comprehensive validation gate for the generated sentiment dataset.
    
    Returns:
        (is_valid: bool, errors: List[str], warnings: List[str], metrics: Dict[str, Any])
    """
    errors: List[str] = []
    warnings: List[str] = []
    metrics: Dict[str, Any] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Daily Sentiment CSV Validation
    # ──────────────────────────────────────────────────────────────────────────
    if not os.path.exists(daily_csv_path):
        errors.append(f"Daily sentiment file not found at: {daily_csv_path}")
        return False, errors, warnings, metrics

    try:
        df_sent = pd.read_csv(daily_csv_path)
    except Exception as e:
        errors.append(f"Failed to read daily sentiment CSV: {e}")
        return False, errors, warnings, metrics

    # Schema check
    expected_cols = ["Date", "Sentiment_Score"] + ticker_cols
    if list(df_sent.columns) != expected_cols:
        missing_cols = set(expected_cols) - set(df_sent.columns)
        extra_cols = set(df_sent.columns) - set(expected_cols)
        if missing_cols:
            errors.append(f"Missing required columns in CSV: {missing_cols}")
        if extra_cols:
            errors.append(f"Unexpected extra columns in CSV: {extra_cols}")
        if set(df_sent.columns) == set(expected_cols) and list(df_sent.columns) != expected_cols:
            errors.append(f"Columns not in correct order. Expected: {expected_cols}")

    # Date column validation
    if "Date" in df_sent.columns:
        df_sent["_dt"] = pd.to_datetime(df_sent["Date"], errors="coerce")
        if df_sent["_dt"].isna().any():
            errors.append("Unparseable date strings found in 'Date' column.")

        # Chronological order
        if not df_sent["_dt"].is_monotonic_increasing:
            errors.append("Dates in daily sentiment CSV are not in strictly chronological order.")

        # Duplicate dates
        dups = df_sent["_dt"].duplicated().sum()
        if dups > 0:
            errors.append(f"Found {dups} duplicate dates in daily sentiment CSV.")

        # Weekend check (no Saturday/Sunday should exist in NSE trading days)
        weekend_mask = df_sent["_dt"].dt.weekday >= 5
        if weekend_mask.any():
            weekend_dates = df_sent.loc[weekend_mask, "Date"].tolist()
            errors.append(f"Found {len(weekend_dates)} weekend dates in CSV: {weekend_dates[:5]}")

        # Match against expected trading dates
        if expected_trading_dates:
            sent_dates_set = set(df_sent["Date"])
            exp_dates_set = set(expected_trading_dates)
            missing_dates = exp_dates_set - sent_dates_set
            extra_dates = sent_dates_set - exp_dates_set
            if missing_dates:
                errors.append(f"Missing {len(missing_dates)} expected NSE trading days in CSV.")
            if extra_dates:
                errors.append(f"Found {len(extra_dates)} dates not present in NSE trading calendar.")

    # Numeric & Range Checks
    check_cols = ["Sentiment_Score"] + [c for c in ticker_cols if c in df_sent.columns]
    nan_count = df_sent[check_cols].isna().sum().sum()
    if nan_count > 0:
        errors.append(f"Found {nan_count} NaN values across sentiment columns.")

    inf_count = np.isinf(df_sent[check_cols].to_numpy(dtype=float, na_value=0.0)).sum()
    if inf_count > 0:
        errors.append(f"Found {inf_count} infinite values in sentiment columns.")

    # Bounds check [-1.0, +1.0]
    out_of_bounds = ((df_sent[check_cols] < -1.0) | (df_sent[check_cols] > 1.0)).sum().sum()
    if out_of_bounds > 0:
        errors.append(f"Found {out_of_bounds} values outside the strict range [-1.0, +1.0].")

    # Repetition / Suspicious Run Detection
    repetition_stats = {}
    for col in ticker_cols:
        if col not in df_sent.columns:
            continue
        s = df_sent[col]
        nunique = int(s.nunique())
        
        # Calculate longest identical consecutive run
        runs = (s != s.shift()).cumsum()
        run_sizes = s.groupby(runs).size()
        max_run = int(run_sizes.max()) if not run_sizes.empty else 0
        
        # Details of longest run
        if max_run > 0:
            max_run_id = run_sizes.idxmax()
            run_indices = df_sent[runs == max_run_id].index
            run_start = df_sent.loc[run_indices[0], "Date"]
            run_end = df_sent.loc[run_indices[-1], "Date"]
            run_val = float(df_sent.loc[run_indices[0], col])
        else:
            run_start, run_end, run_val = "N/A", "N/A", 0.0

        repetition_stats[col] = {
            "unique_values": nunique,
            "longest_identical_run": max_run,
            "longest_run_start": run_start,
            "longest_run_end": run_end,
            "longest_run_value": run_val
        }

        # Flag suspicious non-zero repeated values
        if max_run > 30 and run_val != 0.0:
            warnings.append(
                f"[Anomaly] Ticker {col} has {max_run} consecutive identical non-zero sentiment values ({run_val}) "
                f"from {run_start} to {run_end}."
            )

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Metadata Invariant Validation
    # ──────────────────────────────────────────────────────────────────────────
    if not os.path.exists(metadata_csv_path):
        errors.append(f"Metadata file not found at: {metadata_csv_path}")
    else:
        try:
            df_meta = pd.read_csv(metadata_csv_path)
            meta_req_cols = [
                "Date", "Ticker", "Article_Count", "Positive_Count",
                "Neutral_Count", "Negative_Count", "Avg_Sentiment",
                "Sentiment_Score", "News_Available"
            ]
            for col in meta_req_cols:
                if col not in df_meta.columns:
                    errors.append(f"Missing column '{col}' in sentiment_metadata.csv")

            if "Article_Count" in df_meta.columns:
                # Negative counts check
                neg_counts = (df_meta[["Article_Count", "Positive_Count", "Neutral_Count", "Negative_Count"]] < 0).sum().sum()
                if neg_counts > 0:
                    errors.append("Negative article counts found in metadata.")

                # Invariant: Article_Count = Positive_Count + Neutral_Count + Negative_Count
                sum_breakdown = df_meta["Positive_Count"] + df_meta["Neutral_Count"] + df_meta["Negative_Count"]
                mismatch = (df_meta["Article_Count"] != sum_breakdown).sum()
                if mismatch > 0:
                    errors.append(f"Found {mismatch} rows where Article_Count != (Positive + Neutral + Negative) in metadata.")

                # Invariant: News_Available == (Article_Count > 0)
                expected_news_avail = df_meta["Article_Count"] > 0
                actual_news_avail = df_meta["News_Available"].astype(bool)
                news_mismatch = (expected_news_avail != actual_news_avail).sum()
                if news_mismatch > 0:
                    errors.append(f"Found {news_mismatch} rows where News_Available does not strictly equal (Article_Count > 0).")

        except Exception as e:
            errors.append(f"Failed to validate metadata CSV: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Parquet Audit Trail Validation
    # ──────────────────────────────────────────────────────────────────────────
    if not os.path.exists(parquet_audit_path):
        errors.append(f"Raw articles Parquet audit file not found at: {parquet_audit_path}")
    else:
        try:
            df_parquet = pd.read_parquet(parquet_audit_path)
            audit_cols = [
                "article_id", "ticker", "company", "headline", "source",
                "url", "published_at", "seen_at", "source_timestamp",
                "trading_date", "finbert_label", "finbert_confidence", "sentiment_score"
            ]
            for c in audit_cols:
                if c not in df_parquet.columns:
                    errors.append(f"Missing column '{c}' in news_articles.parquet audit trail.")

            if "article_id" in df_parquet.columns and not df_parquet.empty:
                dup_ids = df_parquet["article_id"].duplicated().sum()
                if dup_ids > 0:
                    errors.append(f"Found {dup_ids} duplicate article_id records in Parquet audit trail.")
                metrics["total_audit_articles"] = len(df_parquet)
            else:
                metrics["total_audit_articles"] = 0
        except Exception as e:
            errors.append(f"Failed to read Parquet audit trail: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # 4. SQLite Fetch Periods Completeness Validation
    # ──────────────────────────────────────────────────────────────────────────
    try:
        from .cache import get_unresolved_failed_periods
        failed_periods = get_unresolved_failed_periods()
        if failed_periods:
            warnings.append(
                f"Found {len(failed_periods)} unresolved 'failed' period window(s) in SQLite cache. "
                f"These periods must be retried before declaring full historical completeness."
            )
            metrics["unresolved_failed_periods"] = len(failed_periods)
        else:
            metrics["unresolved_failed_periods"] = 0
    except Exception as e:
        warnings.append(f"Could not inspect fetch_periods table: {e}")

    # Final summary metrics
    metrics["total_rows"] = len(df_sent)
    metrics["date_range"] = [df_sent["Date"].min() if "Date" in df_sent.columns else "N/A",
                             df_sent["Date"].max() if "Date" in df_sent.columns else "N/A"]
    metrics["repetition_stats"] = repetition_stats

    is_valid = (len(errors) == 0)
    return is_valid, errors, warnings, metrics

