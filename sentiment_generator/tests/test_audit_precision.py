"""
Unit tests for BigQuery Precision Audit Tooling (sentiment_generator/audit_bigquery_precision.py).
"""

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

import tempfile
import sqlite3
import unittest
import pandas as pd

from sentiment_generator.config import CACHE_DB_PATH, DATA_DIR
from sentiment_generator.audit_bigquery_precision import (
    generate_stratified_sample,
    calculate_precision_metrics,
    categorize_headline,
    LIVE_POC_PARQUET
)


class TestAuditPrecisionTooling(unittest.TestCase):

    def setUp(self):
        # Record initial cache DB state
        conn = sqlite3.connect(CACHE_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM raw_articles")
        self.initial_raw_count = c.fetchone()[0]
        c.execute("SELECT status, COUNT(*) FROM fetch_periods GROUP BY status ORDER BY status")
        self.initial_statuses = c.fetchall()
        conn.close()

    def tearDown(self):
        # Assert cache DB was never written
        conn = sqlite3.connect(CACHE_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM raw_articles")
        raw_count = c.fetchone()[0]
        c.execute("SELECT status, COUNT(*) FROM fetch_periods GROUP BY status ORDER BY status")
        statuses = c.fetchall()
        conn.close()

        self.assertEqual(raw_count, self.initial_raw_count, "Production SQLite raw_articles was mutated!")
        self.assertEqual(statuses, self.initial_statuses, "Production SQLite fetch_periods was mutated!")

    def test_deterministic_sampling_seed_42(self):
        """Seed 42 generates exactly 120 records with identical audit_ids."""
        sample1 = generate_stratified_sample(parquet_path=LIVE_POC_PARQUET, random_seed=42, existing_csv_path=None)
        sample2 = generate_stratified_sample(parquet_path=LIVE_POC_PARQUET, random_seed=42, existing_csv_path=None)

        self.assertEqual(len(sample1), 120)
        self.assertEqual(len(sample2), 120)
        self.assertTrue(sample1["gkg_record_id"].equals(sample2["gkg_record_id"]),
                        "Same input and seed must produce identical record order")

    def test_required_columns_and_blank_labels(self):
        """Generated sample contains all required audit columns with blank human_label and human_notes."""
        sample = generate_stratified_sample(parquet_path=LIVE_POC_PARQUET, random_seed=42, existing_csv_path=None)
        expected_cols = [
            "audit_id", "ticker", "trading_date", "headline", "source", "url",
            "match_category", "timestamp_basis", "published_at", "seen_at",
            "gkg_record_id", "human_label", "human_notes"
        ]
        self.assertEqual(list(sample.columns), expected_cols)
        self.assertTrue((sample["human_label"] == "").all(), "Initial human_label must be blank")
        self.assertTrue((sample["human_notes"] == "").all(), "Initial human_notes must be blank")

    def test_preserves_existing_human_labels(self):
        """Re-generating the sample preserves already entered manual labels."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "test_audit.csv")
            s1 = generate_stratified_sample(parquet_path=LIVE_POC_PARQUET, random_seed=42, existing_csv_path=None)
            
            # Simulate manual labeling of first 2 rows
            s1.loc[0, "human_label"] = "DIRECTLY_RELEVANT"
            s1.loc[0, "human_notes"] = "Test note 1"
            s1.loc[1, "human_label"] = "NOT_RELEVANT"
            s1.loc[1, "human_notes"] = "Test note 2"
            s1.to_csv(csv_path, index=False)

            # Re-generate with existing_csv_path
            s2 = generate_stratified_sample(parquet_path=LIVE_POC_PARQUET, random_seed=42, existing_csv_path=csv_path)
            self.assertEqual(s2.loc[0, "human_label"], "DIRECTLY_RELEVANT")
            self.assertEqual(s2.loc[0, "human_notes"], "Test note 1")
            self.assertEqual(s2.loc[1, "human_label"], "NOT_RELEVANT")
            self.assertEqual(s2.loc[1, "human_notes"], "Test note 2")
            self.assertEqual(s2.loc[2, "human_label"], "")

    def test_invalid_label_detection(self):
        """Invalid human labels raise ValueError with offending rows."""
        df = pd.DataFrame([{
            "audit_id": "AUDIT_001",
            "human_label": "SOME_UNKNOWN_LABEL",
            "match_category": "A: Reliance Industries / RIL",
            "timestamp_basis": "gdelt_gkg_observation",
            "source": "reuters.com"
        }])
        with self.assertRaises(ValueError) as ctx:
            calculate_precision_metrics(df)
        self.assertIn("SOME_UNKNOWN_LABEL", str(ctx.exception))

    def test_precision_metrics_mathematical_correctness(self):
        """Precision math strictly computes strict_precision, usable_precision, not_relevant_rate."""
        df = pd.DataFrame([
            {"audit_id": "01", "human_label": "DIRECTLY_RELEVANT", "match_category": "A", "timestamp_basis": "precise", "source": "s1"},
            {"audit_id": "02", "human_label": "DIRECTLY_RELEVANT", "match_category": "A", "timestamp_basis": "precise", "source": "s1"},
            {"audit_id": "03", "human_label": "INDIRECTLY_RELEVANT", "match_category": "A", "timestamp_basis": "obs", "source": "s1"},
            {"audit_id": "04", "human_label": "NOT_RELEVANT", "match_category": "B", "timestamp_basis": "obs", "source": "s2"},
            {"audit_id": "05", "human_label": "", "match_category": "B", "timestamp_basis": "obs", "source": "s2"}, # unlabelled
        ])
        metrics = calculate_precision_metrics(df)
        self.assertEqual(metrics["labeled_count"], 4)
        self.assertEqual(metrics["unlabeled_count"], 1)
        self.assertAlmostEqual(metrics["strict_precision"], 2 / 4) # 50%
        self.assertAlmostEqual(metrics["usable_precision"], 3 / 4) # 75%
        self.assertAlmostEqual(metrics["not_relevant_rate"], 1 / 4) # 25%
        self.assertEqual(metrics["gate_verdict"], "RED (Stop scaling; matcher redesign required)")

    def test_zero_labeled_rows_safe_handling(self):
        """Unlabeled dataset returns status='AWAITING_HUMAN_LABELS' without division by zero."""
        df = pd.DataFrame([
            {"audit_id": "01", "human_label": "", "match_category": "A", "timestamp_basis": "obs", "source": "s1"},
            {"audit_id": "02", "human_label": "", "match_category": "B", "timestamp_basis": "obs", "source": "s2"}
        ])
        metrics = calculate_precision_metrics(df)
        self.assertEqual(metrics["status"], "AWAITING_HUMAN_LABELS")
        self.assertIsNone(metrics["strict_precision"])
        self.assertIsNone(metrics["usable_precision"])

    def test_category_stratification_coverage(self):
        """Stratified sample allocates counts to all 6 target categories."""
        sample = generate_stratified_sample(parquet_path=LIVE_POC_PARQUET, random_seed=42, existing_csv_path=None)
        counts = sample["match_category"].value_counts().to_dict()

        self.assertEqual(counts.get("A: Reliance Industries / RIL"), 40)
        self.assertEqual(counts.get("D: Mukesh Ambani"), 39)
        self.assertEqual(counts.get("B: Reliance Jio / Jio Platforms"), 24)
        self.assertEqual(counts.get("E: Bare Reliance (RIL Context)"), 10)
        self.assertEqual(counts.get("C: Reliance Retail"), 6)
        self.assertEqual(counts.get("F: Other RIL Verticals / Energy"), 1)


if __name__ == "__main__":
    unittest.main()
