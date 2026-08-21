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
        "ICICI Bank", "ICICI Bank Ltd", "ICICI Prudential", "ICICI Securities"
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
        "Bharti Airtel", "Bharti Airtel Ltd", "Airtel", "Sunil Mittal", "Bharti Enterprises"
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
        "Bajaj Finance", "Bajaj Finance Ltd", "Bajaj Finserv", "Bajaj Auto Finance"
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
CACHE_DB_PATH = os.path.join(CACHE_DIR, "sentiment_cache.db")

# Root Output Path for direct application usage (only updated after validation gate PASSES)
ROOT_DAILY_SENTIMENT_CSV = os.path.abspath(os.path.join(BASE_DIR, "..", "daily_sentiment.csv"))

# ─── Hugging Face Model & Execution Configuration ─────────────────────────────
FINBERT_MODEL_NAME = "ProsusAI/finbert"
GDELT_MAX_RECORDS = 250
# Safety budget for recursive GDELT pagination per fetch_gdelt_window() root call.
# A single month window can bisect at most ~log2(30*24*3600/3600) ≈ 10 levels deep,
# so 64 is generous while preventing runaway recursion on pathological inputs.
GDELT_MAX_REQUESTS_PER_WINDOW = 64

# Mandatory pre-request sleep before every GDELT DOC API call.
# This is the PRIMARY rate-limit defence; the exponential back-off retry is the
# safety net for transient spikes, not the routine throttle mechanism.
#
# GDELT strictly enforces a 5-second per-IP rate limit on the DOC 2.0 API:
# "Please limit requests to one every 5 seconds".
# At 5.0s per request with FETCH_WORKERS=1, the rate remains strictly compliant.
GDELT_REQUEST_SLEEP_SECONDS = 5.0

# Number of concurrent worker threads for multi-ticker fetching.
# Kept at 1 to prevent multiple threads from concurrently violating GDELT's 5-second IP limit.
FETCH_WORKERS = 1

LOW_COVERAGE_THRESHOLD = 0.10  # Flag years/tickers with <10% trading-day coverage
