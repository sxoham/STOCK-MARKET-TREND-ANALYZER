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

import unittest
import datetime
import zoneinfo
import sqlite3
import pandas as pd
from unittest.mock import patch, MagicMock

from sentiment_generator.bigquery_fetcher import BigQueryGKGExtractor
from sentiment_generator.config import CACHE_DB_PATH
from sentiment_generator.cache import load_all_articles_df, get_period_status


class TestBigQueryGKGExtractor(unittest.TestCase):

    def setUp(self):
        # NSE trading calendar Jan 2024 (2024-01-26 Republic Day holiday omitted)
        self.calendar = [
            "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
            "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",
            "2024-01-22", "2024-01-23", "2024-01-24", "2024-01-25",
            # 2024-01-26 is Republic Day (holiday)
            "2024-01-29", "2024-01-30", "2024-01-31",
        ]
        self.extractor = BigQueryGKGExtractor(trading_calendar=self.calendar)
        self.tz_ist = zoneinfo.ZoneInfo("Asia/Kolkata")
        self.tz_utc = zoneinfo.ZoneInfo("UTC")

    # 1. BigQuery row -> normalized article mapping
    def test_valid_gkg_row_mapping(self):
        row = {
            "GKGRECORDID": "20240115100000-1",
            "DATE": "20240115043000",  # 04:30 UTC -> 10:00 IST
            "SourceCommonName": "economictimes.indiatimes.com",
            "DocumentIdentifier": "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html?utm_source=twitter",
            "V2Organizations": "Reliance Industries;Jio Platforms",
            "V2Persons": "Mukesh Ambani",
            "page_title": "Reliance Industries Q3 Profit Rises 11% &amp; Beats Estimates",
            "precise_pub_time": None
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertIsNotNone(res)
        self.assertEqual(res["ticker"], "RELIANCE.NS")
        self.assertEqual(res["headline"], "Reliance Industries Q3 Profit Rises 11% & Beats Estimates")
        self.assertEqual(res["trading_date"], "2024-01-15")
        self.assertEqual(res["timestamp_basis"], "gdelt_gkg_observation")
        self.assertEqual(res["url"], "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html")
        self.assertTrue(res["article_id"])

    # 2. Missing/Empty headline rejection
    def test_missing_headline_rejected(self):
        row = {
            "GKGRECORDID": "20240115100000-2",
            "DATE": "20240115043000",
            "SourceCommonName": "reuters.com",
            "DocumentIdentifier": "https://reuters.com/article/1",
            "V2Organizations": "Reliance Industries",
            "page_title": None,
            "precise_pub_time": None
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertIsNone(res)
        self.assertEqual(self.extractor.stats["rejected_missing_title"], 1)

    # 3. Invalid/missing timestamps
    def test_invalid_timestamp_rejected(self):
        row = {
            "GKGRECORDID": "20240115100000-3",
            "DATE": "INVALID_TS",
            "SourceCommonName": "reuters.com",
            "DocumentIdentifier": "https://reuters.com/article/1",
            "V2Organizations": "Reliance Industries",
            "page_title": "Reliance Industries Q3 Results Announced",
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertIsNone(res)
        self.assertEqual(self.extractor.stats["rejected_invalid_timestamp"], 1)

    # 4. UTC -> IST conversion & 15:30 IST session cutoff
    def test_utc_to_ist_cutoff_before_1530(self):
        # 09:59 UTC = 15:29 IST -> Day D (2024-01-15)
        row = {
            "GKGRECORDID": "20240115100000-4",
            "DATE": "20240115095900",
            "SourceCommonName": "reuters.com",
            "DocumentIdentifier": "https://reuters.com/article/1",
            "V2Organizations": "Reliance Industries",
            "page_title": "Reliance Industries expands Jio network",
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertEqual(res["trading_date"], "2024-01-15")

    def test_utc_to_ist_cutoff_after_1530(self):
        # 10:01 UTC = 15:31 IST -> Next day (2024-01-16)
        row = {
            "GKGRECORDID": "20240115100000-5",
            "DATE": "20240115100100",
            "SourceCommonName": "reuters.com",
            "DocumentIdentifier": "https://reuters.com/article/1",
            "V2Organizations": "Reliance Industries",
            "page_title": "Reliance Industries after market results",
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertEqual(res["trading_date"], "2024-01-16")

    # 5. Weekend & Holiday Rollover
    def test_weekend_rollover(self):
        # Saturday 2024-01-13 06:00 UTC -> Monday 2024-01-15
        row = {
            "GKGRECORDID": "20240113100000-1",
            "DATE": "20240113060000",
            "SourceCommonName": "reuters.com",
            "DocumentIdentifier": "https://reuters.com/article/weekend",
            "V2Organizations": "Reliance Industries",
            "page_title": "Reliance Retail announces weekend acquisition",
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertEqual(res["trading_date"], "2024-01-15")

    def test_holiday_rollover(self):
        # Republic Day 2024-01-26 -> Monday 2024-01-29
        row = {
            "GKGRECORDID": "20240126100000-1",
            "DATE": "20240126060000",
            "SourceCommonName": "reuters.com",
            "DocumentIdentifier": "https://reuters.com/article/holiday",
            "V2Organizations": "Reliance Industries",
            "page_title": "Reliance Industries signs energy pact",
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertEqual(res["trading_date"], "2024-01-29")

    # 6. Company Matcher & Ambiguous Ticker Filtering
    def test_irrelevant_company_rejected(self):
        row = {
            "GKGRECORDID": "20240115100000-6",
            "DATE": "20240115043000",
            "SourceCommonName": "bbc.com",
            "DocumentIdentifier": "https://bbc.com/news/1",
            "V2Organizations": "Reliance Security Services",
            "page_title": "India aims to reduce reliance on imported oil",
        }
        res = self.extractor.parse_gkg_record(row, "RELIANCE.NS")
        self.assertIsNone(res)
        self.assertEqual(self.extractor.stats["rejected_company_match"], 1)

    # 7. URL & Canonical Deduplication
    def test_deduplication_via_url(self):
        records = [
            {
                "GKGRECORDID": "1",
                "DATE": "20240115043000",
                "SourceCommonName": "et.com",
                "DocumentIdentifier": "https://et.com/ril-news?utm_source=fb",
                "V2Organizations": "Reliance Industries",
                "page_title": "Reliance Industries Q3 profit rises",
            },
            {
                "GKGRECORDID": "2",
                "DATE": "20240115043000",
                "SourceCommonName": "et.com",
                "DocumentIdentifier": "https://et.com/ril-news?ref=feed",
                "V2Organizations": "Reliance Industries",
                "page_title": "Reliance Industries Q3 profit rises",
            }
        ]
        accepted = self.extractor.process_records(records, "RELIANCE.NS")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.extractor.stats["duplicates_removed"], 1)

    # 8. Timestamp Provenance Labeling
    def test_timestamp_provenance_labels(self):
        # Record with only GKG DATE
        rec1 = {
            "GKGRECORDID": "1",
            "DATE": "20240115043000",
            "DocumentIdentifier": "https://et.com/1",
            "V2Organizations": "Reliance Industries",
            "page_title": "Reliance Industries expansion plan",
            "precise_pub_time": None
        }
        res1 = self.extractor.parse_gkg_record(rec1, "RELIANCE.NS")
        self.assertEqual(res1["timestamp_basis"], "gdelt_gkg_observation")
        self.assertIsNone(res1["published_at"])

        # Record with precise pub time
        rec2 = {
            "GKGRECORDID": "2",
            "DATE": "20240115043000",
            "DocumentIdentifier": "https://et.com/2",
            "V2Organizations": "Reliance Industries",
            "page_title": "Reliance Industries AGM update",
            "precise_pub_time": "20240115041500"
        }
        res2 = self.extractor.parse_gkg_record(rec2, "RELIANCE.NS")
        self.assertEqual(res2["timestamp_basis"], "publisher_published_at")
        self.assertIsNotNone(res2["published_at"])

    # 9. Invariant: Existing SQLite Cache & Fetch Periods Untouched
    def test_existing_cache_integrity_preserved(self):
        conn = sqlite3.connect(CACHE_DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM raw_articles")
        article_count = c.fetchone()[0]
        c.execute("SELECT status, COUNT(*) FROM fetch_periods GROUP BY status")
        period_counts = dict(c.fetchall())
        conn.close()

        # Database must still have 634 articles and 66 failed periods
        self.assertEqual(article_count, 634)
        self.assertEqual(period_counts.get("failed"), 66)
        self.assertEqual(period_counts.get("success"), 23)


if __name__ == "__main__":
    unittest.main()
