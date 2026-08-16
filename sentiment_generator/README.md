# Stock Market Sentiment Generator (FinBERT & Historical News Engine)

This module builds and updates company-specific daily stock market sentiment using Hugging Face's `ProsusAI/finbert` NLP transformer and historical financial news sources (GDELT DOC 2.0 API).

## Module Architecture

```text
sentiment_generator/
│
├── generate_sentiment.py   # Master pipeline (two-phase architecture + validation gate)
├── config.py               # Market timing cutoffs, tickers, aliases, file paths
├── news_fetcher.py         # GDELT recursive bisection pagination & contextual entity filtering
├── finbert_sentiment.py    # FinBERT classification engine (strictly ProsusAI/finbert)
├── aggregation.py          # Daily aggregation & per-ticker/per-year coverage reporting
├── cache.py                # SQLite WAL cache with audit timestamps & fetch status tracking
├── validation.py           # Production validation gate (dates, bounds, metadata invariants)
├── requirements.txt        # Module dependencies
├── README.md               # Architecture and timing documentation
│
├── data/
│   ├── daily_sentiment.csv     # Staging daily stock sentiment matrix (rebuilt independently)
│   ├── sentiment_metadata.csv  # Daily article counts, pos/neu/neg counts, News_Available flag
│   ├── sentiment_coverage.csv  # Per-ticker & per-year historical coverage report
│   ├── data_quality_report.txt # Comprehensive reliability & coverage audit report
│   ├── data_quality_report.csv # Tabular coverage quality report
│   └── news_articles.parquet   # Immutable raw article audit dataset
│
└── cache/
    └── sentiment_cache.db      # SQLite cache tracking articles & fetch periods (success/empty/failed)
```

## Prediction Timing & Look-Ahead Bias Protection

The Stock Market Trend Analyzer uses information available at the close of trading session $D$ to predict price movements on $D+1$. To eliminate look-ahead bias:

1. **Market Cutoff Configuration**:
   - `MARKET_TIMEZONE`: `"Asia/Kolkata"`
   - `MARKET_CLOSE_HOUR`: `15` (3:00 PM IST)
   - `MARKET_CLOSE_MINUTE`: `30` (3:30 PM IST)
2. **Timing Assignment Rules**:
   - **Trading Day $D$ before 15:30 IST**: Mapped to trading date $D$. The information is available before market close on day $D$ to predict $D+1$.
   - **Trading Day $D$ at/after 15:30 IST**: Mapped to the *next* open NSE trading day. Arriving post-close, it becomes part of the next session's information set.
   - **Saturdays, Sundays, & NSE Holidays**: Mapped to the *next* open NSE trading day. Weekend/holiday news is never discarded.

## Sentiment Aggregation & No-News Handling

- **FinBERT Model**: Exclusively `ProsusAI/finbert`. Conversion formula:
  - `positive` $\rightarrow + \text{confidence}$
  - `neutral` $\rightarrow 0.0$
  - `negative` $\rightarrow - \text{confidence}$
- **Daily Aggregation**: Mean of article sentiment scores mapped to that trading day, bounded in $[-1.0, +1.0]$.
- **Distinction between Neutral News and No News**:
  - `Article_Count == 0`, `Sentiment_Score == 0.0`, `News_Available == False` $\rightarrow$ **No available news**.
  - `Article_Count > 0`, `Sentiment_Score == 0.0`, `News_Available == True` $\rightarrow$ **Articles existed but aggregated to neutral**.
- **No Synthetic Baseline**: No interpolation, forward-filling, backward-filling, annual baseline copying, or synthetic noise.

## Macro vs. Ticker Sentiment

- `Sentiment_Score`: General macro market sentiment preserved from the existing `daily_sentiment.csv` (not overwritten by company sentiment).
- `RELIANCE.NS`, `TCS.NS`, ... `NTPC.NS`: Rebuilt independently from company-specific news.

## Cache Failure Retry Rules

The SQLite cache records every ticker fetch period:
- `success`: Query succeeded and articles were parsed $\rightarrow$ skipped on subsequent runs.
- `empty`: Query succeeded and 0 articles were found $\rightarrow$ skipped on subsequent runs.
- `failed`: Query failed (HTTP 429, timeout, network error) $\rightarrow$ **must be retried on next execution**.

## Production Validation Hard Gate

Staging files are written to `sentiment_generator/data/`. The production gate validates:
1. Date monotonicity, no duplicate dates, no weekends, matches NSE trading calendar.
2. Exact schema: `Date`, `Sentiment_Score`, followed by the 20 tickers.
3. No NaN, no infinity, all values in $[-1.0, +1.0]$.
4. Metadata invariants: `Article_Count == Positive_Count + Neutral_Count + Negative_Count` and `News_Available == (Article_Count > 0)`.
5. Parquet audit trail integrity.

**Only when all validation checks pass** is the staging CSV copied to the project root `daily_sentiment.csv`. If validation fails or `--dry-run` is active, root production files are never modified.

## CLI Usage

```powershell
# Test a single ticker in dry-run mode (does not modify root daily_sentiment.csv)
python -m sentiment_generator.generate_sentiment --ticker RELIANCE.NS --start-date 2024-01-01 --end-date 2024-01-31 --dry-run

# Test all 20 tickers for a date range
python -m sentiment_generator.generate_sentiment --start-date 2024-01-01 --end-date 2024-01-31 --dry-run

# Force refetching ignoring cached status
python -m sentiment_generator.generate_sentiment --ticker RELIANCE.NS --start-date 2024-01-01 --end-date 2024-01-31 --dry-run --force-refetch

# Full production run (after tests pass)
python -m sentiment_generator.generate_sentiment
```
