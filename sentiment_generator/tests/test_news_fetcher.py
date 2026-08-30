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

try:
    import torch
except Exception:
    pass

import unittest
import datetime
import zoneinfo
import threading
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
        self.tz_utc = zoneinfo.ZoneInfo("UTC")

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

    def test_A3b_trading_day_one_second_after_1530(self):
        """Monday 15:30:01 IST -> next trading day (one second after close)."""
        dt = datetime.datetime(2024, 1, 15, 15, 30, 1, tzinfo=self.tz_ist)
        self.assertEqual(self.fetcher.map_to_nse_trading_session(dt), "2024-01-16")

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
                datetime.datetime(2024, 1, 10, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 10, 23, 59, 59, tzinfo=self.tz_utc),
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
                datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc),
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
                datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc),
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
                datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc),
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
                    datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc),
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

    def test_C2_same_headline_same_domain_no_url_deduplication(self):
        """
        Headline-based deduplication only fires when the normalized URL is empty.
        Two articles with different (non-empty) URLs from the same domain use the
        URL as their dedup key and are kept separately, even if the headline matches.
        This tests the FALLBACK path: articles with empty URLs and same headline
        from the same domain (identified by source field) ARE deduplicated.
        """
        # Empty-URL articles: fallback key = (ticker, date, norm_headline::src_domain)
        # With empty URL, src_domain is extracted from the empty url -> "".
        # So both share key (ticker, date, "ril announces dividend::") -> one kept.
        a1 = _make_article("RELIANCE.NS", "2024-01-15", "RIL announces dividend - Reuters", "")
        a2 = _make_article("RELIANCE.NS", "2024-01-15", "RIL announces dividend | Reuters", "")
        deduped = self.fetcher._deduplicate_articles([a1, a2])
        self.assertEqual(len(deduped), 1,
                         "Same headline with empty URL from same context must deduplicate")

    def test_C2b_same_headline_different_paths_same_domain_not_merged(self):
        """
        Two articles with different URL paths on the same domain use the URL as
        their primary dedup key and must NOT be merged, even if headlines match.
        This is correct: they are genuinely different articles (different canonical URLs).
        """
        a1 = _make_article("RELIANCE.NS", "2024-01-15", "RIL announces dividend - Reuters",
                            "https://reuters.com/ril-div-v1")
        a2 = _make_article("RELIANCE.NS", "2024-01-15", "RIL announces dividend | Reuters",
                            "https://reuters.com/ril-div-v2")
        deduped = self.fetcher._deduplicate_articles([a1, a2])
        self.assertEqual(len(deduped), 2,
                         "Different URL paths on same domain must NOT be merged")

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
        # Explicit phrase matches
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Ltd reports strong Q3 profit growth", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Limited declares interim dividend", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC hotels business demerger approved by board", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Hotels to launch its maiden international luxury hotel in Sri Lanka", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("President to open ITC Ratnadipa Colombo next week", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC expands global presence via subsidiary Fortune Hotels", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Welcomhotel by ITC Hotels reopens in Chennai", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Infotech embarks on a global footprint expansion spree", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Sanjiv Puri discusses ITC strategy and FMCG growth", "ITC.NS"))

        # Bare 'ITC' + strong corporate/financial signals
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC fmcg segment posts record crore turnover", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC shares jump 4% after analyst upgrade", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC cigarette volumes recover in Q2 earnings", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Q3 net profit rises 6.5% to Rs 5,335 crore", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC board approves acquisition of cloud kitchen startup", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Jefferies maintains 'Buy' on ITC with target Rs 520", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC, Piramal Enterprises, Navin Fluorine may deliver up to 14% return in short term", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC, Kellogg's spinoff firm eye stake in another PE-backed healthy snacks brand", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC's 'Krishi Mitra' uses Microsoft Copilot to help farmers", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Viksit Bharat Budget 2024: ITC jumps 5% as govt keeps sin tax unchanged", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Titan, ITC seen benefitting most from consumption thrust in Budget 2024", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("Trade Spotlight: How should you trade LIC, HUL, ITC, DMart, Orient Cement, and others ahead of budget day?", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("HUL, ITC, Hero Moto among 14 picks Axis Securities is positive on post Budget 2024", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Hotels demerger approved", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Ratnadipa expands hospitality footprint", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC seen benefitting from Budget consumption push", "ITC.NS"))

    def test_D2_unrelated_itc_acronym_and_false_positive_families_rejected(self):
        # US International Trade Commission / Legal Proceedings
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Chief Administrative Law Judge issues ITC report", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Navigating the post-Loper Bright world at the ITC", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("International Trade Council holds meeting in Geneva", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("WIT ITC Report: Chief Administrative Law Judge Clark Cheney - International Trade & Investment", "ITC.NS"))

        # San Antonio Institute of Texan Cultures / Landmark Status
        self.assertFalse(
            self.fetcher.is_relevant_to_company("ITC building gains landmark status in San Antonio", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Institute of Texan Cultures receives landmark status", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("San Antonio's ITC building gains landmark status amid Spurs arena talks", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Elected leaders rally for Prop D; ITC gets landmark status; Castro slams Trump comedian", "ITC.NS"))

        # SAICA Board Exams
        self.assertFalse(
            self.fetcher.is_relevant_to_company("ITC exam schedule released for 2024", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Over 1,700 candidates now one SAICA board exam away from becoming CA(SA) as they pass their ITC exams", "ITC.NS"))

        # GST / VAT / Input Tax Credit Collisions
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Bombay HC hears Maharashtra ITC rule challenge", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Bombay HC seeks response from revenue dept in Maharashtra ITC rule case", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("VAT dealer claims ITC entitlement", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Flair Writing receives tax notice for excess ITC claims", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("GST Detects 29,000 Firms Involved In Fake ITC Claims Worth Rs 44,000 Crore", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("GST officers detect bogus firms involved in fake ITC claims", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("fake input tax credit claims detected by tax authorities", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("ITC Admissible On Sale And Buyback Transactions When Payment Is Settled: AAR", "ITC.NS"))

        # Foreign Imperial Tobacco / Imperial Brands Collisions
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Minister Holland right to call out Imperial Tobacco: Quebec Coalition", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Imperial Tobacco Canada announces new CEO", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Imperial Brands / Imperial Tobacco UK launches new Rizla variant", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Imperial Tobacco Appoints Cluster Marketing Director in UK", "ITC.NS"))

        # Generic Tobacco / Smoking Health Research
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Reducing nicotine in tobacco would help people quit without prohibiting cigarettes", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Smoking - Health Risks, Addiction, History in global tobacco report", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Public health joins forces against the sale of nicotine pouches", "ITC.NS"))

        # Static / Job Portal / Promotional Pages
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Business Analyst | ITC - Job openings in IT company", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Data Analyst/Business Intelligence Developer | ITC", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("Mementos by ITC Hotels Ekaaya Udaipur awarded the Best New Hotel Resort", "ITC.NS"))

    def test_D2b_itc_lifecycle_and_transition_boundary_tests(self):
        """
        Validates entity lifecycle boundaries for ITC.NS vs standalone ITC Hotels Limited:
        1. PAIRED TEMPORAL TESTS (Identical headline, opposite result based on timestamp):
           - "ITC Hotels reports strong quarterly profit"
             * 2024 timestamp -> True (pre-separation subsidiary)
             * 2025 post-listing timestamp -> False (standalone company)
           - "ITC Hotels opens new luxury property"
             * 2024-04-15 (pre-separation) -> True
             * 2025-02-01 (post-listing) -> False
        2. BOUNDARY DATE TESTS (Immediately before / on / after 2025-01-29 listing date):
           - "ITC Hotels opens new luxury property"
             * 2025-01-28 (1 day before listing) -> True
             * 2025-01-29 (on listing date) -> False
             * 2025-01-30 (1 day after listing) -> False
           - "ITC Hotels reports quarterly results"
             * 2025-01-28 -> True
             * 2025-01-30 -> False
        3. TRANSITION / DEMERGER AFFECTING PARENT ITC.NS:
           - "ITC Hotels lists after demerger from ITC" -> True
           - "ITC shares rise after ITC Hotels listing" -> True (parent ITC explicitly affected)
           - "ITC trades sans hotels division at Rs 455 apiece" -> True
           - "Indian conglomerate ITC's value adjusts 5% lower after hotels business spin-off" -> True
           - "ITC Hotels will list tomorrow- 10 things that shareholders need to know NOW" -> True
           - "ITC Hotels demerger: Everything that you need to know about ITC's hotel business" -> True
        4. POST-SEPARATION STANDALONE COMPANY (ITCHOTELS.NS ONLY):
           - "ITC Hotels share down 5 percent" (post-listing) -> False
           - "What price will ITC Hotels list at? Here's what analysts predict" -> False
           - "ITC Hotels to see $190 million of fund outflows" -> False
           - "ITC Hotels Closes at a 33 per cent Discount to the Discovered Price. What Should Investors Do?" -> False
           - "Heartwarming! ITC Delhi's Former Watchman Dines Inside Luxury Hotel" -> False
        """
        rel = self.fetcher.is_relevant_to_company

        # ── 1. PAIRED TEMPORAL TESTS ──────────────────────────────────────────
        # Same headline: "ITC Hotels reports strong quarterly profit"
        self.assertTrue(
            rel("ITC Hotels reports strong quarterly profit", "ITC.NS", article_datetime="2024-05-15"),
            "Pre-separation hotel segment profit must belong to parent ITC.NS"
        )
        self.assertFalse(
            rel("ITC Hotels reports strong quarterly profit", "ITC.NS", article_datetime="2025-02-15"),
            "Post-listing standalone hotel profit must NOT belong to parent ITC.NS"
        )

        # Same headline: "ITC Hotels opens new luxury property"
        self.assertTrue(
            rel("ITC Hotels opens new luxury property", "ITC.NS", article_datetime="2024-04-15"),
            "Pre-separation luxury hotel opening belongs to parent ITC.NS"
        )
        self.assertFalse(
            rel("ITC Hotels opens new luxury property", "ITC.NS", article_datetime="2025-02-01"),
            "Post-listing standalone luxury hotel opening must NOT belong to parent ITC.NS"
        )

        # ── 2. EXACT BOUNDARY DATES (Listing date: 2025-01-29) ────────────────
        # Immediately BEFORE listing date (2025-01-28)
        self.assertTrue(
            rel("ITC Hotels opens new luxury property", "ITC.NS", article_datetime="2025-01-28"),
            "Day before listing must still evaluate as pre-separation"
        )
        self.assertTrue(
            rel("ITC Hotels reports quarterly results", "ITC.NS", article_datetime="2025-01-28"),
            "Day before listing must still evaluate as pre-separation"
        )

        # ON listing date (2025-01-29) - Transition demerger event
        self.assertTrue(
            rel("ITC Hotels lists after demerger from ITC", "ITC.NS", article_datetime="2025-01-29"),
            "Listing of demerged entity is a transition event for ITC.NS"
        )

        # Immediately AFTER listing date (2025-01-30) - Standalone ITCHOTELS.NS only
        self.assertFalse(
            rel("ITC Hotels opens new luxury property", "ITC.NS", article_datetime="2025-01-30"),
            "Day after listing must evaluate as standalone post-separation"
        )
        self.assertFalse(
            rel("ITC Hotels reports quarterly results", "ITC.NS", article_datetime="2025-01-30"),
            "Day after listing standalone results must not belong to parent ITC.NS"
        )

        # ── 3. TRANSITION / DEMERGER AFFECTING PARENT ITC.NS ───────────────────
        self.assertTrue(
            rel("ITC Hotels lists after demerger from ITC", "ITC.NS", article_datetime="2025-01-28"),
            "Demerger transition story must belong to ITC.NS"
        )
        self.assertTrue(
            rel("ITC shares rise after ITC Hotels listing", "ITC.NS", article_datetime="2025-01-30"),
            "Post-listing headline where parent ITC is explicitly affected must pass"
        )
        self.assertTrue(
            rel("ITC trades sans hotels division at Rs 455 apiece", "ITC.NS", article_datetime="2025-01-06"))
        self.assertTrue(
            rel("Indian conglomerate ITC's value adjusts 5% lower after hotels business spin-off", "ITC.NS", article_datetime="2025-01-06"))
        self.assertTrue(
            rel("ITC Hotels will list tomorrow- 10 things that shareholders need to know NOW", "ITC.NS", article_datetime="2025-01-28"))
        self.assertTrue(
            rel("ITC Hotels demerger: Everything that you need to know about ITC's hotel business", "ITC.NS", article_datetime="2025-01-29"))

        # ── 4. POST-SEPARATION STANDALONE (ITCHOTELS.NS ONLY) ─────────────────
        self.assertFalse(
            rel("ITC Hotels share down 5 percent", "ITC.NS", article_datetime="2025-01-30"))
        self.assertFalse(
            rel("What price will ITC Hotels list at? Here's what analysts predict", "ITC.NS", article_datetime="2025-01-30"))
        self.assertFalse(
            rel("ITC Hotels to see $190 million of fund outflows", "ITC.NS", article_datetime="2025-01-30"))
        self.assertFalse(
            rel("ITC Hotels Closes at a 33 per cent Discount to the Discovered Price. What Should Investors Do?", "ITC.NS", article_datetime="2025-01-30"))
        self.assertFalse(
            rel("Heartwarming! ITC Delhi's Former Watchman Dines Inside Luxury Hotel", "ITC.NS", article_datetime="2025-01-24"))

    def test_D3_valid_lt_article(self):
        # Full corporate entity
        self.assertTrue(self.fetcher.is_relevant_to_company("Larsen & Toubro bags mega infrastructure contract in Middle East", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Larsen and Toubro wins Rs 7,000 crore order from bullet train project", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Larsen Toubro Limited reports robust revenue growth", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Larsen & Toubro wins Rs 5,000 crore order", "LT.NS"))
        # L&T entity & subsidiaries
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T construction arm secures Rs 4000 cr order", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T secures major hydrocarbons contract from Saudi Aramco", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T Q3 profit rises 15% to Rs 2,947 crore; declares dividend", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T Heavy Engineering bags key equipment orders", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T Realty launches luxury residential project in Mumbai", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T semiconductor chip design unit announced", "LT.NS"))
        # Infrastructure & Projects (Airport construction, Bypass projects)
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T wins major airport construction contract", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T awarded highway bypass construction project", "LT.NS"))
        # Workforce Expansion
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T plans to hire 10,000 engineers", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T announces major workforce expansion", "LT.NS"))
        # Leadership
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T Chairman SN Subrahmanyan sees robust order pipeline", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("AM Naik steps down as L&T Group Chairman", "LT.NS"))
        # Market / Brokerage roundups
        self.assertTrue(self.fetcher.is_relevant_to_company("LT shares rise after strong quarterly results", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("L&T shares rise after strong quarterly results", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Trade Spotlight: How to trade Tata Motors, L&T, Infosys today", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Top stock picks: Brokerages bullish on Reliance, L&T, HDFC Bank", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("लार्सन एंड टुब्रो को मिला 5000 करोड़ का बड़ा ऑर्डर", "LT.NS"))

    def test_D4_unrelated_lt_acronym_rejected(self):
        # Military / Police ranks
        self.assertFalse(self.fetcher.is_relevant_to_company("Lt Governor visits flood-affected villages in Jammu", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Lt Gen Upendra Dwivedi reviews security situation along LoC", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Lt Col MS Dhoni visits Army camp in Kashmir", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("US Navy Lt Commander faces disciplinary action", "LT.NS"))
        # Technical / Electrical / Finance terms
        self.assertFalse(self.fetcher.is_relevant_to_company("Long-term interest rate outlook remains uncertain", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Long-term (LT) capital gains tax rules explained", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("MSEDCL upgrades LT line and distribution transformers in Pune", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("LT consumer power tariffs to increase by 5% in Tamil Nadu", "LT.NS"))
        # Automotive trims
        self.assertFalse(self.fetcher.is_relevant_to_company("Chevrolet reveals new 2024 Silverado LT Trail Boss edition", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Chevrolet Tahoe LT review: Is it worth the upgrade?", "LT.NS"))
        # Sports abbreviations
        self.assertFalse(self.fetcher.is_relevant_to_company("Giants sign star LT Andrew Thomas to massive contract extension", "LT.NS"))
        # Foreign organizations & geography & medicine
        self.assertFalse(self.fetcher.is_relevant_to_company("Philippine conglomerate LT Group reports full-year net income", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Massive iceberg breaks off Larsen C ice shelf in Antarctica", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Clinical trial investigates LT receptor antagonists in asthma patients", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Postoperative outcomes in adult liver transplantation (LT) recipients", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Bare lt without any corporate context", "LT.NS"))
        # Airport retail / Liquor & Tobacco
        self.assertFalse(self.fetcher.is_relevant_to_company("S Korea retail giants ignite L&T contest at Gimpo duty-free", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Liquor & Tobacco L&T category tender at airport", "LT.NS"))
        # Geographical bypass road
        self.assertFalse(self.fetcher.is_relevant_to_company("Police register case after protest on L&T bypass", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Accident near L&T bypass leaves traffic disrupted", "LT.NS"))
        # Routine job postings / vacancies
        self.assertFalse(self.fetcher.is_relevant_to_company("L&T Infotech recruits for Test Specialist", "LT.NS"))
        # Finnish Lassila & Tikanoja (LAT1V)
        self.assertFalse(self.fetcher.is_relevant_to_company("L&T:llä vastatuulta monesta suunnasta - Tulos painui", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Lassila & Tikanoja Q1 tulos ja näkymät", "LT.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("LAT1V osakekurssi laskee Helsingin pörssissä", "LT.NS"))
        # Malformed publisher concatenated token (documented regression case, left rejected for now)
        self.assertFalse(self.fetcher.is_relevant_to_company("Sensex reclaims 72,000-levels, Nifty above 21,700; Adani Ports and L&Trises 3%", "LT.NS"))

    def test_D4B_is_article_url_quality_filtering(self):
        # Static CMS taxonomy, category, tag, author, search URLs must be rejected
        self.assertFalse(self.fetcher.is_article_url("https://udaipurkiran.com/tag/lt-finance-holdings"))
        self.assertFalse(self.fetcher.is_article_url("https://udaipurkiran.com/tag/lt-finance-holdings/"))
        self.assertFalse(self.fetcher.is_article_url("https://udaipurkiran.com/tags/reliance-industries"))
        self.assertFalse(self.fetcher.is_article_url("https://moneycontrol.com/category/markets"))
        self.assertFalse(self.fetcher.is_article_url("https://economictimes.indiatimes.com/author/example-writer"))
        self.assertFalse(self.fetcher.is_article_url("https://example.com/search/?q=larsen"))
        self.assertFalse(self.fetcher.is_article_url("https://example.com/topic/infrastructure"))
        self.assertFalse(self.fetcher.is_article_url("https://example.com/archive/2024/04"))
        self.assertFalse(self.fetcher.is_article_url("https://example.com/?q=larsen"))
        self.assertFalse(self.fetcher.is_article_url("https://example.com/"))
        self.assertFalse(self.fetcher.is_article_url(""))
        self.assertFalse(self.fetcher.is_article_url(None))

        # Legitimate article URLs must pass
        self.assertTrue(self.fetcher.is_article_url(
            "https://economictimes.indiatimes.com/markets/stocks/news/stock-radar-lt-breaks-out-from-cup-handle-pattern-stock-likely-to-hit-4000-levels/articleshow/108941845.cms"))
        self.assertTrue(self.fetcher.is_article_url(
            "https://www.moneycontrol.com/news/business/markets/mc-interview-rs-1-lakh-cr-hydrocarbon-order-book-gives-revenue-visibility-not-worried-about-saudi-project-deferment-lt-energy-head-12567441.html"))
        self.assertTrue(self.fetcher.is_article_url(
            "https://www.livemint.com/companies/news/kandla-port-l-t-ril-to-invest-rs-1-lakh-crore-in-green-energy-deendayal-port-authority-allots-14-land-parcels-11712722353305.html"))
        self.assertTrue(self.fetcher.is_article_url(
            "https://idrw.org/lt-arm-flags-off-crucial-component-for-indias-1st-domestically-built-700-mw-nuclear-reactor"))
        self.assertTrue(self.fetcher.is_article_url(
            "https://www.thehindubusinessline.com/markets/stock-markets/lt-sells-stake-in-lt-infrastructure-development-projects/article68053125.ece"))
        self.assertTrue(self.fetcher.is_article_url(
            "https://udaipurkiran.com/lt-finance-holdings-trades-higher-on-reporting-33-rise-in-retail-disbursements-during-q4fy24"))
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
    # D7.  WORD-BOUNDARY FINANCIAL CONTEXT (moved tokens)
    # =========================================================================

    def test_D7_word_boundary_tokens_match_as_whole_words(self):
        """
        Tokens moved from FINANCIAL_CONTEXT_KEYWORDS to _FINANCIAL_CONTEXT_WORDBOUND
        (tech, auto, deal, power, tax, bank) must:
        (a) still fire on _has_financial_context() when they appear as whole words;
        (b) NOT fire when they appear only as substrings of longer words.
        """
        has_ctx = self.fetcher._has_financial_context

        # (a) Whole-word matches must still return True
        self.assertTrue(has_ctx("reliance tech arm announces deal"),
                        "'tech' and 'deal' as whole words must trigger financial context")
        self.assertTrue(has_ctx("auto sector sales rise on festive demand"),
                        "'auto' as whole word must trigger financial context")
        self.assertTrue(has_ctx("power sector capex revised upward"),
                        "'power' as whole word must trigger financial context")
        self.assertTrue(has_ctx("bank npa ratio improves in q3"),
                        "'bank' as whole word must trigger financial context")
        self.assertTrue(has_ctx("tax relief announced for smes"),
                        "'tax' as whole word must trigger financial context")

        # (b) Substring-only appearances must NOT fire via the word-boundary path.
        # Note: other keywords in FINANCIAL_CONTEXT_KEYWORDS may still trigger;
        # these sentences are crafted to avoid all other financial keywords.
        self.assertFalse(has_ctx("the automatic renewal clause was signed yesterday"),
                         "'auto' inside 'automatic' must not trigger financial context")
        self.assertFalse(has_ctx("the new technical committee convened on tuesday"),
                         "'tech' inside 'technical' must not trigger financial context")
        self.assertFalse(has_ctx("the ordeal continued through the night session"),
                         "'deal' inside 'ordeal' must not trigger financial context")
        self.assertFalse(has_ctx("empower communities through local governance"),
                         "'power' inside 'empower' must not trigger financial context")

    # =========================================================================
    # D8.  TCS / SBI WORD-BOUNDARY REGRESSION
    # =========================================================================

    def test_D8_tcs_tech_and_deal_word_boundary(self):
        """
        'tech' and 'deal' must be matched as whole words for TCS, not as substrings.

        Required cases (from review):
          TCS technical announcement  → False  ('tech' inside 'technical' must not fire)
          TCS ideal situation         → False  ('deal' inside 'ideal' must not fire)
          TCS tech contract           → True   ('tech' as whole word fires via word-boundary)
          TCS deal with Microsoft     → True   ('deal' as whole word fires via word-boundary)
        """
        rel = self.fetcher.is_relevant_to_company

        # False cases: substring-only occurrence — must not match
        self.assertFalse(
            rel("TCS technical announcement raises concerns about workforce planning", "TCS.NS"),
            "'tech' inside 'technical' must not trigger TCS match")
        self.assertFalse(
            rel("TCS ideal outcome for the workforce reorganisation was announced", "TCS.NS"),
            "'deal' inside 'ideal' must not trigger TCS match")

        # True cases: whole-word occurrence — must match
        self.assertTrue(
            rel("TCS tech unit wins outsourcing mandate from European insurer", "TCS.NS"),
            "'tech' as whole word must trigger TCS match")
        self.assertTrue(
            rel("TCS deal with Microsoft worth over 500 million dollars announced", "TCS.NS"),
            "'deal' as whole word must trigger TCS match")

        # Additional coverage: explicit strong keywords still work
        self.assertTrue(
            rel("TCS Q3 results beat analyst estimates on strong revenue growth", "TCS.NS"),
            "Unambiguous keywords (results, revenue) must still trigger TCS match")
        self.assertTrue(
            rel("Tata Consultancy Services signs 1 billion dollar contract", "TCS.NS"),
            "Full company name must always trigger TCS match")

    def test_D8b_sbi_bank_word_boundary(self):
        """
        'bank' must be matched as a whole word for SBI, not as a substring.
        'embankment' contains 'bank' as a substring and must not trigger an SBI match.
        """
        rel = self.fetcher.is_relevant_to_company

        # False: 'bank' inside 'embankment' — must not fire
        self.assertFalse(
            rel("SBI embankment project near the riverside to be completed by 2027", "SBIN.NS"),
            "'bank' inside 'embankment' must not trigger SBI match")

        # True: 'bank' as whole word — must fire
        self.assertTrue(
            rel("SBI bank branch network expanded to 23000 locations", "SBIN.NS"),
            "'bank' as whole word must trigger SBI match")
        self.assertTrue(
            rel("State Bank of India cuts home loan rates by 25 basis points", "SBIN.NS"),
            "Explicit 'State Bank of India' phrase must always trigger SBI match")

        # ─── SBIN.NS Adversarial Validation Suite ─────────────────────────────
        # Strong Positives
        self.assertTrue(rel("State Bank of India reports record quarterly profit", "SBIN.NS"))
        self.assertTrue(rel("State Bank of India raises Rs 10,000 crore through infrastructure bonds", "SBIN.NS"))
        self.assertTrue(rel("SBI shares rise 4% after strong quarterly results", "SBIN.NS"))
        self.assertTrue(rel("SBI reports improvement in gross NPA ratio", "SBIN.NS"))
        self.assertTrue(rel("SBI board approves fundraising plan", "SBIN.NS"))
        self.assertTrue(rel("SBI loan growth remains strong", "SBIN.NS"))
        self.assertTrue(rel("SBI chairman Dinesh Khara discusses credit growth", "SBIN.NS"))
        self.assertTrue(rel("Supreme Court directs SBI to disclose electoral bond serial numbers", "SBIN.NS"))
        self.assertTrue(rel("CS Setty takes charge as new SBI chairman", "SBIN.NS"))
        self.assertTrue(rel("SBI hikes fixed deposit interest rates by 25 bps", "SBIN.NS"))
        self.assertTrue(rel("RBI imposes monetary penalty on State Bank of India", "SBIN.NS"))

        # Foreign / Generic State Bank Negatives (EXPECT FALSE)
        self.assertFalse(rel("State Bank of Pakistan raises policy rate to 22%", "SBIN.NS"))
        self.assertFalse(rel("State Bank of Vietnam cuts lending rates", "SBIN.NS"))
        self.assertFalse(rel("State Bank of Texas opens new branch", "SBIN.NS"))
        self.assertFalse(rel("Chinese state banks intervene to support yuan", "SBIN.NS"))
        self.assertFalse(rel("Several state banks reported stronger lending", "SBIN.NS"))
        self.assertFalse(rel("Central bank and state banks discuss liquidity", "SBIN.NS"))
        self.assertFalse(rel("State Bank of Cross Plains announces quarterly dividend", "SBIN.NS"))

        # Standalone Separately-Listed Subsidiaries (EXPECT FALSE without parent context)
        self.assertFalse(rel("SBI Life shares fall after quarterly results", "SBIN.NS"))
        self.assertFalse(rel("SBI Cards reports quarterly profit", "SBIN.NS"))
        self.assertFalse(rel("Brokerage upgrades SBI Life with target of Rs 1,800", "SBIN.NS"))
        self.assertFalse(rel("SBI Cards stock rises 5% on festive spending surge", "SBIN.NS"))

        # Material Subsidiary / Parent Corporate Transactions (EXPECT TRUE)
        self.assertTrue(rel("State Bank of India plans to sell stake in SBI Life", "SBIN.NS"))
        self.assertTrue(rel("SBI board considers stake sale in SBI Cards", "SBIN.NS"))

        # ─── Step 4 Adversarial Precision Tests ──────────────────────────────
        # EXPECT FALSE: Foreign SBI & Pure Macroeconomic Authorship Reports
        self.assertFalse(rel("SBI Holdings stock rises in Tokyo", "SBIN.NS"))
        self.assertFalse(rel("SBI Shinsei Bank reports earnings", "SBIN.NS"))
        self.assertFalse(rel("SBI VC Trade expands crypto service", "SBIN.NS"))
        self.assertFalse(rel("SBI Research forecasts India's GDP growth at 7%", "SBIN.NS"))
        self.assertFalse(rel("SBI Ecowrap expects inflation to moderate", "SBIN.NS"))
        self.assertFalse(rel("SBI Research analyses RBI monetary policy", "SBIN.NS"))
        self.assertFalse(rel("SBI Mutual Fund launches a new ETF", "SBIN.NS"))
        self.assertFalse(rel("SBI General Insurance launches a travel policy", "SBIN.NS"))

        # EXPECT TRUE: Core Corporate Actions & Material Events
        self.assertTrue(rel("SBI raises $250 million through green bonds", "SBIN.NS"))
        self.assertTrue(rel("SBI removes company from fraud list", "SBIN.NS"))
        self.assertTrue(rel("HC restrains SBI from acting on SARFAESI debt notice", "SBIN.NS"))
        self.assertTrue(rel("SBI among Motilal Oswal's top banking picks", "SBIN.NS"))
        self.assertTrue(rel("Company raises funding from SBI and HDFC Bank", "SBIN.NS"))
        self.assertTrue(rel("SBI Research report says bank credit growth supports SBI loan outlook", "SBIN.NS"))
        self.assertTrue(rel("SBI considers listing SBI General Insurance", "SBIN.NS"))
        self.assertTrue(rel("State Bank of India sells stake in SBI Mutual Fund", "SBIN.NS"))

        # Acronym Negatives (EXPECT FALSE)
        self.assertFalse(rel("Small Business Index (SBI) shows growth in manufacturing", "SBIN.NS"))
        self.assertFalse(rel("Study analyzes sterol biosynthesis inhibitor (SBI) resistance", "SBIN.NS"))

    def test_tcs_matching(self):
        """
        Validates contextual disambiguation for TCS.NS (Tata Consultancy Services Limited).
        Enforces:
        - Exclusion of Tax Collected at Source (TCS) in tax/remittance context
        - Exclusion of foreign tickers (The Container Store NYSE: TCS, Tecsys TSE: TCS, TCS Group Holding/Tinkoff)
        - Exclusion of foreign sports/auto clubs (Touring Club Suisse, Four Hills Ski Jumping 72. TCS)
        - Strict separation from other standalone Tata companies (Motors, Steel, Power, Tech, Elxsi)
        - Inclusion of genuine full-name, leadership, earnings, deals, disputes, workforce, and governance signals.
        """
        rel = self.fetcher.is_relevant_to_company

        # ─── 1. Tax Collected at Source Exclusions (EXPECT FALSE) ─────────────
        self.assertFalse(rel("TDS and TCS rules updated for FY24 foreign remittances", "TCS.NS"))
        self.assertFalse(rel("Govt collects Rs 25,000 crore via TCS on foreign remittances", "TCS.NS"))
        self.assertFalse(rel("Understanding TCS on overseas tour packages and credit cards", "TCS.NS"))
        self.assertFalse(rel("Higher TCS rate on LRS transactions to take effect from October", "TCS.NS"))
        self.assertFalse(rel("Buying US Bitcoin ETF in India: Understand all about TDS, TCS, capital gains tax", "TCS.NS"))
        self.assertFalse(rel("Will the Budget bring credit card international spends under TCS?", "TCS.NS"))

        # ─── 2. Foreign Tickers & Acronym Collisions (EXPECT FALSE) ───────────
        self.assertFalse(rel("The Container Store Group reports decline in quarterly retail sales", "TCS.NS"))
        self.assertFalse(rel("Analyzing Bed Bath & Beyond and The Container Store Group (NYSE:TCS)", "TCS.NS"))
        self.assertFalse(rel("Tecsys (TSE:TCS) Sets New 12-Month High at $35.48", "TCS.NS"))
        self.assertFalse(rel("Touring Club Suisse assists over 300,000 motorists in 2023", "TCS.NS"))
        self.assertFalse(rel("TCS leistet 2023 mehr Einsatze im Pannendienst", "TCS.NS"))
        self.assertFalse(rel("TCS Group Holding PLC files for delisting from London Stock Exchange", "TCS.NS"))
        self.assertFalse(rel("Tinkoff parent TCS Group shareholders approve redomiciliation", "TCS.NS"))
        self.assertFalse(rel("Trussville City Schools board reviews annual budget", "TCS.NS"))
        self.assertFalse(rel("72. TCS: Ryoyu Kobayashi wins qualification in Bischofshofen", "TCS.NS"))
        self.assertFalse(rel("Western Railway TCs nab commuter with fake AC pass", "TCS.NS"))
        self.assertFalse(rel("Copper concentrate TCs index falls amid smelter curbs", "TCS.NS"))

        # ─── 3. Other Standalone Tata Group Entities (EXPECT FALSE) ───────────
        self.assertFalse(rel("Tata Motors global wholesales rise 9% in third quarter", "TCS.NS"))
        self.assertFalse(rel("Tata Steel completes furnace overhaul at Jamshedpur plant", "TCS.NS"))
        self.assertFalse(rel("Tata Power signs agreement for 500MW solar project", "TCS.NS"))
        self.assertFalse(rel("Tata Technologies shares jump on EV engineering contract", "TCS.NS"))
        self.assertFalse(rel("Tata Elxsi reports 3% sequential revenue growth in Q3", "TCS.NS"))
        self.assertFalse(rel("Tata Consumer Products acquires Capital Foods for Rs 5,100 crore", "TCS.NS"))
        self.assertFalse(rel("Tata Communications expands enterprise cloud cybersecurity portfolio", "TCS.NS"))

        # ─── 4. Positive Full Name & Leadership Context (EXPECT TRUE) ─────────
        self.assertTrue(rel("Tata Consultancy Services reports 2% rise in net profit", "TCS.NS"))
        self.assertTrue(rel("Tata Consultancy Services wins multi-million dollar cloud deal with UK retailer", "TCS.NS"))
        self.assertTrue(rel("टाटा कंसल्टेंसी सर्विसेज ने अंतरिम डिविडेंड की घोषणा की", "TCS.NS"))
        self.assertTrue(rel("TCS CEO K Krithivasan sees revival in BFSI tech spending", "TCS.NS"))
        self.assertTrue(rel("Krithivasan says TCS is positioning for generative AI leadership", "TCS.NS"))

        # ─── 5. Positive Bare TCS with Contextual Families (EXPECT TRUE) ──────
        # A. Earnings & Market Identity
        self.assertTrue(rel("TCS Q3 net profit rises to Rs 11,058 crore", "TCS.NS"))
        self.assertTrue(rel("TCS shares gain 3% after strong quarterly earnings", "TCS.NS"))
        self.assertTrue(rel("TCS declares interim dividend of Rs 9 per share, sets record date", "TCS.NS"))
        self.assertTrue(rel("Brokerage upgrades TCS to buy with target price of Rs 4,200", "TCS.NS"))
        self.assertTrue(rel("Mcap of top-10 firms declines; TCS and HDFC Bank major laggards", "TCS.NS"))
        self.assertTrue(rel("experts predict muted results for tcs in q3", "TCS.NS"))
        self.assertTrue(rel("Stocks to watch today: L&T, Voltas, TCS, PNB in focus", "TCS.NS"))

        # B. IT Services Deals & Enterprise Partnerships
        self.assertTrue(rel("TCS signs 15-year strategic partnership extension with Aviva in UK", "TCS.NS"))
        self.assertTrue(rel("TCS and Finland partner to build modern post-trade platform", "TCS.NS"))
        self.assertTrue(rel("TCS partners with AWS to roll out enterprise generative AI solutions", "TCS.NS"))
        self.assertTrue(rel("TCS bags $1 billion deal from UK client", "TCS.NS"))
        self.assertTrue(rel("TCS Ranked No. 1 in Customer Satisfaction in France", "TCS.NS"))

        # C. Contract Disputes & Technical Glitches
        self.assertTrue(rel("Oxford University ends ties with TCS citing technical glitches in admission tests", "TCS.NS"))
        self.assertTrue(rel("University severs ties with TCS following entrance test debacle", "TCS.NS"))
        self.assertTrue(rel("Oxford Gave A Big Blow To TCS, Broke Partnership Over Online Entrance Exams", "TCS.NS"))

        # D. Workforce, Labour & Office Policies
        self.assertTrue(rel("Maharashtra labour ministry issues notice to TCS over forced transfers", "TCS.NS"))
        self.assertTrue(rel("TCS stopped pay of 900 employees, forced transfers of 2000 workers", "TCS.NS"))
        self.assertTrue(rel("TCS looks to double staff in France over next three years", "TCS.NS"))
        self.assertTrue(rel("TCS links employee promotions to return-to-office mandate", "TCS.NS"))
        self.assertTrue(rel("TCS headcount drops by 5,600 in third quarter", "TCS.NS"))

        # E. Governance & Leadership Transitions
        self.assertTrue(rel("Former TCS executive director Phiroz Vandrevala passes away at 70", "TCS.NS"))
        self.assertTrue(rel("TCS independent director Daniel Hughes Callahan term ends", "TCS.NS"))
        self.assertTrue(rel("TCS SVP Dinanath Kholkar resigns", "TCS.NS"))
        self.assertTrue(rel("JNTU confers honorary doctorate on V. Rajanna, TCS President", "TCS.NS"))

        # G. Physical Corporate Facilities & Regional Expansion (EXPECT TRUE)
        self.assertTrue(rel("TCS Opens New Delivery Centre in France", "TCS.NS"))
        self.assertTrue(rel("TCS Inaugurates Oman Office: Expanding Middle East Presence", "TCS.NS"))
        self.assertTrue(rel("TCS planning to inaugurate its Vizag office within three months", "TCS.NS"))
        self.assertTrue(rel("TCS setting up 37-acre campus in Kochi", "TCS.NS"))

        # H. Telecom & BSNL Tender Decisions (EXPECT TRUE)
        self.assertTrue(rel("TCS skips BSNL 5G tender; Tejas, Lekha, Galore only bidders", "TCS.NS"))
        self.assertTrue(rel("TCS-CDoT consortium skips BSNL's 5G tender for Delhi-NCR", "TCS.NS"))
        self.assertTrue(rel("TCS wins contract for BSNL 4G sites rollout across India", "TCS.NS"))

        # I. Workforce Policy & Compensation (EXPECT TRUE)
        self.assertTrue(rel("TCS rolls out new guidelines for WFO exceptions", "TCS.NS"))
        self.assertTrue(rel("TCS ties variable pay to office attendance policy", "TCS.NS"))
        self.assertTrue(rel("TCS further tightens its work from office policy", "TCS.NS"))

        # J. Corporate & Regulatory Exposure (EXPECT TRUE)
        self.assertTrue(rel("Our dependence on H-1B visa is limited: TCS chief", "TCS.NS"))
        self.assertTrue(rel("TCS, Infosys set to gain from sliding rupee: Moody's", "TCS.NS"))

        # K. Strategic Alliances & AI Business Units (EXPECT TRUE)
        self.assertTrue(rel("TCS partners with NVIDIA to build AI solutions for telcos", "TCS.NS"))
        self.assertTrue(rel("FICO Partners with TCS to Deliver Major Efficiency Gains", "TCS.NS"))

        # ─── 6. Additional Adversarial Negative Exclusions (EXPECT FALSE) ────
        self.assertFalse(rel("Interview with musician ahead of TCS Ruhaniyat 2025 in Bengaluru", "TCS.NS"))
        self.assertFalse(rel("TCS Group Berhad bags RM100m construction contract in Malaysia", "TCS.NS"))
        self.assertFalse(rel("Town Centre Securities PLC announces half-year financial results", "TCS.NS"))
        self.assertFalse(rel("73. TCS: Stefan Kraft wins ski jumping event in Oberstdorf", "TCS.NS"))
        self.assertFalse(rel("Local NGO announces TCS free meal program for underprivileged", "TCS.NS"))

    def test_infy_matching(self):
        rel = self.fetcher.is_relevant_to_company

        # ─── 1. Definitive Positive Corporate Multi-Word & Executive Cases ───
        self.assertTrue(rel("Infosys Limited reports 7.3% decline in Q3 profit to Rs 6,106 crore", "INFY.NS"))
        self.assertTrue(rel("Infosys Ltd signs multi-year deal with global enterprise client", "INFY.NS"))
        self.assertTrue(rel("Infosys BPM expands delivery operations in Europe", "INFY.NS"))
        self.assertTrue(rel("Infosys Finacle selected by major Australian bank for digital banking", "INFY.NS"))
        self.assertTrue(rel("Infosys CEO Salil Parekh sees accelerating traction in GenAI pipeline", "INFY.NS"))
        self.assertTrue(rel("Nandan Nilekani highlights Infosys AI transformation and cloud strategy", "INFY.NS"))

        # ─── 2. Financial Shorthand Infy / INFY with Market Context ──────────
        self.assertTrue(rel("Sensex nears 73k as Infy, TCS stocks rally post earnings", "INFY.NS"))
        self.assertTrue(rel("Street unaffected by Infy's guidance cut; tech stocks charge up", "INFY.NS"))
        self.assertTrue(rel("Selling pressure in IT stocks ahead of Infy, TCS Q3 results", "INFY.NS"))
        self.assertTrue(rel("Short Call: Perils of buying expensive stocks, Infy Q3 preview", "INFY.NS"))
        self.assertTrue(rel("MC Pro Inside Edge: Last-minute mystery buyer in Infy", "INFY.NS"))
        self.assertTrue(rel("Infy soars 7%, TCS up 5% post Q3 results announcement", "INFY.NS"))

        # ─── 3. Vernacular Devanagari Corporate Headlines ─────────────────────
        self.assertTrue(rel("इंफोसिस का मुनाफा 7.3 फीसदी गिरकर 6,106 करोड़ रुपये रहा", "INFY.NS"))
        self.assertTrue(rel("जेपी मॉर्गन ने इंफोसिस की रेटिंग बढ़ाई, टारगेट प्राइस में इजाफा", "INFY.NS"))

        # ─── 4. Founder Narayana Murthy with Corporate Performance / Earnings ───
        self.assertTrue(rel("Narayana Murthy on Infosys corporate governance and Q3 earnings performance", "INFY.NS"))
        self.assertTrue(rel("Narayana Murthy comments on Infosys quarterly profit growth and revenue", "INFY.NS"))

        # ─── 5. Adversarial Negative Non-Corporate Founder & Lifestyle Cases ──
        self.assertFalse(rel("Narayana Murthy gifts Rs 240 crore Infosys shares to four-month-old grandson", "INFY.NS"))
        self.assertFalse(rel("Narayana Murthy defends 70-hour work week remark: 'Lot of western friends called...'", "INFY.NS"))
        self.assertFalse(rel("Narayana Murthy regrets not letting Sudha Murty join Infosys: 'I was wrongly idealistic'", "INFY.NS"))
        self.assertFalse(rel("Rohan Murty: Sudha Murty and Narayana Murthy's son carving path beyond shadows of Infosys", "INFY.NS"))
        self.assertFalse(rel("Sudha Murty reveals why she spells her surname differently from Narayana Murthy", "INFY.NS"))
        self.assertFalse(rel("Narayana Murthy again defends 70-hr work week advice for Indian youth", "INFY.NS"))
        self.assertFalse(rel("Infosys Co-founder Narayana Murthy Flies Economy Class To Bengaluru; Co-passenger's Post Goes Viral", "INFY.NS"))
        self.assertFalse(rel("Startup CEO shares meeting with Infosys co-founder Narayana Murthy on a flight; shares his gem advice", "INFY.NS"))
        self.assertFalse(rel("Chitra Banerjee profiles power couple Sudha and Narayana Murthy in 'An Uncommon Love'", "INFY.NS"))

        # ─── 6. Adversarial Negative Generic Campus & Sector Noise ────────────
        self.assertFalse(rel("Top 10 colleges announce Infosys campus recruitment drive for freshers", "INFY.NS"))
        self.assertFalse(rel("Tech Money For Social Good: From Kris Gopalakrishnan To K Dinesh, Azim Premji, Nandan Nilekani", "INFY.NS"))

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

    def test_reliance_disambiguation_positive_and_negative_matrix(self):
        """
        Validates the hardened RELIANCE.NS disambiguation rules:
        - True: Reliance Industries, Retail, Jio, Mukesh Ambani, RIL
        - False: ADAG entities (Power, Infra, Capital, Comms, Naval), Bank, generic grammatical 'reliance on'
        - False: Collision tests where negative entities contain strong financial keywords (profit, shares, loan)
        """
        rel = self.fetcher.is_relevant_to_company

        # ── Expected TRUE (RIL Entity Family) ─────────────────────────────────
        self.assertTrue(rel("Reliance Industries reports quarterly profit growth", "RELIANCE.NS"))
        self.assertTrue(rel("Reliance Industries Ltd announces Q3 results", "RELIANCE.NS"))
        self.assertTrue(rel("RIL shares rise after earnings beat estimates", "RELIANCE.NS"))
        self.assertTrue(rel("Reliance Jio expands 5G network", "RELIANCE.NS"))
        self.assertTrue(rel("Jio Platforms reports subscriber growth", "RELIANCE.NS"))
        self.assertTrue(rel("Reliance Retail Ventures raises capital", "RELIANCE.NS"))
        self.assertTrue(rel("Mukesh Ambani addresses Reliance AGM", "RELIANCE.NS"))

        # ── Expected FALSE (ADAG / Unrelated Entities / Grammatical) ─────────
        self.assertFalse(rel("Reliance Power reports quarterly results", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Power shares surge 12%", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Infrastructure wins metro contract", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Infra bags new infrastructure order", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Capital insolvency proceedings continue", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Communications lenders meet", "RELIANCE.NS"))
        self.assertFalse(rel("RCom lenders approve resolution plan", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Bank Pace wins race", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Home Finance resolution approved", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Nippon Life announces new fund", "RELIANCE.NS"))
        self.assertFalse(rel("India's reliance on coal remains high", "RELIANCE.NS"))
        self.assertFalse(rel("Europe reduces reliance on Russian gas", "RELIANCE.NS"))
        self.assertFalse(rel("Growing reliance on AI raises concerns", "RELIANCE.NS"))

        # ── Financial Keyword Collision Tests (Must remain False) ─────────────
        self.assertFalse(rel("Reliance Power profit rises 30%", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Infrastructure shares rally", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Capital quarterly results", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Bank reports loan growth", "RELIANCE.NS"))

    def test_mukesh_ambani_policy_corporate_positives_and_listicle_negatives(self):
        """
        Validates the refined Mukesh Ambani policy:
        - Positives: Requires co-occurring RIL/Jio/Retail/capex/earnings/business context.
        - Negatives: Explicitly rejects wealth listicles, celebrity, lifestyle, and political praise.
        """
        rel = self.fetcher.is_relevant_to_company

        # ── Step 4 Positives (Corporate / Economic Context) ───────────────────
        self.assertTrue(rel("Mukesh Ambani outlines Reliance Industries capex plans", "RELIANCE.NS"))
        self.assertTrue(rel("Mukesh Ambani announces Jio expansion at Reliance AGM", "RELIANCE.NS"))
        self.assertTrue(rel("Mukesh Ambani says Reliance Retail growth remains strong", "RELIANCE.NS"))
        self.assertTrue(rel("RIL chairman Mukesh Ambani discusses new energy investment", "RELIANCE.NS"))
        self.assertTrue(rel("Mukesh Ambani announces Reliance Jio IPO plans", "RELIANCE.NS"))
        self.assertTrue(rel("Mukesh Ambani addresses shareholders after Reliance quarterly results", "RELIANCE.NS"))

        # ── Step 3 Exclusions (ADAG / Non-RIL Reliance Entities) ─────────────────
        self.assertFalse(rel("Reliance Power share price crashes 5%", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Infrastructure wins arbitration award", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Capital resolution plan approved by NCLT", "RELIANCE.NS"))
        self.assertFalse(rel("Reliance Communications debt restructuring", "RELIANCE.NS"))
        self.assertFalse(rel("India must reduce reliance on crude oil imports", "RELIANCE.NS"))



    def test_utc_to_ist_conversion(self):
        # 2024-01-15 09:30:00 UTC == 2024-01-15 15:00:00 IST (+5:30)
        # parse_gdelt_timestamp returns a 4-tuple: (source_timestamp, seen_at_iso, ist_dt, date_only)
        src_ts, seen_at, ist_dt, date_only = self.fetcher.parse_gdelt_timestamp("20240115093000")
        self.assertEqual(src_ts, "20240115093000")
        self.assertFalse(date_only, "Full 14-digit timestamp must set date_only=False")
        self.assertEqual(ist_dt.year, 2024)
        self.assertEqual(ist_dt.month, 1)
        self.assertEqual(ist_dt.day, 15)
        self.assertEqual(ist_dt.hour, 15)
        self.assertEqual(ist_dt.minute, 0)
        self.assertEqual(ist_dt.second, 0, "Seconds must be preserved in UTC->IST conversion")
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

    def test_date_only_YYYYMMDD_raises_low_precision(self):
        """YYYYMMDD timestamps must raise LowPrecisionTimestampError (ValueError subclass)."""
        from sentiment_generator.news_fetcher import LowPrecisionTimestampError
        with self.assertRaises(LowPrecisionTimestampError):
            self.fetcher.parse_gdelt_timestamp("20240115")
        # Counter must be incremented inside parse_gdelt_timestamp
        self.assertEqual(self.fetcher.stats["articles_rejected_low_precision_timestamp"], 1)

    def test_11_digit_timestamp_raises_plain_value_error(self):
        """9–13 digit counts are not date-only (8) nor canonical full (14) — plain ValueError."""
        from sentiment_generator.news_fetcher import LowPrecisionTimestampError
        with self.assertRaises(ValueError) as ctx:
            self.fetcher.parse_gdelt_timestamp("20240115093")  # 11 digits
        # Must NOT be the LowPrecisionTimestampError subclass
        self.assertNotIsInstance(ctx.exception, LowPrecisionTimestampError)

    def test_over14_digit_timestamp_raises_value_error(self):
        """
        A string that yields >14 numeric digits after stripping non-numeric characters
        must be rejected with ValueError, NOT silently parsed by taking the first 14 digits.

        Example: "20260821103045ABC999" strips to "20260821103045999" (17 digits).
        The old defensive path would have silently accepted "20260821103045" from that.
        The strict path must refuse it, because the extra digits come from a corrupted or
        non-standard input and fabricating a timestamp from them is not safe.
        """
        from sentiment_generator.news_fetcher import LowPrecisionTimestampError
        # Non-numeric suffix that strips to 17 digits
        corrupt_ts = "20260821103045ABC999"
        with self.assertRaises(ValueError) as ctx:
            self.fetcher.parse_gdelt_timestamp(corrupt_ts)
        self.assertNotIsInstance(ctx.exception, LowPrecisionTimestampError,
                                 "Over-14-digit rejection must be plain ValueError, not LowPrecisionTimestampError")
        # Confirm the error message mentions the digit count so it's diagnosable
        self.assertIn("17", str(ctx.exception))

        # Verify that a plain 15-digit all-numeric string (no letter stripping) is also rejected
        self.fetcher2 = type(self.fetcher)(trading_calendar=self.calendar)
        with self.assertRaises(ValueError):
            self.fetcher2.parse_gdelt_timestamp("202601011030459")  # 15 digits

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
                datetime.datetime(2024, 1, 15, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 15, 23, 59, 59, tzinfo=self.tz_utc),
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
                    datetime.datetime(2024, 1, 15, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 15, 23, 59, 59, tzinfo=self.tz_utc),
                )

    @patch("requests.Session.get")
    def test_budget_exhaustion_raises_runtime_error(self, mock_get):
        """A zeroed-out request budget must immediately raise RuntimeError and increment the counter."""
        with patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                self.fetcher.fetch_gdelt_window(
                    "RELIANCE.NS",
                    datetime.datetime(2024, 1, 10, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 10, 23, 59, 59, tzinfo=self.tz_utc),
                    _request_budget=[0],   # pre-exhausted budget
                )
        self.assertEqual(self.fetcher.stats["pagination_budget_exhausted"], 1)
        mock_get.assert_not_called()  # No HTTP request should be made

    def test_final_dates_strictly_in_nse_calendar(self):
        # Saturday 13-Jan maps to Monday 15-Jan
        dt = datetime.datetime(2024, 1, 13, 14, 0, 0, tzinfo=self.tz_ist)
        mapped_td = self.fetcher.map_to_nse_trading_session(dt)
        self.assertIn(mapped_td, self.calendar)
        self.assertEqual(mapped_td, "2024-01-15")

    def test_empty_url_and_empty_headline_both_preserved(self):
        """Articles with BOTH empty URL and empty headline must never be collapsed together."""
        a1 = _make_article("RELIANCE.NS", "2024-01-10", "", "")
        a2 = _make_article("RELIANCE.NS", "2024-01-10", "", "")
        deduped = self.fetcher._deduplicate_articles([a1, a2])
        self.assertEqual(len(deduped), 2,
                         "Records with no URL and no headline must be kept independently")
        # Neither should be counted as a duplicate
        self.assertEqual(self.fetcher.stats["duplicates_removed"], 0)

    def test_www_prefix_domain_normalisation(self):
        """
        Domain normalisation must strip 'www.' as a prefix (not character-set lstrip).
        'web.ndtv.com' must not become 'eb.ndtv.com'.
        """
        url_www = "https://www.reuters.com/article/ril"
        url_web = "https://web.ndtv.com/article/ril"
        # Make two articles with empty URLs (so headline-fallback path is exercised)
        # and verify domain extraction via direct normalize_url
        norm_www = NewsFetcher.normalize_url(url_www)
        norm_web = NewsFetcher.normalize_url(url_web)
        # reuters.com without www
        import urllib.parse
        netloc_www = urllib.parse.urlparse(norm_www).netloc
        netloc_web = urllib.parse.urlparse(norm_web).netloc
        self.assertEqual(netloc_www, "www.reuters.com",
                         "normalize_url must preserve www in netloc (lowercased)")
        self.assertEqual(netloc_web, "web.ndtv.com",
                         "web.ndtv.com must not be mutilated by lstrip")
        # Now test that the dedupe key uses startswith stripping, not lstrip
        a_web = _make_article("RELIANCE.NS", "2024-01-10", "RIL Q3 profit rises", url_web)
        key = self.fetcher._article_dedupe_key(a_web)
        # URL is non-empty -> URL-based key, so domain stripping is only relevant in fallback.
        # Test the fallback path directly with empty-URL articles:
        a_no_url = _make_article("RELIANCE.NS", "2024-01-10", "RIL Q3 profit rises", "")
        key_no_url = self.fetcher._article_dedupe_key(a_no_url)
        # src_domain should be empty string when URL is empty
        self.assertTrue(key_no_url[2].endswith("::"),
                        f"Empty-URL fallback key should end with '::' (empty domain), got {key_no_url[2]!r}")

    def test_tracking_param_only_urls_deduplicate_via_url_key(self):
        """Two URLs differing only in tracking params must share a canonical URL key."""
        a1 = _make_article("RELIANCE.NS", "2024-01-10", "RIL result",
                            "https://et.com/ril?utm_source=twitter&utm_medium=social")
        a2 = _make_article("RELIANCE.NS", "2024-01-10", "RIL result",
                            "https://et.com/ril?gclid=abc123")
        key1 = self.fetcher._article_dedupe_key(a1)
        key2 = self.fetcher._article_dedupe_key(a2)
        self.assertEqual(key1, key2, "Tracking-param-only URLs must produce identical canonical keys")
        deduped = self.fetcher._deduplicate_articles([a1, a2])
        self.assertEqual(len(deduped), 1)

    def test_sunday_news_maps_to_next_monday(self):
        """Sunday news must map forward to Monday (next trading session)."""
        # 2024-01-14 is a Sunday
        dt = datetime.datetime(2024, 1, 14, 10, 0, 0, tzinfo=self.tz_ist)
        result = self.fetcher.map_to_nse_trading_session(dt)
        self.assertEqual(result, "2024-01-15",
                         "Sunday news must forward to the following Monday")

    def test_gdelt_rate_limiter_monotonic_governor(self):
        """GDELTRateLimiter must enforce spacing using Lock + monotonic time."""
        from sentiment_generator.news_fetcher import GDELTRateLimiter
        import time
        limiter = GDELTRateLimiter(min_interval=0.05)
        t0 = time.monotonic()
        limiter.wait()
        limiter.wait()
        t1 = time.monotonic()
        self.assertGreaterEqual(t1 - t0, 0.045, "Rate limiter must enforce minimum interval")

    def test_concurrent_workers_strictly_spaced(self):
        """Multiple concurrent threads calling GDELTRateLimiter.wait() must never fire within min_interval."""
        from sentiment_generator.news_fetcher import GDELTRateLimiter
        import concurrent.futures
        import time

        interval = 0.10
        limiter = GDELTRateLimiter(min_interval=interval)
        num_threads = 4
        execution_times = []
        lock = threading.Lock()

        def worker_call():
            limiter.wait()
            ts = time.monotonic()
            with lock:
                execution_times.append(ts)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker_call) for _ in range(num_threads)]
            concurrent.futures.wait(futures)

        sorted_times = sorted(execution_times)
        self.assertEqual(len(sorted_times), num_threads)
        for i in range(1, len(sorted_times)):
            diff = sorted_times[i] - sorted_times[i - 1]
            self.assertGreaterEqual(
                diff, interval * 0.80,
                f"Consecutive requests at index {i-1} and {i} were spaced by only {diff:.4f}s (< {interval*0.80:.4f}s)"
            )

    def test_rate_limiter_lock_released_during_sleep(self):
        """Lock must be held only to reserve target timestamp slot, not during sleep duration."""
        from sentiment_generator.news_fetcher import GDELTRateLimiter
        import time

        limiter = GDELTRateLimiter(min_interval=0.2)
        # Thread 1 reserves slot and sleeps
        t = threading.Thread(target=limiter.wait)
        t.start()
        time.sleep(0.01)

        # Thread 2 should be able to acquire limiter._lock immediately (< 0.05s)
        # because Thread 1 released the lock before sleeping.
        t_acq_start = time.monotonic()
        acquired = limiter._lock.acquire(timeout=0.05)
        t_acq_end = time.monotonic()
        if acquired:
            limiter._lock.release()

        t.join()
        self.assertTrue(acquired, "Lock should not be held while a worker is sleeping")
        self.assertLess(t_acq_end - t_acq_start, 0.05, "Lock acquisition must be near-instantaneous")

    @patch("requests.Session.get")
    def test_missing_title_and_url_telemetry(self, mock_get):
        """Articles with empty title or missing URL must increment dedicated telemetry stats."""
        mock_200 = MagicMock(status_code=200, text='{"articles": ['
            '{"title": "", "url": "https://sample.com/1", "seendate": "20240115T100000Z"},'
            '{"title": "   ", "url": "https://sample.com/2", "seendate": "20240115T100000Z"},'
            '{"title": "Reliance Industries Q3 revenue up 10%", "url": "", "seendate": "20240115T100000Z"}'
        ']}')
        mock_200.json.return_value = {"articles": [
            {"title": "", "url": "https://sample.com/1", "seendate": "20240115T100000Z"},
            {"title": "   ", "url": "https://sample.com/2", "seendate": "20240115T100000Z"},
            {"title": "Reliance Industries Q3 revenue up 10%", "url": "", "seendate": "20240115T100000Z"}
        ]}
        mock_get.return_value = mock_200
        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "RELIANCE.NS",
                datetime.datetime(2024, 1, 15, 0, 0, 0, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 15, 23, 59, 59, tzinfo=self.tz_utc)
            )
        diag = self.fetcher.get_diagnostics()
        self.assertEqual(diag["articles_rejected_missing_title"], 2,
                         "Empty/whitespace titles must increment articles_rejected_missing_title")
        self.assertEqual(diag["articles_missing_url"], 1,
                         "Empty URL must increment articles_missing_url")
        self.assertEqual(len(arts), 1, "Only the valid article with headline should be accepted")

    @patch("requests.Session.get")
    def test_first_429_retries_once_and_succeeds(self, mock_get):
        """First 429 response retries once and successfully returns articles on second request."""
        mock_429 = MagicMock(status_code=429, headers={}, text="Rate limit exceeded")
        mock_200 = MagicMock(status_code=200, text='{"articles": [{"title": "Reliance Q3 profit jumps", "url": "https://et.com/ril", "seendate": "20240115T100000Z"}]}')
        mock_200.json.return_value = {"articles": [{"title": "Reliance Q3 profit jumps", "url": "https://et.com/ril", "seendate": "20240115T100000Z"}]}
        mock_get.side_effect = [mock_429, mock_200]

        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "RELIANCE.NS",
                datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
            )
        self.assertEqual(len(arts), 1)
        self.assertEqual(mock_get.call_count, 2, "First 429 must trigger exactly one retry request")

    @patch("requests.Session.get")
    def test_second_consecutive_429_raises_gdelt_rate_limit_exhausted_and_no_third_call(self, mock_get):
        """Second consecutive 429 response raises GDELTRateLimitExhausted and executes no 3rd request."""
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted
        mock_429 = MagicMock(status_code=429, headers={}, text="Rate limit exceeded")
        mock_get.side_effect = [mock_429, mock_429, mock_429]

        with patch("time.sleep"):
            with self.assertRaises(GDELTRateLimitExhausted) as ctx:
                self.fetcher.fetch_gdelt_window(
                    "RELIANCE.NS",
                    datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
                )
        self.assertIn("HTTP 429 Rate Limit", str(ctx.exception))
        self.assertEqual(mock_get.call_count, 2, "Fail-fast must stop after exactly 2 requests (1 retry)")

    @patch("requests.Session.get")
    def test_http_200_textual_rate_limit_retries_once_and_succeeds(self, mock_get):
        """HTTP-200 with textual rate limit message retries once and successfully returns on 2nd attempt."""
        mock_200_rl = MagicMock(status_code=200, text="Please limit requests to one every 5 seconds, switch to our ngrams dataset, or contact kalev.leetaru5@gmail.com")
        mock_200_ok = MagicMock(status_code=200, text='{"articles": [{"title": "TCS wins major deal", "url": "https://et.com/tcs", "seendate": "20240115T100000Z"}]}')
        mock_200_ok.json.return_value = {"articles": [{"title": "TCS wins major deal", "url": "https://et.com/tcs", "seendate": "20240115T100000Z"}]}
        mock_get.side_effect = [mock_200_rl, mock_200_ok]

        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "TCS.NS",
                datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
            )
        self.assertEqual(len(arts), 1)
        self.assertEqual(mock_get.call_count, 2, "HTTP-200 textual rate limit must trigger exactly one retry")

    @patch("requests.Session.get")
    def test_http_200_textual_rate_limit_exhaustion_raises_gdelt_rate_limit_exhausted(self, mock_get):
        """Second consecutive HTTP-200 textual rate limit raises GDELTRateLimitExhausted and halts."""
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted
        mock_200_rl = MagicMock(status_code=200, text="Please limit requests to one every 5 seconds, switch to our ngrams dataset, or contact kalev.leetaru5@gmail.com")
        mock_get.side_effect = [mock_200_rl, mock_200_rl, mock_200_rl]

        with patch("time.sleep"):
            with self.assertRaises(GDELTRateLimitExhausted) as ctx:
                self.fetcher.fetch_gdelt_window(
                    "TCS.NS",
                    datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
                )
        self.assertIn("Rate Limit", str(ctx.exception))
        self.assertEqual(mock_get.call_count, 2, "Must stop after 2 attempts (1 initial + 1 retry)")

    def test_is_gdelt_rate_limit_text_narrow_matching(self):
        """_is_gdelt_rate_limit_text only matches specific GDELT throttle phrases, not generic service outages."""
        from sentiment_generator.news_fetcher import _is_gdelt_rate_limit_text

        # Valid GDELT throttle phrases
        self.assertTrue(_is_gdelt_rate_limit_text("Please limit requests to one every 5 seconds, switch to our ngrams dataset"))
        self.assertTrue(_is_gdelt_rate_limit_text("Limit requests to one every 5 seconds"))
        self.assertTrue(_is_gdelt_rate_limit_text("Please switch to our ngrams dataset for bulk analysis"))
        self.assertTrue(_is_gdelt_rate_limit_text("HTTP 429: Rate limit exceeded"))

        # Generic upstream outages or errors must NOT be classified as rate limits
        self.assertFalse(_is_gdelt_rate_limit_text("Service Unavailable"))
        self.assertFalse(_is_gdelt_rate_limit_text("503 Service Temporarily Unavailable"))
        self.assertFalse(_is_gdelt_rate_limit_text("Internal Server Error"))
        self.assertFalse(_is_gdelt_rate_limit_text("Gateway Timeout"))
        self.assertFalse(_is_gdelt_rate_limit_text("Database connection failed"))
        self.assertFalse(_is_gdelt_rate_limit_text(""))
        self.assertFalse(_is_gdelt_rate_limit_text(None))

    @patch("requests.Session.get")
    def test_generic_service_unavailable_text_routes_through_transient_error_path(self, mock_get):
        """Generic 'Service Unavailable' text in HTTP 200/503 raises standard RuntimeError, NOT GDELTRateLimitExhausted."""
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted
        import json
        mock_503 = MagicMock(status_code=200, text="Service Unavailable: upstream database reconnecting")
        mock_503.json.side_effect = json.JSONDecodeError("Expecting value", "doc", 0)
        mock_get.return_value = mock_503

        with patch("time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                self.fetcher.fetch_gdelt_window(
                    "RELIANCE.NS",
                    datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
                )
            self.assertNotIsInstance(ctx.exception, GDELTRateLimitExhausted)
            self.assertIn("JSON parse error", str(ctx.exception))

    @patch("requests.Session.get")
    def test_genuine_empty_http_200_returns_zero_articles(self, mock_get):
        """Truly empty HTTP-200 response body immediately returns zero articles without retries."""
        mock_200_empty = MagicMock(status_code=200, text="")
        mock_get.return_value = mock_200_empty

        with patch("time.sleep"):
            arts = self.fetcher.fetch_gdelt_window(
                "INFY.NS",
                datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
            )
        self.assertEqual(len(arts), 0)
        self.assertEqual(mock_get.call_count, 1, "Truly empty HTTP 200 must succeed immediately on first call")

    @patch("requests.Session.get")
    def test_exhausted_429_raises_gdelt_rate_limit_exhausted(self, mock_get):
        """Exhausted 429 retries must raise GDELTRateLimitExhausted."""
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted
        mock_429 = MagicMock(status_code=429, headers={}, text="Rate limit exceeded")
        mock_get.return_value = mock_429

        with patch("time.sleep"):
            with self.assertRaises(GDELTRateLimitExhausted) as ctx:
                self.fetcher.fetch_gdelt_window(
                    "RELIANCE.NS",
                    datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
                )
        self.assertIn("HTTP 429 Rate Limit", str(ctx.exception))
        diag = self.fetcher.get_diagnostics()
        self.assertGreater(diag["rate_limit_responses"], 0)
        self.assertGreater(diag["failed_requests"], 0)

    @patch("requests.Session.get")
    def test_ordinary_500_raises_generic_runtime_error(self, mock_get):
        """Ordinary non-429 failures must raise generic RuntimeError, NOT GDELTRateLimitExhausted."""
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted
        mock_500 = MagicMock(status_code=500, headers={}, text="Internal Server Error")
        mock_get.return_value = mock_500

        with patch("time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                self.fetcher.fetch_gdelt_window(
                    "RELIANCE.NS",
                    datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=self.tz_utc),
                    datetime.datetime(2024, 1, 31, 23, 59, 59, tzinfo=self.tz_utc)
                )
            # Ensure it is NOT a GDELTRateLimitExhausted instance
            self.assertNotIsInstance(ctx.exception, GDELTRateLimitExhausted)
            self.assertIn("HTTP 500", str(ctx.exception))

    @patch("sentiment_generator.generate_sentiment.time.sleep")
    @patch("sentiment_generator.generate_sentiment.save_raw_articles", return_value=0)
    @patch("sentiment_generator.generate_sentiment.record_fetch_period")
    @patch("sentiment_generator.generate_sentiment.get_period_status", return_value=None)
    @patch("sentiment_generator.generate_sentiment.set_circuit_breaker_state")
    @patch("sentiment_generator.generate_sentiment.get_circuit_breaker_state", return_value=None)
    def test_circuit_breaker_halts_further_period_requests(self, mock_get_cb, mock_set_cb, mock_status, mock_record, mock_save, mock_sleep):
        """When GDELTRateLimitExhausted occurs, the circuit breaker opens and stops later periods."""
        from sentiment_generator.generate_sentiment import process_ticker_news_fetch
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted

        circuit_breaker = threading.Event()
        mock_fetcher = MagicMock()
        err_msg = "GDELT API request failed for TEST_CB.NS (20240201000000 to 20240229235959): HTTP 429 Rate Limit (exceeded 5 retries)"
        err = GDELTRateLimitExhausted(err_msg)

        # Month 1 succeeds, Month 2 exhausts 429, Month 3 should NEVER be called
        mock_fetcher.fetch_gdelt_window.side_effect = [
            [],   # Month 1: success (empty)
            err,  # Month 2: 429 exhausted
            []    # Month 3: should NOT be called
        ]

        m1 = (datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 1, 31, tzinfo=self.tz_utc), "2024-01-01", "2024-01-31")
        m2 = (datetime.datetime(2024, 2, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 2, 29, tzinfo=self.tz_utc), "2024-02-01", "2024-02-29")
        m3 = (datetime.datetime(2024, 3, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 3, 31, tzinfo=self.tz_utc), "2024-03-01", "2024-03-31")

        res = process_ticker_news_fetch(
            ticker="TEST_CB.NS",
            month_ranges=[m1, m2, m3],
            fetcher=mock_fetcher,
            force_refetch=True,
            circuit_breaker_event=circuit_breaker
        )

        self.assertTrue(circuit_breaker.is_set(), "Circuit breaker event must be set upon GDELTRateLimitExhausted")
        self.assertEqual(mock_fetcher.fetch_gdelt_window.call_count, 2, "Month 3 must NOT be called after circuit breaker trips")
        self.assertEqual(res["failed_periods"], 1)
        self.assertEqual(res["periods_fetched"], 1)
        mock_record.assert_any_call("TEST_CB.NS", "2024-02-01", "2024-02-29", status="failed", article_count=0, error_message=err_msg)

    @patch("sentiment_generator.generate_sentiment.get_circuit_breaker_state")
    def test_active_cooldown_prevents_all_http_calls(self, mock_get_cb):
        """Active persistent cooldown prevents Phase 1 from executing any GDELT requests."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        cooldown_until = now_utc + datetime.timedelta(minutes=45)
        mock_get_cb.return_value = {
            "breaker_opened_at": now_utc.isoformat(),
            "cooldown_until": cooldown_until.isoformat(),
            "reason": "HTTP 429 Rate Limit",
            "updated_at": now_utc.isoformat()
        }

        # Simulate pre-flight check in Phase 1
        breaker_state = mock_get_cb()
        cooldown_active = False
        if breaker_state:
            cooldown_until_dt = datetime.datetime.fromisoformat(breaker_state["cooldown_until"])
            now_check = datetime.datetime.now(datetime.timezone.utc)
            if now_check < cooldown_until_dt:
                cooldown_active = True

        self.assertTrue(cooldown_active, "Active cooldown must flag cooldown_active as True to skip crawl")

    @patch("sentiment_generator.generate_sentiment.get_circuit_breaker_state")
    def test_expired_cooldown_allows_request(self, mock_get_cb):
        """Expired persistent cooldown allows Phase 1 to proceed with normal crawling."""
        past_utc = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=70)
        cooldown_until = past_utc + datetime.timedelta(minutes=60)  # Expired 10m ago
        mock_get_cb.return_value = {
            "breaker_opened_at": past_utc.isoformat(),
            "cooldown_until": cooldown_until.isoformat(),
            "reason": "HTTP 429 Rate Limit",
            "updated_at": past_utc.isoformat()
        }

        breaker_state = mock_get_cb()
        cooldown_active = False
        if breaker_state:
            cooldown_until_dt = datetime.datetime.fromisoformat(breaker_state["cooldown_until"])
            now_check = datetime.datetime.now(datetime.timezone.utc)
            if now_check < cooldown_until_dt:
                cooldown_active = True

        self.assertFalse(cooldown_active, "Expired cooldown must allow normal resume")

    @patch("sentiment_generator.generate_sentiment.time.sleep")
    @patch("sentiment_generator.generate_sentiment.save_raw_articles", return_value=0)
    @patch("sentiment_generator.generate_sentiment.record_fetch_period")
    @patch("sentiment_generator.generate_sentiment.get_period_status")
    def test_resume_skips_success_and_empty_and_retries_failed(self, mock_status, mock_record, mock_save, mock_sleep):
        """Normal resume skips 'success' and 'empty' periods and retries 'failed' periods."""
        from sentiment_generator.generate_sentiment import process_ticker_news_fetch

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_gdelt_window.return_value = []

        m_success = (datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 1, 31, tzinfo=self.tz_utc), "2024-01-01", "2024-01-31")
        m_empty   = (datetime.datetime(2024, 2, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 2, 29, tzinfo=self.tz_utc), "2024-02-01", "2024-02-29")
        m_failed  = (datetime.datetime(2024, 3, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 3, 31, tzinfo=self.tz_utc), "2024-03-01", "2024-03-31")

        def fake_status(ticker, start, end):
            if start == "2024-01-01":
                return {"status": "success", "article_count": 50}
            elif start == "2024-02-01":
                return {"status": "empty", "article_count": 0}
            elif start == "2024-03-01":
                return {"status": "failed", "article_count": 0}
            return None

        mock_status.side_effect = fake_status

        res = process_ticker_news_fetch(
            ticker="TEST_RESUME.NS",
            month_ranges=[m_success, m_empty, m_failed],
            fetcher=mock_fetcher,
            force_refetch=False
        )

        self.assertEqual(res["periods_skipped"], 2, "Success and empty periods must be skipped")
        self.assertEqual(res["periods_fetched"], 1, "Failed period must be retried and fetched")
        self.assertEqual(mock_fetcher.fetch_gdelt_window.call_count, 1, "Only 1 fetch call should be made for the failed period")

    def test_persistent_circuit_breaker_db_roundtrip(self):
        """set_circuit_breaker_state, get_circuit_breaker_state, and clear_circuit_breaker_state roundtrip."""
        from sentiment_generator.cache import (
            init_db, set_circuit_breaker_state, get_circuit_breaker_state, clear_circuit_breaker_state
        )
        init_db()
        clear_circuit_breaker_state()
        self.assertIsNone(get_circuit_breaker_state())

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        cooldown_until = now_utc + datetime.timedelta(minutes=60)
        set_circuit_breaker_state(
            breaker_opened_at=now_utc.isoformat(),
            cooldown_until=cooldown_until.isoformat(),
            reason="Test 429 exhaustion"
        )

        state = get_circuit_breaker_state()
        self.assertIsNotNone(state)
        self.assertEqual(state["reason"], "Test 429 exhaustion")
        self.assertEqual(state["cooldown_until"], cooldown_until.isoformat())

        clear_circuit_breaker_state()
        self.assertIsNone(get_circuit_breaker_state())

    @patch("sentiment_generator.generate_sentiment.time.sleep")
    @patch("sentiment_generator.generate_sentiment.save_raw_articles", return_value=0)
    @patch("sentiment_generator.generate_sentiment.record_fetch_period")
    @patch("sentiment_generator.generate_sentiment.get_period_status", return_value=None)
    @patch("sentiment_generator.generate_sentiment.set_circuit_breaker_state")
    def test_exhaustion_persists_circuit_breaker_cooldown(self, mock_set_cb, mock_status, mock_record, mock_save, mock_sleep):
        """When 429 retries are exhausted, the persistent circuit breaker cooldown is written."""
        from sentiment_generator.generate_sentiment import process_ticker_news_fetch
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted

        circuit_breaker = threading.Event()
        mock_fetcher = MagicMock()
        err = GDELTRateLimitExhausted("HTTP 429 Rate Limit (exceeded 5 retries)")
        mock_fetcher.fetch_gdelt_window.side_effect = err

        m1 = (datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 1, 31, tzinfo=self.tz_utc), "2024-01-01", "2024-01-31")

        res = process_ticker_news_fetch(
            ticker="TEST_PERSIST.NS",
            month_ranges=[m1],
            fetcher=mock_fetcher,
            force_refetch=True,
            circuit_breaker_event=circuit_breaker
        )

        self.assertTrue(circuit_breaker.is_set())
        mock_set_cb.assert_called_once()
        call_kwargs = mock_set_cb.call_args.kwargs
        self.assertIn("breaker_opened_at", call_kwargs)
        self.assertIn("cooldown_until", call_kwargs)
        self.assertIn("reason", call_kwargs)

    @patch("sentiment_generator.generate_sentiment.time.sleep")
    @patch("sentiment_generator.generate_sentiment.save_raw_articles", return_value=0)
    @patch("sentiment_generator.generate_sentiment.record_fetch_period")
    @patch("sentiment_generator.generate_sentiment.get_period_status", return_value=None)
    @patch("sentiment_generator.generate_sentiment.get_circuit_breaker_state", return_value={"reason": "429"})
    @patch("sentiment_generator.generate_sentiment.clear_circuit_breaker_state")
    def test_successful_request_clears_expired_breaker(self, mock_clear_cb, mock_get_cb, mock_status, mock_record, mock_save, mock_sleep):
        """Successful fetch after an expired breaker clears the persisted circuit breaker state."""
        from sentiment_generator.generate_sentiment import process_ticker_news_fetch

        mock_fetcher = MagicMock()
        mock_fetcher.fetch_gdelt_window.return_value = [{"title": "Good News"}]

        m1 = (datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 1, 31, tzinfo=self.tz_utc), "2024-01-01", "2024-01-31")

        res = process_ticker_news_fetch(
            ticker="TEST_RECOVER.NS",
            month_ranges=[m1],
            fetcher=mock_fetcher,
            force_refetch=True
        )

        mock_clear_cb.assert_called_once()
        self.assertEqual(res["periods_fetched"], 1)

    @patch("sentiment_generator.generate_sentiment.time.sleep")
    @patch("sentiment_generator.generate_sentiment.save_raw_articles", return_value=0)
    @patch("sentiment_generator.generate_sentiment.record_fetch_period")
    @patch("sentiment_generator.generate_sentiment.get_period_status", return_value=None)
    def test_unattempted_periods_after_breaker_remain_absent(self, mock_status, mock_record, mock_save, mock_sleep):
        """Periods never attempted after the circuit breaker trips are NOT recorded as failed."""
        from sentiment_generator.generate_sentiment import process_ticker_news_fetch
        from sentiment_generator.news_fetcher import GDELTRateLimitExhausted

        circuit_breaker = threading.Event()
        mock_fetcher = MagicMock()
        err = GDELTRateLimitExhausted("HTTP 429 Rate Limit (exceeded 5 retries)")

        # Period 1 fails with 429, Periods 2 & 3 must never be touched
        mock_fetcher.fetch_gdelt_window.side_effect = [err, [], []]

        m1 = (datetime.datetime(2024, 1, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 1, 31, tzinfo=self.tz_utc), "2024-01-01", "2024-01-31")
        m2 = (datetime.datetime(2024, 2, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 2, 29, tzinfo=self.tz_utc), "2024-02-01", "2024-02-29")
        m3 = (datetime.datetime(2024, 3, 1, tzinfo=self.tz_utc), datetime.datetime(2024, 3, 31, tzinfo=self.tz_utc), "2024-03-01", "2024-03-31")

        res = process_ticker_news_fetch(
            ticker="TEST_ABSENT.NS",
            month_ranges=[m1, m2, m3],
            fetcher=mock_fetcher,
            force_refetch=True,
            circuit_breaker_event=circuit_breaker
        )

        # Only Period 1 should be recorded as failed; Periods 2 & 3 must NOT be recorded at all
        self.assertEqual(mock_record.call_count, 1)
        mock_record.assert_called_once_with("TEST_ABSENT.NS", "2024-01-01", "2024-01-31", status="failed", article_count=0, error_message=str(err))


class TestICICIBankMatcherValidation(unittest.TestCase):
    """
    Adversarial and boundary test suite for ICICIBANK.NS (ICICI Bank Limited) entity matcher.
    Covers:
    - Positive parent-bank financial/earnings/operational/governance coverage.
    - Positive bare ICICI disambiguation with banking/market/peer signals.
    - Positive subsidiary corporate actions connecting back to parent bank (delisting/merger).
    - Positive leadership governance (Sandeep Bakhshi, Chanda Kochhar bank cases).
    - Negative standalone subsidiary products/earnings (ICICI Prudential Life, AMC, Lombard, Securities, Venture, Foundation).
    - Negative automated 13F/SEC portfolio filing notices.
    - Negative third-party stock recommendations from ICICI Securities / Direct.
    - Negative multi-bank retail rate aggregator/SEO lists.
    - Negative unrelated historical executive commentary.
    """

    def setUp(self):
        self.fetcher = NewsFetcher(trading_calendar=["2024-01-01", "2024-01-02", "2024-01-03"])

    # ─── Positive Tests ──────────────────────────────────────────────────────
    def test_icicibank_positive_earnings_and_financials(self):
        """Explicit ICICI Bank financial results, NIM, NII, asset quality, and shares."""
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank Q3 net profit rises 23.6% to Rs 10,272 crore", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank reports record NII and improved asset quality in Q3", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank Limited deposit growth outpaces loan growth in Q3 FY24", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank shares gain 2% following strong quarterly earnings and margin expansion", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Brokerage upgrades ICICI Bank target price to Rs 1250 on robust balance sheet", "ICICIBANK.NS"))

    def test_icicibank_positive_regulatory_and_operations(self):
        """ICICI Bank regulatory, governance, digital banking, and operational developments."""
        self.assertTrue(self.fetcher.is_relevant_to_company("RBI approves appointment of Executive Director at ICICI Bank", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank launches new digital banking features on iMobile app", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank revises fixed deposit interest rates across select tenures", "ICICIBANK.NS"))

    def test_icicibank_positive_subsidiary_parent_materiality_override(self):
        """Subsidiary actions materially involving parent bank (merger, delisting, parent stake)."""
        self.assertTrue(self.fetcher.is_relevant_to_company("NCLT clears ICICI Bank-ICICI Securities merger scheme; EGM set for March 27", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank to delist ICICI Securities via share swap arrangement", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank increases stake in ICICI Lombard General Insurance", "ICICIBANK.NS"))

    def test_icicibank_positive_bare_icici_contextual(self):
        """Bare ICICI references with strong banking, market mover, or peer signals."""
        self.assertTrue(self.fetcher.is_relevant_to_company("Paytm, SBI, Axis, ICICI, Kotak Bank, HDFC Bank shares: Bernstein sees up to 47% upside", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Stock markets recover after 3 days of loss; ICICI, Airtel major movers", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Union, IDBI, ICICI & IDFC stocks rise on strong Q3 FY24 profits", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("HDFC Bank, Tata Steel, RIL, ICICI, JSW Steel to drive Q3 Nifty results, says Motilal", "ICICIBANK.NS"))

    def test_icicibank_positive_leadership_governance(self):
        """Current and former leadership in corporate/legal/governance contexts."""
        self.assertTrue(self.fetcher.is_relevant_to_company("ICICI Bank CEO Sandeep Bakhshi on digital lending growth and risk management", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company("Bombay HC quashes arrest of Chanda Kochhar in ICICI Bank-Videocon loan case", "ICICIBANK.NS"))

    # ─── Negative Tests ──────────────────────────────────────────────────────
    def test_icicibank_negative_standalone_subsidiaries(self):
        """Standalone subsidiary products, earnings, and operations without parent bank materiality."""
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Prudential Life Q3 results: Net profit flat at Rs 227 crore", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Prudential Life introduces ICICI Pru Guaranteed Pension Plan Flexi with benefit enhancer", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Prudential Mutual Fund launches ICICI Prudential Nifty50 Value 20 Index Fund", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Lombard Share Price: ICICI Lombard shares down 0.78% as Sensex falls", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Venture exits logistics firm via secondary market transaction", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Foundation opens new rural vocational skill development centre in Gujarat", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Pru Sensex ETF completes 21 years, delivers 17% CAGR", "ICICIBANK.NS"))

    def test_icicibank_negative_13f_foreign_fund_filings(self):
        """Automated 13F and SEC foreign portfolio filing notices."""
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Prudential Asset Management Co Ltd Purchases 3,140 Shares of Equifax Inc. (NYSE:EFX)", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Prudential Asset Management Co Ltd Sells 1,600 Shares of Salesforce, Inc. (NYSE:CRM)", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Tesla, Inc. (NASDAQ:TSLA) Shares Sold by ICICI Prudential Asset Management Co Ltd", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("ICICI Prudential Asset Management Co Ltd Has $7.32 Million Holdings in Pfizer Inc. (NYSE:PFE)", "ICICIBANK.NS"))

    def test_icicibank_negative_third_party_brokerage_recos(self):
        """Third-party stock recommendations issued by ICICI Securities or ICICI Direct."""
        self.assertFalse(self.fetcher.is_relevant_to_company("Buy Delhivery; target of Rs 500: ICICI Securities", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Reduce Gujarat Gas; target of Rs 385: ICICI Securities", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Buy Shipping Corporation of India, target price Rs 185: ICICI Direct", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Market Strategy: ICICI Securities suggests these themes, stocks for wealth creation in 2024", "ICICIBANK.NS"))

    def test_icicibank_negative_generic_retail_seo_lists(self):
        """Generic personal loan, credit card rules, and multi-bank comparison tables."""
        self.assertFalse(self.fetcher.is_relevant_to_company("provident fund loan interest rate emi of personal loan calculator sbi pnb icici hdfc mdn", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("New credit card rules: SBI, HDFC, ICICI and Axis Bank announce changes for customers", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("SBI vs HDFC vs ICICI vs PNB vs BoB vs Kotak vs Axis: Which bank is offering the highest interest rate on fixed deposits?", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Senior Citizen 5-Year FDs Of SBI, HDFC, ICICI, PNB vs SCSS: Where Should You Park Your Savings?", "ICICIBANK.NS"))

    def test_icicibank_negative_unrelated_executive_and_recruitment(self):
        """Unrelated historical executive speeches and generic recruitment listings."""
        self.assertFalse(self.fetcher.is_relevant_to_company("India will become a $10 trillion economy by 2035, says KV Kamath", "ICICIBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company("Freshers apply for Software Engineer | ICICI Bank recruitment drive 2024", "ICICIBANK.NS"))


class TestAxisBankMatcherValidation(unittest.TestCase):
    """
    Adversarial and boundary test suite for AXISBANK.NS (Axis Bank Limited) entity matcher.
    Covers:
    - Positive: explicit Axis Bank financial/earnings/operational/regulatory coverage.
    - Positive: brokerage analyst calls, target price, upgrade/downgrade.
    - Positive: contextual bare 'Axis' with strong banking-peer or corporate signals.
    - Positive: subsidiary corporate actions with parent-materiality override.
    - Negative: standalone subsidiary activities (Securities, AMC, Finance, Foundation, etc.).
    - Negative: formulaic peer-bank FD/interest-rate/credit-card comparison tables.
    - Negative: generic multi-stock watchlists, "stocks to watch", market roundups.
    - Negative: bare Axis in clearly non-bank contexts (x-axis, geopolitical, optics, Axis Corp).
    - Negative: minor incidental financing participations.
    """

    def setUp(self):
        self.fetcher = NewsFetcher(trading_calendar=["2024-01-01", "2024-01-02", "2024-01-03"])

    # ─── Positive: Explicit Parent Bank Corporate Identity ───────────────────
    def test_axisbank_positive_q3_earnings_and_financials(self):
        """Q3/quarterly results and key financial metrics explicitly about Axis Bank."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank reports Q3 profit growth of 4% YoY to Rs 6,071 crore", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank Q3 FY24: NII rises 9%, NIM contracts slightly to 4.01%", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank Q3 Results: Net profit rises 4%, asset quality stays healthy", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank Q3 net profit up 4% at Rs 6,071 cr, net interest income rises 9%", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank balance sheet growth not powering profits yet: analyst note", "AXISBANK.NS"))

    def test_axisbank_positive_shares_and_brokerage_calls(self):
        """Analyst recommendations and share price movements directly on Axis Bank."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Buy Axis Bank; target of Rs 1250: Prabhudas Lilladher", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank shares fall 5% post Q3 earnings. Should you buy, sell or hold?", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank raises deposit rates; analysts see margin pressure easing in H2", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Brokerage upgrades Axis Bank to Buy with target price Rs 1350", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Neutral on Axis Bank, target price Rs 1175: Motilal Oswal", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Technical Analysis: Axis Bank shows bullish breakout above 200-DMA", "AXISBANK.NS"))

    def test_axisbank_positive_regulatory_and_governance(self):
        """RBI actions, SEBI/legal matters, and governance events involving Axis Bank."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "RBI takes action against Axis Bank for KYC non-compliance", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "SAT rejects Axis Bank's plea on invoking pledged shares in Karvy case", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Consumer forum orders Axis Bank to pay Rs 1 lakh compensation for deficiency in service", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Cheque fraud victims to get Rs 74 lakh from Axis Bank after 15-year legal fight", "AXISBANK.NS"))

    def test_axisbank_positive_operations_and_products(self):
        """Axis Bank product launches, partnerships, credit cards, and operational news."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank My Zone Credit Card: rewards, benefits and more", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank GuarantCo Enable INR 1 Billion Loan for Everest Fleet electric taxis", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank CEO Amitabh Chaudhry says India in a better place for economic growth", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "HDFC Bank, Axis Bank to be closed on Jan 22 on account of Ram Mandir inauguration", "AXISBANK.NS"))

    def test_axisbank_positive_bare_axis_with_peer_banks(self):
        """Bare 'Axis' shorthand recoverable when co-occurring with named banking peers."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis, ICICI go slow on hiring, HDFC Bank pushing ahead", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "SBI, HDFC Bank and Axis hiring trends diverge in FY24, says report", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis shares rebound sharply as ICICI Bank and Kotak Bank also recover from losses", "AXISBANK.NS"))

    def test_axisbank_positive_historical_name_uti_bank(self):
        """UTI Bank (historical name before 2007 rebranding) maps to Axis Bank."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "UTI Bank rebranded as Axis Bank in 2007: revisiting the transformation", "AXISBANK.NS"))

    # ─── Positive: Subsidiary With Parent-Materiality Override ───────────────
    def test_axisbank_positive_subsidiary_parent_override(self):
        """Subsidiary activity that materially involves the parent bank (stake, merger, etc.)."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank to acquire stake in Axis Finance to consolidate lending vertical", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank announces merger of Axis Capital with parent entity", "AXISBANK.NS"))

    # ─── Negative: Standalone Subsidiary Activities ───────────────────────────
    def test_axisbank_negative_axis_securities_standalone(self):
        """Axis Securities brokerage recommendations targeting other companies."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Securities recommends buying Tata Motors on strong EV outlook", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Securities के 3 एफएंडओ कॉल्स निवेशकों को करेंगे मालामाल, Sun Pharma का सस्ता ऑप्शन", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Direct initiates coverage on Reliance Industries with Buy, target Rs 3200", "AXISBANK.NS"))

    def test_axisbank_negative_axis_mutual_fund_standalone(self):
        """Axis Mutual Fund NAV, portfolio, and product launches without parent bank nexus."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Mutual Fund increases stake in Infosys in latest portfolio reshuffle", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis AMC launches new Nifty 50 Index Fund; NFO opens next Monday", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Asset Management Company reports AUM crosses Rs 1 lakh crore milestone", "AXISBANK.NS"))

    def test_axisbank_negative_axis_finance_standalone(self):
        """Axis Finance lending activity without parent bank being a material party."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Finance lends Rs 500 crore to real estate developer for township project", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bombay HC asks Zee promoter to deposit Rs 61.6 crore over default to Axis Finance", "AXISBANK.NS"))

    def test_axisbank_negative_axis_foundation_csr(self):
        """Axis Bank Foundation CSR activities are NOT parent bank financial operations."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Bank Foundation marks a decade of creating inclusive work opportunities for over 24,000 youths", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Foundation launches CSR initiative for rural skilling in Rajasthan", "AXISBANK.NS"))

    def test_axisbank_negative_axis_capital_standalone(self):
        """Axis Capital investment banking deals without parent bank material nexus."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Capital manages QIP of Rs 2,500 crore for hospitality major", "AXISBANK.NS"))

    # ─── Negative: Peer-Bank Comparison / FD Rate Tables ─────────────────────
    def test_axisbank_negative_fd_comparison_tables(self):
        """Formulaic FD/interest-rate comparison tables listing multiple banks."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Fixed deposit interest rates: SBI vs ICICI Bank vs HDFC Bank vs Axis Bank vs PNB compared", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "SCSS vs senior citizen FDs of SBI, HDFC Bank, ICICI Bank, Axis Bank, PNB: Which offers highest interest?", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "SBI vs HDFC vs ICICI vs PNB vs BoB vs Kotak vs Axis: Which bank is offering the highest interest rate on fixed deposits?", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Senior Citizen Saving Scheme vs SBI, HDFC Bank, ICICI Bank, Axis Bank, and PNB fixed deposits: Which is better?", "AXISBANK.NS"))

    def test_axisbank_negative_credit_card_rule_comparisons(self):
        """Multi-bank credit card rule comparisons without Axis-specific material analysis."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "New credit card rules: Know major changes in HDFC Bank SBI Card, ICICI Bank and Axis Bank credit cards", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "New credit card rules: SBI, HDFC, ICICI और Axis Bank के ग्राहक ध्यान दें, क्रेडिट कार्ड को लेकर बदल गए हैं नियम", "AXISBANK.NS"))

    # ─── Negative: Generic Market Roundup / Watchlist ────────────────────────
    def test_axisbank_negative_market_roundup_lists(self):
        """Generic 'Stocks to watch', 'Stocks in news', and market index roundups."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks to watch: Tata Motors, Axis Bank, Infosys, Reliance on Tuesday", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks To Watch On January 24: Pidilite, Axis Bank, Tata Elxsi, Bharti Airtel & Others", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks in news: ZEE, Cipla, Axis Bank, IndiGo, Kotak Bank, ICICI Bank, Persistent Systems", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "India Movers: Axis Bank, Karnataka Bank, REC, Zee Entertainment", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks To Watch On 24 January: Axis Bank, L&T Finance, JSW Energy, Others In News", "AXISBANK.NS"))

    # ─── Negative: Bare Axis in Non-Banking Contexts ─────────────────────────
    def test_axisbank_negative_bare_axis_unrelated_contexts(self):
        """Bare 'Axis' in clearly non-banking contexts must be rejected."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Earth's rotational axis shifts by 80 cm due to groundwater extraction", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "x-axis and y-axis explained: plotting data in two dimensions", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "New geopolitical axis emerges as China, Russia and Iran deepen ties", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Communications launches AI-powered security camera for airports", "AXISBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Understanding the axis of symmetry in quadratic equations", "AXISBANK.NS"))

    # ─── Negative: Incidental / Minor Financing Participation ─────────────────
    def test_axisbank_negative_minor_startup_investment(self):
        """Minor bridge round participation by Axis Bank in a startup is NOT material."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "VRO Hospitality raises $10M in bridge round led by Axis Bank and Nikhil Kamath's Gruhas", "AXISBANK.NS"))

    # ─── Regression: Eight Previously GREEN Tickers Unaffected ──────────────
    def test_axisbank_no_regression_on_frozen_tickers(self):
        """AXISBANK matcher must not interfere with the 8 frozen tickers."""
        # ICICIBANK.NS positive — must still pass
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "ICICI Bank Q3 net profit rises 23.6% to Rs 10,272 crore", "ICICIBANK.NS"))
        # TCS.NS positive — must still pass
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "TCS Q3 results: Net profit at Rs 11,058 crore, above estimate", "TCS.NS"))
        # INFY.NS positive — must still pass
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Infosys Q3 revenue misses estimates; FY24 guidance narrowed", "INFY.NS"))
        # AXISBANK pattern must NOT match for ICICIBANK ticker
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Axis Bank Q3 results: Net profit rises 4%", "ICICIBANK.NS"))


class TestKotakBankMatcherValidation(unittest.TestCase):
    """
    Comprehensive adversarial test suite for Kotak Mahindra Bank Limited (KOTAKBANK.NS) entity matching.
    Tests positive parent bank events, regulatory actions, leadership governance,
    and exclusions for subsidiaries, peer FD tables, roundups, and personal biography.
    """

    def setUp(self):
        self.fetcher = NewsFetcher(trading_calendar=[])

    # ─── Positive: Explicit Parent Bank Earnings & Financials ─────────────────
    def test_kotakbank_positive_earnings_and_financials(self):
        """Quarterly profit, NII, NIM, PAT, asset quality for Kotak Mahindra Bank."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra Bank Q3 net profit rises 7.6% to Rs 3,005 crore", "KOTAKBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Bank shares rise 2% after Q3 results beat Street estimates", "KOTAKBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra logs Rs 3,005 crore PAT for Q3; asset quality stable", "KOTAKBANK.NS"))

    # ─── Positive: Stock, Analyst Coverage, Brokerage Targets ─────────────────
    def test_kotakbank_positive_stock_and_analyst_coverage(self):
        """Brokerage upgrades, target prices, analyst recommendations."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Brokerage retains 'Buy' on Kotak Mahindra Bank with target price of Rs 2,150", "KOTAKBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Bank share price drops 3% as deposit growth lags advances", "KOTAKBANK.NS"))

    # ─── Positive: RBI Regulatory & Governance Actions ────────────────────────
    def test_kotakbank_positive_regulatory_and_governance(self):
        """RBI supervisory directives, approvals, and penalties."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "RBI approves appointment of Ashok Vaswani as MD & CEO of Kotak Mahindra Bank", "KOTAKBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra, HDFC, and RBL Banks Make Provisions on AIF Investments Following RBI Norms", "KOTAKBANK.NS"))

    # ─── Positive: Uday Kotak Bank Governance Context ─────────────────────────
    def test_kotakbank_positive_uday_kotak_governance(self):
        """Uday Kotak in bank governance, succession, leadership, and promoter context."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra Bank को मिला नया एमडी और सीईओ, Uday Kotak के इस्तीफे के बाद अशोक वासवानी ने संभाला पद", "KOTAKBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Uday Kotak steps down as MD & CEO of Kotak Mahindra Bank", "KOTAKBANK.NS"))

    # ─── Positive: Parent Materiality Overrides for Subsidiaries ──────────────
    def test_kotakbank_positive_parent_materiality_override(self):
        """Parent bank stake sales, acquisitions, or Zurich deal involving insurance subsidiary."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "CCI approves Zurich Insurance's acquisition of 51% stake in Kotak General Insurance", "KOTAKBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra Bank to sell 70% stake in Kotak General Insurance to Zurich Insurance", "KOTAKBANK.NS"))

    # ─── Negative: Uday Kotak Personal / Biography / Lifestyle ────────────────
    def test_kotakbank_negative_uday_kotak_personal_bio(self):
        """Personal biography, net worth, family, wedding, lifestyle articles are NOT bank relevant."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Uday Kotak: History, Biography, Net Worth, Education & Family", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Inside billionaire Uday Kotak's lavish lifestyle and net worth", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Uday Kotak's son Jay Kotak ties the knot in grand wedding ceremony", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "At Davos, Uday Kotak speaks on Indian entrepreneurship and global economic outlook", "KOTAKBANK.NS"))

    # ─── Negative: Standalone Subsidiary Brokerage Recommendations ───────────
    def test_kotakbank_negative_standalone_subsidiary_brokerage(self):
        """Kotak Securities third-party stock recommendations are NOT parent bank relevant."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Kotak Securities recommends Buy on Tata Motors with target price of Rs 950", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Kotak Institutional Equities downgrades Infosys to reduce", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Kotak Securities moves Bombay High Court against NSE directive", "KOTAKBANK.NS"))

    # ─── Negative: Standalone Mutual Fund / AMC Products ──────────────────────
    def test_kotakbank_negative_standalone_amc_products(self):
        """Kotak Mutual Fund / AMC NFOs and portfolio updates are NOT parent bank relevant."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Kotak Mutual Fund launches Kotak Multi Asset Allocation Fund NFO", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra AMC adds HDFC Bank, trims Reliance in latest portfolio rebalance", "KOTAKBANK.NS"))

    # ─── Negative: Formulaic Multi-Bank FD & Personal Loan Rate Tables ────────
    def test_kotakbank_negative_formulaic_peer_comparisons(self):
        """Formulaic comparison tables of FD and loan rates across multiple banks."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "After SBI And Kotak Mahindra, PNB Raises Interest Rates On Fixed Deposits, Details", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "SBI vs HDFC vs ICICI vs Kotak vs Axis: Which bank offers highest FD interest rate?", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Personal loan from banks with lowest interest rates: ICICI, HDFC, SBI, Kotak Mahindra", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Credit card rules changing from Feb 1: SBI, HDFC, ICICI, Kotak Bank revise reward points", "KOTAKBANK.NS"))

    # ─── Negative: Generic Multi-Stock Watchlists / Roundups ──────────────────
    def test_kotakbank_negative_generic_roundups_and_watchlists(self):
        """Generic multi-stock listicles where Kotak is merely an incidental member."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks in news: RIL, ICICI Bank, Kotak Bank, ZEE, Paytm, Fortis", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks in news: ZEE, Cipla, Axis Bank, IndiGo, Kotak Bank, ICICI Bank, Persistent", "KOTAKBANK.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks to watch on January 23: Axis Bank, Kotak Mahindra Bank, Havells, Tata Steel", "KOTAKBANK.NS"))

    # ─── Regression: Nine Previously GREEN Tickers Unaffected ────────────────
    def test_kotakbank_no_regression_on_nine_frozen_tickers(self):
        """KOTAKBANK matcher must not interfere with the 9 frozen matchers."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Reliance Industries Q3 net profit rises 11% to Rs 17,265 crore", "RELIANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "HDFC Bank Q3 net profit surges 33% to Rs 16,372 crore", "HDFCBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "ITC reports 10.75% rise in Q3 net profit at Rs 5,572 crore", "ITC.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "L&T bags mega order worth over Rs 15,000 crore for hydrocarbon business", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "State Bank of India reports net profit of Rs 9,164 crore for Q3", "SBIN.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "TCS Q3 results: Net profit at Rs 11,058 crore, above estimate", "TCS.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Infosys Q3 revenue misses estimates; FY24 guidance narrowed", "INFY.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "ICICI Bank Q3 net profit rises 23.6% to Rs 10,272 crore", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank Q3 net profit rises 4% to Rs 6,071 crore", "AXISBANK.NS"))
        # Negative cross-ticker check
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra Bank Q3 net profit rises 7%", "AXISBANK.NS"))


class TestBajajFinanceMatcherValidation(unittest.TestCase):
    """
    Focused adversarial test suite for Bajaj Finance Limited (BAJFINANCE.NS)
    precedence entity matcher.
    """

    def setUp(self):
        self.fetcher = NewsFetcher(trading_calendar=[])

    def test_bajfinance_positive_earnings_and_financials(self):
        """Quarterly profit, AUM growth, loan additions, and asset quality."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finance Q3 net profit jumps 22% to Rs 3,639 crore, AUM up 35%", "BAJFINANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finance reports robust customer franchise addition of 3.85 million in Q3", "BAJFINANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finance Q3 results: Net interest income rises 29% YoY", "BAJFINANCE.NS"))

    def test_bajfinance_positive_stock_and_analyst_coverage(self):
        """Brokerage ratings, target prices, stock movements."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Morgan Stanley maintains Overweight on Bajaj Finance with target price of Rs 9,000", "BAJFINANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finance shares fall 5% on margin compression worries", "BAJFINANCE.NS"))

    def test_bajfinance_positive_regulatory_and_fundraising(self):
        """RBI regulatory directives and corporate NCD fundraising."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finance works with RBI on lifting curbs on eCOM and Insta EMI cards", "BAJFINANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finance to raise up to Rs 10,000 crore via non-convertible debentures", "BAJFINANCE.NS"))

    def test_bajfinance_positive_parent_materiality_override(self):
        """Holding company stake changes or subsidiary IPO plans involving Bajaj Finance."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finserv to hike stake in Bajaj Finance through warrant conversion", "BAJFINANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Bajaj Finance plans IPO of subsidiary Bajaj Housing Finance", "BAJFINANCE.NS"))

    def test_bajfinance_negative_standalone_finserv_products(self):
        """Standalone Bajaj Finserv operations are NOT parent Bajaj Finance relevant."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Finserv brings you India's First Credit Pass, powered by CIBIL", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Finserv Mutual Fund launches Bajaj Finserv Nifty Bank ETF", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Finserv's healthtech arm to acquire Vidal Healthcare for ₹325 crore", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "India on cusp of FDI flood, Bajaj Finserv Chair Sanjiv Bajaj says in Davos", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Finserv has announced the vacancy of Senior Relationship Executive", "BAJFINANCE.NS"))

    def test_bajfinance_negative_standalone_housing_finance(self):
        """Standalone Bajaj Housing Finance operational news."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Housing Finance reduces home loan interest rates to 8.50%", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "BSE : Listing of new debt securities of Bajaj Housing Finance Limited", "BAJFINANCE.NS"))

    def test_bajfinance_negative_standalone_insurance(self):
        """Bajaj Allianz Life / General Insurance products."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Allianz General Insurance launches new health insurance plan", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Allianz Life declares bonus of Rs 1,201 crore for policyholders", "BAJFINANCE.NS"))

    def test_bajfinance_negative_other_group_companies(self):
        """Bajaj Auto, Bajaj Holdings, Bajaj Electricals."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Auto board approves share buyback of Rs 4,000 crore", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Bajaj Holdings Q3 results: Net profit rises 28% to ₹1,644 crore", "BAJFINANCE.NS"))

    def test_bajfinance_negative_formulaic_peer_comparisons(self):
        """Multi-institution comparison tables for FD and loan rates."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "SBI vs HDFC vs ICICI vs Bajaj Finance: Who offers the highest FD interest rates?", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Best fixed deposit rates in January 2024: SBI, Post Office, Bajaj Finance compared", "BAJFINANCE.NS"))

    def test_bajfinance_negative_generic_roundups_and_watchlists(self):
        """Multi-stock listicles where Bajaj Finance is an incidental member."""
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks to Watch: Adani Ports, NTPC, Bajaj Finance, LIC, Power Grid, Wipro", "BAJFINANCE.NS"))
        self.assertFalse(self.fetcher.is_relevant_to_company(
            "Stocks in news: Reliance, TCS, HDFC Bank, Infosys, Bajaj Finance, ITC", "BAJFINANCE.NS"))

    def test_bajfinance_no_regression_on_ten_frozen_tickers(self):
        """BAJFINANCE matcher must not interfere with the 10 frozen matchers."""
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Reliance Industries Q3 consolidated net profit rises 9% to Rs 17,265 crore", "RELIANCE.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "HDFC Bank Q3 net profit surges 33% to Rs 16,372 crore", "HDFCBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "ITC reports 10.75% rise in Q3 net profit at Rs 5,572 crore", "ITC.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "L&T bags mega order worth over Rs 15,000 crore for hydrocarbon business", "LT.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "State Bank of India reports net profit of Rs 9,164 crore for Q3", "SBIN.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "TCS Q3 results: Net profit at Rs 11,058 crore, above estimate", "TCS.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Infosys Q3 revenue misses estimates; FY24 guidance narrowed", "INFY.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "ICICI Bank Q3 net profit rises 23.6% to Rs 10,272 crore", "ICICIBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Axis Bank Q3 net profit rises 4% to Rs 6,071 crore", "AXISBANK.NS"))
        self.assertTrue(self.fetcher.is_relevant_to_company(
            "Kotak Mahindra Bank Q3 net profit rises 7.6% to Rs 3,005 crore", "KOTAKBANK.NS"))


if __name__ == "__main__":
    unittest.main()




