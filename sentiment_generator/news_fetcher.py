import urllib.parse
import time
import random
import datetime
import re
import bisect
import threading
import logging
from typing import List, Dict, Any, Tuple, Optional, Set
import zoneinfo
import requests

from .config import (
    STOCKS, COMPANY_ALIASES, MARKET_TIMEZONE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, GDELT_MAX_RECORDS
)

logger = logging.getLogger(__name__)

GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# Contextual financial & corporate keywords for verifying ambiguous short names/acronyms.
#
# IMPORTANT: only include tokens that are safe for plain substring matching (i.e. they are
# long enough that they will never appear as an incidental substring of an unrelated word).
# Short/ambiguous tokens such as 'it', 'md', 'npa' are handled separately via word-boundary
# regex in _has_financial_context() to avoid false positives like 'it' inside 'schedule'.
FINANCIAL_CONTEXT_KEYWORDS = {
    "stock", "shares", "results", "profit", "loss", "revenue", "quarter",
    "dividend", "earnings", "board", "management", "nifty", "sensex", "bse", "nse",
    "market", "growth", "sales", "crore", "ebitda", "margin", "target", "analyst",
    "ceo", "chairman", "acquisition", "order", "contract", "tax", "rbi", "sebi",
    "tobacco", "cigarette", "fmcg", "hotel", "hotels", "infra", "construction", "bank",
    "banking", "loan", "npa", "lending", "tech", "deal", "software", "steel",
    "power", "energy", "pharma", "drug", "auto", "vehicle", "cars", "jewellery", "jewelry"
}

# Short tokens that require whole-word (\b-bounded) matching to avoid substring collisions.
# 'it' matches inside words like 'schedule', 'distribution'; 'md' inside 'cmd'; etc.
_FINANCIAL_CONTEXT_WORDBOUND = re.compile(
    r'\b(?:it|md|npa)\b', re.IGNORECASE
)

# Tracking query parameters to strip during canonical URL normalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "fbclid", "gclid", "source", "ocid", "cmp", "campaign",
    "ncid", "sr_share", "ved", "usqp"
}


class NewsFetcher:
    """
    Production-grade historical news fetcher for Indian equities (NSE) using GDELT DOC API 2.0.

    Guarantees:
    - Zero synthetic data, zero interpolated sentiment, zero forward/backward filling.
    - Full UTC -> Asia/Kolkata timezone awareness with complete second-level 15:30:00 cutoff comparison.
    - Look-ahead bias elimination: pre-market/mid-session news -> day D; after-market news -> next session.
    - Strict NSE calendar boundaries: articles mapping beyond verified calendar return None and are skipped.
    - Robust recursive bisection pagination to eliminate 250-article silent truncations.
      Recursion terminates only when the window is <= min_window_seconds, never by a fixed depth cap.
      Sub-window failures raise RuntimeError and are never silently treated as empty coverage.
      Midpoint ownership: left branch = [start, mid); right branch = [mid, end)  (right-exclusive mid+1s).
    - Thread-local requests.Session for concurrent ThreadPoolExecutor thread-safety.
    - Strict canonical URL/headline+source deduplication.
    - Full rejection of invalid/unparseable timestamps without fabricating temporal placement.
    """
    def __init__(self, trading_calendar: List[str]):
        """
        trading_calendar: Sorted list of valid NSE trading dates (YYYY-MM-DD).
        """
        self.trading_calendar = sorted(list(set(trading_calendar)))
        self.tz_market = zoneinfo.ZoneInfo(MARKET_TIMEZONE)
        self.tz_utc = zoneinfo.ZoneInfo("UTC")

        # Thread-local storage for requests.Session
        self._thread_local = threading.local()

        # Thread-safe diagnostic telemetry counters
        self._stats_lock = threading.Lock()
        self.stats = {
            "api_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "rate_limit_responses": 0,
            "articles_retrieved": 0,
            "articles_rejected_company_match": 0,
            "articles_rejected_invalid_timestamp": 0,
            "articles_rejected_out_of_range": 0,
            "articles_skipped_no_trading_session": 0,
            "articles_mapped_to_trading_sessions": 0,
            "duplicates_removed": 0,
            "articles_missing_published_at": 0
        }

        # Precompile company matching regexes once at initialization
        self._compiled_matchers = self._compile_matchers()

    def _get_session(self) -> requests.Session:
        """Returns an isolated requests.Session per worker thread."""
        if not hasattr(self._thread_local, "session"):
            session = requests.Session()
            session.headers.update(HEADERS)
            self._thread_local.session = session
        return self._thread_local.session

    def _inc_stat(self, key: str, count: int = 1):
        """Thread-safe increment of diagnostic counters."""
        with self._stats_lock:
            if key in self.stats:
                self.stats[key] += count

    def get_diagnostics(self) -> Dict[str, int]:
        """Returns a snapshot of diagnostic telemetry."""
        with self._stats_lock:
            return dict(self.stats)

    # ─── Precompiled Contextual Entity Matchers ─────────────────────────────────
    def _compile_matchers(self) -> Dict[str, Dict[str, Any]]:
        """Precompiles regular expressions for each ticker for high performance and accuracy."""
        matchers = {}
        for ticker, company_name in STOCKS.items():
            aliases = COMPANY_ALIASES.get(ticker, [company_name])
            
            multi_word = [a.lower() for a in aliases if " " in a]
            single_word = [a for a in aliases if " " not in a]
            single_patterns = [
                re.compile(r'\b' + re.escape(w) + r'\b', re.IGNORECASE)
                for w in single_word
            ]

            # Specialized contextual rules for ambiguous tickers
            custom_fn = None
            if ticker == "ITC.NS":
                custom_fn = self._match_itc
            elif ticker == "LT.NS":
                custom_fn = self._match_lt
            elif ticker == "TITAN.NS":
                custom_fn = self._match_titan
            elif ticker == "SBIN.NS":
                custom_fn = self._match_sbi
            elif ticker == "TCS.NS":
                custom_fn = self._match_tcs
            elif ticker == "RELIANCE.NS":
                custom_fn = self._match_reliance

            matchers[ticker] = {
                "multi_word": multi_word,
                "single_patterns": single_patterns,
                "custom_fn": custom_fn
            }
        return matchers

    # ─── Contextual Entity Matching Implementation ───────────────────────────
    @staticmethod
    def _has_financial_context(text_lower: str) -> bool:
        """
        Returns True if text_lower contains at least one financial/corporate signal,
        using:
        - Substring matching for long unambiguous tokens (FINANCIAL_CONTEXT_KEYWORDS).
        - Word-boundary regex for short/ambiguous tokens (e.g. 'it', 'md', 'npa')
          to prevent false positives from incidental substrings (e.g. 'it' in 'schedule').
        """
        if any(kw in text_lower for kw in FINANCIAL_CONTEXT_KEYWORDS):
            return True
        if _FINANCIAL_CONTEXT_WORDBOUND.search(text_lower):
            return True
        return False

    def is_relevant_to_company(self, text: str, ticker: str) -> bool:
        """
        Determines whether a headline is genuinely relevant to the target company,
        preventing false positives on ambiguous acronyms (e.g., ITC, LT, TITAN).
        """
        if not text or not text.strip():
            return False

        matcher = self._compiled_matchers.get(ticker)
        if not matcher:
            return False

        text_lower = text.lower()

        # 1. Specialized contextual verification for ambiguous tickers
        if matcher["custom_fn"]:
            return matcher["custom_fn"](text, text_lower)

        # 2. Multi-word exact phrase matching (e.g. "Hindustan Unilever", "Tata Steel")
        for phrase in matcher["multi_word"]:
            if phrase in text_lower:
                return True

        # 3. Precompiled word-boundary matching for single-word aliases
        for pat in matcher["single_patterns"]:
            if pat.search(text_lower):
                return True

        return False

    def _match_itc(self, text: str, text_lower: str) -> bool:
        if re.search(r'\bitc\s+(?:ltd|limited|hotels|infotech|paperboards|shares|stock|q[1-4]|board|dividend|agm)\b', text_lower):
            return True
        if "imperial tobacco" in text_lower or "itc limited" in text_lower or "itc ltd" in text_lower:
            return True
        if re.search(r'\bITC\b', text):
            # Require business or financial context for bare uppercase ITC.
            # Use the helper which applies word-boundary checks for short tokens.
            if self._has_financial_context(text_lower):
                return True
        return False

    def _match_lt(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:l&t|larsen\s*(?:&|and)\s*toubro)\b', text_lower):
            return True
        if re.search(r'\bLT\b', text):
            if any(kw in text_lower for kw in ["construction", "infra", "order", "contract", "larsen", "toubro", "shares", "results", "subramanian", "am naik", "infotech", "technology", "q1", "q2", "q3", "q4"]):
                return True
        return False

    def _match_titan(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:titan\s+(?:company|ltd|limited|watches|jewellery|jewelry|eyeplus)|tanishq|fastrack|mia|caratlane)\b', text_lower):
            return True
        if re.search(r'\bTitan\b', text):
            if any(kw in text_lower for kw in ["tata", "jewellery", "jewelry", "watches", "tanishq", "quarter", "results", "shares", "stock", "profit", "sales", "eyewear"]):
                return True
        return False

    def _match_sbi(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:state\s+bank\s+of\s+india|state\s+bank)\b', text_lower):
            return True
        if re.search(r'\bSBI\b', text):
            if any(kw in text_lower for kw in ["bank", "banking", "shares", "stock", "lending", "npa", "dinesh khara", "quarter", "results", "loan", "card", "life", "mutual fund"]):
                return True
            if _FINANCIAL_CONTEXT_WORDBOUND.search(text_lower):  # catches bare 'npa', 'md'
                return True
        return False

    def _match_tcs(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:tata\s+consultancy\s+services|tata\s+consultancy)\b', text_lower):
            return True
        if re.search(r'\bTCS\b', text):
            if any(kw in text_lower for kw in ["tata", "tech", "deal", "contract", "shares", "stock", "quarter", "results", "krithivasan", "ceo", "earnings", "q1", "q2", "q3", "q4", "profit", "revenue", "dividend"]):
                return True
            if _FINANCIAL_CONTEXT_WORDBOUND.search(text_lower):  # catches 'it services', 'md'
                return True
        return False

    def _match_reliance(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:reliance\s+(?:industries|retail|jio|oil|telecom|digital|power|petroleum|bp|greens|ent)|mukesh\s+ambani|ril)\b', text_lower):
            return True
        if re.search(r'\bReliance\b', text):
            if self._has_financial_context(text_lower):
                return True
        return False

    # ─── URL Normalization & Article Deduplication ────────────────────────────
    @staticmethod
    def normalize_url(url: str) -> str:
        """
        Normalizes a URL by stripping tracking query params, fragments,
        trailing slashes, and lowercasing the domain for canonical deduplication.
        """
        if not url or not url.strip():
            return ""

        url = url.strip()
        try:
            parsed = urllib.parse.urlparse(url)
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            path = parsed.path.rstrip('/')

            # Strip tracking query parameters
            query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
            filtered_query = [
                (k, v) for k, v in query_pairs
                if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
            ]
            new_query = urllib.parse.urlencode(sorted(filtered_query))

            # Reconstruct clean URL without fragment
            clean_url = urllib.parse.urlunparse((scheme, netloc, path, "", new_query, ""))
            return clean_url
        except Exception:
            return url.strip().rstrip('/')

    @staticmethod
    def normalize_headline(headline: str) -> str:
        """Cleans headline string for duplicate detection."""
        if not headline:
            return ""
        # Remove trailing source tags (e.g., "- Reuters", "| Economic Times", "- NDTV Profit")
        h = re.sub(r'\s+[-|]\s+[A-Za-z0-9\s.,&]+$', '', headline.strip())
        h = re.sub(r'\s+', ' ', h)
        return h.strip().lower()

    def _article_dedupe_key(self, article: Dict[str, Any]) -> Tuple[str, str, str]:
        """Generates a stable, canonical deduplication key for an article record."""
        ticker = article.get("ticker", "")
        trading_date = article.get("trading_date", "")
        norm_url = self.normalize_url(article.get("url", ""))
        if norm_url:
            return (ticker, trading_date, norm_url)
        norm_h = self.normalize_headline(article.get("headline", ""))
        src_ts = str(article.get("source_timestamp", ""))
        return (ticker, trading_date, f"{norm_h}::{src_ts}")

    # ─── Timestamp & NSE Trading Session Mapping ──────────────────────────────
    def parse_gdelt_timestamp(self, seendate_raw: str) -> Tuple[str, str, datetime.datetime]:
        """
        Parses GDELT seendate (UTC) into:
        1. source_timestamp: raw string from source
        2. seen_at: ISO-8601 string with timezone (+05:30)
        3. ist_dt: datetime object in Asia/Kolkata timezone
        
        Raises ValueError on malformed or unparseable timestamps (never falls back to now()).
        """
        raw_clean = re.sub(r'[^0-9]', '', str(seendate_raw))
        if len(raw_clean) >= 14:
            dt_utc = datetime.datetime.strptime(raw_clean[:14], "%Y%m%d%H%M%S").replace(tzinfo=self.tz_utc)
        elif len(raw_clean) >= 8:
            dt_utc = datetime.datetime.strptime(raw_clean[:8] + "000000", "%Y%m%d%H%M%S").replace(tzinfo=self.tz_utc)
        else:
            raise ValueError(f"Malformed or unparseable GDELT timestamp: '{seendate_raw}'")

        ist_dt = dt_utc.astimezone(self.tz_market)
        seen_at_iso = ist_dt.isoformat()
        return str(seendate_raw), seen_at_iso, ist_dt

    def get_next_trading_day(self, cal_date: str) -> Optional[str]:
        """
        Finds the earliest NSE trading day strictly after cal_date.
        Returns None if no subsequent trading day exists in the calendar.
        """
        idx = bisect.bisect_right(self.trading_calendar, cal_date)
        if idx < len(self.trading_calendar):
            return self.trading_calendar[idx]
        return None

    def map_to_nse_trading_session(self, ist_dt: datetime.datetime) -> Optional[str]:
        """
        Maps an article's timestamp (IST) to the correct NSE trading session.
        
        Look-Ahead Bias Elimination Rules:
        1. Trading day D + arrival before 15:30:00 IST -> D (available before close to predict D+1).
        2. Trading day D + arrival at/after 15:30:00 IST -> next_trading_day(D).
        3. Weekend / NSE Holiday -> next_trading_day(D).
        
        Returns None if no valid next trading session exists in the verified calendar.
        """
        cal_date = ist_dt.strftime("%Y-%m-%d")
        cutoff_time = datetime.time(MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, 0, 0)
        article_time = ist_dt.time()

        if cal_date in self.trading_calendar:
            if article_time < cutoff_time:
                return cal_date
            else:
                return self.get_next_trading_day(cal_date)
        else:
            return self.get_next_trading_day(cal_date)

    # ─── GDELT DOC API 2.0 with Robust Recursive Pagination ──────────────────
    def fetch_gdelt_window(
        self,
        ticker: str,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
        min_window_seconds: int = 3600,
        _recursing: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetches GDELT articles for a date range with recursive bisection pagination
        whenever the 250-record GDELT limit is reached.

        Recursion terminates ONLY when the window is <= min_window_seconds (default 1 hour).
        There is no fixed depth cap — splitting continues until sub-windows are below the
        API limit or the minimum window size is reached.

        Boundary convention (non-overlapping half-open intervals):
          Left sub-request  : [start_dt,         mid_dt)       → enddatetime = mid_dt - 1s
          Right sub-request : [mid_dt,            end_dt)       → startdatetime = mid_dt
        This ensures an article timestamped exactly at mid_dt belongs to the right branch only.

        On sub-window failure a RuntimeError is raised immediately — the failure is NEVER
        silently absorbed and treated as empty coverage.

        Guarantees:
        - Never silently truncates at 250 articles.
        - Deduplicates merged intervals by canonical URL and normalized headline+source.
        - Strictly validates trading_date in verified NSE calendar.
        - Rejects malformed historical timestamps without fabricating temporal placement.
        - Preserves raw audit fields (source_timestamp, seen_at, published_at=None).
        """
        company_name = STOCKS.get(ticker, ticker.split('.')[0])
        query = f'"{company_name}"'

        start_str = start_dt.strftime("%Y%m%d%H%M%S")
        end_str = end_dt.strftime("%Y%m%d%H%M%S")

        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(GDELT_MAX_RECORDS),
            "startdatetime": start_str,
            "enddatetime": end_str,
            "format": "json"
        }

        session = self._get_session()
        raw_items = []
        fetch_success = False
        last_error = None

        self._inc_stat("api_requests")

        for attempt in range(5):
            try:
                res = session.get(GDELT_DOC_API_URL, params=params, timeout=15)
                if res.status_code == 200 and res.text.strip():
                    try:
                        data = res.json()
                        if isinstance(data, dict):
                            raw_items = data.get("articles", [])
                            if not isinstance(raw_items, list):
                                raw_items = []
                        else:
                            raw_items = []
                        fetch_success = True
                        self._inc_stat("successful_requests")
                        break
                    except Exception as je:
                        last_error = f"JSON parse error: {je}"
                        time.sleep(2 + attempt * 2)
                elif res.status_code == 429:
                    self._inc_stat("rate_limit_responses")
                    retry_after = res.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        sleep_sec = float(retry_after) + random.uniform(0.5, 1.5)
                    else:
                        sleep_sec = (2 ** attempt) * 4 + random.uniform(0.5, 2.0)
                    last_error = f"HTTP 429 Rate Limit (sleeping {sleep_sec:.1f}s)"
                    time.sleep(sleep_sec)
                else:
                    last_error = f"HTTP {res.status_code}"
                    time.sleep(2 + attempt * 2)
            except Exception as e:
                last_error = str(e)
                time.sleep(2 + attempt * 2)

        if not fetch_success:
            self._inc_stat("failed_requests")
            raise RuntimeError(f"GDELT API request failed for {ticker} ({start_str} to {end_str}): {last_error}")

        self._inc_stat("articles_retrieved", len(raw_items))

        # Process retrieved items for this window
        articles: List[Dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue

            title = (item.get("title") or "").strip()
            if not title or not self.is_relevant_to_company(title, ticker):
                self._inc_stat("articles_rejected_company_match")
                continue

            seendate_raw = (item.get("seendate") or "").strip()
            try:
                src_ts, seen_at_iso, ist_dt = self.parse_gdelt_timestamp(seendate_raw)
            except Exception:
                # Rejects malformed historical timestamp without fabricating temporal placement
                self._inc_stat("articles_rejected_invalid_timestamp")
                continue

            trading_date = self.map_to_nse_trading_session(ist_dt)
            if not trading_date:
                # Beyond trading calendar with no subsequent session
                self._inc_stat("articles_skipped_no_trading_session")
                continue

            if trading_date not in self.trading_calendar:
                self._inc_stat("articles_skipped_no_trading_session")
                continue

            self._inc_stat("articles_mapped_to_trading_sessions")
            self._inc_stat("articles_missing_published_at")  # GDELT seendate is index time, not verified pub time

            raw_url = (item.get("url") or "").strip()

            articles.append({
                "ticker": ticker,
                "company": STOCKS.get(ticker, ""),
                "headline": title,
                "source": "GDELT",
                "url": raw_url,
                "published_at": None,  # Explicitly None: do not invent publication times
                "seen_at": seen_at_iso,
                "source_timestamp": src_ts,
                "trading_date": trading_date
            })

        # Recursive splitting if GDELT limit (250) was reached and window is still splittable
        duration_sec = (end_dt - start_dt).total_seconds()
        if len(raw_items) >= GDELT_MAX_RECORDS and duration_sec > min_window_seconds:
            mid_dt = start_dt + (end_dt - start_dt) / 2

            # ── Boundary convention (non-overlapping half-open intervals) ──────────────
            # Left  branch covers [start_dt, mid_dt).  Its GDELT enddatetime is mid_dt-1s
            # so an article timestamped exactly at mid_dt is NOT fetched by the left branch.
            # Right branch covers [mid_dt, end_dt).  Its GDELT startdatetime is mid_dt.
            # This prevents any article at the exact midpoint from appearing in both results.
            left_end_dt = mid_dt - datetime.timedelta(seconds=1)

            # Delay with jitter before bisection sub-requests to avoid HTTP 429
            time.sleep(2 + random.uniform(0.2, 0.8))

            # Sub-window failures raise RuntimeError immediately; they are NEVER silently
            # absorbed or substituted with a subset of the parent's truncated 250-item list.
            left_arts: List[Dict[str, Any]] = self.fetch_gdelt_window(
                ticker, start_dt, left_end_dt, min_window_seconds, _recursing=True
            )

            time.sleep(2 + random.uniform(0.2, 0.8))

            right_arts: List[Dict[str, Any]] = self.fetch_gdelt_window(
                ticker, mid_dt, end_dt, min_window_seconds, _recursing=True
            )

            # Combine and deduplicate across the two non-overlapping sub-intervals
            articles = self._deduplicate_articles(left_arts + right_arts)
        else:
            # Window at or below min_window_seconds: accept current results as-is.
            # If we reached here because the window is too small to split further but
            # still hit 250 records, log a warning so the operator can investigate.
            if len(raw_items) >= GDELT_MAX_RECORDS:
                logger.warning(
                    f"[{ticker}] Minimum window ({min_window_seconds}s) reached but result"
                    f" count={len(raw_items)} still hits the GDELT limit "
                    f"({start_str} to {end_str}). Some articles in this interval may be"
                    f" truncated. Consider reducing min_window_seconds."
                )
            articles = self._deduplicate_articles(articles)

        return articles

    def _deduplicate_articles(self, articles_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicates article records using canonical deduplication keys.

        Primary key  (URL-based):      (ticker, trading_date, normalized_url)
        Fallback key (headline-based): (ticker, trading_date, normalized_headline, source)

        The source domain is included in the headline fallback key to avoid merging
        genuinely different articles that happen to share a headline across publishers
        (e.g., a Reuters wire republished verbatim by multiple outlets with the same
        normalized headline but distinct URLs that were lost during URL normalization).
        Only articles from the exact same publisher with the exact same headline are
        treated as duplicates under the fallback path.
        """
        seen_url_keys: Set[Tuple[str, str, str]] = set()
        seen_hl_keys: Set[Tuple[str, str, str, str]] = set()
        unique: List[Dict[str, Any]] = []

        for a in articles_list:
            ticker = a["ticker"]
            td = a["trading_date"]
            dedupe_key = self._article_dedupe_key(a)
            norm_h = self.normalize_headline(a.get("headline", ""))
            # Extract normalized source domain for headline fallback key
            raw_url = a.get("url", "") or ""
            try:
                src_domain = urllib.parse.urlparse(raw_url).netloc.lower().lstrip("www.")
            except Exception:
                src_domain = ""
            # Headline fallback includes source domain to prevent cross-publisher merges
            hl_key: Tuple[str, str, str, str] = (ticker, td, norm_h, src_domain)

            is_dup = False
            if dedupe_key in seen_url_keys:
                is_dup = True
            elif norm_h and hl_key in seen_hl_keys:
                # Only deduplicate on headline if both have the same source domain
                is_dup = True

            if not is_dup:
                seen_url_keys.add(dedupe_key)
                if norm_h:
                    seen_hl_keys.add(hl_key)
                unique.append(dict(a))
            else:
                self._inc_stat("duplicates_removed")

        return unique
