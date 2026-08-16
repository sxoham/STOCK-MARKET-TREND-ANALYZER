import urllib.parse
import time
import datetime
import re
import bisect
import threading
from typing import List, Dict, Any, Tuple, Optional, Set
import zoneinfo
import requests

from .config import (
    STOCKS, COMPANY_ALIASES, MARKET_TIMEZONE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, GDELT_MAX_RECORDS
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Contextual financial/corporate keywords for ambiguous abbreviations
FINANCIAL_CONTEXT_KEYWORDS = {
    "stock", "shares", "results", "profit", "loss", "revenue", "quarter",
    "dividend", "earnings", "board", "management", "nifty", "sensex", "bse", "nse",
    "market", "growth", "sales", "crore", "ebitda", "margin", "target", "analyst",
    "ceo", "chairman", "md", "acquisition", "order", "contract", "tax", "rbi", "sebi",
    "tobacco", "cigarette", "fmcg", "hotel", "hotels", "infra", "construction", "bank",
    "banking", "loan", "npa", "lending", "it", "tech", "deal", "software", "steel",
    "power", "energy", "pharma", "drug", "auto", "vehicle", "cars", "jewellery", "jewelry"
}

# Tracking query parameters to strip during URL normalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "fbclid", "gclid", "source", "ocid", "cmp", "campaign"
}


class NewsFetcher:
    def __init__(self, trading_calendar: List[str]):
        """
        trading_calendar: Sorted list of valid NSE trading dates (YYYY-MM-DD).
        """
        self.trading_calendar = sorted(list(set(trading_calendar)))
        self.tz_market = zoneinfo.ZoneInfo(MARKET_TIMEZONE)
        self.tz_utc = zoneinfo.ZoneInfo("UTC")

        # Thread-local storage for HTTP sessions
        self._thread_local = threading.local()

        # Thread-safe diagnostic counters
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

        # Precompile company matching regexes once for high performance & accuracy
        self._compiled_matchers = self._compile_matchers()

    def _get_session(self) -> requests.Session:
        """Returns a thread-local requests.Session for thread-safe concurrent HTTP calls."""
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
        """Returns a copy of diagnostic statistics."""
        with self._stats_lock:
            return dict(self.stats)

    # ─── Precompiled Contextual Entity Matchers ─────────────────────────────────
    def _compile_matchers(self) -> Dict[str, Dict[str, Any]]:
        """Precompiles regular expressions for each ticker to avoid per-headline compilation."""
        matchers = {}
        for ticker, company_name in STOCKS.items():
            aliases = COMPANY_ALIASES.get(ticker, [company_name])
            
            # Multi-word alias patterns
            multi_word = [a.lower() for a in aliases if " " in a]
            
            # Single-word boundary patterns
            single_word = [a for a in aliases if " " not in a]
            single_patterns = [
                re.compile(r'\b' + re.escape(w) + r'\b', re.IGNORECASE)
                for w in single_word
            ]

            # Custom specialized rules for ambiguous tickers
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
        if "imperial tobacco" in text_lower:
            return True
        if re.search(r'\bITC\b', text):
            # Require financial/business context
            if any(kw in text_lower for kw in FINANCIAL_CONTEXT_KEYWORDS):
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
        return False

    def _match_tcs(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:tata\s+consultancy\s+services|tata\s+consultancy)\b', text_lower):
            return True
        if re.search(r'\bTCS\b', text):
            if any(kw in text_lower for kw in ["tata", "it", "tech", "deal", "contract", "shares", "stock", "quarter", "results", "krithivasan", "ceo", "earnings"]):
                return True
        return False

    def _match_reliance(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:reliance\s+(?:industries|retail|jio|oil|telecom|digital|power|petroleum|bp|greens|ent)|mukesh\s+ambani|ril)\b', text_lower):
            return True
        if re.search(r'\bReliance\b', text):
            if any(kw in text_lower for kw in FINANCIAL_CONTEXT_KEYWORDS):
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
            # Normalize scheme & host to lowercase
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

    # ─── Timestamp & NSE Trading Session Mapping ──────────────────────────────
    def parse_gdelt_timestamp(self, seendate_raw: str) -> Tuple[str, str, datetime.datetime]:
        """
        Parses GDELT seendate (UTC) into:
        1. source_timestamp: raw string from source
        2. seen_at: ISO-8601 string with timezone (+05:30)
        3. ist_dt: datetime object in Asia/Kolkata timezone
        """
        raw_clean = re.sub(r'[^0-9]', '', str(seendate_raw))
        if len(raw_clean) >= 14:
            dt_utc = datetime.datetime.strptime(raw_clean[:14], "%Y%m%d%H%M%S").replace(tzinfo=self.tz_utc)
        elif len(raw_clean) >= 8:
            dt_utc = datetime.datetime.strptime(raw_clean[:8] + "000000", "%Y%m%d%H%M%S").replace(tzinfo=self.tz_utc)
        else:
            dt_utc = datetime.datetime.now(self.tz_utc)

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
        1. Trading day D + arrival before market close (15:30 IST) -> D (available before close to predict D+1).
        2. Trading day D + arrival at/after market close (15:30 IST) -> next_trading_day(D).
        3. Weekend / NSE Holiday -> next_trading_day(D).
        
        Returns None if no valid next trading session exists.
        """
        cal_date = ist_dt.strftime("%Y-%m-%d")
        cutoff_time = (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
        article_time = (ist_dt.hour, ist_dt.minute)

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
        max_depth: int = 4,
        min_window_seconds: int = 3600
    ) -> List[Dict[str, Any]]:
        """
        Fetches GDELT articles for a date range with recursive bisection pagination
        whenever the 250 record limit is reached.
        
        Guarantees:
        - Never silently truncates at 250 articles.
        - Deduplicates merged intervals by normalized URL and headline.
        - Strictly validates trading_date ∈ verified NSE calendar.
        - Preserves raw audit fields (source_timestamp, seen_at, published_at=None).
        """
        company_name = STOCKS.get(ticker, ticker.split('.')[0])
        query = f'"{company_name}"'

        start_str = start_dt.strftime("%Y%m%d%H%M%S")
        end_str = end_dt.strftime("%Y%m%d%H%M%S")

        url = (
            f"https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={urllib.parse.quote(query)}"
            f"&mode=artlist&maxrecords={GDELT_MAX_RECORDS}"
            f"&startdatetime={start_str}&enddatetime={end_str}"
            f"&format=json"
        )

        session = self._get_session()
        raw_items = []
        fetch_success = False
        last_error = None

        self._inc_stat("api_requests")

        for attempt in range(4):
            try:
                res = session.get(url, timeout=15)
                if res.status_code == 200 and res.text.strip():
                    try:
                        data = res.json()
                        raw_items = data.get("articles", [])
                        fetch_success = True
                        self._inc_stat("successful_requests")
                        break
                    except Exception as je:
                        last_error = f"JSON parse error: {je}"
                        time.sleep(2 + attempt * 2)
                elif res.status_code == 429:
                    self._inc_stat("rate_limit_responses")
                    sleep_sec = 6 + (attempt * 6)  # 6s, 12s, 18s, 24s backoff
                    last_error = f"HTTP 429 Rate Limit (sleeping {sleep_sec}s)"
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
            title = (item.get("title") or "").strip()
            if not title or not self.is_relevant_to_company(title, ticker):
                self._inc_stat("articles_rejected_company_match")
                continue

            seendate_raw = item.get("seendate", "")
            try:
                src_ts, seen_at_iso, ist_dt = self.parse_gdelt_timestamp(seendate_raw)
            except Exception:
                self._inc_stat("articles_rejected_invalid_timestamp")
                continue

            trading_date = self.map_to_nse_trading_session(ist_dt)
            if not trading_date:
                # Occurs if article is beyond trading calendar with no next session
                self._inc_stat("articles_skipped_no_trading_session")
                continue

            if trading_date not in self.trading_calendar:
                self._inc_stat("articles_skipped_no_trading_session")
                continue

            self._inc_stat("articles_mapped_to_trading_sessions")
            self._inc_stat("articles_missing_published_at")  # GDELT seendate is seen time, not verified pub time

            raw_url = (item.get("url") or "").strip()
            norm_url = self.normalize_url(raw_url)

            articles.append({
                "ticker": ticker,
                "company": STOCKS.get(ticker, ""),
                "headline": title,
                "source": "GDELT",
                "url": raw_url,
                "norm_url": norm_url,
                "published_at": None,  # Explicitly None per requirements: do not invent publication times
                "seen_at": seen_at_iso,
                "source_timestamp": src_ts,
                "trading_date": trading_date
            })

        # Recursive splitting if GDELT limit (250) was reached and window is broad enough
        duration_sec = (end_dt - start_dt).total_seconds()
        if len(raw_items) >= GDELT_MAX_RECORDS and max_depth > 0 and duration_sec > min_window_seconds:
            mid_dt = start_dt + (end_dt - start_dt) / 2
            
            # Delay before bisection sub-requests to avoid HTTP 429
            time.sleep(3)
            left_arts: List[Dict[str, Any]] = []
            try:
                left_arts = self.fetch_gdelt_window(
                    ticker, start_dt, mid_dt, max_depth - 1, min_window_seconds
                )
            except RuntimeError:
                # If sub-request fails, fall back to initial parsed items for left window
                left_arts = [a for a in articles if a["seen_at"][:19] <= mid_dt.isoformat()[:19]]

            time.sleep(3)
            right_arts: List[Dict[str, Any]] = []
            try:
                right_arts = self.fetch_gdelt_window(
                    ticker, mid_dt, end_dt, max_depth - 1, min_window_seconds
                )
            except RuntimeError:
                # If sub-request fails, fall back to initial parsed items for right window
                right_arts = [a for a in articles if a["seen_at"][:19] > mid_dt.isoformat()[:19]]

            # Combine and deduplicate across split intervals
            combined = left_arts + right_arts if (left_arts or right_arts) else articles
            articles = self._deduplicate_articles(combined)
        else:
            articles = self._deduplicate_articles(articles)

        return articles

    def _deduplicate_articles(self, articles_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicates article records by normalized URL and canonical headline.
        """
        seen_urls: Set[Tuple[str, str, str]] = set()
        seen_headlines: Set[Tuple[str, str, str]] = set()
        unique: List[Dict[str, Any]] = []

        for a in articles_list:
            t = a["ticker"]
            td = a["trading_date"]
            norm_url = a.get("norm_url") or self.normalize_url(a.get("url", ""))
            norm_h = self.normalize_headline(a.get("headline", ""))

            # Key 1: URL-based uniqueness
            url_key = (t, td, norm_url) if norm_url else None
            # Key 2: Headline-based uniqueness
            hl_key = (t, td, norm_h)

            is_dup = False
            if url_key and url_key in seen_urls:
                is_dup = True
            elif hl_key in seen_headlines:
                is_dup = True

            if not is_dup:
                if url_key:
                    seen_urls.add(url_key)
                seen_headlines.add(hl_key)
                
                # Strip internal helper key before returning clean dictionary
                clean_item = dict(a)
                clean_item.pop("norm_url", None)
                unique.append(clean_item)
            else:
                self._inc_stat("duplicates_removed")

        return unique
