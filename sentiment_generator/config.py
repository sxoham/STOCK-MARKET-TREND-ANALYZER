import os
import datetime

# ─── Date Configuration ────────────────────────────────────────────────────────
START_DATE = "2000-01-01"
END_DATE = datetime.date.today().strftime("%Y-%m-%d")

# ─── Market & Prediction Timing ───────────────────────────────────────────────
# Timezone and cutoff for associating news with the appropriate NSE trading day.
# Articles arriving before market close on a trading day are available for that day's session (to predict D+1).
# Articles arriving after market close (or on weekends/holidays) roll into the next NSE trading day.
MARKET_TIMEZONE = "Asia/Kolkata"
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# ─── 20 Stocks Configuration ──────────────────────────────────────────────────
STOCKS = {
    "RELIANCE.NS": "Reliance Industries",
    "TCS.NS": "Tata Consultancy Services",
    "HDFCBANK.NS": "HDFC Bank",
    "ICICIBANK.NS": "ICICI Bank",
    "INFY.NS": "Infosys",
    "HINDUNILVR.NS": "Hindustan Unilever",
    "ITC.NS": "ITC Limited",
    "SBIN.NS": "State Bank of India",
    "BHARTIARTL.NS": "Bharti Airtel",
    "KOTAKBANK.NS": "Kotak Mahindra Bank",
    "LT.NS": "Larsen & Toubro",
    "AXISBANK.NS": "Axis Bank",
    "ASIANPAINT.NS": "Asian Paints",
    "MARUTI.NS": "Maruti Suzuki",
    "TITAN.NS": "Titan Company",
    "BAJFINANCE.NS": "Bajaj Finance",
    "SUNPHARMA.NS": "Sun Pharmaceutical",
    "HCLTECH.NS": "HCL Technologies",
    "TATASTEEL.NS": "Tata Steel",
    "NTPC.NS": "NTPC"
}

# ─── Company Aliases for Contextual Entity Matching ───────────────────────────
# Specific keywords and aliases to avoid false positive matches on ambiguous short strings
COMPANY_ALIASES = {
    "RELIANCE.NS": [
        "Reliance Industries", "Reliance Industries Ltd", "Reliance Jio",
        "Reliance Retail", "Mukesh Ambani", "RIL"
    ],
    "TCS.NS": [
        "Tata Consultancy Services", "Tata Consultancy", "TCS"
    ],
    "HDFCBANK.NS": [
        "HDFC Bank", "HDFC Bank Ltd", "Housing Development Finance Corp", "HDFC Ltd"
    ],
    "ICICIBANK.NS": [
        "ICICI Bank", "ICICI Bank Ltd", "ICICI Bank Limited", "ICICIBANK", "Sandeep Bakhshi"
    ],
    "INFY.NS": [
        "Infosys", "Infosys Ltd", "Infosys Technologies", "Salil Parekh", "Narayana Murthy"
    ],
    "HINDUNILVR.NS": [
        "Hindustan Unilever", "Hindustan Unilever Ltd", "HUL", "Hindustan Lever"
    ],
    "ITC.NS": [
        "ITC Ltd", "ITC Limited", "ITC Hotels", "Imperial Tobacco Company of India"
    ],
    "SBIN.NS": [
        "State Bank of India", "SBI", "State Bank", "Dinesh Khara"
    ],
    "BHARTIARTL.NS": [
        "Bharti Airtel", "Bharti Airtel Ltd", "Bharti Airtel Limited"
    ],
    "KOTAKBANK.NS": [
        "Kotak Mahindra Bank", "Kotak Bank", "Kotak Mahindra", "Uday Kotak"
    ],
    "LT.NS": [
        "Larsen & Toubro", "Larsen and Toubro", "L&T", "L&T Infotech", "L&T Technology"
    ],
    "AXISBANK.NS": [
        "Axis Bank", "Axis Bank Ltd", "UTI Bank"
    ],
    "ASIANPAINT.NS": [
        "Asian Paints", "Asian Paints Ltd"
    ],
    "MARUTI.NS": [
        "Maruti Suzuki", "Maruti Suzuki India", "Maruti Udyog"
    ],
    "TITAN.NS": [
        "Titan Company", "Titan Company Ltd", "Titan Watches", "Titan Eyeplus", "Tanishq", "Fastrack"
    ],
    "BAJFINANCE.NS": [
        "Bajaj Finance", "Bajaj Finance Ltd", "Bajaj Finance Limited", "Bajaj Auto Finance"
    ],
    "SUNPHARMA.NS": [
        "Sun Pharmaceutical", "Sun Pharma", "Sun Pharmaceuticals", "Dilip Shanghvi"
    ],
    "HCLTECH.NS": [
        "HCL Technologies", "HCL Tech", "HCL Technologies Ltd", "HCL Enterprise", "Shiv Nadar"
    ],
    "TATASTEEL.NS": [
        "Tata Steel", "Tata Steel Ltd", "Tisco", "Tata Steel Europe", "Corus"
    ],
    "NTPC.NS": [
        "NTPC", "NTPC Ltd", "National Thermal Power Corporation"
    ]
}

# ─── BigQuery Candidate Retrieval Terms ──────────────────────────────────────
# Explicit candidate retrieval terms for BigQuery SQL.
# Keeps broad SQL candidate discovery separated from the final Python entity acceptance aliases.
# Tickers not specified here fallback to COMPANY_ALIASES.
BIGQUERY_CANDIDATE_TERMS = {
    "BHARTIARTL.NS": [
        "Bharti Airtel", "Bharti Airtel Ltd", "Airtel", "Sunil Mittal", "Bharti Enterprises"
    ],
    "BAJFINANCE.NS": [
        "Bajaj Finance", "Bajaj Finance Ltd", "Bajaj Finserv", "Bajaj Auto Finance"
    ],
    "ITC.NS": [
        "ITC", "ITC Ltd", "ITC Limited", "ITC Hotels", "Imperial Tobacco", "Sanjiv Puri"
    ],
    "LT.NS": [
        "Larsen & Toubro", "Larsen and Toubro", "L&T", "Larsen Toubro", "L&T Construction",
        "S.N. Subrahmanyan", "SN Subrahmanyan", "A.M. Naik", "AM Naik", "L&T Energy",
        "L&T Hydrocarbon", "L&T Infotech", "L&T Technology", "L&T Finance", "L&T Realty",
        "L&T Metro", "L&T Semiconductor"
    ],
    "SBIN.NS": [
        "State Bank of India", "SBI", "Dinesh Khara", "Dinesh Kumar Khara",
        "C.S. Setty", "CS Setty", "Challa Sreenivasulu Setty", "भारतीय स्टेट बैंक"
    ],
    "TCS.NS": [
        "Tata Consultancy Services", "Tata Consultancy Services Ltd",
        "Tata Consultancy Services Limited", "Tata Consultancy", "TCS",
        "K. Krithivasan", "K Krithivasan", "Krithivasan", "टाटा कंसल्टेंसी सर्विसेज"
    ],
    "INFY.NS": [
        "Infosys", "Infosys Ltd", "Infosys Limited", "Infosys Technologies",
        "INFY", "Salil Parekh", "Narayana Murthy", "N. R. Narayana Murthy",
        "इंफोसिस"
    ],
    "ICICIBANK.NS": [
        "ICICI Bank", "ICICI Bank Limited", "ICICI Bank Ltd", "ICICIBANK",
        "ICICI", "Sandeep Bakhshi"
    ]
}

# ─── Corporate Entity Lifecycle Configuration ───────────────────────────────
# Explicit lifecycle boundaries for subsidiaries, demergers, and spinoffs.
# Ensures that subsidiary sentiment is attributed to the parent during its pre-separation
# and transition phases, but not after the subsidiary becomes an independent listed entity
# (unless parent entity or demerger transition is explicitly referenced).
CORPORATE_ENTITY_LIFECYCLES = {
    "ITC.NS": {
        "subsidiaries": {
            "ITC Hotels Limited": {
                "pre_separation_aliases": ["itc hotels", "itc hotel", "itc ratnadipa", "fortune hotels", "welcomhotel", "होटल"],
                "scheme_effective_date": "2025-01-01",
                "ex_date": "2025-01-06",
                "listing_date": "2025-01-29",
                "standalone_ticker": "ITCHOTELS.NS",
                "transition_signals": [
                    "demerger", "spinoff", "spin-off", "trades sans", "sans", "adjusts",
                    "shareholder", "shareholders", "entitlement", "allotment", "scheme of arrangement",
                    "डिमर्जर", "scission"
                ],
                "post_separation_treatment": "STANDALONE_INDEPENDENT"
            }
        }
    }
}

# ─── Directory Paths ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(BASE_DIR, "cache")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ─── Output File Paths ────────────────────────────────────────────────────────
DAILY_SENTIMENT_CSV = os.path.join(DATA_DIR, "daily_sentiment.csv")
SENTIMENT_METADATA_CSV = os.path.join(DATA_DIR, "sentiment_metadata.csv")
SENTIMENT_COVERAGE_CSV = os.path.join(DATA_DIR, "sentiment_coverage.csv")
ARTICLES_PARQUET = os.path.join(DATA_DIR, "news_articles.parquet")
QUALITY_REPORT_TXT = os.path.join(DATA_DIR, "data_quality_report.txt")
QUALITY_REPORT_CSV = os.path.join(DATA_DIR, "data_quality_report.csv")
CACHE_VERSION = os.getenv("SENTIMENT_CACHE_VERSION", "v2")
CACHE_DB_PATH = os.path.join(CACHE_DIR, f"sentiment_cache_{CACHE_VERSION}.db")
LEGACY_FORENSIC_CACHE_DB_PATH = os.path.join(CACHE_DIR, "sentiment_cache.db")

# Root Output Path for direct application usage (only updated after validation gate PASSES)
ROOT_DAILY_SENTIMENT_CSV = os.path.abspath(os.path.join(BASE_DIR, "..", "daily_sentiment.csv"))

# ─── Hugging Face Model & Execution Configuration ─────────────────────────────
FINBERT_MODEL_NAME = "ProsusAI/finbert"
GDELT_MAX_RECORDS = 250
# Safety budget for recursive GDELT pagination per fetch_gdelt_window() root call.
# A single month window can bisect at most ~log2(30*24*3600/3600) ≈ 10 levels deep,
# so 64 is generous while preventing runaway recursion on pathological inputs.
GDELT_MAX_REQUESTS_PER_WINDOW = 64

# Mandatory pre-request sleep interval between GDELT DOC API calls.
# Managed by process-wide Lock + monotonic() governor.
GDELT_REQUEST_SLEEP_SECONDS = 8.0

# Persistent circuit-breaker cooldown window in minutes after exhausted 429 retries
GDELT_CIRCUIT_BREAKER_COOLDOWN_MINUTES = int(os.getenv("GDELT_CIRCUIT_BREAKER_COOLDOWN_MINUTES", "60"))

# Number of concurrent worker threads for multi-ticker fetching.
FETCH_WORKERS = 1

LOW_COVERAGE_THRESHOLD = 0.10  # Flag years/tickers with <10% trading-day coverage
