"""
Precision Audit Tool for GDELT BigQuery Accepted Articles.

Modes:
  1. Generate Stratified Audit Sample (120 records, seed 42):
     python -m sentiment_generator.audit_bigquery_precision --generate-sample

  2. Calculate & Report Precision Metrics (from manual human labels):
     python -m sentiment_generator.audit_bigquery_precision --report-metrics
"""

import os
import sys
import re
import argparse
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

from .config import DATA_DIR

LIVE_POC_PARQUET = os.path.join(DATA_DIR, "bigquery_live_poc_reliance_2024_01.parquet")
AUDIT_CSV_PATH = os.path.join(DATA_DIR, "reliance_2024_01_precision_audit.csv")

VALID_HUMAN_LABELS = {
    "DIRECTLY_RELEVANT",
    "INDIRECTLY_RELEVANT",
    "NOT_RELEVANT"
}

# Precompiled category regex patterns
RE_CAT_A = re.compile(r'\b(?:reliance\s+industries(?:\s+(?:ltd|limited))?|ril)\b', re.IGNORECASE)
RE_CAT_B = re.compile(r'\b(?:reliance\s+jio(?:\s+infocomm)?|jio\s+platforms|jio)\b', re.IGNORECASE)
RE_CAT_C = re.compile(r'\b(?:reliance\s+retail(?:\s+ventures)?)\b', re.IGNORECASE)
RE_CAT_F = re.compile(r'\b(?:reliance\s+(?:petroleum|oil|bp|new\s+energy|greens))\b', re.IGNORECASE)
RE_CAT_D = re.compile(r'\b(?:mukesh\s+ambani|ambani)\b', re.IGNORECASE)
RE_CAT_E = re.compile(r'\bReliance\b')
RE_RIL_CONTEXT = re.compile(
    r'\b(?:jamnagar|hazira|kg-d6|kg\s+d6|refinery|petrochemicals?|oil-to-chemicals|o2c|polyester|telecom|retail|jio|greens?|new\s+energy|giga\s+factory|agm|q[1-4]|annual\s+general\s+meeting)\b',
    re.IGNORECASE
)

TARGET_STRATA_COUNTS = {
    "A: Reliance Industries / RIL": 40,
    "D: Mukesh Ambani": 39,
    "B: Reliance Jio / Jio Platforms": 24,
    "E: Bare Reliance (RIL Context)": 10,
    "C: Reliance Retail": 6,
    "F: Other RIL Verticals / Energy": 1
}


def categorize_headline(headline: str) -> str:
    """
    Categorizes an accepted RELIANCE.NS headline into mutually exclusive primary strata
    using deterministic precedence.
    """
    hl = str(headline or "")
    if RE_CAT_A.search(hl):
        return "A: Reliance Industries / RIL"
    if RE_CAT_B.search(hl):
        return "B: Reliance Jio / Jio Platforms"
    if RE_CAT_C.search(hl):
        return "C: Reliance Retail"
    if RE_CAT_F.search(hl):
        return "F: Other RIL Verticals / Energy"
    if RE_CAT_D.search(hl):
        return "D: Mukesh Ambani"
    if RE_CAT_E.search(hl) and RE_RIL_CONTEXT.search(hl):
        return "E: Bare Reliance (RIL Context)"
    return "E: Bare Reliance (RIL Context)"


def generate_stratified_sample(
    parquet_path: str = LIVE_POC_PARQUET,
    sample_size: int = 120,
    random_seed: int = 42,
    existing_csv_path: Optional[str] = AUDIT_CSV_PATH
) -> pd.DataFrame:
    """
    Generates a deterministic 120-row stratified audit sample from the accepted live BigQuery dataset.
    Preserves existing human labels and notes if the file already exists.
    """
    if not os.path.exists(parquet_path):
        raise FileNotFoundError(f"Staging parquet file not found: {parquet_path}")

    df = pd.read_parquet(parquet_path)
    if len(df) == 0:
        raise ValueError("Parquet dataset is empty.")

    # Assign primary matching category
    df["match_category"] = df["headline"].apply(categorize_headline)

    # Deterministic stratified sampling by category and timestamp_basis
    sampled_dfs = []
    np.random.seed(random_seed)

    for cat, target_n in TARGET_STRATA_COUNTS.items():
        cat_df = df[df["match_category"] == cat].copy()
        if len(cat_df) <= target_n:
            sampled_dfs.append(cat_df)
            continue

        # Sub-stratify by timestamp_basis
        precise_df = cat_df[cat_df["timestamp_basis"] == "publisher_precise_timestamp"]
        obs_df = cat_df[cat_df["timestamp_basis"] == "gdelt_gkg_observation"]

        prop_precise = len(precise_df) / len(cat_df)
        n_precise = int(round(target_n * prop_precise))
        n_precise = max(0, min(n_precise, len(precise_df)))
        n_obs = target_n - n_precise
        if n_obs > len(obs_df):
            n_obs = len(obs_df)
            n_precise = target_n - n_obs

        s_precise = precise_df.sample(n=n_precise, random_state=random_seed) if n_precise > 0 else pd.DataFrame()
        s_obs = obs_df.sample(n=n_obs, random_state=random_seed) if n_obs > 0 else pd.DataFrame()

        sampled_dfs.append(pd.concat([s_precise, s_obs]))

    sample_df = pd.concat(sampled_dfs).sort_values("seen_at").reset_index(drop=True)
    sample_df["audit_id"] = [f"AUDIT_{i:03d}" for i in range(1, len(sample_df) + 1)]

    # Load existing labels if available to prevent overwriting manual work
    existing_labels: Dict[str, Tuple[str, str]] = {}
    if existing_csv_path and os.path.exists(existing_csv_path):
        try:
            prev_df = pd.read_csv(existing_csv_path, dtype=str).fillna("")
            for _, row in prev_df.iterrows():
                key = str(row.get("gkg_record_id") or row.get("audit_id") or "")
                if key:
                    existing_labels[key] = (
                        str(row.get("human_label", "")).strip(),
                        str(row.get("human_notes", "")).strip()
                    )
        except Exception:
            pass

    human_labels = []
    human_notes = []
    for _, row in sample_df.iterrows():
        gkg_id = str(row.get("gkg_record_id", ""))
        audit_id = str(row.get("audit_id", ""))
        if gkg_id in existing_labels:
            lbl, note = existing_labels[gkg_id]
        elif audit_id in existing_labels:
            lbl, note = existing_labels[audit_id]
        else:
            lbl, note = "", ""
        human_labels.append(lbl)
        human_notes.append(note)

    sample_df["human_label"] = human_labels
    sample_df["human_notes"] = human_notes

    # Keep required audit columns in canonical order
    cols = [
        "audit_id", "ticker", "trading_date", "headline", "source", "url",
        "match_category", "timestamp_basis", "published_at", "seen_at",
        "gkg_record_id", "human_label", "human_notes"
    ]
    return sample_df[cols]


def calculate_precision_metrics(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Computes strict and usable precision metrics from human labels.
    """
    # Clean labels
    df = df.copy()
    df["human_label"] = df["human_label"].fillna("").astype(str).str.strip()

    # Check for invalid labels
    invalid_rows = df[~df["human_label"].isin(VALID_HUMAN_LABELS) & (df["human_label"] != "")]
    if len(invalid_rows) > 0:
        invalid_list = invalid_rows[["audit_id", "human_label"]].to_dict(orient="records")
        raise ValueError(f"Found {len(invalid_rows)} invalid human labels: {invalid_list}")

    labeled_df = df[df["human_label"].isin(VALID_HUMAN_LABELS)].copy()
    total_samples = len(df)
    labeled_count = len(labeled_df)

    if labeled_count == 0:
        return {
            "total_samples": total_samples,
            "labeled_count": 0,
            "unlabeled_count": total_samples,
            "status": "AWAITING_HUMAN_LABELS",
            "strict_precision": None,
            "usable_precision": None,
            "not_relevant_rate": None,
            "breakdowns": {}
        }

    direct_count = int((labeled_df["human_label"] == "DIRECTLY_RELEVANT").sum())
    indirect_count = int((labeled_df["human_label"] == "INDIRECTLY_RELEVANT").sum())
    not_relevant_count = int((labeled_df["human_label"] == "NOT_RELEVANT").sum())

    strict_precision = direct_count / labeled_count
    usable_precision = (direct_count + indirect_count) / labeled_count
    not_relevant_rate = not_relevant_count / labeled_count

    # Breakdowns by category, timestamp basis, and top sources
    breakdowns = {
        "by_category": {},
        "by_timestamp_basis": {},
        "by_source": {}
    }

    for cat, group in labeled_df.groupby("match_category"):
        n = len(group)
        d = int((group["human_label"] == "DIRECTLY_RELEVANT").sum())
        ind = int((group["human_label"] == "INDIRECTLY_RELEVANT").sum())
        nr = int((group["human_label"] == "NOT_RELEVANT").sum())
        breakdowns["by_category"][cat] = {
            "count": n,
            "directly_relevant": d,
            "indirectly_relevant": ind,
            "not_relevant": nr,
            "usable_precision": (d + ind) / n if n > 0 else 0.0,
            "strict_precision": d / n if n > 0 else 0.0
        }

    for ts_basis, group in labeled_df.groupby("timestamp_basis"):
        n = len(group)
        d = int((group["human_label"] == "DIRECTLY_RELEVANT").sum())
        ind = int((group["human_label"] == "INDIRECTLY_RELEVANT").sum())
        nr = int((group["human_label"] == "NOT_RELEVANT").sum())
        breakdowns["by_timestamp_basis"][ts_basis] = {
            "count": n,
            "directly_relevant": d,
            "indirectly_relevant": ind,
            "not_relevant": nr,
            "usable_precision": (d + ind) / n if n > 0 else 0.0,
            "strict_precision": d / n if n > 0 else 0.0
        }

    # Sources with at least 3 labeled records
    for src, group in labeled_df.groupby("source"):
        if len(group) >= 3:
            n = len(group)
            d = int((group["human_label"] == "DIRECTLY_RELEVANT").sum())
            ind = int((group["human_label"] == "INDIRECTLY_RELEVANT").sum())
            nr = int((group["human_label"] == "NOT_RELEVANT").sum())
            breakdowns["by_source"][src] = {
                "count": n,
                "directly_relevant": d,
                "indirectly_relevant": ind,
                "not_relevant": nr,
                "usable_precision": (d + ind) / n if n > 0 else 0.0,
                "strict_precision": d / n if n > 0 else 0.0
            }

    # Decision Gate Determination
    if usable_precision >= 0.95:
        gate_verdict = "GREEN (Proceed to next ticker POC)"
    elif usable_precision >= 0.90:
        gate_verdict = "YELLOW (Targeted matcher refinement recommended; STOP for review)"
    else:
        gate_verdict = "RED (Stop scaling; matcher redesign required)"

    return {
        "total_samples": total_samples,
        "labeled_count": labeled_count,
        "unlabeled_count": total_samples - labeled_count,
        "status": "LABELED",
        "direct_count": direct_count,
        "indirect_count": indirect_count,
        "not_relevant_count": not_relevant_count,
        "strict_precision": strict_precision,
        "usable_precision": usable_precision,
        "not_relevant_rate": not_relevant_rate,
        "gate_verdict": gate_verdict,
        "breakdowns": breakdowns
    }


def main():
    parser = argparse.ArgumentParser(description="Audit BigQuery Precision for RELIANCE.NS")
    parser.add_argument("--generate-sample", action="store_true", help="Generate 120-row stratified audit CSV")
    parser.add_argument("--report-metrics", action="store_true", help="Compute precision metrics from labeled audit CSV")
    parser.add_argument("--output", type=str, default=AUDIT_CSV_PATH, help="Path for audit CSV")
    args = parser.parse_args()

    if args.generate_sample:
        print("=" * 70)
        print("  GENERATING STRATIFIED AUDIT SAMPLE (120 ROWS, SEED 42)")
        print("=" * 70)
        sample_df = generate_stratified_sample(existing_csv_path=args.output)
        sample_df.to_csv(args.output, index=False, encoding="utf-8")
        print(f"  Successfully wrote 120 audit records to: {args.output}")
        print("\n  Sample Strata Allocation:")
        for cat, cnt in sample_df["match_category"].value_counts().items():
            print(f"    - {cat:<45}: {cnt:2d}")
        print("\n  Timestamp Basis Allocation:")
        for ts, cnt in sample_df["timestamp_basis"].value_counts().items():
            print(f"    - {ts:<45}: {cnt:2d}")
        print("=" * 70)

    elif args.report_metrics:
        print("=" * 70)
        print("  PRECISION AUDIT METRICS REPORT")
        print("=" * 70)
        if not os.path.exists(args.output):
            print(f"Audit file not found: {args.output}")
            sys.exit(1)
        audit_df = pd.read_csv(args.output, dtype=str)
        metrics = calculate_precision_metrics(audit_df)

        if metrics["status"] == "AWAITING_HUMAN_LABELS":
            print(f"  Total Sample Rows : {metrics['total_samples']}")
            print(f"  Labeled Rows      : 0")
            print(f"  Status            : AWAITING MANUAL HUMAN LABELING")
            print(f"\n  Please manually populate 'human_label' in {args.output} with:")
            print(f"    - DIRECTLY_RELEVANT")
            print(f"    - INDIRECTLY_RELEVANT")
            print(f"    - NOT_RELEVANT")
        else:
            print(f"  Total Sample Rows   : {metrics['total_samples']}")
            print(f"  Labeled Rows        : {metrics['labeled_count']} ({metrics['labeled_count']/metrics['total_samples']*100:.1f}%)")
            print(f"  Directly Relevant   : {metrics['direct_count']} / {metrics['labeled_count']} ({metrics['strict_precision']*100:.2f}%)")
            print(f"  Indirectly Relevant : {metrics['indirect_count']} / {metrics['labeled_count']}")
            print(f"  Not Relevant        : {metrics['not_relevant_count']} / {metrics['labeled_count']} ({metrics['not_relevant_rate']*100:.2f}%)")
            print(f"  Strict Precision    : {metrics['strict_precision']*100:.2f}% ({metrics['direct_count']}/{metrics['labeled_count']})")
            print(f"  Usable Precision    : {metrics['usable_precision']*100:.2f}% ({metrics['direct_count']+metrics['indirect_count']}/{metrics['labeled_count']})")
            print(f"  Decision Gate       : {metrics['gate_verdict']}")

            print("\n  Category Breakdown:")
            for cat, c_data in metrics["breakdowns"]["by_category"].items():
                print(f"    {cat:<45}: Usable {c_data['usable_precision']*100:5.1f}% ({c_data['directly_relevant']+c_data['indirectly_relevant']}/{c_data['count']}) | Strict {c_data['strict_precision']*100:5.1f}%")

            print("\n  Timestamp Basis Breakdown:")
            for ts, t_data in metrics["breakdowns"]["by_timestamp_basis"].items():
                print(f"    {ts:<45}: Usable {t_data['usable_precision']*100:5.1f}% ({t_data['directly_relevant']+t_data['indirectly_relevant']}/{t_data['count']}) | Strict {t_data['strict_precision']*100:5.1f}%")
        print("=" * 70)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
