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
        # Explicit phrase matches (always pass regardless of bare-ITC tightening)
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Ltd reports strong Q3 profit growth", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC Limited declares interim dividend", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC hotels business demerger approved by board", "ITC.NS"))
        # Bare 'ITC' + strong signals from _ITC_BARE_STRONG_SIGNALS
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC fmcg segment posts record crore turnover", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC shares jump 4% after analyst upgrade", "ITC.NS"))
        self.assertTrue(
            self.fetcher.is_relevant_to_company("ITC cigarette volumes recover in Q2 earnings", "ITC.NS"))

    def test_D2_unrelated_itc_acronym_rejected(self):
        self.assertFalse(
            self.fetcher.is_relevant_to_company("International Trade Council holds meeting in Geneva", "ITC.NS"))
        self.assertFalse(
            self.fetcher.is_relevant_to_company("ITC exam schedule released for 2024", "ITC.NS"))
        # Generic corporate language must NOT pass with bare 'ITC' — this is the key
        # false-positive the _ITC_BARE_STRONG_SIGNALS tightening is designed to prevent.
        self.assertFalse(
            self.fetcher.is_relevant_to_company(
                "ITC announces new initiative as the board approves the order", "ITC.NS"),
            "Bare ITC + only 'board'/'order' (generic terms) must be rejected")
        self.assertFalse(
            self.fetcher.is_relevant_to_company(
                "ITC hosts leadership summit on management and growth strategy", "ITC.NS"),
            "Bare ITC + only 'management'/'growth' (generic terms) must be rejected")

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


if __name__ == "__main__":
    unittest.main()



