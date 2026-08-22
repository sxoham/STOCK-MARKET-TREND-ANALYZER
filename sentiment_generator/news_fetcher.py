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
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, GDELT_MAX_RECORDS,
    GDELT_MAX_REQUESTS_PER_WINDOW, GDELT_REQUEST_SLEEP_SECONDS
)

logger = logging.getLogger(__name__)

GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

class GDELTRateLimiter:
    """
    Process-wide thread-safe rate limiter for GDELT API requests.
    Enforces a strict minimum spacing between consecutive outbound HTTP calls
    across all worker threads, retry loops, and recursive bisection branches.
    """
    def __init__(self, min_interval: float = GDELT_REQUEST_SLEEP_SECONDS):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_request_time = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                time.sleep(sleep_time)
            self._last_request_time = time.monotonic()

_global_gdelt_rate_limiter = GDELTRateLimiter(GDELT_REQUEST_SLEEP_SECONDS)

# Contextual financial & corporate keywords for verifying ambiguous short names/acronyms.
#
# IMPORTANT: only include tokens that are safe for plain substring matching (i.e. they are
# long enough or specific enough that they will rarely appear as an incidental substring
# of an unrelated word in a financial news article).
# Short/ambiguous tokens that CAN appear as substrings of common words are handled
# separately via word-boundary regex in _FINANCIAL_CONTEXT_WORDBOUND.
#
# Tokens intentionally excluded from this set (moved to word-boundary regex below):
#   'tech'  — appears in 'technical', 'technology', 'biotechnology'
#   'auto'  — appears in 'automatic', 'automation', 'automated'
#   'deal'  — appears in 'ideal', 'ordeal'
#   'power' — appears in 'empower', 'powerless', 'powerpoint'
#   'tax'   — appears in 'syntax', 'intax'
#   'bank'  — appears in 'embankment'; also, 'banking' below covers most contexts
FINANCIAL_CONTEXT_KEYWORDS = {
    "stock", "shares", "results", "profit", "loss", "revenue", "quarter",
    "dividend", "earnings", "board", "management", "nifty", "sensex", "bse", "nse",
    "market", "growth", "sales", "crore", "ebitda", "margin", "target", "analyst",
    "ceo", "chairman", "acquisition", "order", "contract", "rbi", "sebi",
    "tobacco", "cigarette", "fmcg", "hotel", "hotels", "infra", "construction",
    "banking", "loan", "lending", "software", "steel",
    "energy", "pharma", "drug", "vehicle", "cars", "jewellery", "jewelry"
}

# Short tokens that require whole-word (\b-bounded) matching to avoid substring collisions.
# 'it' matches inside 'schedule', 'distribution'; 'md' inside 'cmd'; 'npa' inside 'unpaid'.
# 'tech' matches inside 'technical', 'technology'; 'auto' inside 'automatic', 'automation';
# 'deal' matches inside 'ideal', 'ordeal'; 'power' inside 'empower', 'powerless';
# 'tax' matches inside 'syntax'; 'bank' inside 'embankment'.
_FINANCIAL_CONTEXT_WORDBOUND = re.compile(
    r'\b(?:it|md|npa|tech|auto|deal|power|tax|bank)\b', re.IGNORECASE
)

# Strong financial signals used exclusively for the bare-'ITC' matching path.
#
# Why a separate set: FINANCIAL_CONTEXT_KEYWORDS contains broad terms like 'board',
# 'market', 'sales', 'order', and 'management' that appear in countless non-ITC corporate
# articles.  A headline such as "ITC announces initiative as board approves the order"
# would pass the broad check even though 'ITC' could refer to any organisation.
# This tighter set requires a clearly financial signal — earnings metrics, securities,
# market indices, regulatory bodies, or ITC's specific business verticals — before
# accepting a bare 'ITC' match.
_ITC_BARE_STRONG_SIGNALS: frozenset = frozenset({
    # ITC-specific business verticals
    "tobacco", "cigarette", "fmcg",
    # Securities and earnings
    "shares", "stock", "profit", "loss", "revenue", "earnings", "ebitda",
    "margin", "crore", "quarter", "results", "dividend",
    # Market references
    "nifty", "sensex", "bse", "nse",
    # Analyst/valuation
    "analyst", "target", "rating", "valuation",
    # Corporate actions
    "ceo", "chairman", "acquisition", "buyback",
    # Regulatory
    "rbi", "sebi",
})

# Precompiled word-boundary regex derived from _ITC_BARE_STRONG_SIGNALS.
#
# Sorted longest-first so that longer tokens take priority in the alternation
# (e.g. 'cigarette' is tried before 'ceo'), which is the standard convention
# for regex alternations to avoid partial-match shadowing.
#
# Using \b anchors on both sides means each token must occur as a whole word.
# Examples:
#   'loss'    will not fire on 'glossy'    ('gl' precedes the match, no boundary)
#   'revenue' will not fire on 'revenues'  (no \b between 'e' and 's'; both are \w)
#   'nse'     will not fire inside a longer alphanumeric token
# All of the above are correct behaviour for this filter.
_ITC_BARE_STRONG_SIGNALS_RE: re.Pattern = re.compile(
    r'\b(?:' +
    '|'.join(re.escape(tok) for tok in sorted(_ITC_BARE_STRONG_SIGNALS, key=len, reverse=True)) +
    r')\b',
    re.IGNORECASE,
)

# Tracking query parameters to strip during canonical URL normalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "fbclid", "gclid", "source", "ocid", "cmp", "campaign",
    "ncid", "sr_share", "ved", "usqp"
}

# Minimum alias token length / characteristics for inclusion in GDELT query.
# Tokens shorter than this AND not containing a space are excluded to prevent
# overly broad GDELT searches (e.g. "IT" alone would match almost everything).
_GDELT_ALIAS_MIN_LEN = 4


class LowPrecisionTimestampError(ValueError):
    """
    Raised by parse_gdelt_timestamp() when a GDELT timestamp carries only
    date-level precision (YYYYMMDD) without an intra-day time component.

    Inherits from ValueError so existing broad except-ValueError callers
    continue to work, but callers that need to distinguish low-precision
    rejections from genuinely malformed timestamps can catch this subclass
    first, incrementing only the correct diagnostic counter.
    """


class NewsFetcher:
    """
    Production-grade historical news fetcher for Indian equities (NSE) using GDELT DOC API 2.0.

    Guarantees:
    - Zero synthetic data, zero interpolated sentiment, zero forward/backward filling.
    - Full UTC -> Asia/Kolkata timezone awareness with complete second-level 15:30:00 cutoff comparison.
    - Look-ahead bias elimination: pre-market/mid-session news -> day D; after-market news -> next session.
    - Strict NSE calendar boundaries: articles mapping beyond verified calendar return None and are skipped.
    - Robust recursive bisection pagination to eliminate 250-article silent truncations.
      Recursion terminates ONLY when the window is <= min_window_seconds (never by a fixed depth cap),
      or when the shared request budget is exhausted (raises RuntimeError).
      Sub-window failures raise RuntimeError and are never silently treated as empty coverage.
      Midpoint ownership: GDELT startdatetime/enddatetime are both inclusive.
          Left  branch covers [start,   mid − 1s] (enddatetime = mid − 1s).
          Right branch covers [mid,     end      ] (enddatetime = end).
          An article timestamped exactly at mid is owned exclusively by the right branch;
          an article at (mid − 1s) is owned exclusively by the left branch.
          There is no overlap and no gap between the two branches.
    - Shared mutable request budget passed through the entire recursive tree to bound HTTP requests.
    - Thread-local requests.Session for concurrent ThreadPoolExecutor thread-safety.
    - Strict canonical URL/headline+source deduplication.
      Articles with both empty URL and empty headline are never collapsed into each other.
    - Full rejection of invalid/unparseable timestamps without fabricating temporal placement.
    - Date-only GDELT timestamps are rejected (never silently converted to midnight).
    - Truncated windows (>= 250 results, unsplittable) are tracked as machine-readable diagnostics.
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
            "articles_rejected_missing_title": 0,
            "articles_rejected_company_match": 0,
            "articles_rejected_invalid_timestamp": 0,
            "articles_rejected_low_precision_timestamp": 0,
            "articles_rejected_out_of_range": 0,
            "articles_skipped_no_trading_session": 0,
            "articles_mapped_to_trading_sessions": 0,
            "duplicates_removed": 0,
            "articles_missing_published_at": 0,
            "articles_missing_url": 0,
            # Pagination / window-level diagnostics
            "pagination_splits": 0,
            "truncated_windows": 0,
            "incomplete_windows": 0,
            # complete_windows counts windows where the GDELT response was strictly below the
            # 250-record cap — i.e. the window was NOT silently truncated by the API limit.
            # This does NOT guarantee that all news in that period was discovered: GDELT's
            # own indexing coverage may be incomplete regardless of the response cap.
            "complete_windows": 0,
            "query_failures": 0,
            "pagination_budget_exhausted": 0,
        }

        # Internally tracked truncated window ranges for quality reporting.
        # Each entry is a tuple: (ticker, start_str, end_str).
        # Access via get_diagnostics() which returns a copy.
        self._truncated_ranges: List[Tuple[str, str, str]] = []
        self._truncated_ranges_lock = threading.Lock()

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

    def get_diagnostics(self) -> Dict[str, Any]:
        """
        Returns a snapshot of all diagnostic telemetry, including:
        - All stat counters (thread-safe copy).
        - 'truncated_ranges': list of (ticker, start_str, end_str) tuples identifying
          time windows that hit the GDELT 250-record cap and could not be subdivided
          further. These represent potentially incomplete coverage periods.
        """
        with self._stats_lock:
            snapshot = dict(self.stats)
        with self._truncated_ranges_lock:
            snapshot["truncated_ranges"] = list(self._truncated_ranges)
        return snapshot

    # ─── GDELT Query Construction ────────────────────────────────────────────────
    def _build_gdelt_query(self, ticker: str) -> str:
        """
        Builds a GDELT OR query from the primary company name and configured aliases,
        maximising recall while keeping the query precise.

        Selection criteria for alias inclusion:
        - Alias contains a space (multi-word phrases are specific enough), OR
        - Alias length >= _GDELT_ALIAS_MIN_LEN (4) characters.
        - Extremely short generic tokens (e.g. "IT") are excluded to avoid
          unrelated result flooding.

        The primary company name from STOCKS is always included first.
        Duplicates are removed case-insensitively.
        The final query is capped at 512 characters; lower-priority aliases
        (i.e. those beyond the primary name) are trimmed greedily if needed.

        The post-fetch is_relevant_to_company() filter remains the authoritative
        relevance gate — this query is solely for GDELT recall maximisation.
        """
        primary = STOCKS.get(ticker, ticker.split('.')[0])
        aliases = COMPANY_ALIASES.get(ticker, [])

        # Collect eligible candidates: primary name + all aliases except exact duplicate
        raw_candidates: List[str] = [primary]
        for alias in aliases:
            if alias != primary:
                raw_candidates.append(alias)

        # Filter: keep multi-word aliases OR single-word aliases meeting minimum length.
        # Deduplicate case-insensitively while filtering.
        seen_lower: Set[str] = set()
        multi_word: List[str] = []     # contains a space — more specific
        single_word: List[str] = []    # no space, len >= _GDELT_ALIAS_MIN_LEN

        for cand in raw_candidates:
            cand_stripped = cand.strip()
            if not cand_stripped:
                continue
            lower = cand_stripped.lower()
            if lower in seen_lower:
                continue
            seen_lower.add(lower)

            if " " in cand_stripped:
                multi_word.append(cand_stripped)
            elif len(cand_stripped) >= _GDELT_ALIAS_MIN_LEN:
                single_word.append(cand_stripped)
            # else: too short and no space — excluded from GDELT query

        # Deterministic priority ordering:
        #   1. Primary company name (first multi-word or first single-word, already first in raw_candidates)
        #   2. Remaining multi-word aliases (longest first for better specificity)
        #   3. Single-word aliases (longest first)
        # The primary name is always in exactly one of the two buckets and was appended
        # first, so it will sort to the front of its respective group naturally when we
        # sort by descending length — but to guarantee it stays first regardless of
        # length, we separate it out and prepend it explicitly.
        primary_stripped = primary.strip()

        # Remove primary from its bucket so we can prepend it unconditionally
        multi_word_rest = sorted(
            [a for a in multi_word if a != primary_stripped],
            key=lambda s: (-len(s), s)
        )
        single_word_rest = sorted(
            [a for a in single_word if a != primary_stripped],
            key=lambda s: (-len(s), s)
        )

        # Build ordered list: primary first, then multi-word aliases (longest first),
        # then single-word aliases (longest first).  All three branches produce the same
        # list structure; the if/elif only documents which bucket the primary fell into.
        if primary_stripped in multi_word or primary_stripped in single_word:
            selected: List[str] = [primary_stripped] + multi_word_rest + single_word_rest
        else:
            # Primary didn't pass the filter (too short, no space); include it anyway
            # as the mandatory first term so it is always present in the GDELT query.
            selected = [primary_stripped] + multi_word_rest + single_word_rest

        if not selected:
            selected = [primary_stripped]

        # Assemble OR query, capping at 512 characters.
        # Primary term is always included; trim lower-priority aliases greedily.
        # GDELT DOC API requires multi-term queries using boolean OR to be enclosed in parentheses:
        # e.g., ("Reliance Industries" OR "Reliance Jio" OR "Mukesh Ambani")
        MAX_QUERY_LEN = 512
        parts = [f'"{selected[0]}"']
        for term in selected[1:]:
            candidate_part = f' OR "{term}"'
            # +2 accounts for surrounding parentheses '(' and ')' when multiple terms are present
            if len("".join(parts)) + len(candidate_part) + 2 <= MAX_QUERY_LEN:
                parts.append(candidate_part)
            else:
                break

        if len(parts) > 1:
            return f"({''.join(parts)})"
        return parts[0]

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
            # For bare uppercase ITC, require strong financial/corporate vocabulary.
            #
            # IMPORTANT: do NOT call _has_financial_context() here — it includes \bit\b
            # (word-boundary match for 'it') which fires on ordinary sentences such as
            # "ITC launches initiative. It will help..." even with zero financial content.
            #
            # Do NOT use the full FINANCIAL_CONTEXT_KEYWORDS set here either: it contains
            # generic terms like 'board', 'market', 'sales', 'order', and 'management'
            # that appear in countless non-ITC corporate articles.  A headline such as
            # "ITC announces initiative as the board approves the order" would pass the
            # broad check despite 'ITC' referring to an unrelated organisation.
            #
            # Use _ITC_BARE_STRONG_SIGNALS_RE: each signal is matched as a whole word
            # via \b anchors, so short tokens like 'loss' cannot fire on 'glossy',
            # 'nse' cannot fire inside a longer token, etc.
            if _ITC_BARE_STRONG_SIGNALS_RE.search(text_lower):
                return True
        return False

    def _match_lt(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:l&t|larsen\s*(?:&|and)\s*toubro)\b', text_lower):
            return True
        if re.search(r'\bLT\b', text):
            if any(kw in text_lower for kw in [
                "construction", "infra", "order", "contract", "larsen", "toubro",
                "shares", "results", "infotech", "technology", "q1", "q2", "q3", "q4"
            ]):
                return True
            # Named executive signals: require "l&t" context nearby via phrase check
            if re.search(r'\b(?:sn\s+subramanian|s\.n\.\s+subramanian|am\s+naik|a\.m\.\s+naik)\b', text_lower):
                return True
        return False

    def _match_titan(self, text: str, text_lower: str) -> bool:
        # NOTE: bare "mia" is intentionally excluded — it is too short and generic
        # (e.g. "Mia Khalifa", "missing in action") and causes false-positive matches.
        # "Mia by Tanishq" is covered by the Tanishq branch below.
        if re.search(
            r'\b(?:titan\s+(?:company|ltd|limited|watches|jewellery|jewelry|eyeplus)|tanishq|fastrack|caratlane)\b',
            text_lower
        ):
            return True
        if re.search(r'\bTitan\b', text):
            if any(kw in text_lower for kw in [
                "tata", "jewellery", "jewelry", "watches", "tanishq", "quarter",
                "results", "shares", "stock", "profit", "sales", "eyewear"
            ]):
                return True
        return False

    def _match_sbi(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:state\s+bank\s+of\s+india|state\s+bank)\b', text_lower):
            return True
        if re.search(r'\bSBI\b', text):
            if any(kw in text_lower for kw in [
                # 'bank' is intentionally excluded from this substring list — it is short
                # enough to appear inside 'embankment'.  The word-boundary check below
                # (via _FINANCIAL_CONTEXT_WORDBOUND which includes \bbank\b) handles it.
                "banking", "shares", "stock", "lending", "npa", "dinesh khara",
                "quarter", "results", "loan", "card", "life", "mutual fund"
            ]):
                return True
            # Word-boundary check: catches 'bank' (whole word), 'npa', 'md', 'it services', etc.
            if _FINANCIAL_CONTEXT_WORDBOUND.search(text_lower):
                return True
        return False

    def _match_tcs(self, text: str, text_lower: str) -> bool:
        if re.search(r'\b(?:tata\s+consultancy\s+services|tata\s+consultancy)\b', text_lower):
            return True
        if re.search(r'\bTCS\b', text):
            if any(kw in text_lower for kw in [
                # 'tech' and 'deal' are intentionally excluded from this substring list.
                # 'tech' appears inside 'technical', 'technology', 'biotechnology';
                # 'deal' appears inside 'ideal', 'ordeal'.
                # Both are handled via the word-boundary check below.
                "tata", "contract", "shares", "stock", "quarter",
                "results", "krithivasan", "ceo", "earnings", "q1", "q2", "q3", "q4",
                "profit", "revenue", "dividend"
            ]):
                return True
            # Word-boundary check: catches 'tech' (whole word), 'deal' (whole word),
            # 'it' (as in 'IT services'), 'md', 'bank', etc.
            if _FINANCIAL_CONTEXT_WORDBOUND.search(text_lower):
                return True
        return False

    def _match_reliance(self, text: str, text_lower: str) -> bool:
        if re.search(
            r'\b(?:reliance\s+(?:industries|retail|jio|oil|telecom|digital|power|petroleum|bp|greens|ent)|mukesh\s+ambani|ril)\b',
            text_lower
        ):
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

    def _article_dedupe_key(self, article: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
        """
        Generates a stable, canonical deduplication key for an article record.

        Returns None if BOTH the normalized URL and normalized headline are empty,
        meaning the article cannot be safely keyed and must NOT be deduplicated
        against any other record (each such article is kept independently).

        Primary path (URL-based):  (ticker, trading_date, normalized_url)
        Fallback path (hl-based):  (ticker, trading_date, norm_headline::src_domain)
        """
        ticker = article.get("ticker", "")
        trading_date = article.get("trading_date", "")
        norm_url = self.normalize_url(article.get("url", ""))
        if norm_url:
            return (ticker, trading_date, norm_url)
        norm_h = self.normalize_headline(article.get("headline", ""))
        if not norm_h:
            # Both URL and headline are empty — cannot generate a meaningful key.
            # Return None to signal: do NOT deduplicate this record against anything.
            return None
        # Include source domain to prevent cross-publisher merges on shared headlines.
        raw_url = article.get("url", "") or ""
        try:
            netloc_lower = urllib.parse.urlparse(raw_url).netloc.lower()
            # Use startswith to strip the "www." prefix as a unit, NOT lstrip() which
            # strips individual characters from the set {'w', '.'} and would incorrectly
            # mutilate domains like "web.ndtv.com" → "eb.ndtv.com".
            src_domain = netloc_lower[4:] if netloc_lower.startswith("www.") else netloc_lower
        except Exception:
            src_domain = ""
        return (ticker, trading_date, f"{norm_h}::{src_domain}")

    # ─── Timestamp & NSE Trading Session Mapping ──────────────────────────────
    def parse_gdelt_timestamp(self, seendate_raw: str) -> Tuple[str, str, datetime.datetime, bool]:
        """
        Parses a GDELT seendate string (UTC) into a 4-tuple:

            (source_timestamp, seen_at_iso, ist_dt, date_only)

        where:
            source_timestamp : raw string from GDELT, preserved as audit field
            seen_at_iso      : ISO-8601 string with Asia/Kolkata (+05:30) offset
            ist_dt           : datetime object in Asia/Kolkata timezone
            date_only        : True if the GDELT timestamp carries only date precision
                               (YYYYMMDD), not full second-level precision (YYYYMMDDHHMMSS)

        Precision rules:
            - YYYYMMDDHHMMSS (exactly 14 digits after stripping non-numeric chars): full
              precision, date_only=False.  GDELT's canonical seendate is exactly this format;
              any other digit count is treated as malformed or low-precision and rejected.
            - YYYYMMDD (exactly 8 digits): date-only precision, date_only=True.
              These are NOT converted to midnight; the caller must reject them.
            - Anything else (including >14 digits that result from stripping non-numeric
              characters out of a corrupt or extended-format string): malformed — raises
              ValueError immediately.  The >14 case is NOT silently truncated to 14 digits
              because doing so would accept corrupted data and contradict the guarantee of
              zero fabricated temporal placement.

        Raises:
            LowPrecisionTimestampError
                When exactly 8 numeric digits are found (YYYYMMDD): date-only precision.
                This is a subclass of ValueError, so broad ``except ValueError`` callers
                still catch it, but callers that need to distinguish the low-precision case
                from a genuinely malformed timestamp can catch this subclass first.
                ``articles_rejected_low_precision_timestamp`` is incremented here, before
                the raise, so the caller must NOT increment it again.
            ValueError
                When the numeric digit count after stripping non-numeric characters is not
                exactly 8 or exactly 14.  This covers 0–7, 9–13, and >14 digit counts.
                In particular, >14 digits (e.g. from a corrupt or non-standard string) are
                rejected rather than silently truncated, because accepting the first 14 digits
                of a longer string would fabricate temporal precision from corrupted input.
                The caller is responsible for incrementing ``articles_rejected_invalid_timestamp``.

        There is NO fallback to datetime.now(). Missing or malformed timestamps
        are always rejected without fabricating temporal placement.
        """
        raw_clean = re.sub(r'[^0-9]', '', str(seendate_raw))

        if len(raw_clean) == 14:
            # Canonical GDELT seendate format: exactly YYYYMMDDHHMMSS (14 digits).
            dt_utc = datetime.datetime.strptime(raw_clean, "%Y%m%d%H%M%S").replace(tzinfo=self.tz_utc)
            ist_dt = dt_utc.astimezone(self.tz_market)
            seen_at_iso = ist_dt.isoformat()
            return str(seendate_raw), seen_at_iso, ist_dt, False

        if len(raw_clean) == 8:
            # Exactly 8 digits: YYYYMMDD date-only precision.
            # Do NOT convert to midnight — a midnight timestamp would introduce false temporal
            # precision and could violate look-ahead-bias requirements (an article seen on
            # 2024-01-15 with no time component could belong to any hour of that day,
            # possibly after market close).
            # Raises LowPrecisionTimestampError (a ValueError subclass) so the call site
            # can distinguish this case from a genuinely malformed timestamp and avoid
            # double-counting across both diagnostic counters.
            self._inc_stat("articles_rejected_low_precision_timestamp")
            logger.debug(
                "Rejected date-only GDELT timestamp '%s' — insufficient precision "
                "to safely assign to a trading session without look-ahead risk.",
                seendate_raw
            )
            raise LowPrecisionTimestampError(
                f"Date-only GDELT timestamp (insufficient precision): '{seendate_raw}'"
            )

        # Any other digit count (0–7, 9–13, or >14) does not map to a known GDELT format.
        # >14 digits specifically arise when non-numeric corruption is stripped away by the
        # re.sub() above, leaving more raw digits than the YYYYMMDDHHMMSS format expects.
        # Silently truncating to the first 14 digits would accept corrupted data and fabricate
        # temporal precision from an unknown source — this is explicitly rejected.
        raise ValueError(
            f"Malformed or unparseable GDELT timestamp (unexpected digit count "
            f"{len(raw_clean)}): '{seendate_raw}'"
        )

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
        Maps an article's IST timestamp to the correct NSE trading session date.

        Temporal provenance
        -------------------
        The ``ist_dt`` parameter is expected to come from GDELT's ``seendate`` field
        (converted to IST), NOT from a verified publisher publication timestamp.
        GDELT's seendate records when GDELT's crawler indexed the article, which can
        lag the actual publication time by minutes or hours.

        This means the guarantee this function provides is:
            "The article was seen/indexed by GDELT before the mapped trading cutoff."
        It is NOT:
            "The article was published before the mapped trading cutoff."

        For a stronger temporal guarantee, callers that have access to a verified
        ``published_at`` field should pass that value instead.  For GDELT-only records,
        the seendate-based mapping is the best available approximation.  Each article
        dict carries ``timestamp_basis="gdelt_seendate"`` to make this provenance explicit.

        Look-ahead bias elimination rules
        ----------------------------------
        1. Trading day D + article arrives before 15:30:00 IST  →  D
           (information was available before market close on D)
        2. Trading day D + article arrives at/after 15:30:00 IST  →  next_trading_day(D)
           (after-hours news; not usable for same-day D signal)
        3. Weekend or NSE holiday  →  next_trading_day(date)
           (market was closed; news belongs to the next open session)
        4. No valid next session in the calendar  →  None  (article is skipped)

        Downstream modeling semantics
        ------------------------------
        The sentiment row produced for trading day D is computed from articles
        **available to a trader before D closes** (rule 1 above).  The model uses
        this row as a feature for **predicting the next session's (D+1) direction**,
        NOT the movement of D itself.  Do not change this mapping unless the
        training pipeline is verified to use D-sentiment to predict D (same-day).

        Parameters
        ----------
        ist_dt : datetime.datetime
            Article timestamp localised to Asia/Kolkata (IST).

        Returns
        -------
        str or None
            YYYY-MM-DD trading date, or None if no valid session exists.

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
        _recursing: bool = False,
        _request_budget: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetches GDELT articles for a UTC datetime range with recursive bisection
        pagination whenever the 250-record GDELT limit is reached.

        Parameters
        ----------
        ticker : str
            NSE ticker symbol (e.g. 'RELIANCE.NS').
        start_dt : datetime.datetime
            Window start — must be timezone-aware (UTC).
        end_dt : datetime.datetime
            Window end — must be timezone-aware (UTC) and strictly after start_dt.
        min_window_seconds : int
            Minimum sub-window size in seconds before bisection stops (default 3600 = 1 hour).
        _recursing : bool
            Internal flag; True when called from a recursive bisection branch.
        _request_budget : Optional[List[int]]
            Internal shared mutable counter [remaining_requests].
            Initialised automatically at the root call; passed as-is to all recursive
            calls so the entire pagination tree shares a single budget.

        Returns
        -------
        List[Dict[str, Any]]
            Validated, deduplicated articles mapped to NSE trading sessions.
            Return type is always List[Dict] regardless of pagination depth.

        Raises
        ------
        ValueError
            If start_dt >= end_dt, or if either timestamp is timezone-naive.
        RuntimeError
            If all HTTP retries fail for a window, or if the shared request
            budget is exhausted before pagination completes.

        Pagination guarantees
        ----------------------
        - Never silently truncates at 250 articles.
        - Bisection continues until sub-windows are below min_window_seconds,
          or until the shared budget is exhausted (raises RuntimeError).
        - Non-overlapping boundaries (GDELT startdatetime/enddatetime are both inclusive):
            Left  sub-request : startdatetime=start, enddatetime=mid − 1s  (inclusive on both ends)
            Right sub-request : startdatetime=mid,   enddatetime=end        (inclusive on both ends)
          An article at exactly mid belongs only to the right branch; an article at (mid − 1s)
          belongs only to the left branch.  There is no overlap and no gap.
        - Sub-window failures raise RuntimeError immediately — never silently absorbed.
        - Complete windows (< 250 results) increment complete_windows.
        - Truncated windows (>= 250, unsplittable) increment truncated_windows and
          incomplete_windows, and are recorded in self._truncated_ranges.
        """
        # ── Input validation ──────────────────────────────────────────────────
        if start_dt.tzinfo is None or end_dt.tzinfo is None:
            raise ValueError(
                f"fetch_gdelt_window requires timezone-aware datetimes; "
                f"got start_dt.tzinfo={start_dt.tzinfo!r}, end_dt.tzinfo={end_dt.tzinfo!r}"
            )

        # Normalize to whole seconds — GDELT's API operates at second-level precision.
        # Microseconds in start_dt/end_dt would silently be dropped in strftime("%Y%m%d%H%M%S")
        # and could cause the post-normalization inequality check to flip on equal walls.
        # Timezone information is preserved; only the sub-second component is zeroed.
        start_dt = start_dt.replace(microsecond=0)
        end_dt = end_dt.replace(microsecond=0)

        if start_dt >= end_dt:
            raise ValueError(
                f"fetch_gdelt_window requires start_dt < end_dt; "
                f"got start_dt={start_dt.isoformat()}, end_dt={end_dt.isoformat()}"
            )

        # ── Shared request budget initialisation (root call only) ─────────────
        # IMPORTANT: recursive calls must pass the SAME _request_budget list object.
        # Never create a fresh budget inside a recursive call.
        if _request_budget is None:
            _request_budget = [GDELT_MAX_REQUESTS_PER_WINDOW]

        # ── Budget check before issuing the HTTP request ──────────────────────
        if _request_budget[0] <= 0:
            self._inc_stat("pagination_budget_exhausted")
            msg = (
                f"GDELT pagination budget exhausted for {ticker} "
                f"({start_dt.strftime('%Y%m%d%H%M%S')} to {end_dt.strftime('%Y%m%d%H%M%S')}). "
                f"Some sub-windows were not fetched. Coverage for this period is INCOMPLETE."
            )
            logger.error(msg)
            raise RuntimeError(msg)

        # Consume one unit of the shared budget
        _request_budget[0] -= 1

        # ── Build GDELT query ──────────────────────────────────────────────────
        query = self._build_gdelt_query(ticker)

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
        raw_items: List[Dict[str, Any]] = []
        fetch_success = False
        last_error = None
        # 429s get their own retry budget so a throttling burst does not prematurely
        # exhaust the general error retry pool.
        # - MAX_ATTEMPTS   : total loop iterations (7) — covers transient network flaps
        # - MAX_RL_RETRIES : max 429 responses before giving up specifically on rate-limits
        MAX_ATTEMPTS   = 7
        MAX_RL_RETRIES = 5
        rate_limit_attempts = 0

        self._inc_stat("api_requests")

        for attempt in range(MAX_ATTEMPTS):
            try:
                # Mandatory process-wide rate-limit governor (Lock + monotonic).
                # Enforces minimum interval between consecutive HTTP requests across
                # all worker threads, retry loops, and recursive branches.
                _global_gdelt_rate_limiter.wait()
                res = session.get(GDELT_DOC_API_URL, params=params, timeout=15)
                if res.status_code == 200:
                    body = res.text.strip()
                    if not body:
                        # GDELT returns HTTP 200 with an empty body when a query produces
                        # zero results for the requested time window.  This is NOT a failure
                        # — it simply means no articles were indexed in that interval.
                        # Treat as zero results and stop retrying immediately.
                        raw_items = []
                        fetch_success = True
                        self._inc_stat("successful_requests")
                        break
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
                    rate_limit_attempts += 1
                    if rate_limit_attempts > MAX_RL_RETRIES:
                        last_error = f"HTTP 429 Rate Limit (exceeded {MAX_RL_RETRIES} retries)"
                        break  # Give up — persistent throttle, not transient
                    retry_after = res.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        sleep_sec = float(retry_after) + random.uniform(0.5, 1.5)
                    else:
                        sleep_sec = (2 ** rate_limit_attempts) * 4 + random.uniform(0.5, 2.0)
                    last_error = f"HTTP 429 Rate Limit (sleeping {sleep_sec:.1f}s, attempt {rate_limit_attempts}/{MAX_RL_RETRIES})"
                    logger.warning(
                        "GDELT 429 rate-limit for %s [%s to %s]; sleeping %.1fs (attempt %d/%d)",
                        ticker, start_str, end_str, sleep_sec, rate_limit_attempts, MAX_RL_RETRIES
                    )
                    # Close the session connection pool so the next retry uses a fresh TCP connection
                    try:
                        session.close()
                    except Exception:
                        pass
                    time.sleep(sleep_sec)
                else:
                    last_error = f"HTTP {res.status_code}"
                    time.sleep(2 + attempt * 2)
            except Exception as e:
                last_error = str(e)
                time.sleep(2 + attempt * 2)

        if not fetch_success:
            self._inc_stat("failed_requests")
            self._inc_stat("query_failures")
            raise RuntimeError(
                f"GDELT API request failed for {ticker} ({start_str} to {end_str}): {last_error}"
            )

        self._inc_stat("articles_retrieved", len(raw_items))

        # ── Process retrieved items for this window ────────────────────────────
        articles: List[Dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue

            title = (item.get("title") or "").strip()
            if not title:
                # Article has no headline at all — cannot filter or map safely.
                # Tracked under dedicated telemetry counter.
                self._inc_stat("articles_rejected_missing_title")
                continue
            if not self.is_relevant_to_company(title, ticker):
                self._inc_stat("articles_rejected_company_match")
                continue

            seendate_raw = (item.get("seendate") or "").strip()
            try:
                # parse_gdelt_timestamp returns a 4-tuple.
                # LowPrecisionTimestampError (subclass of ValueError) is raised for date-only
                # timestamps; it already incremented articles_rejected_low_precision_timestamp
                # inside parse_gdelt_timestamp — do NOT also increment invalid_timestamp here.
                # Plain ValueError covers genuinely malformed/unparseable timestamps only.
                src_ts, seen_at_iso, ist_dt, _date_only = self.parse_gdelt_timestamp(seendate_raw)
            except LowPrecisionTimestampError:
                # articles_rejected_low_precision_timestamp already incremented — nothing more to do.
                continue
            except ValueError:
                # Genuinely malformed timestamp (not a precision issue).
                self._inc_stat("articles_rejected_invalid_timestamp")
                continue

            trading_date = self.map_to_nse_trading_session(ist_dt)
            if not trading_date:
                # Beyond trading calendar with no subsequent session
                self._inc_stat("articles_skipped_no_trading_session")
                continue

            if trading_date not in self.trading_calendar:
                # map_to_nse_trading_session returned a non-None date that is not in
                # the calendar — this is the "out of range" boundary case (e.g. the
                # mapped next-trading-day lies beyond the calendar's end).
                self._inc_stat("articles_rejected_out_of_range")
                continue

            self._inc_stat("articles_mapped_to_trading_sessions")
            # articles_missing_published_at counts every GDELT article that was mapped to a
            # trading session.  Because GDELT's seendate is its own index/observation time and
            # NOT a verified publisher publication timestamp, published_at is always None for
            # GDELT-sourced records.  This counter therefore equals articles_mapped_to_trading_sessions
            # for GDELT; it exists so cross-source pipelines can distinguish sources that do
            # supply a verified published_at from those (GDELT) that do not.
            self._inc_stat("articles_missing_published_at")

            raw_url = (item.get("url") or "").strip()
            if not raw_url:
                self._inc_stat("articles_missing_url")

            articles.append({
                "ticker": ticker,
                "company": STOCKS.get(ticker, ""),
                "headline": title,
                "source": "GDELT",
                "url": raw_url,
                "published_at": None,  # Explicitly None: do not invent publication times.
                "seen_at": seen_at_iso,
                "source_timestamp": src_ts,
                # timestamp_basis records which timestamp was used for trading-session mapping.
                # For all GDELT-sourced records this is always 'gdelt_seendate': the time GDELT
                # indexed the article, which may differ from the actual publisher publication time.
                # Downstream pipelines MUST NOT assume this equals the article's original
                # publish time.  It is preserved here for provenance/audit purposes.
                "timestamp_basis": "gdelt_seendate",
                "trading_date": trading_date
            })

        # ── Recursive splitting or window completion ───────────────────────────
        duration_sec = (end_dt - start_dt).total_seconds()

        if len(raw_items) >= GDELT_MAX_RECORDS and duration_sec > min_window_seconds:
            # Window hit the cap AND is still large enough to split — recurse.
            self._inc_stat("pagination_splits")

            # Compute midpoint using integer-second arithmetic to guarantee whole-second
            # boundaries.  start_dt and end_dt already have microsecond=0 (enforced above),
            # but dividing a timedelta by 2.0 can still produce a fractional-second result
            # when the window duration is an odd number of seconds (e.g. 3601s // 2 = 1800s
            # but / 2.0 = 1800.5s → microsecond=500000).  Fractional midpoints make the
            # left_end and right_start different after the recursive microsecond-normalization
            # step, potentially causing boundary drift across recursion levels.
            duration_int_sec = int((end_dt - start_dt).total_seconds())
            mid_dt = start_dt + datetime.timedelta(seconds=duration_int_sec // 2)

            # Sanity guard: mid_dt must be strictly between start_dt and end_dt.
            # If the window is so small that integer division collapses the midpoint onto
            # a boundary, we cannot split safely — treat this window as truncated instead.
            if mid_dt <= start_dt or mid_dt >= end_dt:
                self._inc_stat("truncated_windows")
                self._inc_stat("incomplete_windows")
                with self._truncated_ranges_lock:
                    self._truncated_ranges.append((ticker, start_str, end_str))
                logger.warning(
                    "[%s] TRUNCATED WINDOW: window too small to bisect safely "
                    "(%s to %s, %ds). Returning partial results.",
                    ticker, start_str, end_str, duration_int_sec
                )
                articles = self._deduplicate_articles(articles)
                return articles

            # GDELT boundary semantics (both startdatetime and enddatetime are inclusive):
            #   Left  sub-request : startdatetime=start_str,   enddatetime=(mid - 1s)_str
            #   Right sub-request : startdatetime=mid_str,     enddatetime=end_str
            # An article timestamped exactly at mid_dt is fetched ONLY by the right branch.
            # An article timestamped exactly at (mid_dt - 1s) is fetched ONLY by the left.
            # There is no overlap and no gap between the two windows.
            left_end_dt = mid_dt - datetime.timedelta(seconds=1)

            # Polite jitter before sub-requests to reduce HTTP 429 risk
            time.sleep(2 + random.uniform(0.2, 0.8))

            # Sub-window failures raise RuntimeError immediately; they are NEVER silently
            # absorbed or substituted with a subset of the parent's truncated 250-item list.
            # The SAME _request_budget object is passed to both branches so the tree shares
            # one global budget — no branch can create a fresh local budget.
            left_arts: List[Dict[str, Any]] = self.fetch_gdelt_window(
                ticker, start_dt, left_end_dt, min_window_seconds,
                _recursing=True, _request_budget=_request_budget
            )

            time.sleep(2 + random.uniform(0.2, 0.8))

            right_arts: List[Dict[str, Any]] = self.fetch_gdelt_window(
                ticker, mid_dt, end_dt, min_window_seconds,
                _recursing=True, _request_budget=_request_budget
            )

            # Combine and deduplicate across the two non-overlapping sub-intervals
            articles = self._deduplicate_articles(left_arts + right_arts)

        elif len(raw_items) >= GDELT_MAX_RECORDS and duration_sec <= min_window_seconds:
            # Window hit the 250-record cap but is already at or below the minimum
            # splittable size.  Coverage for this interval is POTENTIALLY INCOMPLETE.
            self._inc_stat("truncated_windows")
            self._inc_stat("incomplete_windows")
            # complete_windows is intentionally NOT incremented here.

            with self._truncated_ranges_lock:
                self._truncated_ranges.append((ticker, start_str, end_str))

            logger.warning(
                "[%s] TRUNCATED WINDOW: minimum window (%ds) reached but result count=%d "
                "still hits the GDELT cap (%d). Articles in [%s, %s] may be incomplete. "
                "Consider reducing min_window_seconds to improve coverage.",
                ticker, min_window_seconds, len(raw_items), GDELT_MAX_RECORDS,
                start_str, end_str
            )
            articles = self._deduplicate_articles(articles)

        else:
            # Window returned fewer than GDELT_MAX_RECORDS — coverage is complete.
            self._inc_stat("complete_windows")
            articles = self._deduplicate_articles(articles)

        return articles

    def _deduplicate_articles(self, articles_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicates article records using canonical deduplication keys.

        Primary key  (URL-based):      (ticker, trading_date, normalized_url)
        Fallback key (headline-based): (ticker, trading_date, normalized_headline::src_domain)

        The source domain is included in the headline fallback key to avoid merging
        genuinely different articles that happen to share a headline across publishers
        (e.g., a Reuters wire republished verbatim by multiple outlets with the same
        normalized headline but distinct URLs that were lost during URL normalization).
        Only articles from the exact same publisher with the exact same headline are
        treated as duplicates under the fallback path.

        Articles for which BOTH the normalized URL and normalized headline are empty
        are never deduplicated against each other — each such article is preserved
        independently to avoid spurious collapsing of unrelated records.
        """
        seen_keys: Set[Tuple[str, str, str]] = set()
        unique: List[Dict[str, Any]] = []

        for a in articles_list:
            key = self._article_dedupe_key(a)

            if key is None:
                # Both URL and headline are empty: cannot generate a meaningful key.
                # Preserve the article independently without deduplication.
                unique.append(dict(a))
                continue

            if key in seen_keys:
                self._inc_stat("duplicates_removed")
            else:
                seen_keys.add(key)
                unique.append(dict(a))

        return unique
