"""
Test suite for sentiment_generator/news_fetcher.py

Coverage:
  A. Trading-date mapping  (7 cases)
  B. Pagination            (4 cases)
  C. Deduplication         (4 cases)
  D. Entity matching       (6 cases)
  + UTC->IST conversion, timestamp rejection, rate-limit retry, API failure,
    URL normalisation, headline normalisation (preserved from original suite)
"""
import unittest
import datetime
import zoneinfo
from unittest.mock import MagicMock, patch, call

from sentiment_generator.news_fetcher import NewsFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_article(ticker, trading_date, headline, url, src_ts="20240115100000"):
    """Construct a minimal article dict as produced by fetch_gdelt_window."""
    ist = zoneinfo.ZoneInfo("Asia/Kolkata")
    return {
        "ticker": ticker,
        "company": "",
        "headline": headline,
        "source": "GDELT",
        "url": url,
        "published_at": None,
        "seen_at": datetime.datetime(2024, 1, 15, 15, 30, 0, tzinfo=ist).isoformat(),
        "source_timestamp": src_ts,
        "trading_date": trading_date,
    }


def _mock_200(articles_list):
    m = MagicMock(status_code=200, text="ok")
    m.json.return_value = {"articles": articles_list}
    return m


def _gdelt_item(i, seendate="20240110T100000Z"):
    """Minimal GDELT article dict for a clearly Reliance-relevant headline."""
    return {
        "title": f"Reliance Industries Q3 profit up {i}%",
        "url": f"https://sample.com/article/{i}",
        "seendate": seendate,
    }


# ---------------------------------------------------------------------------
# Test Class
# ---------------------------------------------------------------------------
class TestNewsFetcher(unittest.TestCase):

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
        self.fetcher = NewsFetcher(trading_calendar=self.calendar)
        self.tz_ist = zoneinfo.ZoneInfo("Asia/Kolkata")

    # =========================================================================
    # A.  TRADING-DATE MAPPING  (7 cases)
    # =========================================================================

    def test_A1_trading_day_before_1530(self):
        """Monday 14:00 IST -> same trading day."""
        dt = datetime.datetime(2024, 1, 15, 14, 0, 0, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-15")

    def test_A2_trading_day_exactly_at_1530(self):
        """Monday 15:30:00 IST exactly -> next trading day (look-ahead guard)."""
        dt = datetime.datetime(2024, 1, 15, 15, 30, 0, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-16")

    def test_A3_trading_day_one_second_before_1530(self):
        """Monday 15:29:59 IST -> same trading day (still within session)."""
        dt = datetime.datetime(2024, 1, 15, 15, 29, 59, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-15")

    def test_A4_trading_day_after_1530(self):
        """Monday 18:45 IST -> next trading day."""
        dt = datetime.datetime(2024, 1, 15, 18, 45, 0, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-16")

    def test_A5_weekend(self):
        """Saturday 11:00 IST -> following Monday."""
        dt = datetime.datetime(2024, 1, 13, 11, 0, 0, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-15")

    def test_A6_nse_holiday(self):
        """Republic Day (Friday 26-Jan-2024) -> following Monday."""
        dt = datetime.datetime(2024, 1, 26, 10, 0, 0, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-29")

    def test_A7_beyond_final_trading_date_returns_none(self):
        """Post-market article on the final calendar day must return None (no future session)."""
        dt = datetime.datetime(2024, 1, 31, 18, 0, 0, tzinfo=self.tz_ist)
        self.assertIsNone(self.fetcher.map_to_nse_trading_session(dt))

    def test_A7b_article_on_day_after_last_calendar_day(self):
        """Article on a date completely outside the calendar also returns None."""
        dt = datetime.datetime(2024, 2, 1, 9, 0, 0, tzinfo=self.tz_ist)
        self.assertIsNone(self.fetcher.map_to_nse_trading_session(dt))

    # =========================================================================
    # B.  PAGINATION  (4 cases)
    # =========================================================================

    @patch("requests.Session.get")
    def test_B1_below_250_no_split(self, mock_get):
        """< 250 results: no recursive split, single request only."""
        items = [_gdelt_item(i) for i in range(100)]
        mock_get.return_value = _mock_200(items)

        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "RELIANCE.NS",
                datetime.datetime(2024, 1, 10),
                datetime.datetime(2024, 1, 10, 23, 59, 59),
            )

        mock_get.assert_called_once()
        # All 100 items pass is_relevant_to_company for "Reliance Industries"
        self.assertEqual(len(arts), 100)

    @patch("requests.Session.get")
    def test_B2_exactly_250_triggers_split(self, mock_get):
        """Exactly 250 results must trigger recursive split into two sub-requests."""
        items_250 = [_gdelt_item(i) for i in range(250)]
        # Sub-windows each return fewer than 250 -> no further split
        items_sub = [_gdelt_item(i) for i in range(30)]
        mock_get.side_effect = [_mock_200(items_250), _mock_200(items_sub), _mock_200(items_sub)]

        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "RELIANCE.NS",
                datetime.datetime(2024, 1, 1),
                datetime.datetime(2024, 1, 31, 23, 59, 59),
            )

        # 3 requests: parent + left + right
        self.assertEqual(mock_get.call_count, 3)
        # We get unique articles from the two sub-windows (sub results deduplicated)
        self.assertGreater(len(arts), 0)

    @patch("requests.Session.get")
    def test_B3_one_split_recovers_overflow(self, mock_get):
        """250-item result triggers one split; combined sub-results are returned."""
        full = [_gdelt_item(i) for i in range(250)]
        left = [_gdelt_item(i, seendate="20240101T050000Z") for i in range(120)]
        right = [_gdelt_item(i + 200, seendate="20240116T050000Z") for i in range(80)]
        mock_get.side_effect = [_mock_200(full), _mock_200(left), _mock_200(right)]

        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "RELIANCE.NS",
                datetime.datetime(2024, 1, 1),
                datetime.datetime(2024, 1, 31, 23, 59, 59),
            )

        self.assertGreater(len(arts), 0)
        # Parent was split -> 3 total calls
        self.assertEqual(mock_get.call_count, 3)

    @patch("requests.Session.get")
    def test_B4_multiple_recursive_splits(self, mock_get):
        """
        >500 articles requiring multiple recursive splits.
        Parent=250, left=250 (triggers another split), left-left=50, left-right=50, right=50.
        Total: 5 API requests.
        """
        full_250 = [_gdelt_item(i) for i in range(250)]
        sub_250  = [_gdelt_item(i + 300) for i in range(250)]
        sub_50   = [_gdelt_item(i + 600) for i in range(50)]
        mock_get.side_effect = [
            _mock_200(full_250),   # parent  -> split
            _mock_200(sub_250),    # left    -> split again
            _mock_200(sub_50),     # left-left
            _mock_200(sub_50),     # left-right
            _mock_200(sub_50),     # right
        ]

        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "RELIANCE.NS",
                datetime.datetime(2024, 1, 1),
                datetime.datetime(2024, 1, 31, 23, 59, 59),
            )

        self.assertEqual(mock_get.call_count, 5)
        self.assertGreater(len(arts), 0)

    @patch("requests.Session.get")
    def test_B5_subwindow_failure_propagates(self, mock_get):
        """
        A sub-window API failure must raise RuntimeError rather than silently
        returning the parent's truncated 250-item list as if it were complete.
        """
        full_250 = [_gdelt_item(i) for i in range(250)]
        mock_500 = MagicMock(status_code=500, text="Internal Server Error")
        # Parent succeeds with 250, left sub-window always returns 500
        mock_get.side_effect = [_mock_200(full_250)] + [mock_500] * 5  # retries

        with patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                self.fetcher.fetch_gdelt_window(
                    "RELIANCE.NS",
                    datetime.datetime(2024, 1, 1),
                    datetime.datetime(2024, 1, 31, 23, 59, 59),
                )

    # =========================================================================
    # C.  DEDUPLICATION  (4 cases)
    # =========================================================================

    def test_C1_same_url_deduplication(self):
        """Two records sharing a normalised URL (tracking params stripped) -> one kept."""
        a1 = _make_article("RELIANCE.NS", "2024-01-15", "RIL Q3 profit rises",
                            "https://et.com/ril?utm_source=tw")
        a2 = _make_article("RELIANCE.NS", "2024-01-15", "RIL Q3 profit rises",
                            "https://et.com/ril?ref=social")
        deduped = self.fetcher._deduplicate_articles([a1, a2])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(self.fetcher.stats["duplicates_removed"], 1)

    def test_C2_same_headline_different_url_same_source(self):
        """Same normalised headline from the exact same domain -> duplicate."""
        a1 = _make_article("RELIANCE.NS", "2024-01-15", "RIL announces dividend - Reuters",
                            "https://reuters.com/ril-div-v1")
        a2 = _make_article("RELIANCE.NS", "2024-01-15", "RIL announces dividend | Reuters",
                            "https://reuters.com/ril-div-v2")
        deduped = self.fetcher._deduplicate_articles([a1, a2])
        self.assertEqual(len(deduped), 1)

    def test_C3_same_headline_different_publisher_not_deduplicated(self):
        """
        Same headline syndicated to two different publishers should NOT be merged,
        because they are genuinely different publications (different source domains).
        """
        a1 = _make_article("RELIANCE.NS", "2024-01-15", "RIL Q3 profit surges - Reuters",
                            "https://reuters.com/ril-q3")
        a2 = _make_article("RELIANCE.NS", "2024-01-15", "RIL Q3 profit surges - Mint",
                            "https://livemint.com/ril-q3")
        deduped = self.fetcher._deduplicate_articles([a1, a2])
        self.assertEqual(len(deduped), 2,
                         "Same headline from different domains must NOT be merged")

    def test_C4_midpoint_article_no_double_count(self):
        """
        The _deduplicate_articles step ensures that even if the same article appears in
        both left and right sub-results (e.g. due to an off-by-one in an external caller),
        it is only counted once.
        """
        a = _make_article("RELIANCE.NS", "2024-01-10", "Reliance Jio 5G milestone",
                          "https://et.com/jio-5g")
        # Simulate midpoint article appearing in both halves
        deduped = self.fetcher._deduplicate_articles([a, dict(a)])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(self.fetcher.stats["duplicates_removed"], 1)

    # =========================================================================
    # D.  ENTITY MATCHING  (6 cases)
    # =========================================================================

    def test_D1_valid_itc_article(self):
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Ltd reports strong Q3 profit growth", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Limited declares interim dividend", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC hotels business demerger approved by board", "ITC.NS"))

    def test_D2_unrelated_itc_acronym_rejected(self):
        self.assertFalse(
            self.fetcher.is_relevant_to_company("International Trade Council holds meeting in Geneva", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("ITC exam schedule released for 2024", "ITC.NS"))

    def test_D3_valid_lt_article(self):
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Larsen & Toubro bags mega infrastructure contract", "LT.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("L&T construction arm secures Rs 4000 cr order", "LT.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("LT shares rise after strong quarterly results", "LT.NS"))

    def test_D4_unrelated_lt_acronym_rejected(self):
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Lt Governor visits flood-affected villages", "LT.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Long-term interest rate outlook remains uncertain", "LT.NS"))

    def test_D5_valid_titan_article(self):
        self.assertTrue(
            self.fetcher.is_relevant_to_company(
                "Titan Company jewellery revenue jumps 20% in festive season", "TITAN.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Tanishq opens 15 new flagship stores across India", "TITAN.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company(
                "Titan watches division reports double-digit growth", "TITAN.NS"))

    def test_D6_unrelated_titan_reference_rejected(self):
        self.assertFalse(
            self.fetcher.is_relevant_to_company(
                "Scientists discover new methane lakes on Saturn moon Titan", "TITAN.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Clash of the Titans movie review released", "TITAN.NS"))

    # =========================================================================
    # Original tests (preserved)
    # =========================================================================

    def test_normal_company_matching(self):
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Infosys reports 7% rise in quarterly profit", "INFY.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Hindustan Unilever volume growth improves in Q3", "HINDUNILVR.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Asian Paints expands manufacturing capacity", "ASIANPAINT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Tata Steel acquires new iron ore mining lease", "TATASTEEL.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra Bank appoints new CEO", "KOTAKBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Sun Pharma receives FDA approval for new drug", "SUNPHARMA.NS"))

    def test_ambiguous_ticker_matching(self):
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "ITC shares jump 3% ahead of budget announcement", "ITC.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "State Bank of India cuts home loan interest rates", "SBIN.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "SBI quarterly net profit surges to record high", "SBIN.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Tata Consultancy Services inks $1B digital deal", "TCS.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "TCS Q3 net profit meets analyst estimates", "TCS.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Reliance Industries signs green energy partnership", "RELIANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Mukesh Ambani outlines 5G expansion at AGM", "RELIANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "RIL shares rise after robust quarterly results", "RELIANCE.NS"))

    def test_invalid_company_match_rejection(self):
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "India seeks to reduce reliance on imported crude oil", "RELIANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "TCS test center announced for graduate admissions", "TCS.NS"))

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

    def test_pre_market_article_timing(self):
        dt = datetime.datetime(2024, 1, 15, 8, 0, 0, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-15")

    def test_invalid_timestamp_rejection(self):
        with self.assertRaises(ValueError):
            self.fetcher.parse_gdelt_timestamp("invalid_date")
        with self.assertRaises(ValueError):
            self.fetcher.parse_gdelt_timestamp("")
        with self.assertRaises(ValueError):
            self.fetcher.parse_gdelt_timestamp("12345")

    def test_url_normalization_deduplication(self):
        url1 = "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html?utm_source=twitter&utm_medium=social"
        url2 = "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html?ref=fin_feed"
        norm1 = NewsFetcher.normalize_url(url1)
        norm2 = NewsFetcher.normalize_url(url2)
        self.assertEqual(norm1, norm2)
        self.assertEqual(norm1, "https://economictimes.indiatimes.com/markets/stocks/news/ril-q3-results.html")

    def test_headline_normalization_deduplication(self):
        h1 = "Reliance Industries Q3 profit rises 11% - Reuters"
        h2 = "Reliance Industries Q3 profit rises 11% | Economic Times"
        norm1 = NewsFetcher.normalize_headline(h1)
        norm2 = NewsFetcher.normalize_headline(h2)
        self.assertEqual(norm1, "reliance industries q3 profit rises 11%")
        self.assertEqual(norm2, "reliance industries q3 profit rises 11%")

    @patch("requests.Session.get")
    def test_gdelt_rate_limiting_retry(self, mock_get):
        mock_429 = MagicMock(status_code=429, headers={"Retry-After": "2"}, text="Rate limit exceeded")
        mock_200 = MagicMock(status_code=200,
                             text='{"articles": [{"title": "Infosys quarterly revenue up 6%", "url": "https://sample.com/1", "seendate": "20240115T100000Z"}]}')
        mock_200.json.return_value = {"articles": [
            {"title": "Infosys quarterly revenue up 6%", "url": "https://sample.com/1", "seendate": "20240115T100000Z"}
        ]}
        mock_get.side_effect = [mock_429, mock_200]
        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "INFY.NS",
                datetime.datetime(2024, 1, 15),
                datetime.datetime(2024, 1, 15, 23, 59, 59),
            )
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["ticker"], "INFY.NS")
        self.assertEqual(self.fetcher.stats["rate_limit_responses"], 1)
        self.assertEqual(self.fetcher.stats["successful_requests"], 1)

    @patch("requests.Session.get")
    def test_gdelt_api_permanent_failure(self, mock_get):
        mock_500 = MagicMock(status_code=500, text="Internal Server Error")
        mock_get.return_value = mock_500
        with patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                self.fetcher.fetch_gdelt_window(
                    "INFY.NS",
                    datetime.datetime(2024, 1, 15),
                    datetime.datetime(2024, 1, 15, 23, 59, 59),
                )

    def test_final_dates_strictly_in_nse_calendar(self):
        # Saturday 13-Jan maps to Monday 15-Jan
        dt = datetime.datetime(2024, 1, 13, 14, 0, 0, tzinfo=self.tz_ist)
        mapped_td = self.fetcher.map_to_nse_trading_session(dt)
        self.assertIn(mapped_td, self.calendar)
        self.assertEqual(mapped_td, "2024-01-15")


if __name__ == "__main__":
    unittest.main()
