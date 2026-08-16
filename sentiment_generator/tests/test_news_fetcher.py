import unittest
import datetime
import zoneinfo
from unittest.mock import MagicMock, patch

from sentiment_generator.news_fetcher import NewsFetcher


class TestNewsFetcher(unittest.TestCase):
    def setUp(self):
        # Sample realistic NSE trading calendar (Jan 2024 with 2024-01-26 Republic Day holiday)
        self.calendar = [
            "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
            "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",
            "2024-01-22", "2024-01-23", "2024-01-24", "2024-01-25",
            # 2024-01-26 is Holiday (Republic Day)
            "2024-01-29", "2024-01-30", "2024-01-31"
        ]
        self.fetcher = NewsFetcher(trading_calendar=self.calendar)
        self.tz_ist = zoneinfo.ZoneInfo("Asia/Kolkata")

    # ─── 1. Normal Company Matching ───────────────────────────────────────────
    def test_normal_company_matching(self):
        self.assertTrue(self.fetcher.is_relevant_to_company("Infosys reports 7% rise in quarterly profit", "INFY.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Hindustan Unilever volume growth improves in Q3", "HINDUNILVR.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Asian Paints expands manufacturing capacity", "ASIANPAINT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Tata Steel acquires new iron ore mining lease", "TATASTEEL.NS"))

    # ─── 2. Ambiguous Ticker Matching ─────────────────────────────────────────
    def test_ambiguous_ticker_matching(self):
        # ITC.NS
        self.assertTrue(self.fetcher.is_relevant_to_company("ITC Ltd reports strong Q3 profit growth", "ITC.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ITC shares jump 3% ahead of budget announcement", "ITC.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ITC hotels business demerger approved by board", "ITC.NS"))
        
        # LT.NS
        self.assertTrue(self.fetcher.is_relevant_to_company("Larsen & Toubro bags mega infrastructure contract", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T construction arm secures Rs 4000 cr order", "LT.NS"))
        
        # TITAN.NS
        self.assertTrue(self.fetcher.is_relevant_to_company("Titan Company jewellery revenue jumps 20% in festive season", "TITAN.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Tanishq opens 15 new flagship stores across India", "TITAN.NS"))
        
        # SBIN.NS
        self.assertTrue(self.fetcher.is_relevant_to_company("State Bank of India cuts home loan interest rates", "SBIN.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("SBI quarterly net profit surges to record high", "SBIN.NS"))
        
        # TCS.NS
        self.assertTrue(self.fetcher.is_relevant_to_company("Tata Consultancy Services inks $1B digital deal", "TCS.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("TCS Q3 net profit meets analyst estimates", "TCS.NS"))
        
        # RELIANCE.NS
        self.assertTrue(self.fetcher.is_relevant_to_company("Reliance Industries signs green energy partnership", "RELIANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Mukesh Ambani outlines 5G expansion at AGM", "RELIANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("RIL shares rise after robust quarterly results", "RELIANCE.NS"))

    # ─── 3. Invalid Company Match Rejection ───────────────────────────────────
    def test_invalid_company_match_rejection(self):
        # Generic common nouns
        self.assertFalse(self.fetcher.is_relevant_to_company("India seeks to reduce reliance on imported crude oil", "RELIANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Clash of the Titans movie review released", "TITAN.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Scientists discover new methane lakes on Saturn moon Titan", "TITAN.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Lt Governor visits flood affected villages in northern region", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("International Trade Council holds meeting in Geneva", "ITC.NS"))

    # ─── 4. UTC -> IST Conversion ─────────────────────────────────────────────
    def test_utc_to_ist_conversion(self):
        # 2024-01-15 09:30:00 UTC == 2024-01-15 15:00:00 IST (+5:30)
        src_ts, seen_at, ist_dt = self.fetcher.parse_gdelt_timestamp("20240115093000")
        self.assertEqual(src_ts, "20240115093000")
        self.assertEqual(ist_dt.year, 2024)
        self.assertEqual(ist_dt.month, 1)
        self.assertEqual(ist_dt.day, 15)
        self.assertEqual(ist_dt.hour, 15)
        self.assertEqual(ist_dt.minute, 0)
        self.assertIn("+05:30", seen_at)

    # ─── 5. Pre-Market Article ────────────────────────────────────────────────
    def test_pre_market_article_timing(self):
        # Monday 08:00 IST -> Same trading day (2024-01-15)
        dt = datetime.datetime(2024, 1, 15, 8, 0, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(trading_date, "2024-01-15")

    # ─── 6. Article Before NSE Close ──────────────────────────────────────────
    def test_before_nse_close_timing(self):
        # Monday 14:15 IST -> Same trading day (2024-01-15)
        dt = datetime.datetime(2024, 1, 15, 14, 15, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(trading_date, "2024-01-15")

    # ─── 7. Article Exactly at NSE Close ──────────────────────────────────────
    def test_exact_nse_close_timing(self):
        # Monday 15:30 IST -> Rolls into Next Trading Day (2024-01-16)
        dt = datetime.datetime(2024, 1, 15, 15, 30, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(trading_date, "2024-01-16")

    # ─── 8. Article After NSE Close ───────────────────────────────────────────
    def test_after_nse_close_timing(self):
        # Monday 18:45 IST -> Next Trading Day (2024-01-16)
        dt = datetime.datetime(2024, 1, 15, 18, 45, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(trading_date, "2024-01-16")

    # ─── 9. Weekend Article ───────────────────────────────────────────────────
    def test_weekend_article_timing(self):
        # Saturday 2024-01-13 11:00 IST -> Monday (2024-01-15)
        dt = datetime.datetime(2024, 1, 13, 11, 0, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(trading_date, "2024-01-15")

    # ─── 10. NSE Holiday Article ──────────────────────────────────────────────
    def test_holiday_article_timing(self):
        # Friday 2024-01-26 (Republic Day Holiday) -> Monday (2024-01-29)
        dt = datetime.datetime(2024, 1, 26, 10, 0, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(trading_date, "2024-01-29")

    # ─── 11. Article After End Date (No Next Trading Day) ─────────────────────
    def test_after_end_date_boundary(self):
        # Last trading day in calendar is 2024-01-31. Post-market article on 2024-01-31 -> None
        dt = datetime.datetime(2024, 1, 31, 18, 0, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertIsNone(trading_date)

    # ─── 12. Article Before Start Date ────────────────────────────────────────
    def test_before_start_date(self):
        # Article before 2024-01-01 -> Mapped to earliest session 2024-01-01
        dt = datetime.datetime(2023, 12, 31, 12, 0, 0, tzinfo=self.tz_ist)
        trading_date = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(trading_date, "2024-01-01")

    # ─── 13. get_next_trading_day Strict None on End ──────────────────────────
    def test_get_next_trading_day_none(self):
        # Requesting next trading day beyond the calendar bounds returns None
        self.assertIsNone(self.fetcher.get_next_trading_day("2024-01-31"))
        self.assertIsNone(self.fetcher.get_next_trading_day("2024-02-05"))

    # ─── 14. URL Deduplication with Tracking Parameters ───────────────────────
    def test_url_normalization_deduplication(self):
        url1 = "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html?utm_source=twitter&utm_medium=social"
        url2 = "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html?ref=fin_feed"
        norm1 = NewsFetcher.normalize_url(url1)
        norm2 = NewsFetcher.normalize_url(url2)
        self.assertEqual(norm1, norm2)
        self.assertEqual(norm1, "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html")

    # ─── 15. Duplicate Headline Normalization ─────────────────────────────────
    def test_headline_normalization_deduplication(self):
        h1 = "Reliance Industries Q3 profit rises 11% - Reuters"
        h2 = "Reliance Industries Q3 profit rises 11% | Economic Times"
        norm1 = NewsFetcher.normalize_headline(h1)
        norm2 = NewsFetcher.normalize_headline(h2)
        self.assertEqual(norm1, "reliance industries q3 profit rises 11%")
        self.assertEqual(norm2, "reliance industries q3 profit rises 11%")

    # ─── 16. GDELT HTTP 429 Rate Limiting Backoff ─────────────────────────────
    @patch("requests.Session.get")
    def test_gdelt_rate_limiting_retry(self, mock_get):
        # Mock 429 response followed by 200 response
        mock_429 = MagicMock(status_code=429, text="Rate limit exceeded")
        mock_200 = MagicMock(status_code=200, text='{"articles": [{"title": "Infosys quarterly revenue up 6%", "url": "https://sample.com/1", "seendate": "20240115T100000Z"}]}')
        mock_200.json.return_value = {"articles": [{"title": "Infosys quarterly revenue up 6%", "url": "https://sample.com/1", "seendate": "20240115T100000Z"}]}
        
        mock_get.side_effect = [mock_429, mock_200]
        
        with patch("time.sleep"):  # Speed up test execution
            arts = self.fetcher.fetch_gdelt_window("INFY.NS", datetime.datetime(2024, 1, 15), datetime.datetime(2024, 1, 15, 23, 59, 59))
        
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["ticker"], "INFY.NS")
        self.assertEqual(self.fetcher.stats["rate_limit_responses"], 1)
        self.assertEqual(self.fetcher.stats["successful_requests"], 1)

    # ─── 17. GDELT API Permanent Failure Handling ─────────────────────────────
    @patch("requests.Session.get")
    def test_gdelt_api_permanent_failure(self, mock_get):
        # All 4 attempts return 500
        mock_500 = MagicMock(status_code=500, text="Internal Server Error")
        mock_get.return_value = mock_500
        
        with patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                self.fetcher.fetch_gdelt_window("INFY.NS", datetime.datetime(2024, 1, 15), datetime.datetime(2024, 1, 15, 23, 59, 59))

    # ─── 18. GDELT 250-Record Splitting Trigger ───────────────────────────────
    @patch("requests.Session.get")
    def test_gdelt_250_record_splitting_trigger(self, mock_get):
        # Return 250 records to trigger bisection
        items_250 = [{"title": f"Reliance Retail opens new store {i}", "url": f"https://sample.com/{i}", "seendate": "20240110T100000Z"} for i in range(250)]
        mock_full = MagicMock(status_code=200, text="ok")
        mock_full.json.return_value = {"articles": items_250}
        
        items_sub = [{"title": f"Reliance Retail opens new store {i}", "url": f"https://sample.com/{i}", "seendate": "20240110T100000Z"} for i in range(50)]
        mock_sub = MagicMock(status_code=200, text="ok")
        mock_sub.json.return_value = {"articles": items_sub}
        
        mock_get.side_effect = [mock_full, mock_sub, mock_sub]
        
        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "RELIANCE.NS",
                datetime.datetime(2024, 1, 1),
                datetime.datetime(2024, 1, 31, 23, 59, 59),
                max_depth=1
            )
        # Verify bisection was executed and merged without errors
        self.assertGreaterEqual(len(arts), 50)

    # ─── 19. Recursive Interval Merging & Deduplication ───────────────────────
    def test_deduplicate_articles(self):
        raw_list = [
            {"ticker": "RELIANCE.NS", "trading_date": "2024-01-15", "headline": "RIL announces dividend - Reuters", "url": "https://et.com/ril?utm_source=fb", "norm_url": "https://et.com/ril", "source": "GDELT", "published_at": None, "seen_at": "2024-01-15T10:00:00+05:30", "source_timestamp": "20240115043000"},
            {"ticker": "RELIANCE.NS", "trading_date": "2024-01-15", "headline": "RIL announces dividend | ET", "url": "https://et.com/ril?ref=xyz", "norm_url": "https://et.com/ril", "source": "GDELT", "published_at": None, "seen_at": "2024-01-15T10:05:00+05:30", "source_timestamp": "20240115043500"},
            {"ticker": "RELIANCE.NS", "trading_date": "2024-01-15", "headline": "Reliance Jio launches 5G in 10 cities", "url": "https://et.com/jio", "norm_url": "https://et.com/jio", "source": "GDELT", "published_at": None, "seen_at": "2024-01-15T11:00:00+05:30", "source_timestamp": "20240115053000"}
        ]
        deduped = self.fetcher._deduplicate_articles(raw_list)
        self.assertEqual(len(deduped), 2)

    # ─── 20. Final Returned Dates Belong to NSE Calendar ──────────────────────
    def test_final_dates_in_nse_calendar(self):
        # Create article on Saturday Jan 13 -> mapped to Monday Jan 15
        dt = datetime.datetime(2024, 1, 13, 14, 0, 0, tzinfo=self.tz_ist)
        mapped_td = self.fetcher.map_to_nse_trading_session(dt)
        self.assertIn(mapped_td, self.calendar)
        self.assertEqual(mapped_td, "2024-01-15")


if __name__ == "__main__":
    unittest.main()
