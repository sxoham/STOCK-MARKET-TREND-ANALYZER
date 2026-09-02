import urllib.parse
import time
import random
import datetime
import re
import bisect
import threading
import logging
from typing import List, Dict, Any, Tuple, Optional, Set, Union
import zoneinfo
import requests

from .config import (
    STOCKS, COMPANY_ALIASES, MARKET_TIMEZONE,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE, GDELT_MAX_RECORDS,
    GDELT_MAX_REQUESTS_PER_WINDOW, GDELT_REQUEST_SLEEP_SECONDS,
    CORPORATE_ENTITY_LIFECYCLES
)

logger = logging.getLogger(__name__)

GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

class GDELTRateLimitExhausted(RuntimeError):
    """
    Raised when all dedicated GDELT HTTP 429 rate-limit retries are exhausted.
    Signals the pipeline to open the circuit breaker and pause crawling safely.
    """
    pass

class GDELTRateLimiter:
    """
    Process-wide thread-safe rate limiter for GDELT API requests.
    Coordinates dispatch slots so only one GDELT HTTP request can begin
    within the configured interval across all worker threads, retry loops,
    and recursive bisection branches.

    Crucially: does NOT hold the lock while waiting for HTTP response data or while sleeping.
    The lock is held solely to atomically coordinate and allocate request start timestamps.
    """
    def __init__(self, min_interval: float = GDELT_REQUEST_SLEEP_SECONDS):
        self.min_interval = float(min_interval)
        self._lock = threading.Lock()
        self._next_allowed_time = 0.0

    def wait(self) -> float:
        """
        Atomically reserves the next available request time slot under lock,
        then sleeps OUTSIDE the lock until that time slot arrives.
        Returns the duration slept (in seconds).
        """
        with self._lock:
            now = time.monotonic()
            # If the next allowed time is in the past (e.g. idle period), reset to now
            target_time = max(now, self._next_allowed_time)
            # Advance next allowed time by min_interval for subsequent callers
            self._next_allowed_time = target_time + self.min_interval
            sleep_duration = max(0.0, target_time - now)

        if sleep_duration > 0:
            time.sleep(sleep_duration)

        return sleep_duration

    def reset(self):
        """Resets the rate limiter state (useful for test isolation)."""
        with self._lock:
            self._next_allowed_time = 0.0

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

# Short tokens that require whole-word (\b-bounded) matching to avoid substring collisions:
# Without \b boundary matching, substring searches on 'it' would erroneously match inside
# words like 'schedule' or 'distribution'; 'md' inside 'cmd'; 'npa' inside 'unpaid';
# 'tech' inside 'technical'; 'auto' inside 'automatic'; 'deal' inside 'ideal';
# 'power' inside 'empower'; 'tax' inside 'syntax'; 'bank' inside 'embankment'.
# With \b boundaries, these tokens only match as standalone words.
_FINANCIAL_CONTEXT_WORDBOUND = re.compile(
    r'\b(?:it|md|npa|tech|auto|deal|power|tax|bank)\b', re.IGNORECASE
)

# ─── ITC Refined Matcher Rules & Patterns ─────────────────────────────────────
# Negative disambiguation exclusions for GST / VAT / Input Tax Credit, Foreign Imperial Brands,
# US International Trade Commission, San Antonio Institute of Texan Cultures,
# generic smoking/nicotine research, static pages, SAICA exam acronyms, and foreign job postings.
_ITC_EXCLUSIONS_RE: re.Pattern = re.compile(
    r'\b(?:'
    # GST / VAT / Input Tax Credit Exclusions
    r'gst(?:\s+officers?|\s+notice|\s+authorities|\s+detects?|\s+claims?)?'
    r'|input\s+tax\s+credit'
    r'|fake\s+itc'
    r'|itc\s+claims?'
    r'|excess\s+itc\s+claims?'
    r'|bogus\s+firms?'
    r'|tax\s+credit'
    r'|itc\s+rules?'
    r'|itc\s+reversals?'
    r'|itc\s+availment'
    r'|itc\s+entitlements?'
    r'|itc\s+eligibility(?:\s+on\s+sales)?'
    r'|maharashtra\s+itc'
    r'|vat\s+dealer'
    r'|itc\s+admissible'
    r'|aar'
    # US International Trade Commission & Legal Proceedings
    r'|international\s+trade\s+commission'
    r'|usitc'
    r'|international\s+trade\s+centre'
    r'|administrative\s+law\s+judge'
    r'|alj'
    r'|section\s+337'
    r'|patent\s+infringement'
    r'|import\s+ban'
    r'|exclusion\s+order'
    r'|itc\s+investigations?'
    r'|post-loper\s+bright'
    r'|wit\s+itc'
    r'|at\s+the\s+itc'
    # Institute of Texan Cultures / San Antonio
    r'|institute\s+of\s+texan\s+cultures'
    r'|texan\s+cultures'
    r'|san\s+antonio(?:[\'s]*)?\s+(?:itc|building|landmark|museum|campus)'
    r'|itc\s+building'
    r'|itc\s+gets\s+landmark'
    r'|hemisfair'
    # Foreign Brands & SAICA Exams & Generic Research
    r'|itc\.ua'
    r'|imperial\s+tobacco\s+(?:canada|uk|ukraine|quebec|cluster|poland)'
    r'|imperial\s+brands'
    r'|minister\s+holland'
    r'|quebec\s+coalition'
    r'|reducing\s+nicotine'
    r'|smoking\s*-\s*health\s+risks'
    r'|nicotine\s+pouches'
    r'|smoking-cessation'
    r'|what\s+is\s+stocks'
    r'|awarded\s+the\s+best\s+new\s+hotel'
    r'|wiltshire\s+store'
    r'|forest\s+fires'
    r'|carmel\s+cottage'
    r'|allied\s+blenders'
    r'|glamour\s+to\s+f1'
    r'|rizla'
    r'|saica'
    r'|itc\s+exams?'
    r'|board\s+exams?'
    r'|job\s+openings?'
    r'|itc\s*-\s*[\u0400-\u04FF]'
    r')\b|\|\s*itc\b',
    re.IGNORECASE
)

# Positive multi-word entities definitively belonging to the ITC.NS family
_ITC_POSITIVE_RE: re.Pattern = re.compile(
    r'\b(?:'
    r'itc\s+(?:ltd|limited|infotech|paperboards|agri|agro|foods?|personal\s+care|bukhara)'
    r'|imperial\s+tobacco\s+company\s+(?:limited|of\s+india)'
    r'|sanjiv\s+puri'
    r'|संजीव\s+पुरी'
    r')\b',
    re.IGNORECASE
)

# Transition & Demerger Restructuring Indicators for ITC Hotels -> ITC.NS
_ITC_HOTELS_TRANSITION_RE: re.Pattern = re.compile(
    r'\b(?:'
    r'itc\s+(?:hotels?|ratnadipa)\b.*?\b(?:demerger|demerged|spinoff|spin-off|spin\s+off|trades\s+sans|sans|adjusts|shareholders?|allotment|entitlement|scheme\s+of\s+arrangement|डिमर्जर|scission|split)\b'
    r'|(?:demerger|demerged|spinoff|spin-off|spin\s+off|trades\s+sans|sans|adjusts|shareholders?|allotment|entitlement|scheme\s+of\s+arrangement|डिमर्जर|scission|split)\b.*?\bitc\s+(?:hotels?|ratnadipa)\b'
    r')\b',
    re.IGNORECASE
)

# Operational & Hospitality Expansion Indicators for ITC Hotels
_ITC_HOTELS_OPERATIONAL_RE: re.Pattern = re.compile(
    r'\b(?:'
    r'itc\s+(?:hotels?|ratnadipa)\b.*?\b(?:to\s+launch|launches|luxury|expansion|expands|reopens|makeover|investment|property|properties|hospitality|colombo|overseas|nepal|profit|results|revenue|earnings|q[1-4]|shares|hotel\s+business|hotels\s+business|होटल)\b'
    r'|(?:to\s+launch|launches|luxury|expansion|expands|reopens|makeover|investment|property|properties|hospitality|colombo|overseas|nepal|profit|results|revenue|earnings|q[1-4]|shares|hotel\s+business|hotels\s+business|होटल)\b.*?\bitc\s+(?:hotels?|ratnadipa)\b'
    r'|\'welcom\'\s+to\s+a\s+makeover\s+of\s+itc'
    r')\b',
    re.IGNORECASE
)

# Strong corporate, securities, and business vertical signals for bare "ITC"
_ITC_BARE_STRONG_SIGNALS: frozenset = frozenset({
    # Verticals & Products & Named Initiatives & Subsidiaries
    "tobacco", "cigarettes", "cigarette", "fmcg", "cloud kitchen", "e-choupal", "paperboard", "paperboards",
    "krishi mitra", "storii", "bukhara", "fortune hotels", "welcomhotel", "hotel business", "hotels business",
    # Securities & Financials & Market Actions
    "shares", "stock", "stocks", "profit", "loss", "revenue", "earnings", "ebitda", "margin", "margins",
    "crore", "quarter", "results", "dividend", "pat", "gross revenue", "net profit", "return", "returns",
    "multibagger", "largecap", "blue-chip", "rally", "gain", "gains", "loser", "losers", "deliver",
    "jump", "jumps", "surge", "surges", "soars", "hits", "zooms", "snaps", "trades sans", "sans", "adjusts",
    # Markets & Indices & Macro/Budget
    "nifty", "sensex", "bse", "nse", "d-street", "dalal street", "block deals", "block deal", "f&o",
    "budget", "sin tax", "benefit", "benefits", "benefitting", "beneficiary", "trade", "trading", "trade spotlight",
    # Valuation & Analysts & Corporate Action
    "analyst", "target", "rating", "valuation", "buyback", "acquisition", "acquires", "acquire", "buys", "buy",
    "stake", "invests", "investment", "invest", "demerger", "spinoff", "spin-off", "agm", "board meeting", "board approves",
    "brokerage", "brokerages", "interim dividend", "outperform", "jefferies", "motilal", "nuvama", "sharekhan",
    "picks", "top picks", "shareholder", "shareholders", "entitlement", "to list", "listing",
    # Executive & Corporate Governance & Multilingual
    "sanjiv puri", "david robert simpson", "director", "resigns", "appoints", "guidance", "expansion", "expands", "overseas",
    "डिमर्जर", "संजीव पुरी"
})

_ITC_BARE_STRONG_SIGNALS_RE: re.Pattern = re.compile(
    r'\b(?:' +
    '|'.join(re.escape(tok) for tok in sorted(_ITC_BARE_STRONG_SIGNALS, key=len, reverse=True)) +
    r')\b',
    re.IGNORECASE,
)

# Disambiguation exclusions for unrelated Reliance / ADAG entities (e.g. Reliance Power, Infrastructure, Capital, etc.)
_RELIANCE_EXCLUSIONS_RE: re.Pattern = re.compile(
    r'\b(?:reliance\s+(?:power|infrastructure|infra|capital|communications?|com|naval|defence|defense|home\s+finance|commercial\s+finance|general\s+insurance|nippon(?:\s+life)?|bank(?:\s+pace)?|money|securities|broadcast|media|entertainment|health|life\s+insurance)|rcom|r-com)\b',
    re.IGNORECASE
)

# Positive entities definitively belonging to the RELIANCE.NS entity family
_RELIANCE_POSITIVE_RE: re.Pattern = re.compile(
    r'\b(?:reliance\s+(?:industries(?:\s+(?:ltd|limited))?|retail(?:\s+ventures)?|jio(?:\s+infocomm)?|petroleum|oil|bp|new\s+energy|greens)|jio\s+platforms|mukesh\s+ambani|ril)\b',
    re.IGNORECASE
)

# Strong RIL-specific contextual indicators for bare "Reliance" mentions (verticals, key assets, core business)
_RELIANCE_RIL_SPECIFIC_CONTEXT_RE: re.Pattern = re.compile(
    r'\b(?:jamnagar|hazira|kg-d6|kg\s+d6|refinery|petrochemicals?|oil-to-chemicals|o2c|polyester|telecom|retail|jio|greens?|new\s+energy|giga\s+factory|agm|q[1-4]|annual\s+general\s+meeting)\b',
    re.IGNORECASE
)

# ─── URL & Document Quality Validation ──────────────────────────────────────────
def is_article_url(url: str) -> bool:
    """
    Validates whether a URL represents a genuine news article rather than a static CMS taxonomy,
    category, tag, author, search, or navigation index page.
    """
    if not url or not isinstance(url, str):
        return False

    url_clean = url.strip()
    if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
        return False

    try:
        parsed = urllib.parse.urlparse(url_clean)
        path = parsed.path.lower()
        query = parsed.query.lower()
    except Exception:
        return False

    # Check root domain / empty path (reject homepages, but preserve CMS post id queries like ?p=12345)
    if path in ("", "/"):
        if not re.search(r'(?:^|&)(?:p|post|id|article|article_id|story_id)=\d+', query):
            return False

    # Rejection of explicit taxonomy / tag / category / author / search paths
    # Matches standalone directory segments like /tag/..., /tags/..., /category/..., /author/..., /search/...
    if re.search(r'/(?:tags?|categor(?:y|ies)|authors?|search|topics?|archives?)(?:/|$)', path):
        return False

    # Rejection of search query parameters (e.g. ?q=..., ?search=..., ?s=..., ?tag=...)
    if re.search(r'(?:^|&)(?:q|query|search|s|tag)=', query):
        return False

    return True


# ─── LT.NS (Larsen & Toubro) Refined Matcher Rules & Patterns ─────────────────
# Disambiguation exclusions for unrelated LT acronyms (military ranks, technical voltage, sports, auto trims, medicine, geography, duty-free retail, bypass roads, job vacancy boards, Finnish Lassila & Tikanoja)
_LT_EXCLUSIONS_RE: re.Pattern = re.compile(
    r'\b(?:'
    # Military / Police / Administrative Ranks
    r'lt\s+(?:gen(?:eral)?|gov(?:ernor)?|col(?:onel)?|cmdr|commander|cdr|adm(?:iral)?|capt(?:ain)?|col|lieutenant)'
    r'|lieutenant'
    # Technical / Electrical non-L&T acronyms
    r'|long[\s\-]term'
    r'|low[\s\-]tension'
    r'|low[\s\-]temperature'
    r'|lt\s+(?:line|lines|panel|panels|cable|cables|consumer|consumers|tariff|tariffs|feeder|feeders|motor|motors)'
    # Automotive Trims
    r'|(?:silverado|tahoe|suburban|colorado|corvette|blazer|camaro|equinox|traverse|malibu|cruze|impala|trail\s+boss)\s+lt\b'
    r'|\blt\s+(?:trim|package|edition|trail\s+boss|model)'
    # Sports abbreviations
    r'|left\s+tackle'
    r'|\blt\s+(?:andrew\s+thomas|lawrence\s+taylor|ladainian\s+tomlinson|tyron\s+smith|trent\s+williams|lane\s+johnson|david\s+bakhtiari)'
    # Medical abbreviations
    r'|liver\s+transplant(?:ation)?'
    r'|leukotriene'
    # Foreign organizations & geography (Nordic Lassila & Tikanoja, Philippine LT Group, Larsen Ice Shelf)
    r'|lt\s+group(?:\s+inc)?'
    r'|larsen\s+c(?:\s+ice\s+shelf)?'
    r'|larsen\s+(?:bay|harbour|sound|inlet|glacier|ice\s+shelf)'
    r'|larsen\s+strings?'
    r'|lassila\s*(?:&|ja|and|\-)\s*tikanoja'
    r'|lat1v'
    r'|l&t:(?:llä|lla|n|tä|ta|ssä|ssa|lle|ltä|lta|stä|sta|kin|hen)'
    # Airport retail / Liquor & Tobacco disambiguation
    r'|liquor\s*(?:&|and)\s*tobacco'
    r'|duty[\s\-]free'
    r'|l&t\s+(?:contest|concession|tender|category)\b.*?\b(?:gimpo|incheon|airport|retail|duty[\s\-]free)\b'
    r'|\b(?:gimpo|incheon|airport|retail)\b.*?\bl&t\s+(?:contest|concession|tender|category)\b'
    # Geographical landmark reference (L&T bypass road)
    r'|l&t\s+bypass'
    # Routine job board / recruitment noise
    r'|recruits?\s+for'
    r'|hiring\s+for'
    r'|job\s+openings?'
    r'|walk[\s\-]in\s+interviews?'
    r'|job\s+vacanc(?:y|ies)'
    r'|recruitment\s+drive'
    r')\b',
    re.IGNORECASE
)

# Positive entities definitively belonging to the LT.NS (Larsen & Toubro) family
_LT_POSITIVE_RE: re.Pattern = re.compile(
    r'(?:\b(?:'
    # Core Corporate Entity
    r'larsen\s*(?:&|and)\s*toubro(?:\s+(?:limited|ltd))?'
    r'|larsen\s+toubro(?:\s+(?:limited|ltd))?'
    # Key Subsidiaries & Divisions
    r'|l&t\s+(?:construction|finance(?:\s+holdings)?|infotech|technology(?:\s+services)?|ts|metro(?:\s+rail)?|realty|energy|hydrocarbon|power|heavy\s+engineering|valves|shipbuilding|shipyard|defense|defence|semiconductor|semiconductors|edutech|sufin|chiyoda|mhi|sargent\s*&\s*lundy|howden|special\s+steels|precision\s+engineering)'
    r'|lt\s+(?:construction|finance|infotech|technology(?:\s+services)?|ts|metro|realty|hydrocarbon|valves|shipbuilding|semiconductor)'
    # Key Leadership
    r'|(?:s\.?\s*n\.?\s*subrahmanyan|s\.?\s*n\.?\s*subramanian|a\.?\s*m\.?\s*naik|sekharipuram\s+narayanan\s+subrahmanyan|anil\s+manibhai\s+naik)'
    r')\b|लार्सन\s*(?:एंड|एण्ड|ऐंड)\s*टुब्रो|एलएंडटी)',
    re.IGNORECASE
)

# Strong corporate, EPC, and market signals for bare "L&T" / "LT"
_LT_BARE_STRONG_SIGNALS: frozenset = frozenset({
    # EPC, Infrastructure & Industry
    "order", "orders", "contract", "contracts", "wins", "bags", "secures", "awarded", "pipeline", "epc",
    "infrastructure", "infra", "construction", "heavy engineering", "hydrocarbon", "power transmission",
    "electrolyser", "electrolysers", "green hydrogen", "defence", "defense", "missile", "submarine",
    "shipyard", "bullet train", "high-speed rail", "metro", "semiconductor", "chip", "capex",
    # Securities, Financials, Results & Markets
    "shares", "stock", "stocks", "profit", "loss", "revenue", "earnings", "ebitda", "margin", "margins",
    "crore", "quarter", "results", "dividend", "pat", "guidance", "order book", "order inflow",
    "nifty", "sensex", "bse", "nse", "d-street", "dalal street", "target", "analyst", "rating",
    "buyback", "multibagger", "largecap", "blue-chip", "rally", "gain", "gains", "trade", "trading",
    "brokerage", "brokerages", "outperform", "jefferies", "motilal", "nuvama", "sharekhan", "nomura",
    "top picks", "picks", "trade spotlight", "market cap", "mcap", "q1", "q2", "q3", "q4",
    # Leadership & Corporate
    "subrahmanyan", "subramanian", "am naik", "board approves", "agm"
})

_LT_BARE_STRONG_SIGNALS_RE: re.Pattern = re.compile(
    r'\b(?:' +
    '|'.join(re.escape(tok) for tok in sorted(_LT_BARE_STRONG_SIGNALS, key=len, reverse=True)) +
    r')\b',
    re.IGNORECASE
)

# ─── SBIN.NS (State Bank of India) Refined Matcher Rules & Patterns ───────────
# Negative disambiguation exclusions for foreign state banks, foreign SBI entities,
# pure macroeconomic research reports, and non-financial acronyms
_SBI_EXCLUSIONS_RE: re.Pattern = re.compile(
    r'\b(?:'
    # Foreign / Regional State Banks
    r'state\s+bank\s+of\s+(?:pakistan|vietnam|texas|cross\s+plains|india\s*\(california\)|fulton\s+county|the\s+lakes)'
    r'|(?:chinese|vietnam(?:ese)?|pakistan(?:i)?|us|uk|african|european|state[\s\-]owned)\s+state\s+banks?'
    r'|(?:central\s+bank\s+and\s+state\s+banks?|several\s+state\s+banks?|state\s+banks?\s+(?:intervene|discuss|report|face|warn|in\s+china))'
    # Foreign SBI Entities (Empirically Demonstrated)
    r'|sbi\s+(?:holdings|shinsei(?:\s+bank)?|vc\s+trade|crypto)'
    # Pure Macroeconomic Authorship Reports (when SBI is merely author/source, not subject)
    r'|(?:sbi\s+research|sbi\s+ecowrap|ecowrap)\b.*?\b(?:gdp|inflation|income\s+inequality|inequality|poverty|economy|economic\s+growth|tax\s+collection|tourism|ram\s+mandir|k[\s\-]shaped|msmes|norway|monetary\s+policy|rbi\s+policy|repo\s+rate|policy\s+rate)\b'
    r'|\b(?:gdp|inflation|income\s+inequality|inequality|poverty|economy|economic\s+growth|tax\s+collection|tourism|ram\s+mandir|k[\s\-]shaped|msmes|norway|monetary\s+policy|rbi\s+policy|repo\s+rate|policy\s+rate)\b.*?\b(?:sbi\s+research|sbi\s+ecowrap|ecowrap|says\s+sbi\s+report|sbi\s+report)\b'
    # Non-banking / Scientific / Business acronyms
    r'|small\s+business\s+index'
    r'|sterol\s+biosynthesis\s+inhibitor'
    r'|site[\s\-]directed\s+mutagenesis'
    r')\b',
    re.IGNORECASE
)

# Definitive positive entities for State Bank of India
_SBI_POSITIVE_RE: re.Pattern = re.compile(
    r'(?:\b(?:'
    r'state\s+bank\s+of\s+india(?:\s+(?:limited|ltd))?'
    r'|sbi\s+bank'
    r'|sbi\s+(?:chairman|chief|md|managing\s+director|deputy\s+md|board|management|ecb|yono)'
    r'|(?:dinesh\s+(?:kumar\s+)?khara|c\.?\s*s\.?\s*setty|challa\s+sreenivasulu\s+setty|cs\s+setty)\b.*?\b(?:sbi|state\s+bank|bank|lending|credit|npa|results|deposit|profit|loan)'
    r'|\b(?:sbi|state\s+bank|bank|lending|credit|npa|results|deposit|profit|loan)\b.*?\b(?:dinesh\s+(?:kumar\s+)?khara|c\.?\s*s\.?\s*setty|challa\s+sreenivasulu\s+setty|cs\s+setty)'
    r')\b|भारतीय\s*स्टेट\s*बैंक|स्टेट\s*बैंक\s*ऑफ\s*इंडिया|एसबीआई)',
    re.IGNORECASE
)

# Standalone Subsidiaries (Separately Listed & Routine Unlisted Product Coverage)
_SBI_SUBSIDIARY_SEPARATION_RE: re.Pattern = re.compile(
    r'\b(?:'
    r'sbi\s+life(?:\s+insurance)?'
    r'|sbi\s+cards?(?:\s+(?:and|&)\s+payment(?:\s+services)?)?'
    r'|sbi\s+mutual\s+fund'
    r'|sbi\s+general\s+insurance'
    r'|sbicap'
    r')\b',
    re.IGNORECASE
)

# Explicit parent materiality context allowing subsidiary news to attribute to SBIN.NS
_SBI_PARENT_MATERIALITY_RE: re.Pattern = re.compile(
    r'\b(?:'
    r'state\s+bank\s+of\s+india'
    r'|parent'
    r'|promoter'
    r'|holding'
    r'|stake(?:\s+sale|\s+divestment|\s+in|\s+cut)?'
    r'|sells?\s+(?:stake|shares)'
    r'|divest(?:s|ing|ment)?'
    r'|ipo'
    r'|listing'
    r'|dividend(?:\s+from|\s+to|\s+payout)?'
    r')\b',
    re.IGNORECASE
)

# Strong banking, credit, regulatory, and market signals for bare "SBI" or "State Bank"
_SBI_BARE_SIGNALS_RE: re.Pattern = re.compile(
    r'\b(?:'
    # Banking & Credit Operations
    r'bank|banking|lending|loan|loans|deposit|deposits|credit|casa|npa|npa\s+ratio|bad\s+loans?'
    r'|net\s+interest\s+income|nii|nim|interest\s+rate|repo\s+rate|mclr|home\s+loans?|car\s+loans?'
    r'|fixed\s+deposits?|fd\s+rates?|branch|branches|atm|atms|yono|bonds?|green\s+bonds?|infra\s+bonds?'
    r'|at[\s\-]1\s+bonds?|tier[\s\-]1|tier[\s\-]2|electoral\s+bonds?|bond\s+issuance'
    r'|fundraising|qip|capital\s+adequacy|raises?\s+(?:funds?|\$|\d+|crore)|raised\s+funds?'
    # Corporate Lending & Financing Syndications
    r'|raises?\s+(?:funding|\$|\d+|crore)\s+from|financed\s+by|lender|lenders|financing|credit\s+facility|syndicated\s+loan'
    # Fraud, Classification & Legal/Recovery
    r'|fraud\s+list|fraud\s+classification|fraud\s+label|fraud\s+tag|sarfaesi|debt\s+notice|recovery\s+notice'
    # Financials, Results, Markets, Brokerages & Regulators
    r'|shares?|stock|stocks?|quarter|results?|profit|loss|revenue|earnings|pat|dividend|ebitda'
    r'|nifty|sensex|bse|nse|d[\s\-]street|dalal\s+street|target|analyst|rating|brokerage|top\s+picks?|brokerage\s+picks?'
    r'|brokerage\s+recommendations?|picks?|motilal|jefferies|nomura|morgan\s+stanley|goldman|macquarie|clsa'
    r'|rbi|reserve\s+bank|sebi|ministry\s+of\s+finance|finmin|supreme\s+court'
    r')\b',
    re.IGNORECASE
)

# ─── TCS.NS (Tata Consultancy Services) Matching Regular Expressions ────────
_TCS_EXCLUSIONS_RE = re.compile(
    r'\b(?:'
    r'tax\s+collected\s+at\s+source|tds(?:\s*(?:and|/|&|\+|,)\s*|\s+)tcs|tcs(?:\s*(?:and|/|&|\+|,)\s*|\s+)tds|'
    r'tcs\s+on\s+(?:foreign|remittance|credit\s+card|crypto|lrs|travel|overseas|spends?)|lrs\s+tcs|'
    r'tcs\s+rate|20%\s+tcs|tcs\s+exemption|tcs\s+provisions?|income\s+tax\s+tcs|'
    r'the\s+container\s+store|container\s+store\s+group|tecsys(?:\s+inc)?|'
    r'touring\s+club\s+(?:suisse|switzerland)|tcs\s+suisse|tcs\s+soccorso|tcs\s+leistet|tcs[\s\-]studie|'
    r'tcs[\s\-]patrouille|tcs[\s\-]sektion|tcs[\s\-]verkehrskonferenz|'
    r'tcs\s+group(?:\s+holding)?|tcs\s+group\s+berhad|tinkoff|trussville(?:\s+city\s+schools)?|'
    r'72\.\s*tcs|73\.\s*tcs|turnieju\s+tcs|konkurs(?:ie)?\s+tcs|kwalifikacj(?:e|ach)\s+tcs|vierschanzentournee|'
    r'town\s+centre\s+securities|'
    r'western\s+railway\s+tcs|railway\s+tcs|ticket\s+collector|copper\s+concentrate\s+tcs|tcs\s+index|'
    r'jaguar\s+tcs\s+racing|transitional\s+care|total\s+care\s+services|texas\s+computer\s+science|'
    r'temperature\s+controlled\s+storage|traction\s+control(?:\s+system)?|'
    r'tcs\s+ruhaniyat|tcs\s+free\s+meal'
    r')\b',
    re.IGNORECASE
)

_TCS_OTHER_TATA_RE = re.compile(
    r'\b(?:'
    r'tata\s+motors|tata\s+steel|tata\s+power|tata\s+technologies|tata\s+elxsi|'
    r'tata\s+communications|tata\s+consumer(?:\s+products)?|tata\s+chemicals|'
    r'tata\s+capital|tata\s+aia|tata\s+play|tata\s+digital|tata\s+advanced\s+systems|'
    r'tata\s+trusts'
    r')\b',
    re.IGNORECASE
)

_TCS_POSITIVE_RE = re.compile(
    r'\b(?:'
    r'tata\s+consultancy\s+services(?:\s+(?:ltd|limited))?|'
    r'tata\s+consultancy|'
    r'टाटा\s+कंसल्टेंसी\s+सर्विसेज'
    r')\b',
    re.IGNORECASE
)

_TCS_LEADERSHIP_CONTEXT_RE = re.compile(
    r'\b(?:'
    r'k\.?\s*krithivasan|krithivasan|rajesh\s+gopinathan|n\.?\s*chandrasekaran'
    r')\b.*?\b(?:'
    r'tcs|tata|it\s+services|tech|software|ceo|md|board|results|q[1-4]|earnings|genai|ai|h[\s\-]1b|visa'
    r')\b',
    re.IGNORECASE
)

_TCS_BARE_SIGNALS_RE = re.compile(
    r'\b(?:'
    # Earnings / Financial / Market Identity
    r'q[1-4]|quarterly|results?|earnings?|revenue|profit|margin|dividend|interim\s+dividend|record\s+date|'
    r'buyback|market\s+cap|m[\s\-]cap|mcap|target\s+price|brokerage|shares?|stock|stocks|d[\s\-]street|nse|bse|'
    r'nifty(?:50)?|sensex|valuation|outperform|buy\s+call|price\s+target|top\s+honchos|bhavcopy|'
    r'gainers?|laggards?|upgrades?|downgrades?|target|brand\s+value|sliding\s+rupee|forex\s+impact|'
    # IT Services / Enterprise Client Deals / Strategic Partnerships / Technology
    r'deals?|contracts?|partnerships?|pacts?|alliance|client|clients|vendor|wins?\s+(?:deal|contract|order|mandate)|'
    r'inks?\s+pact|inking\s+pact|collaborat(?:e|ion|ing|es)|research\s+(?:hub|centre|center)|'
    r'multi[\s\-]year|multi[\s\-]million|cloud|ai|generative\s+ai|genai|aws|azure|google\s+cloud|'
    r'digital\s+transformation|platform|post[\s\-]trade|core\s+banking|bancassurance|fintech|aviva|'
    r'enterprise\s+solutions|it\s+spending|tech\s+spending|it\s+major|it\s+firm|it\s+giant|it\s+services|'
    r'customer\s+satisfaction|business\s+unit|'
    r'partners?\s+with\s+(?:tcs|nvidia|fico|insper|tejas|aws|google|microsoft|c[\s\-]dot|cdot)|'
    r'ties\s+up\s+with\s+(?:tcs|nvidia|fico|insper|tejas)|'
    r'alliance\s+with\s+(?:tcs|nvidia|fico|insper|tejas)|'
    # Telecom / BSNL / 4G / 5G / Equipment Deployment
    r'telecom\s+equipment|chinese\s+telecom|telecom\s+oems?|'
    r'4g\s+sites|5g\s+tender|skips?\s+(?:bsnl|tender)|c[\s\-]dot\s+consortium|bsnl\s+data\s+centres?|'
    # Corporate Facilities / Physical & Geographic Expansion
    r'opens?\s+(?:new\s+)?delivery\s+cent(?:re|er)\s+in\s+france|delivery\s+cent(?:re|er)\s+in\s+france|'
    r'inaugurates?\s+(?:.*?\s+)?office|inaugurates?\s+oman\s+office|oman\s+office|'
    r'inaugurat(?:e|es|ed|ion)\s+(?:its\s+)?(?:vizag|kochi|office)|'
    r'foundation\s+stone|operations\s+in\s+visakhapatnam|campus\s+in\s+kochi|'
    # Contract Disputes / Terminations / Technical Glitches
    r'ends?\s+ties|severs?\s+ties|terminates?\s+(?:contract|deal|pact)|ends?\s+partnership|dealt\s+blow|'
    r'blow\s+to\s+tcs|technical\s+glitch(?:es)?|glitches|admissions?\s+tests?|oxford|debacle|contract\s+dispute|'
    r'broke\s+partnership|'
    # Workforce / HR / Labour / Office & Attendance Policies
    r'employees?|headcount|hirings?|hired|attrition|staff|relocat(?:e|ion|ing)|forced\s+transfers?|'
    r'stopped\s+pay|labour\s+(?:ministry|dept|department)|nites|return[\s\-]to[\s\-]office|rto|'
    r'promotions?|promoted|wfh|work\s+from\s+home|layoffs?|fresher|campus\s+hiring|salary|salary\s+hike|workman|'
    r'variable\s+pay|performance\s+bonus|work\s+from\s+office|wfo\s+exceptions?|office\s+attendance|'
    r'dependence\s+on\s+h[\s\-]1b|not\s+expecting.*?h[\s\-]1b|reskilling|'
    # Governance / Leadership / Executive Transitions / Legal
    r'executive\s+director|independent\s+director|board\s+of\s+directors|board|coo|cfo|cro|president|'
    r'vice\s+president|svp|passes\s+away|demise|obituary|quits?|resigns?|appointed|term\s+ends|'
    r'phiroz\s+vandrevala|daniel\s+callahan|rajanna|kholkar|high\s+court|court|it\s+analyst'
    r')\b',
    re.IGNORECASE
)


# ─── INFY.NS (Infosys Limited) Disambiguation Patterns ─────────────────────────
_INFY_EXCLUSIONS_RE = re.compile(
    r'\b(?:'
    # Founder non-corporate / personal / lifestyle / 70-hour work week viral debates
    r'70[\s\-]hour|70\s+hours|deepfake|deep\s+fake|kareena|rajinikanth|cinema|movie|'
    r'sudha\s+murty|sudha\s+murthy|akshata|sunak|catamaran|advice\s+to\s+youth|grandson|'
    r'gifted|shares\s+to\s+grandson|son[\s\-]in[\s\-]law|daughter|personal\s+life|parenting|'
    r'book\s+launch|uncommon\s+love|moral|ethics|spiritual|'
    r'respect\s+wealth\s+creators|work\s+life\s+balance|lazy|sloth|sleeping|vacation|'
    r'dating|marriage|wedding|love\s+story|simple\s+life|flying\s+economy|secret\s+of|'
    r'regrets?\s+not\s+letting|wrongly\s+idealistic|never\s+allowed|advice|youth|shashi\s+tharoor|'
    r'anupam\s+kher|chitra\s+banerjee|memoir|story|journey|early\s+days|first\s+job|'
    r'family\s+separate|rohan\s+murty|son\s+rohan|brother|sister|college|iit|iim|'
    r'flight|economy\s+class|co[\s\-]passenger|encounter|viral\s+post|viral\s+pic|'
    # Generic campus recruitment / college job listing noise / social profiles
    r'top\s+10\s+colleges|internship\s+offer|recruitment\s+drive\s+in\s+college|freshers\s+apply|job\s+opening|'
    r'social\s+good|tech\s+money\s+for\s+social\s+good'
    r')\b',
    re.IGNORECASE
)

_INFY_FOUNDER_CORPORATE_CONTEXT_RE = re.compile(
    r'\b(?:'
    r'infosys|infy|q[1-4]|quarterly|results?|earnings?|revenue|profit|margin|guidance|'
    r'dividend|buyback|shares?|stock|stocks|stake|holding|valuation|salil\s+parekh|'
    r'insemi|acquisition|deal|contract|board|ceo|cfo|attrition|headcount|layoffs?|hiring'
    r')\b',
    re.IGNORECASE
)

_INFY_POSITIVE_RE = re.compile(
    r'\b(?:'
    r'infosys\s+(?:limited|ltd|technologies|bpm|consulting|public\s+services|finacle)|'
    r'salil\s+parekh|nandan\s+nilekani'
    r')\b',
    re.IGNORECASE
)

_INFY_BARE_SIGNALS_RE = re.compile(
    r'\b(?:'
    # Earnings / Financials / Capital Markets / Brokerage
    r'q[1-4]|quarterly|results?|earnings?|revenue|profit|margin|guidance|guidance\s+cut|'
    r'dividend|interim\s+dividend|record\s+date|buyback|market\s+cap|m[\s\-]cap|mcap|'
    r'target\s+price|brokerage|shares?|stock|stocks|d[\s\-]street|nse|bse|nifty(?:50)?|'
    r'sensex|valuation|outperform|buy\s+call|price\s+target|top\s+gainers?|gainers?|'
    r'laggards?|upgrades?|downgrades?|target|soars?|rall(?:y|ies)|slumps?|tumbles?|'
    r'gains?|support|short\s+call|inside\s+edge|mystery\s+buyer|insemi|'
    # Commercial Deals / IT Services / Partnerships / Workforce
    r'deals?|contracts?|partnerships?|pacts?|alliance|client|clients|vendor|'
    r'wins?\s+(?:deal|contract|order|mandate)|cloud|ai|generative\s+ai|genai|'
    r'employees?|headcount|hirings?|hired|attrition|layoffs?|salary|salary\s+hike|'
    r'variable\s+pay|work\s+from\s+office|wfo|rto|salil\s+parekh|'
    # Multi-IT pair shorthand
    r'tcs\s+and\s+infy|infy\s+and\s+tcs|infy\s*,\s*tcs|tcs\s*,\s*infy|'
    r'ahead\s+of\s+infy|infy\s+q3|infy\s+q4'
    r')\b',
    re.IGNORECASE
)


# ─── ICICIBANK.NS (ICICI Bank Limited) Disambiguation Patterns ────────────────
_ICICIBANK_EXCLUSIONS_RE = re.compile(
    r'\b(?:'
    # Standalone subsidiaries without parent bank connection
    r'icici\s+prudential\s+life|icici\s+pru\s+life|icici\s+pru|icici\s+lombard|'
    r'icici\s+prudential\s+amc|icici\s+pru\s+amc|icici\s+prudential\s+mutual\s+fund|icici\s+pru\s+mutual\s+fund|'
    r'icici\s+prudential\s+asset\s+management|icici\s+venture|icici\s+foundation|'
    r'icici\s+home\s+finance|icici\s+hf|icici\s+securities|icici\s+direct|i[\s\-]sec|'
    # Generic personal loan calculator / SEO lists / campus recruitment
    r'personal\s+loan\s+calculator|provident\s+fund\s+loan|which\s+bank\s+is\s+offering|where\s+should\s+you\s+park|'
    r'top\s+10\s+colleges|freshers\s+apply|job\s+opening'
    r')\b',
    re.IGNORECASE
)

_ICICIBANK_13F_FILING_RE = re.compile(
    r'\b(?:'
    r'purchases?\s+\d+[\d,]*\s+shares|buys?\s+\d+[\d,]*\s+shares|sells?\s+\d+[\d,]*\s+shares|'
    r'shares\s+(?:sold|bought|purchased|acquired)\s+by|'
    r'stock\s+position\s+(?:trimmed|raised|boosted|cut|lowered|increased)|'
    r'has\s+\$[\d\.]+\s+million\s+(?:position|holdings?|stock\s+position)|'
    r'increases\s+stock\s+(?:holdings?|position)|decreases\s+stock\s+holdings?|grows\s+stock\s+holdings?|'
    r'trims\s+position\s+in|raises\s+stake\s+in|lowers\s+holdings\s+in|takes\s+position\s+in|'
    r'boosts\s+position\s+in|acquires\s+\d+[\d,]*\s+shares|cuts\s+stake\s+in|'
    r'holdings\s+raised\s+by|stake\s+raised\s+by|buys\s+shares\s+of'
    r')\b',
    re.IGNORECASE
)

_ICICIBANK_THIRD_PARTY_RECO_RE = re.compile(
    r'\b(?:'
    r'(?:buy|sell|hold|reduce|accumulate|add)\s+[^:;,\n]+(?::|\s+says|\s+suggests|\s+sees|\s+targets?)\s+(?:icici\s+securities|icici\s+direct)|'
    r'(?:icici\s+securities|icici\s+direct)\s+(?:suggests|recommends|sees|maintains|picks|has\s+buy|gives\s+target|initiates|upgrades|downgrades|cuts\s+target|bullish\s+on)|'
    r'stocks?:\s+(?:buy|sell|hold|add|reduce)\s+[^:;,\n]+(?::|\s+target\s+price|\s+rs|\s+\$|\s+icici)'
    r')\b',
    re.IGNORECASE
)

_ICICIBANK_MUTUAL_FUND_PRODUCT_RE = re.compile(
    r'\b(?:'
    r'icici\s+prudential\s+(?:[^:\n]+)?(?:fund|etf|nfo|sip|index\s+fund|pension\s+plan|annuity\s+plan|guaranteed\s+pension)|'
    r'(?:fund|etf|nfo|sip|index\s+fund|pension\s+plan|annuity\s+plan)\s+(?:[^:\n]+)?icici\s+prudential|'
    r'icici\s+pru\s+(?:[^:\n]+)?(?:fund|etf|nfo|sip|index\s+fund|pension\s+plan|annuity\s+plan)'
    r')\b',
    re.IGNORECASE
)

_ICICIBANK_PARENT_OVERRIDE_RE = re.compile(
    r'\b(?:'
    r'icici\s+bank\s+(?:to\s+)?(?:delist|buys?|sells?|acquires?|merges?|divests?|increases?\s+stake|stake\s+in|offloads?)|'
    r'delisting\s+(?:of\s+icici\s+securities|discontent|meet)|'
    r'merger\s+(?:of\s+icici\s+securities|with\s+icici\s+bank)|'
    r'icici\s+bank[\s\-]icici\s+securities\s+merger|'
    r'icici\s+bank\s*:\s*nclt|'
    r'nclt\s+clears\s+icici\s+bank'
    r')\b',
    re.IGNORECASE
)

_ICICIBANK_PARENT_POSITIVE_RE = re.compile(
    r'\b(?:'
    r'icici\s+bank(?:\s+limited|\s+ltd)?|icicibank'
    r')\b',
    re.IGNORECASE
)

_ICICIBANK_BARE_SIGNALS_RE = re.compile(
    r'\b(?:'
    # Earnings / Financials / Banking Metrics
    r'q[1-4]|quarterly|results?|earnings?|net\s+profit|profit|nii|nim|provisions?|asset\s+quality|gnpa|nnpa|npa|'
    r'deposits?|advances?|loan\s+growth|lending|casa|net\s+interest|'
    # Capital Markets / Stock Performance / Valuation / Brokerage Target
    r'shares?|stock|stocks|d[\s\-]street|nse|bse|nifty(?:50)?|sensex|valuation|target\s+price|price\s+target|'
    r'brokerage|buy\s+call|top\s+gainers?|gainers?|major\s+movers?|laggards?|rall(?:y|ies)|'
    r'slumps?|tumbles?|gains?|soars?|rebound|upside|bernstein|motilal|clsa|nomura|jefferies|morgan\s+stanley|goldman|macquarie'
    r')\b',
    re.IGNORECASE
)

_ICICIBANK_LEADERSHIP_RE = re.compile(
    r'\b(?:'
    r'sandeep\s+bakhshi|bakhshi|chanda\s+kochhar|kochhar'
    r')\b',
    re.IGNORECASE
)


# ─── AXISBANK.NS (Axis Bank Limited) Disambiguation Patterns ────────────────────
_AXISBANK_SUBSIDIARY_RE = re.compile(
    r'\b(?:'
    # Standalone subsidiaries / affiliates — no parent bank nexus by default
    r'axis\s+securities|axis\s+direct|'
    r'axis\s+mutual\s+fund|axis\s+(?:amc|asset\s+management)|'
    r'axis\s+finance(?!\s*,\s*(?:sbi|hdfc|icici|kotak|rbl|indusind))|'
    r'axis\s+capital|axis\s+trustee|axis(?:\s+bank)?\s+foundation'
    r')\b',
    re.IGNORECASE
)

_AXISBANK_PARENT_MATERIALITY_RE = re.compile(
    r'\b(?:'
    # Corporate actions connecting subsidiary/affiliate to parent bank
    r'axis\s+bank\s+(?:to\s+)?(?:acquires?|buys?|sells?|divests?|stakes?|merges?|offloads?|increases?\s+stake|stake\s+in)|'
    r'axis\s+bank.{0,40}(?:parent|consolidat|restructur|merger|divestment|stake\s+sale|capital\s+infusion)|'
    r'(?:merger|acquisition|divestment|restructur|consolidat|stake\s+sale|capital\s+infusion).{0,40}axis\s+bank|'
    r'axis\s+bank(?:\s+limited|\s+ltd)?\s*(?:owned|controlled|backed|sponsored)'
    r')\b',
    re.IGNORECASE
)

_AXISBANK_MINOR_FINANCING_RE = re.compile(
    r'\b(?:'
    # Incidental venture/bridge round participation in startups (minor financing)
    r'(?:raises?|raised|funding|bridge\s+round|seed\s+round|series\s+[a-z]).{0,60}(?:led\s+by|backed\s+by|from|participat\w+\s+by).{0,40}axis\s+bank|'
    r'axis\s+bank.{0,40}(?:leads?|led|participat\w+).{0,40}(?:bridge\s+round|seed\s+round|series\s+[a-z]|startup|\$\d+m\s+round)'
    r')\b',
    re.IGNORECASE
)

_AXISBANK_PEER_COMPARISON_RE = re.compile(
    r'(?:'
    # Comparison keywords
    r'(?:sbi|state\s+bank|hdfc|icici|kotak|pnb|bank\s+of\s+baroda|bob|canara|boi|rbl|idbi|scss|senior\s+citizen).{0,80}'
    r'(?:vs\.?|versus|compared?|comparison|which\s+is\s+better|which\s+(?:bank\s+)?offers?|which\s+bank\s+is\s+offering).{0,80}axis|'
    r'axis.{0,80}(?:vs\.?|versus|compared?|comparison).{0,80}(?:sbi|state\s+bank|hdfc|icici|kotak|pnb|bob|bank\s+of\s+baroda)|'
    # Multi-bank FD / rates
    r'(?:sbi|hdfc|icici).{0,40}axis(?:\s+bank)?.{0,40}(?:pnb|kotak|bob|boi).{0,60}(?:fd|fixed\s+deposit|interest\s+rate|saving|deposit|loan)|'
    r'(?:fd|fixed\s+deposit|interest\s+rate|scss|senior\s+citizen\s+fd).{0,80}(?:sbi|hdfc|icici).{0,50}axis(?:\s+bank)?|'
    # Credit cards multi-bank
    r'(?:sbi|hdfc|icici).{0,50}axis(?:\s+bank)?.{0,50}(?:credit\s+card|card\s+rule|card\s+change)|'
    r'(?:credit\s+card\s+rule|card\s+rule|new\s+credit\s+card\s+rules).{0,80}(?:sbi|hdfc|icici|axis)|'
    # Multi-bank broad sector trend/outlook: "How will HDFC Bank, Axis Bank, SBI and others perform in 2024? 5 key trends..."
    r'how\s+will.{0,60}axis\s+bank.{0,60}(?:perform|5\s+key\s+trends|trends\s+that\s+will\s+determine)'
    r')',
    re.IGNORECASE
)

_AXISBANK_ROUNDUP_RE = re.compile(
    r'(?:'
    # Multiple specific stocks to watch / in news list patterns
    r'stocks?\s+to\s+watch(?:\s*:\s*|\s+on|\s+today).{0,60}axis\s+bank|'
    r'stocks?\s+in\s+news\s*:\s*zee,\s*cipla,\s*axis\s+bank|'
    r'india\s+movers\s*:\s*axis\s+bank,\s*karnataka\s+bank|'
    r'sensex\s+today\s*\|\s*stock\s+market\s+live\s+updates\s*:\s*sensex\s+sheds.{0,80}axis'
    r')',
    re.IGNORECASE
)

_AXISBANK_POSITIVE_RE = re.compile(
    r'\b(?:'
    # Explicit parent bank corporate identity
    r'axis\s+bank(?:\s+limited|\s+ltd)?|uti\s+bank'
    r')\b',
    re.IGNORECASE
)

# Contextual bare Axis: specifically for workforce/hiring or banking peer corporate operations
_AXISBANK_BARE_CONTEXT_RE = re.compile(
    r'\b(?:'
    r'axis,\s+icici\s+go\s+slow\s+on\s+hiring|'
    r'axis\s+go(?:es)?\s+slow\s+on\s+hiring|'
    r'(?:sbi|hdfc\s+bank|icici\s+bank|axis).{0,40}hiring\s+trends?\s+diverge|'
    r'axis\s+shares?\s+rebound.{0,60}(?:icici|hdfc|sbi|kotak)'
    r')\b',
    re.IGNORECASE
)

_AXISBANK_BARE_DISQUALIFY_RE = re.compile(
    r'\b(?:'
    # Hard-exclude bare "axis" when clearly non-bank context
    r'(?:x|y|z)\s*[\-]?axis|axis\s+of\s+(?:evil|rotation|symmetry|the\s+earth|earth)|'
    r'rotational\s+axis|geopolit(?:ical)?\s+axis|political\s+axis|'
    r'optical\s+axis|anatomical\s+axis|mechanical\s+axis|'
    r'axis\s+communications?|axis\s+corp(?:oration)?(?!\s+bank)'
    r')\b',
    re.IGNORECASE
)


# ─── Kotak Mahindra Bank Precompiled Disambiguation Patterns (KOTAKBANK.NS) ──
_KOTAKBANK_SUBSIDIARY_RE = re.compile(
    r'\b(?:'
    r'kotak\s+securities|kotak\s+institutional\s+equities|'
    r'kotak\s+(?:mahindra\s+)?(?:mutual\s+fund|amc|asset\s+management)|'
    r'kotak\s+(?:mahindra\s+)?(?:capital|investment\s+banking)|'
    r'kotak\s+(?:mahindra\s+)?(?:life(?:\s+insurance)?|general\s+insurance)|'
    r'kotak\s+(?:alternate|private\s+equity|cherry)|'
    r'kotak\s+(?:mahindra\s+)?(?:trustees?)|'
    r'kotak\s+(?:mahindra\s+)?foundation'
    r')\b',
    re.IGNORECASE
)

_KOTAKBANK_PARENT_MATERIALITY_RE = re.compile(
    r'\b(?:'
    r'(?:stake\s+(?:sale|buy|acquisition|purchase|divest\w*|in))|'
    r'(?:(?:acquires?|buys?|sells?|divests?|holds?|holding)\s+(?:(?:\d+(?:\.\d+)?%|majority|minority)\s+)?stake)|'
    r'capital\s+infusion|merger|demerger|amalgamation|'
    r'zurich.{0,60}(?:deal|nod|cci|stake|acquisition|regulatory|insurance)|'
    r'rbi\s+(?:nod|approval|penalty|order|directive)|'
    r'cci\s+(?:approves?|clears?|nod)|'
    r'delisting|restructur\w+'
    r')\b',
    re.IGNORECASE
)

_KOTAKBANK_PEER_COMPARISON_RE = re.compile(
    r'(?:'
    r'(?:sbi|state\s+bank|hdfc|icici|axis|pnb|bob|bank\s+of\s+baroda|canara|boi|union\s+bank|yes\s+bank|idfc).{0,80}'
    r'(?:vs\.?|versus|compared?|comparison|which\s+is\s+better|which\s+offers?|which\s+bank\s+is\s+offering).{0,80}'
    r'kotak|'
    r'kotak.{0,80}(?:vs\.?|versus|compared?|comparison).{0,80}'
    r'(?:sbi|state\s+bank|hdfc|icici|axis|pnb|bob|canara)|'
    r'(?:sbi|hdfc|icici).{0,40}kotak(?:\s+mahindra)?(?:\s+bank)?.{0,40}(?:pnb|axis|bob|boi|canara).{0,60}'
    r'(?:fd|fixed\s+deposit|interest\s+rates?|saving|deposit|loan|personal\s+loan)|'
    r'(?:fd|fixed\s+deposit|interest\s+rates?|scss|senior\s+citizen\s+fd).{0,80}'
    r'(?:sbi|hdfc|icici).{0,50}kotak(?:\s+mahindra)?(?:\s+bank)?|'
    r'(?:sbi|hdfc|icici).{0,50}kotak(?:\s+mahindra)?(?:\s+bank)?.{0,50}'
    r'(?:credit\s+card|card\s+rules?|card\s+charges?|debit\s+card)|'
    r'(?:credit\s+card\s+rules?|card\s+rules?|new\s+credit\s+card\s+rules?).{0,80}'
    r'(?:sbi|hdfc|icici|kotak|axis)|'
    r'personal\s+loan\s+from\s+banks\s+with\s+lowest\s+interest\s+rates?.{0,80}(?:icici|hdfc|sbi|kotak)|'
    r'after\s+sbi\s+and\s+kotak\s+mahindra,\s+pnb\s+raises\s+interest\s+rates?'
    r')',
    re.IGNORECASE
)

_KOTAKBANK_ROUNDUP_RE = re.compile(
    r'(?:'
    r'stocks?\s+to\s+watch(?:\s*:\s*|\s+on|\s+today).{0,60}kotak|'
    r'stocks?\s+in\s+news\s*:\s*.{0,80}kotak|'
    r'india\s+movers\s*:\s*.{0,80}kotak|'
    r'sensex\s+today\s*\|\s*stock\s+market\s+live\s+updates\s*:\s*.{0,80}kotak|'
    r'stock\s+market\s+closing\s+bell\s*\|\s*.{0,80}kotak'
    r')',
    re.IGNORECASE
)

_KOTAKBANK_POSITIVE_RE = re.compile(
    r'\b(?:'
    r'kotak\s+mahindra\s+bank(?:\s+limited|\s+ltd)?|'
    r'kotak\s+bank|'
    r'kotakbank'
    r')\b',
    re.IGNORECASE
)

_KOTAKBANK_UDAY_GOVERNANCE_RE = re.compile(
    r'\b(?:'
    r'ashok\s+vaswani|ceo|md|board|resignation|resign|succession|'
    r'transition|promoter|stake|governance|rbi|shares?|term|handover'
    r')\b',
    re.IGNORECASE
)

_KOTAKBANK_UDAY_DISQUALIFY_RE = re.compile(
    r'\b(?:'
    r'biography|net\s+worth|wedding|family|lifestyle|son|education|'
    r'speech|davos|entrepreneurship|budget\s+wishlist|opinion|calls\s+for|'
    r'ai\s+will|advice|wealth|billionaire'
    r')\b',
    re.IGNORECASE
)

# ─── Bajaj Finance Precompiled Disambiguation Patterns (BAJFINANCE.NS) ────────
_BAJFINANCE_UNRELATED_GROUP_RE = re.compile(
    r'\b(?:'
    r'bajaj\s+auto(?!\s+finance)|'
    r'bajaj\s+electricals|'
    r'bajaj\s+consumer(?:\s+care)?|'
    r'bajaj\s+holdings(?:\s+&\s+investment)?|'
    r'bajaj\s+energy|'
    r'bajaj\s+hindusthan|'
    r'bajaj\s+allianz(?:\s+general\s+insurance|\s+life(?:\s+insurance)?)?'
    r')\b',
    re.IGNORECASE
)

_BAJFINANCE_FINSERV_STANDALONE_RE = re.compile(
    r'\b(?:'
    r'bajaj\s+finserv(?:\s+mutual\s+fund|\s+amc|\s+health|\s+direct|\s+credit\s+pass|\s+emi\s+store|\s+apps?|\s+markets)?'
    r')\b',
    re.IGNORECASE
)

_BAJFINANCE_HOUSING_STANDALONE_RE = re.compile(
    r'\b(?:'
    r'bajaj\s+housing\s+finance|'
    r'bhfl'
    r')\b',
    re.IGNORECASE
)

_BAJFINANCE_PARENT_MATERIALITY_RE = re.compile(
    r'\b(?:'
    r'(?:stake\s+(?:sale|buy|acquisition|purchase|divest\w*|in|hike|increase|decrease))|'
    r'(?:(?:acquires?|buys?|sells?|divests?|holds?|holding|raises?)\s+(?:(?:\d+(?:\.\d+)?%|majority|minority)\s+)?stake)|'
    r'capital\s+(?:infusion|support|raising)|merger|demerger|amalgamation|'
    r'ipo|delisting|restructur\w+|promoter\s+holding|parent\s+company'
    r')\b',
    re.IGNORECASE
)

_BAJFINANCE_PEER_COMPARISON_RE = re.compile(
    r'(?:'
    r'(?:sbi|hdfc|icici|axis|shriram|m&m|chola|muthoot|jio\s+financial|pnb|bob|bank).{0,80}'
    r'(?:vs\.?|versus|compared?|comparison|which\s+is\s+better|which\s+offers?|best\s+fd|highest\s+interest).{0,80}bajaj\s+finance|'
    r'bajaj\s+finance.{0,80}(?:vs\.?|versus|compared?|comparison).{0,80}(?:sbi|hdfc|icici|axis|shriram|jio)|'
    r'(?:fd|fixed\s+deposit|interest\s+rate|personal\s+loan|saving|deposit).{0,80}(?:sbi|hdfc|icici).{0,50}bajaj\s+finance'
    r')',
    re.IGNORECASE
)

_BAJFINANCE_ROUNDUP_RE = re.compile(
    r'(?:'
    r'stocks?\s+to\s+watch(?:\s*:\s*|\s+on|\s+today).{0,60}bajaj\s+finance|'
    r'stocks?\s+in\s+news\s*:\s*.{0,80}bajaj\s+finance|'
    r'india\s+movers\s*:\s*.{0,80}bajaj\s+finance|'
    r'sensex\s+today\s*\|\s*stock\s+market\s+live\s+updates\s*:\s*.{0,80}bajaj\s+finance|'
    r'stock\s+market\s+closing\s+bell\s*\|\s*.{0,80}bajaj\s+finance'
    r')',
    re.IGNORECASE
)

_BAJFINANCE_POSITIVE_RE = re.compile(
    r'\b(?:'
    r'bajaj\s+finance(?:\s+limited|\s+ltd)?|'
    r'bajaj\s+auto\s+finance'
    r')\b',
    re.IGNORECASE
)

# ─── Bharti Airtel Precompiled Disambiguation Patterns (BHARTIARTL.NS) ───────
_BHARTIARTL_CONSUMER_PLAN_RE = re.compile(
    r'\b(?:'
    r'cheapest|best\s+(?:prepaid|postpaid|data|recharge|5g|ott)?\s*plans?|affordable\s+plan|'
    r'free\s+ott|disney\s*\+|netflix|hotstar|amazon\s+prime|'
    r'unlimited\s+5g(?:\s+data|\s+plans?)?|data\s+plans?|recharge\s+plans?|prepaid\s+plans?|'
    r'postpaid\s+plans?|validity|daily\s+data|plans?\s+under\s+rs|plans?\s+with\s+\d+\s+days?|'
    r'data\s+voucher|recharge\s+offers?|sms\s+pack|talktime|roaming\s+pack|recharge\s+for|'
    r'brings\s+new\s+plan|launch(?:es)?\s+(?:new\s+)?(?:recharge|prepaid|postpaid)\s+plan|'
    r'to\s+end\s+unlimited\s+5g|withdraw\s+unlimited\s+5g|start\s+charging\s+for\s+5g|'
    r'unlimited\s+5g\s+data'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_TARIFF_OVERRIDE_RE = re.compile(
    r'\b(?:'
    r'target\s+price|price\s+target|upgrade|downgrade|result|earnings?|q[1-4]|'
    r'brokerage|analyst|arpu|subscriber\s+(?:growth|addition|loss|gain)|'
    r'revenue|ebitda|financials?'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_AFRICA_STANDALONE_RE = re.compile(
    r'\b(?:'
    r'airtel\s+africa|airtel\s+nigeria|airtel\s+kenya|airtel\s+uganda|'
    r'airtel\s+tanzania|airtel\s+zambia|airtel\s+rwanda|airtel\s+malawi|'
    r'airtel\s+money'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_PAYMENTS_BANK_RE = re.compile(
    r'\b(?:'
    r'airtel\s+payments\s+bank|airtel\s+bank'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_HEXACOM_STANDALONE_RE = re.compile(
    r'\b(?:'
    r'bharti\s+hexacom|hexacom'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_INDUS_TOWERS_RE = re.compile(
    r'\b(?:'
    r'indus\s+towers?'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_NXTRA_RE = re.compile(
    r'\b(?:'
    r'nxtra\s+data|nxtra\s+by\s+airtel|nxtra'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_PARENT_MATERIALITY_RE = re.compile(
    r'\b(?:'
    r'(?:stake\s+(?:sale|buy|acquisition|purchase|divest\w*|in|hike|increase|decrease))|'
    r'(?:(?:acquires?|buys?|sells?|divests?|holds?|holding|raises?)\s+(?:(?:\d+(?:\.\d+)?%|majority|minority)\s+)?stake)|'
    r'capital\s+(?:infusion|support|raising)|merger|demerger|amalgamation|'
    r'ipo|draft\s+papers?|drhp|delisting|restructur\w+|promoter\s+holding|parent\s+company'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_LEADERSHIP_RE = re.compile(
    r'\b(?:'
    r'sunil\s+mittal|sunil\s+bharti\s+mittal'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_LEADERSHIP_DISQUALIFY_RE = re.compile(
    r'\b(?:'
    r'davos|ram\s+mandir|pran\s+pratistha|ayodhya|wedding|biography|'
    r'modi\s+govt|unfinished\s+agenda|bright\s+spot|3rd\s+term|speaks?\s+at|'
    r'wef|summit|says\s+3rd\s+term|people\s+want\s+to\s+invest|vodafone\s+idea'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_ROUNDUP_RE = re.compile(
    r'(?:'
    r'stocks?\s+to\s+watch(?:\s*:\s*|\s+on|\s+today).{0,60}(?:airtel|bharti)|'
    r'stocks?\s+in\s+news\s*:\s*.{0,80}(?:airtel|bharti)|'
    r'india\s+movers\s*:\s*.{0,80}(?:airtel|bharti)|'
    r'sensex\s+today\s*\|\s*stock\s+market\s+live\s+updates\s*:\s*.{0,80}(?:airtel|bharti)|'
    r'stock\s+market\s+closing\s+bell\s*\|\s*.{0,80}(?:airtel|bharti)|'
    r'top\s+gainers\s+on\s+.{0,40}(?:airtel|bharti)|'
    r'among\s+jefferies.{0,40}(?:airtel|bharti)'
    r')',
    re.IGNORECASE
)

_BHARTIARTL_POSITIVE_RE = re.compile(
    r'\b(?:'
    r'bharti\s+airtel(?:\s+limited|\s+ltd)?|'
    r'airtel\s+india'
    r')\b',
    re.IGNORECASE
)

_BHARTIARTL_BARE_AIRTEL_CORP_RE = re.compile(
    r'\b(?:'
    r'5g|4g|spectrum|trai|dot|arpu|subscribers?|tariff|broadband|xstream|dth|'
    r'capex|network|shares?|stock|target\s+price|price\s+target|results?|earnings?|'
    r'profit|loss|revenue|ebitda|oneweb|fdi|fundrais\w*|ncd|debt|'
    r'market\s+share|telecom|brokerage|analyst'
    r')\b',
    re.IGNORECASE
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


def _is_gdelt_rate_limit_text(text: str) -> bool:
    """
    Checks if a response body contains GDELT rate-limit or service throttling text.
    Handles non-standard HTTP 200 responses with rate limit messages.
    """
    if not text:
        return False
    lower = text.lower()
    return (
        "please limit requests" in lower
        or "limit requests to one every" in lower
        or "switch to our ngrams dataset" in lower
        or "rate limit" in lower
    )


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
        self.stats: Dict[str, Any] = {
            "api_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "rate_limit_responses": 0,
            "global_rate_limit_waits": 0,
            "global_rate_limit_wait_seconds": 0.0,
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
            snapshot: Dict[str, Any] = dict(self.stats)
        with self._truncated_ranges_lock:
            snapshot["truncated_ranges"] = list(self._truncated_ranges)
        return snapshot

    @staticmethod
    def is_article_url(url: str) -> bool:
        """Validates whether a URL represents a genuine article rather than a taxonomy/category/tag index."""
        return is_article_url(url)

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
            elif ticker == "INFY.NS":
                custom_fn = self._match_infy
            elif ticker == "ICICIBANK.NS":
                custom_fn = self._match_icicibank
            elif ticker == "AXISBANK.NS":
                custom_fn = self._match_axisbank
            elif ticker == "KOTAKBANK.NS":
                custom_fn = self._match_kotakbank
            elif ticker == "BAJFINANCE.NS":
                custom_fn = self._match_bajfinance
            elif ticker == "BHARTIARTL.NS":
                custom_fn = self._match_bhartiartl

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

    def is_relevant_to_company(
        self,
        text: str,
        ticker: str,
        article_datetime: Optional[Union[datetime.datetime, datetime.date, str]] = None
    ) -> bool:
        """
        Determines whether a headline is genuinely relevant to the target company,
        preventing false positives on ambiguous acronyms (e.g., ITC, LT, TITAN).
        Optionally accepts article_datetime to apply temporal corporate lifecycle rules.
        """
        if not text or not text.strip():
            return False

        matcher = self._compiled_matchers.get(ticker)
        if not matcher:
            return False

        text_lower = text.lower()

        # Normalize article_date if provided
        article_date: Optional[datetime.date] = None
        if article_datetime is not None:
            if isinstance(article_datetime, datetime.datetime):
                article_date = article_datetime.date()
            elif isinstance(article_datetime, datetime.date):
                article_date = article_datetime
            elif isinstance(article_datetime, str):
                clean_str = article_datetime.strip()
                if len(clean_str) >= 10 and clean_str[4] == '-' and clean_str[7] == '-':
                    try:
                        article_date = datetime.date.fromisoformat(clean_str[:10])
                    except Exception:
                        pass
                elif len(clean_str) >= 8 and clean_str[:8].isdigit():
                    try:
                        article_date = datetime.datetime.strptime(clean_str[:8], "%Y%m%d").date()
                    except Exception:
                        pass

        # 1. Specialized contextual verification for ambiguous tickers
        if matcher["custom_fn"]:
            try:
                return matcher["custom_fn"](text, text_lower, article_date=article_date)
            except TypeError:
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

    def _match_itc(self, text: str, text_lower: str, article_date: Optional[datetime.date] = None) -> bool:
        # Stage 1: Explicit Negative Disambiguation Exclusion
        if _ITC_EXCLUSIONS_RE.search(text_lower):
            if re.search(r'\|\s*itc\b', text_lower) and not re.search(r'\b(?:gst|fake|usitc|saica|nicotine|job)\b', text_lower):
                if not re.search(r'\b(?:records?|reports?|posts?|shares?|profit|results?|q[1-4]|dividend)\b', text_lower):
                    return False
            else:
                return False

        # Stage 2: Positive Multi-Word Entity Match
        if _ITC_POSITIVE_RE.search(text_lower):
            return True

        # Stage 3: Hospitality Match (Driven by Corporate Entity Lifecycle)
        if "itc hotel" in text_lower or "itc ratnadipa" in text_lower or "welcomhotel" in text_lower or "'welcom'" in text_lower or "fortune hotel" in text_lower or "होटल" in text:
            # 3a. Transition & Demerger Restructuring (Always relevant to parent ITC.NS)
            if _ITC_HOTELS_TRANSITION_RE.search(text_lower):
                return True

            # 3b. Cross-Entity Context: Explicit Parent ITC Mentioned Alongside Hotel
            if re.search(r'\b(?:itc\s+shares?|itc\'s\s+value|shares\s+of\s+itc|itc\s+stock)\b', text_lower):
                return True

            # 3c. Lifecycle-aware evaluation
            lifecycle_cfg = CORPORATE_ENTITY_LIFECYCLES.get("ITC.NS", {}).get("subsidiaries", {}).get("ITC Hotels Limited", {})
            listing_date_str = lifecycle_cfg.get("listing_date", "2025-01-29")
            try:
                listing_date = datetime.date.fromisoformat(listing_date_str)
            except Exception:
                listing_date = datetime.date(2025, 1, 29)

            if article_date is not None:
                if article_date <= listing_date:
                    # PRE-SEPARATION / LISTING DAY: Operating, valuation, and listing roadmap news belongs to parent ITC.NS
                    if _ITC_HOTELS_OPERATIONAL_RE.search(text_lower) or _ITC_BARE_STRONG_SIGNALS_RE.search(text_lower):
                        return True
                    if "itc hotel" in text_lower or "fortune hotel" in text_lower or "welcomhotel" in text_lower or "itc ratnadipa" in text_lower:
                        return True
                    return False
                else:
                    # POST-LISTING: Standalone ITCHOTELS.NS news does NOT belong to ITC.NS
                    return False
            else:
                # Undated fallback: Accept genuine operational/expansion news
                if _ITC_HOTELS_OPERATIONAL_RE.search(text_lower) or _ITC_BARE_STRONG_SIGNALS_RE.search(text_lower):
                    return True
                if "fortune hotel" in text_lower or "welcomhotel" in text_lower or "itc ratnadipa" in text_lower:
                    return True
                return False

        # Stage 4: Bare uppercase/titlecase "ITC" with Corporate / Market Signals
        if re.search(r'\b(?:ITC|Itc)\b', text) or "डिमर्जर" in text:
            if _ITC_BARE_STRONG_SIGNALS_RE.search(text_lower):
                return True

        return False

    def _match_lt(self, text: str, text_lower: str) -> bool:
        # Stage 1: Negative Disambiguation Exclusions
        if _LT_EXCLUSIONS_RE.search(text_lower):
            # Strict override only if explicit full Larsen & Toubro is present
            if not re.search(r'\blarsen\s*(?:&|and)\s*toubro\b', text_lower):
                return False

        # Stage 2: Strong Positive Multi-Word / Subsidiary Entity Match
        if _LT_POSITIVE_RE.search(text_lower):
            return True

        # Stage 3: Distinctive "L&T" / "l&t" with market or corporate context
        if re.search(r'\b(?:L&T|l&t|L&t)\b', text):
            if _LT_BARE_STRONG_SIGNALS_RE.search(text_lower):
                return True
            if self._has_financial_context(text_lower):
                return True
            # Standalone L&T corporate mentions (e.g. "L&T commissions 10MW plant")
            return True

        # Stage 4: Bare uppercase "LT" with Strict Corporate / Market Signals
        if re.search(r'\bLT\b', text):
            if _LT_BARE_STRONG_SIGNALS_RE.search(text_lower):
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
        # Stage 1: Explicit Negative Disambiguation Exclusions
        if _SBI_EXCLUSIONS_RE.search(text_lower):
            # Strict override only if explicit State Bank of India is present
            if not re.search(r'\bstate\s+bank\s+of\s+india\b', text_lower):
                return False

        # Stage 2: Standalone Subsidiary Filter (SBI Life, SBI Cards, SBI MF, SBI General Insurance)
        if _SBI_SUBSIDIARY_SEPARATION_RE.search(text_lower):
            # Allow only if explicit parent materiality context exists
            if _SBI_PARENT_MATERIALITY_RE.search(text_lower):
                return True
            return False

        # Stage 3: Positive Multi-Word & Explicit Entity Matches
        if _SBI_POSITIVE_RE.search(text):
            return True

        # Stage 4: Bare "SBI" or "State Bank" with Banking / Market / Corporate Context
        if re.search(r'\b(?:SBI|Sbi)\b', text) or re.search(r'\bstate\s+bank\b', text_lower):
            if _SBI_BARE_SIGNALS_RE.search(text_lower):
                return True
            if self._has_financial_context(text_lower):
                return True

        return False

    def _match_tcs(self, text: str, text_lower: str) -> bool:
        """
        Determines whether a headline/text is genuinely relevant to TCS.NS (Tata Consultancy Services Limited).
        Enforces strict deterministic precedence:
        1. Explicit Negative Disambiguation Exclusions (Tax Collected at Source, Foreign tickers, Non-IT acronyms).
        2. Standalone Other Tata Group Exclusions (Motors, Steel, Power, Tech, etc. without explicit TCS).
        3. Positive Full-Name Entity Match & Leadership (Tata Consultancy Services, K. Krithivasan).
        4. Bare "TCS" / Hindi "टीसीएस" with Corporate / Market / IT / Workforce / Governance Signals.
        """
        # Stage 1: Explicit Negative Disambiguation Exclusions
        if _TCS_EXCLUSIONS_RE.search(text_lower):
            # Strict override only if explicit full Tata Consultancy Services is present
            if not _TCS_POSITIVE_RE.search(text_lower):
                return False

        # Stage 2: Standalone Other Tata Group Exclusions
        if _TCS_OTHER_TATA_RE.search(text_lower):
            # Allow only if TCS itself or full name is also explicitly mentioned
            if not re.search(r'(?i)\b(?:tcs|tata\s+consultancy)\b|टीसीएस', text):
                return False

        # Stage 3: Positive Full-Name Entity Match & Leadership
        if _TCS_POSITIVE_RE.search(text):
            return True
        if _TCS_LEADERSHIP_CONTEXT_RE.search(text):
            return True

        # Stage 4: Bare "TCS" / "टीसीएस" with Corporate / Market / IT Signals
        if re.search(r'(?i)\bTCS\b|टीसीएस', text):
            if _TCS_BARE_SIGNALS_RE.search(text_lower):
                return True
            if self._has_financial_context(text_lower):
                return True
            if any(w in text for w in ["नतीजे", "शेयर", "करार", "डिविडेंड", "मुनाफा", "तिमाही"]):
                return True

        return False

    def _match_reliance(self, text: str, text_lower: str) -> bool:
        """
        Determines whether a headline/text is genuinely relevant to RELIANCE.NS (Reliance Industries Limited).
        Enforces strict deterministic precedence:
        1. Explicit exclusions for unrelated ADAG / non-RIL Reliance entities (e.g. Power, Infra, Capital, Bank).
        2. Definitive positive matches for Reliance Industries, Retail, Jio, Mukesh Ambani, RIL.
        3. Bare 'Reliance' requiring strict RIL-specific vertical/asset/corporate context (never generic financial keywords).
        """
        # Stage 1: Explicit Negative / Unrelated ADAG Exclusion
        if _RELIANCE_EXCLUSIONS_RE.search(text_lower):
            # Strict override ONLY if text also explicitly names Reliance Industries / RIL / Mukesh Ambani
            if not re.search(r'\b(?:reliance\s+industries(?:\s+(?:ltd|limited))?|mukesh\s+ambani|ril)\b', text_lower):
                return False

        # Stage 2: Strong Positive Multi-Word / Entity Match
        if _RELIANCE_POSITIVE_RE.search(text_lower):
            return True

        # Stage 3: Bare "Reliance" with Strict RIL-Specific Identifiers
        if re.search(r'\bReliance\b', text):
            # Reject grammatical 'reliance on'
            if re.search(r'\breliance\s+on\b', text_lower):
                return False
            # Require RIL-specific vertical, asset, subsidiary, or corporate action
            if _RELIANCE_RIL_SPECIFIC_CONTEXT_RE.search(text_lower):
                return True

        return False

    def _match_infy(self, text: str, text_lower: str) -> bool:
        """
        Determines whether a headline/text is genuinely relevant to INFY.NS (Infosys Limited).
        Enforces strict deterministic precedence:
        1. Explicit Negative Disambiguation Exclusions (Narayana Murthy personal/family lifestyle,
           70-hour work week viral debates, generic campus job listings).
        2. Positive Full-Name Entity Match & Current Leadership (Infosys Limited, Salil Parekh, Nandan Nilekani).
        3. Founder Narayana Murthy requiring strict Infosys corporate/financial/governance context.
        4. Bare "Infosys" or Devanagari "इंफोसिस" / "इन्फोसिस".
        5. Contextual Shorthand "Infy" / "INFY" with strong market, earnings, or corporate signals.
        """
        # Stage 1: Explicit Negative Disambiguation Exclusions
        if _INFY_EXCLUSIONS_RE.search(text_lower):
            # Strict corporate override only if explicit financial quarterly performance or InSemi acquisition is present
            if not re.search(r'\b(?:q[1-4]|quarterly|results?|earnings?|profit|revenue|margin|dividend|insemi)\b', text_lower):
                return False
            if not any(k in text_lower for k in ["infosys", "infy", "salil parekh"]):
                return False

        # Stage 2: Strong Positive Multi-Word Entity Match & Leadership
        if _INFY_POSITIVE_RE.search(text_lower):
            return True

        # Stage 3: Founder Narayana Murthy with Strict Infosys Corporate Context
        if re.search(r'\b(?:narayana\s+murthy|narayanamurthy)\b', text_lower):
            if _INFY_FOUNDER_CORPORATE_CONTEXT_RE.search(text_lower):
                return True
            return False

        # Stage 4: Bare "Infosys" or Devanagari "इंफोसिस" / "इन्फोसिस"
        if re.search(r'\b(?:infosys|इंफोसिस|इन्फोसिस)\b', text_lower):
            return True

        # Stage 5: Contextual Shorthand "Infy" / "INFY" with Financial/Market Signals
        if re.search(r'\b(?:infy)\b', text_lower):
            if _INFY_BARE_SIGNALS_RE.search(text_lower):
                return True

        return False

    def _match_icicibank(self, text: str, text_lower: str) -> bool:
        """
        Determines whether a headline/text is genuinely relevant to ICICIBANK.NS (ICICI Bank Limited).
        Enforces strict deterministic precedence:
        1. Explicit 13F / SEC Foreign Fund Filings Filter (Hard Reject).
        2. Third-party Brokerage Recommendations from ICICI Securities / ICICI Direct (Hard Reject).
        3. Mutual Fund / Insurance Standalone Products (Hard Reject).
        4. Standalone Subsidiary Exclusion with Parent Override.
        5. Explicit Positive Parent Bank Identity (ICICI Bank, ICICI Bank Limited, ICICIBANK).
        6. Leadership (Sandeep Bakhshi, Chanda Kochhar with bank governance/legal context).
        7. Contextual Bare ICICI with strong financial/banking/peer signals.
        """
        # Stage 1: Explicit 13F / SEC Foreign Fund Filings Filter (Hard Reject)
        if _ICICIBANK_13F_FILING_RE.search(text_lower):
            if "icici bank" not in text_lower and "icicibank" not in text_lower:
                return False

        # Stage 2: Third-party Brokerage Recommendation from ICICI Securities / Direct (Hard Reject)
        if _ICICIBANK_THIRD_PARTY_RECO_RE.search(text_lower):
            if not re.search(r'\b(?:icici\s+bank|icicibank|delisting)\b', text_lower):
                return False

        # Stage 3: Mutual Fund / Insurance Standalone Products (Hard Reject)
        if _ICICIBANK_MUTUAL_FUND_PRODUCT_RE.search(text_lower):
            if not _ICICIBANK_PARENT_OVERRIDE_RE.search(text_lower):
                return False

        # Stage 4: Standalone Subsidiary Exclusion with Parent Override
        if _ICICIBANK_EXCLUSIONS_RE.search(text_lower):
            if not _ICICIBANK_PARENT_OVERRIDE_RE.search(text_lower):
                return False

        # Stage 5: Explicit Positive Parent Bank Identity
        if _ICICIBANK_PARENT_POSITIVE_RE.search(text_lower):
            return True

        # Stage 6: Leadership (Sandeep Bakhshi, Chanda Kochhar with bank governance/legal context)
        if _ICICIBANK_LEADERSHIP_RE.search(text_lower):
            if any(k in text_lower for k in ["icici", "bank", "videocon", "cbi", "ed", "court", "loan", "fraud", "bakhshi", "kochhar"]):
                return True

        # Stage 7: Contextual Bare ICICI with strong financial/banking signals
        if re.search(r'\b(?:icici)\b', text_lower):
            # Disallow generic lists (credit cards rules, personal loans, FD comparison tables)
            if any(g in text_lower for g in ["credit card rules", "personal loan", "which bank", "where should you park", "most active on", "dragonfly doji", "hiring", "check fixed deposit"]):
                return False
            if _ICICIBANK_BARE_SIGNALS_RE.search(text_lower):
                if not any(sub in text_lower for sub in ["prudential", "lombard", "venture", "foundation", "securities", "direct", "amc"]):
                    return True

        return False

    def _match_axisbank(self, text: str, text_lower: str) -> bool:
        """
        Determines whether a headline/text is genuinely relevant to AXISBANK.NS (Axis Bank Limited).
        Enforces strict deterministic precedence:
        1. Subsidiary/Affiliate Exclusion with Parent-Materiality Override.
        2. Minor Financing / Venture Bridge Round Exclusion.
        3. Peer-Bank Comparison/Listicle Rejection (FD tables, credit-card rule comparisons).
        4. Generic Market Roundup/Watchlist Rejection (multi-stock lists).
        5. Explicit Parent Bank Identity (Axis Bank, Axis Bank Ltd, UTI Bank).
        6. Contextual Bare 'Axis' with strong banking-peer workforce/operations signals.
        7. Default: False.
        """
        # ─── Stage 1: Subsidiary/Affiliate Exclusion with Parent-Materiality Override ──
        # Reject standalone subsidiary activity unless parent bank is materially involved
        # (acquisition, merger, stake transaction, consolidation, capital infusion, etc.).
        if _AXISBANK_SUBSIDIARY_RE.search(text_lower):
            # Allow if parent-materiality override signals are present
            if not _AXISBANK_PARENT_MATERIALITY_RE.search(text_lower):
                return False

        # ─── Stage 2: Minor Financing / Startup Bridge Round Exclusion ────────────────
        # Reject incidental venture/startup funding rounds led by or participated in by Axis Bank
        # unless there is a material strategic acquisition/stake transaction.
        if _AXISBANK_MINOR_FINANCING_RE.search(text_lower):
            if not _AXISBANK_PARENT_MATERIALITY_RE.search(text_lower):
                return False

        # ─── Stage 3: Peer-Bank Comparison / Listicle Rejection ───────────────────────
        # Reject formulaic multi-bank FD rate tables, credit-card rule comparisons,
        # senior citizen FD comparisons, etc. These are generic product aggregation
        # content with no Axis-Bank-specific analysis.
        # Exception: allow if there is a specific Axis Bank analyst view or target price.
        if _AXISBANK_PEER_COMPARISON_RE.search(text_lower):
            # Check for Axis-specific analytical content (target price, upgrade/downgrade, result)
            if not re.search(
                r'\b(?:target\s+price|price\s+target|upgrade|downgrade|result|earnings?|q[1-4]|brokerage|analyst)'
                r'.{0,60}axis\s+bank|axis\s+bank.{0,60}'
                r'(?:target\s+price|price\s+target|upgrade|downgrade|result|earnings?|q[1-4]|brokerage|analyst)\b',
                text_lower
            ):
                return False

        # ─── Stage 4: Generic Market Roundup / Watchlist Rejection ───────────────────
        # Reject multi-stock watchlists and "stocks in news" aggregations where
        # Axis Bank is one item among many unrelated companies.
        if _AXISBANK_ROUNDUP_RE.search(text_lower):
            return False

        # ─── Stage 5: Explicit Parent Bank Identity ───────────────────────────────────
        # Direct mention of "Axis Bank" or historical name "UTI Bank" → accept.
        if _AXISBANK_POSITIVE_RE.search(text_lower):
            return True

        # ─── Stage 6: Contextual Bare 'Axis' — Workforce/Operations Signal ────────────
        # Accept only when bare "Axis" co-occurs with strong banking workforce/operations signals
        # making Axis Bank identity unambiguous.
        if _AXISBANK_BARE_CONTEXT_RE.search(text_lower):
            return True

        # ─── Stage 7: Default ──────────────────────────────────────────────────────────
        return False

    def _match_kotakbank(self, text: str, text_lower: str) -> bool:
        """
        Specialized entity matching for Kotak Mahindra Bank Limited (KOTAKBANK.NS).

        Precedence Architecture (Deterministic 7-Stage Pipeline):
          Stage 1: Standalone Subsidiary Exclusion with Parent-Materiality Override
                   Rejects standalone operations of Kotak Securities, AMC/Mutual Fund,
                   Capital, Private Equity/Alternate, Trustees, Cherry, and Foundation,
                   unless a material corporate action, stake transaction, capital infusion,
                   merger/demerger, Zurich deal, or RBI/CCI regulatory action is present.
          Stage 2: Peer-Bank Product & FD Comparison Table Exclusion
                   Rejects formulaic multi-bank comparison tables (FD rates, personal loans,
                   credit card charges) unless substantive analytical/stock/earnings content
                   is present.
          Stage 3: Generic Market Roundup / Watchlist Rejection
                   Rejects multi-stock watchlists and 'stocks in news' aggregations where
                   Kotak Bank is merely an incidental list member, unless earnings/management
                   announcements are explicitly present.
          Stage 4: Explicit Parent Bank Identity
                   Direct mention of 'Kotak Mahindra Bank' or 'Kotak Bank' -> accepts.
          Stage 5: Uday Kotak Contextual Governance
                   Requires bank governance, leadership transition (Ashok Vaswani),
                   promoter/ownership stakes, or regulatory context; rejects personal
                   biography, net worth, family, lifestyle, and generic commentary.
          Stage 6: Contextual 'Kotak Mahindra' Group/Corporate
                   Accepts corporate 'Kotak Mahindra' references when not excluded by
                   subsidiary rules.
          Stage 7: Default -> False.
        """
        # ─── Stage 1: Standalone Subsidiary Exclusion with Parent-Materiality Override
        if _KOTAKBANK_SUBSIDIARY_RE.search(text_lower):
            if _KOTAKBANK_PARENT_MATERIALITY_RE.search(text_lower):
                return True
            return False

        # ─── Stage 2: Peer-Bank Product & FD Comparison Table Exclusion ───────────────
        if _KOTAKBANK_PEER_COMPARISON_RE.search(text_lower):
            if not re.search(
                r'\b(?:target\s+price|price\s+target|upgrade|downgrade|result|earnings?|q[1-4]|brokerage|analyst)'
                r'.{0,60}kotak|kotak.{0,60}'
                r'(?:target\s+price|price\s+target|upgrade|downgrade|result|earnings?|q[1-4]|brokerage|analyst)\b',
                text_lower
            ):
                return False

        # ─── Stage 3: Generic Market Roundup / Watchlist Rejection ───────────────────
        if _KOTAKBANK_ROUNDUP_RE.search(text_lower):
            if not re.search(r'\b(?:q[1-4]|pat|profit|results?|earnings?|ashok\s+vaswani)\b', text_lower):
                return False

        # ─── Stage 4: Explicit Parent Bank Identity ───────────────────────────────────
        if _KOTAKBANK_POSITIVE_RE.search(text_lower):
            return True

        # ─── Stage 5: Uday Kotak Contextual Governance ────────────────────────────────
        if 'uday kotak' in text_lower:
            if _KOTAKBANK_UDAY_DISQUALIFY_RE.search(text_lower):
                return False
            if _KOTAKBANK_UDAY_GOVERNANCE_RE.search(text_lower):
                return True
            return False

        # ─── Stage 6: Contextual 'Kotak Mahindra' Group/Corporate ────────────────────
        if 'kotak mahindra' in text_lower:
            return True

        # ─── Stage 7: Default ──────────────────────────────────────────────────────────
        return False

    def _match_bajfinance(self, text: str, text_lower: str) -> bool:
        """
        Specialized entity matching for Bajaj Finance Limited (BAJFINANCE.NS).

        Precedence Architecture (Deterministic 7-Stage Pipeline):
          Stage 1: Unrelated Bajaj Group Entities Exclusion
                   Rejects standalone operations of Bajaj Auto, Bajaj Electricals,
                   Bajaj Consumer Care, Bajaj Holdings, Bajaj Energy, Bajaj Hindusthan,
                   and Bajaj Allianz (Life / General Insurance), unless an explicit
                   material Bajaj Finance parent transaction is present.
          Stage 2: Standalone Bajaj Finserv Exclusion with Parent Materiality Override
                   Rejects standalone holding company operations (Credit Pass, AMC/MF,
                   Health/Vidal, jobs, Sanjiv Bajaj Davos commentary), unless a material
                   Bajaj Finance stake, capital, holding, or restructuring action is present.
          Stage 3: Standalone Bajaj Housing Finance Exclusion with Parent Materiality Override
                   Rejects standalone BHFL mortgage products/bonds unless parent IPO,
                   capital injection, or stake transaction is present.
          Stage 4: Peer-Bank / NBFC Product & FD Comparison Exclusion
                   Rejects formulaic multi-lender FD and personal loan comparison tables,
                   unless substantive analytical/earnings/valuation content is present.
          Stage 5: Generic Market Roundup / Watchlist Rejection
                   Rejects multi-stock watchlists and 'stocks in news' aggregations where
                   Bajaj Finance is merely an incidental list member, unless earnings/AUM
                   or management announcements are explicitly present.
          Stage 6: Explicit Bajaj Finance Parent Identity
                   Direct mention of 'Bajaj Finance', 'Bajaj Finance Ltd/Limited',
                   or 'Bajaj Auto Finance' -> accepts.
          Stage 7: Default -> False.
        """
        # ─── Stage 1: Unrelated Bajaj Group Entities ──────────────────────────────────
        if _BAJFINANCE_UNRELATED_GROUP_RE.search(text_lower):
            if not _BAJFINANCE_POSITIVE_RE.search(text_lower):
                return False
            if _BAJFINANCE_PARENT_MATERIALITY_RE.search(text_lower):
                return True

        # ─── Stage 2: Standalone Bajaj Finserv Exclusion with Parent Materiality Override
        if _BAJFINANCE_FINSERV_STANDALONE_RE.search(text_lower):
            if not _BAJFINANCE_POSITIVE_RE.search(text_lower):
                if _BAJFINANCE_PARENT_MATERIALITY_RE.search(text_lower) and re.search(r'bajaj\s+finance', text_lower):
                    return True
                return False

        # ─── Stage 3: Standalone Bajaj Housing Finance Exclusion with Parent Materiality Override
        if _BAJFINANCE_HOUSING_STANDALONE_RE.search(text_lower):
            if not _BAJFINANCE_POSITIVE_RE.search(text_lower):
                if _BAJFINANCE_PARENT_MATERIALITY_RE.search(text_lower):
                    return True
                return False

        # ─── Stage 4: Peer-Bank / NBFC Product & FD Comparison Exclusion ───────────────
        if _BAJFINANCE_PEER_COMPARISON_RE.search(text_lower):
            if not re.search(
                r'\b(?:target\s+price|price\s+target|upgrade|downgrade|result|earnings?|q[1-4]|brokerage|analyst|aum|asset\s+quality|npa)'
                r'.{0,60}bajaj\s+finance|bajaj\s+finance.{0,60}'
                r'(?:target\s+price|price\s+target|upgrade|downgrade|result|earnings?|q[1-4]|brokerage|analyst|aum|asset\s+quality|npa)\b',
                text_lower
            ):
                return False

        # ─── Stage 5: Generic Market Roundup / Watchlist Rejection ───────────────────
        if _BAJFINANCE_ROUNDUP_RE.search(text_lower):
            if not re.search(r'\b(?:q[1-4]|pat|profit|results?|earnings?|aum|rbi|ban)\b', text_lower):
                return False

        # ─── Stage 6: Explicit Bajaj Finance Parent Identity ───────────────────────────
        if _BAJFINANCE_POSITIVE_RE.search(text_lower):
            return True

        # ─── Stage 7: Default ──────────────────────────────────────────────────────────
        return False

    def _match_bhartiartl(self, text: str, text_lower: str) -> bool:
        """
        Specialized entity matching for Bharti Airtel Limited (BHARTIARTL.NS).

        Precedence Architecture (Deterministic 11-Stage Pipeline):
          Stage 1: Consumer Retail Recharge Plan / SEO Listicle Exclusion
                   Rejects formulaic prepaid/postpaid plan comparisons, daily data pack SEO
                   tables, and OTT bundle listicles, unless substantive corporate tariff hike
                   or earnings/ARPU analysis is present.
          Stage 2: Standalone Airtel Africa Operations Exclusion
                   Rejects standalone regional African operational news (Nigeria, Kenya, etc.)
                   unless parent stake/capital/holding override is present.
          Stage 3: Standalone Airtel Payments Bank Exclusion
                   Rejects standalone payments bank products/deposits unless parent corporate
                   stake/capital action is present.
          Stage 4: Standalone Bharti Hexacom Exclusion
                   Rejects standalone Hexacom operational/telecom news, while preserving parent
                   materiality (DRHP / IPO draft papers / ownership).
          Stage 5: Standalone Indus Towers Exclusion
                   Rejects standalone tower ops and tenant dues unless parent stake transaction.
          Stage 6: Standalone Nxtra Data Centres Exclusion
                   Rejects standalone DC news unless material Airtel capex/acquisition.
          Stage 7: Generic Leadership Exclusion
                   Rejects Sunil Mittal generic Davos/Ram Mandir speeches without corporate Airtel context.
          Stage 8: Generic Market Roundup / Watchlist Rejection
                   Rejects multi-stock watchlists where Airtel is an incidental list member.
          Stage 9: Explicit Bharti Airtel Parent Identity
                   Direct mention of 'Bharti Airtel', 'Bharti Airtel Ltd/Limited', or 'Airtel India'.
          Stage 10: Contextual Bare Airtel Corporate Handling
                   Requires strong Indian telecom/corporate context (5G, 4G, spectrum, TRAI, ARPU, etc.).
          Stage 11: Default -> False.
        """
        # ─── Stage 1: Consumer Retail Recharge Plan / SEO listicle Exclusion ───────────
        if _BHARTIARTL_CONSUMER_PLAN_RE.search(text_lower):
            if not _BHARTIARTL_TARIFF_OVERRIDE_RE.search(text_lower):
                return False

        # ─── Stage 2: Standalone Airtel Africa Operations Exclusion ───────────────────
        if _BHARTIARTL_AFRICA_STANDALONE_RE.search(text_lower):
            if not _BHARTIARTL_PARENT_MATERIALITY_RE.search(text_lower):
                return False

        # ─── Stage 3: Standalone Airtel Payments Bank Exclusion ───────────────────────
        if _BHARTIARTL_PAYMENTS_BANK_RE.search(text_lower):
            if not _BHARTIARTL_PARENT_MATERIALITY_RE.search(text_lower):
                return False

        # ─── Stage 4: Standalone Bharti Hexacom Exclusion ─────────────────────────────
        if _BHARTIARTL_HEXACOM_STANDALONE_RE.search(text_lower):
            if not _BHARTIARTL_PARENT_MATERIALITY_RE.search(text_lower):
                return False

        # ─── Stage 5: Standalone Indus Towers Exclusion ───────────────────────────────
        if _BHARTIARTL_INDUS_TOWERS_RE.search(text_lower):
            if not _BHARTIARTL_PARENT_MATERIALITY_RE.search(text_lower):
                return False

        # ─── Stage 6: Standalone Nxtra Data Centres Exclusion ─────────────────────────
        if _BHARTIARTL_NXTRA_RE.search(text_lower):
            if not re.search(r'airtel.*(?:buys?|acquires?|invests?|powergrid|expansion|bandwidth)', text_lower):
                return False

        # ─── Stage 7: Leadership Sunil Mittal Generic Commentary Exclusion ────────────
        if _BHARTIARTL_LEADERSHIP_RE.search(text_lower):
            if _BHARTIARTL_LEADERSHIP_DISQUALIFY_RE.search(text_lower):
                if not _BHARTIARTL_POSITIVE_RE.search(text_lower):
                    return False
            if not re.search(r'\b(?:airtel|bharti\s+airtel|telecom|5g|oneweb|arpu|spectrum|tariffs?)\b', text_lower):
                return False

        # ─── Stage 8: Generic Market Roundup / Watchlist Rejection ───────────────────
        if _BHARTIARTL_ROUNDUP_RE.search(text_lower):
            if not re.search(r'\b(?:q[1-4]|pat|profit|results?|earnings?|arpu|tariff)\b', text_lower):
                return False

        # ─── Stage 9: Explicit Bharti Airtel Parent Identity ───────────────────────────
        if _BHARTIARTL_POSITIVE_RE.search(text_lower):
            return True

        # ─── Stage 10: Contextual Bare Airtel Corporate Handling ──────────────────────
        if re.search(r'\bairtel\b', text_lower):
            if _BHARTIARTL_BARE_AIRTEL_CORP_RE.search(text_lower):
                return True

        # ─── Stage 11: Default ────────────────────────────────────────────────────────
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
        # Rate-limiting retry policy:
        # - MAX_ATTEMPTS   : total loop iterations (7) — covers transient network flaps
        # - MAX_RL_RETRIES : max rate-limit responses before giving up (fail-fast: at most 1 retry)
        MAX_ATTEMPTS   = 7
        MAX_RL_RETRIES = 1
        rate_limit_attempts = 0

        self._inc_stat("api_requests")

        for attempt in range(MAX_ATTEMPTS):
            try:
                # Mandatory process-wide rate-limit governor (Lock + monotonic).
                # Enforces minimum interval between consecutive HTTP requests across
                # all worker threads, retry loops, and recursive branches.
                wait_sec = _global_gdelt_rate_limiter.wait()
                if wait_sec > 0.0:
                    self._inc_stat("global_rate_limit_waits")
                    with self._stats_lock:
                        self.stats["global_rate_limit_wait_seconds"] = round(
                            self.stats.get("global_rate_limit_wait_seconds", 0.0) + wait_sec, 3
                        )
                res = session.get(GDELT_DOC_API_URL, params=params, timeout=15)
                
                # Check for rate-limiting (HTTP 429 OR non-empty HTTP 200 containing rate-limit text)
                is_rate_limited = False
                rate_limit_reason = ""

                if res.status_code == 429:
                    is_rate_limited = True
                    rate_limit_reason = "HTTP 429 Rate Limit"
                elif res.status_code == 200:
                    body = res.text.strip()
                    if not body:
                        # GDELT returns HTTP 200 with an empty body when a query produces
                        # zero results for the requested time window. Treat as zero results.
                        raw_items = []
                        fetch_success = True
                        self._inc_stat("successful_requests")
                        break
                    elif _is_gdelt_rate_limit_text(body):
                        is_rate_limited = True
                        rate_limit_reason = "GDELT Rate Limit (HTTP 200 text response)"
                    else:
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
                            if _is_gdelt_rate_limit_text(res.text):
                                is_rate_limited = True
                                rate_limit_reason = "GDELT Rate Limit (non-JSON text response)"
                            else:
                                last_error = f"JSON parse error: {je}"
                                time.sleep(2 + attempt * 2)

                if is_rate_limited:
                    self._inc_stat("rate_limit_responses")
                    rate_limit_attempts += 1
                    if rate_limit_attempts > MAX_RL_RETRIES:
                        last_error = f"{rate_limit_reason} (exceeded {MAX_RL_RETRIES} retry)"
                        break  # Fail-fast: give up immediately after 1 retry
                    retry_after = res.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        sleep_sec = float(retry_after) + random.uniform(0.5, 1.5)
                    else:
                        sleep_sec = 8.0 + random.uniform(0.5, 2.0)
                    last_error = f"{rate_limit_reason} (sleeping {sleep_sec:.1f}s, attempt {rate_limit_attempts}/{MAX_RL_RETRIES})"
                    logger.warning(
                        "GDELT rate-limit for %s [%s to %s]: %s; sleeping %.1fs (attempt %d/%d)",
                        ticker, start_str, end_str, rate_limit_reason, sleep_sec, rate_limit_attempts, MAX_RL_RETRIES
                    )
                    # Close the session connection pool so the next retry uses a fresh TCP connection
                    try:
                        session.close()
                    except Exception:
                        pass
                    time.sleep(sleep_sec)
                elif res.status_code != 200:
                    last_error = f"HTTP {res.status_code}"
                    time.sleep(2 + attempt * 2)
            except Exception as e:
                last_error = str(e)
                time.sleep(2 + attempt * 2)

        if not fetch_success:
            self._inc_stat("failed_requests")
            self._inc_stat("query_failures")
            err_msg = f"GDELT API request failed for {ticker} ({start_str} to {end_str}): {last_error}"
            if rate_limit_attempts > MAX_RL_RETRIES or (last_error and ("HTTP 429" in last_error or "Rate Limit" in last_error)):
                raise GDELTRateLimitExhausted(err_msg)
            raise RuntimeError(err_msg)

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

            if not self.is_relevant_to_company(title, ticker, article_datetime=ist_dt):
                self._inc_stat("articles_rejected_company_match")
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
