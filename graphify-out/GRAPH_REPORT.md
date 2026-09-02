# Graph Report - STOCK MARKET TREND ANALYZER  (2026-09-02)

## Corpus Check
- Large corpus: 302 files · ~506,215 words. Semantic extraction will be expensive (many Claude tokens). Consider running on a subfolder.

## Summary
- 685 nodes · 1085 edges · 50 communities (30 shown, 12 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 26 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Market Prediction & Indicators Pipeline
- Frontend Trading UI & Watchlist
- Axis Bank Entity Disambiguation
- Reliance Entity Disambiguation
- BigQuery GKG Extraction Engine
- GDELT Rate Limiting & Circuit Breaker
- Daily Sentiment Aggregation
- BigQuery GKG Extraction Engine
- Kotak Bank Entity Disambiguation
- Bajaj Finance Entity Disambiguation
- Bharti Airtel Entity Disambiguation
- Axis Bank Entity Disambiguation
- Flask Web App & API Endpoints
- News Fetching & Entity Matching
- Sentiment & News Precision Auditing
- GDELT Rate Limiting & Circuit Breaker
- News Fetching & Entity Matching
- GDELT Rate Limiting & Circuit Breaker
- News Article Deduplication
- GDELT Rate Limiting & Circuit Breaker
- Reliance Entity Disambiguation
- GDELT Rate Limiting & Circuit Breaker
- BigQuery GKG Extraction Engine
- Frontend Trading UI & Watchlist
- News Fetching & Entity Matching
- BigQuery GKG Extraction Engine
- Firebase-Auth Module
- News Fetching & Entity Matching
- News Article Deduplication
- News Fetching & Entity Matching
- L&T Entity Disambiguation
- Reliance Entity Disambiguation
- News Fetching & Entity Matching
- News Fetching & Entity Matching
- News Fetching & Entity Matching
- News Fetching & Entity Matching
- Sentiment & News Precision Auditing
- News Fetching & Entity Matching
- News Fetching & Entity Matching
- News Fetching & Entity Matching
- News Fetching & Entity Matching
- News Fetching & Entity Matching

## God Nodes (most connected - your core abstractions)
1. `TestNewsFetcher` - 79 edges
2. `NewsFetcher` - 56 edges
3. `train_single_model()` - 22 edges
4. `TestAxisBankMatcherValidation` - 22 edges
5. `run_pipeline()` - 21 edges
6. `process_ticker_news_fetch()` - 20 edges
7. `TestBigQueryGKGExtractor` - 18 edges
8. `get_connection()` - 15 edges
9. `TestKotakBankMatcherValidation` - 15 edges
10. `TestBajajFinanceMatcherValidation` - 15 edges

## Surprising Connections (you probably didn't know these)
- `backtest_endpoint()` --calls--> `backtest_model()`  [EXTRACTED]
  app.py → main.py
- `test_load()` --calls--> `load_sentiment_data()`  [EXTRACTED]
  scripts/test_loader.py → main.py
- `test_shap_generation()` --calls--> `train_single_model()`  [EXTRACTED]
  scripts/verify_shap.py → main.py
- `get_sentiment()` --calls--> `get_news_sentiment()`  [EXTRACTED]
  app.py → sentiment.py
- `get_prediction()` --calls--> `add_technical_indicators()`  [EXTRACTED]
  app.py → main.py

## Import Cycles
- None detected.

## Communities (50 total, 12 thin omitted)

### Community 0 - "Market Prediction & Indicators Pipeline"
Cohesion: 0.07
Nodes (51): generate_live_prediction(), get_model_db_connection(), get_prediction(), Callback, add_technical_indicators(), backtest_model(), build_lstm_model(), create_sequences() (+43 more)

### Community 1 - "Frontend Trading UI & Watchlist"
Cohesion: 0.07
Nodes (50): checkWatchlistAlerts(), closeTradeModal(), confirmTrade(), CURRENCY_CONFIG, CURRENCY_KEYS, currentAttributions, executeTrade(), fetchSentiment() (+42 more)

### Community 2 - "Axis Bank Entity Disambiguation"
Cohesion: 0.05
Nodes (20): Adversarial and boundary test suite for AXISBANK.NS (Axis Bank Limited) entity…, Q3/quarterly results and key financial metrics explicitly about Axis Bank., Analyst recommendations and share price movements directly on Axis Bank., RBI actions, SEBI/legal matters, and governance events involving Axis Bank., Axis Bank product launches, partnerships, credit cards, and operational news., Bare 'Axis' shorthand recoverable when co-occurring with named banking peers., UTI Bank (historical name before 2007 rebranding) maps to Axis Bank., Subsidiary activity that materially involves the parent bank (stake, merger,… (+12 more)

### Community 3 - "Reliance Entity Disambiguation"
Cohesion: 0.05
Nodes (12): Monday 15:30:01 IST -> next trading day (one second after close)., Validates the hardened RELIANCE.NS disambiguation rules: - True: Reliance…, 9–13 digit counts are not date-only (8) nor canonical full (14) — plain…, A string that yields >14 numeric digits after stripping non-numeric characters…, Sunday news must map forward to Monday (next trading session)., Tokens moved from FINANCIAL_CONTEXT_KEYWORDS to _FINANCIAL_CONTEXT_WORDBOUND…, 'bank' must be matched as a whole word for SBI, not as a substring.…, Validates contextual disambiguation for TCS.NS (Tata Consultancy Services… (+4 more)

### Community 4 - "BigQuery GKG Extraction Engine"
Cohesion: 0.07
Nodes (12): BigQueryGKGExtractor, Any, Parses and validates a single BigQuery GKG row into a normalized raw article…, Ingestion adapter for GDELT Global Knowledge Graph (GKG) 2.0 via Google…, Parses, validates, filters, and deduplicates a batch of BigQuery GKG records., Exports the candidate articles to an isolated staging Parquet file for auditing., Constructs an optimized, partition-pruned BigQuery SQL query for a specific…, Validates whether a URL represents a genuine article rather than a… (+4 more)

### Community 5 - "GDELT Rate Limiting & Circuit Breaker"
Cohesion: 0.06
Nodes (15): patch, A zeroed-out request budget must immediately raise RuntimeError and increment…, Articles with empty title or missing URL must increment dedicated telemetry…, First 429 response retries once and successfully returns articles on second…, Second consecutive 429 response raises GDELTRateLimitExhausted and executes no…, HTTP-200 with textual rate limit message retries once and successfully returns…, Second consecutive HTTP-200 textual rate limit raises GDELTRateLimitExhausted…, Generic 'Service Unavailable' text in HTTP 200/503 raises standard… (+7 more)

### Community 6 - "Daily Sentiment Aggregation"
Cohesion: 0.10
Nodes (27): RuntimeError, aggregate_daily_sentiment(), generate_coverage_report(), Any, DataFrame, Aggregates article-level FinBERT sentiment for a single stock on a single…, Generates: 1. Per-ticker coverage report (sentiment_coverage.csv) 2. Per-year…, get_failed_period_diagnostics() (+19 more)

### Community 7 - "BigQuery GKG Extraction Engine"
Cohesion: 0.10
Nodes (19): calculate_precision_metrics(), categorize_headline(), generate_stratified_sample(), main(), Any, DataFrame, Precision Audit Tool for GDELT BigQuery Accepted Articles. Modes: 1. Generate…, Computes strict and usable precision metrics from human labels. (+11 more)

### Community 8 - "Kotak Bank Entity Disambiguation"
Cohesion: 0.08
Nodes (13): Comprehensive adversarial test suite for Kotak Mahindra Bank Limited…, Quarterly profit, NII, NIM, PAT, asset quality for Kotak Mahindra Bank., Brokerage upgrades, target prices, analyst recommendations., RBI supervisory directives, approvals, and penalties., Uday Kotak in bank governance, succession, leadership, and promoter context., Parent bank stake sales, acquisitions, or Zurich deal involving insurance…, Personal biography, net worth, family, wedding, lifestyle articles are NOT bank…, Kotak Securities third-party stock recommendations are NOT parent bank relevant. (+5 more)

### Community 9 - "Bajaj Finance Entity Disambiguation"
Cohesion: 0.08
Nodes (13): Focused adversarial test suite for Bajaj Finance Limited (BAJFINANCE.NS)…, Quarterly profit, AUM growth, loan additions, and asset quality., Brokerage ratings, target prices, stock movements., RBI regulatory directives and corporate NCD fundraising., Holding company stake changes or subsidiary IPO plans involving Bajaj Finance., Standalone Bajaj Finserv operations are NOT parent Bajaj Finance relevant., Standalone Bajaj Housing Finance operational news., Bajaj Allianz Life / General Insurance products. (+5 more)

### Community 10 - "Bharti Airtel Entity Disambiguation"
Cohesion: 0.08
Nodes (13): Focused adversarial test suite for Bharti Airtel Limited (BHARTIARTL.NS)…, Quarterly results, ARPU growth, revenue, and subscriber additions., Brokerage ratings, target prices, stock movements., Corporate tariff hikes and 5G network rollout., Parent corporate transactions involving Hexacom IPO and Nxtra capex., Retail consumer prepaid/postpaid recharge plan comparisons and OTT bundles., Standalone regional Africa operations without parent corporate action., Standalone banking products. (+5 more)

### Community 11 - "Axis Bank Entity Disambiguation"
Cohesion: 0.09
Nodes (11): NewsFetcher, Production-grade historical news fetcher for Indian equities (NSE) using GDELT…, Returns True if text_lower contains at least one financial/corporate signal,…, Determines whether a headline/text is genuinely relevant to TCS.NS (Tata…, Determines whether a headline/text is genuinely relevant to RELIANCE.NS…, Determines whether a headline/text is genuinely relevant to INFY.NS (Infosys…, Determines whether a headline/text is genuinely relevant to ICICIBANK.NS (ICICI…, Determines whether a headline/text is genuinely relevant to AXISBANK.NS (Axis… (+3 more)

### Community 12 - "Flask Web App & API Endpoints"
Cohesion: 0.16
Nodes (20): backtest_endpoint(), dashboard(), delete_user_data(), get_db_connection(), get_sentiment(), get_stocks(), get_user_data(), index() (+12 more)

### Community 13 - "News Fetching & Entity Matching"
Cohesion: 0.09
Nodes (12): Adversarial and boundary test suite for ICICIBANK.NS (ICICI Bank Limited)…, Explicit ICICI Bank financial results, NIM, NII, asset quality, and shares., ICICI Bank regulatory, governance, digital banking, and operational…, Subsidiary actions materially involving parent bank (merger, delisting, parent…, Bare ICICI references with strong banking, market mover, or peer signals., Current and former leadership in corporate/legal/governance contexts., Standalone subsidiary products, earnings, and operations without parent bank…, Automated 13F and SEC foreign portfolio filing notices. (+4 more)

### Community 14 - "Sentiment & News Precision Auditing"
Cohesion: 0.13
Nodes (12): date, LowPrecisionTimestampError, datetime, Raised by parse_gdelt_timestamp() when a GDELT timestamp carries only date-…, Thread-safe increment of diagnostic counters., Builds a GDELT OR query from the primary company name and configured aliases,…, Determines whether a headline is genuinely relevant to the target company,…, Parses a GDELT seendate string (UTC) into a 4-tuple: (source_timestamp,… (+4 more)

### Community 15 - "GDELT Rate Limiting & Circuit Breaker"
Cohesion: 0.12
Nodes (15): clear_circuit_breaker_state(), init_db(), Initializes tables and performs schema migrations if necessary., Persists the GDELT circuit breaker state and cooldown window in SQLite., Clears / resets the persisted circuit breaker state upon successful recovery., set_circuit_breaker_state(), is_article_url(), _is_gdelt_rate_limit_text() (+7 more)

### Community 16 - "News Fetching & Entity Matching"
Cohesion: 0.12
Nodes (9): _make_article(), Articles with BOTH empty URL and empty headline must never be collapsed…, Two URLs differing only in tracking params must share a canonical URL key., Two records sharing a normalised URL (tracking params stripped) -> one kept., Headline-based deduplication only fires when the normalized URL is empty. Two…, Two articles with different URL paths on the same domain use the URL as their…, Same headline syndicated to two different publishers should NOT be merged,…, The _deduplicate_articles step ensures that even if the same article appears in… (+1 more)

### Community 17 - "GDELT Rate Limiting & Circuit Breaker"
Cohesion: 0.18
Nodes (11): Event, Records the outcome of a period fetch. status: 'success' | 'empty' | 'failed', record_fetch_period(), process_ticker_news_fetch(), Any, Fetches all period windows for a single ticker., GDELTRateLimitExhausted, Raised when all dedicated GDELT HTTP 429 rate-limit retries are exhausted.… (+3 more)

### Community 18 - "News Article Deduplication"
Cohesion: 0.22
Nodes (7): FinBertAnalyzer, Any, Scores a list of article dicts, attaching finbert_label, finbert_confidence,…, Financial sentiment analyzer using ProsusAI/finbert. Strictly FinBERT only --…, Runs batch sentiment classification on headlines using ProsusAI/finbert.…, Live validation test for FinBertAnalyzer: 1. Positive headlines -> +confidence…, test_finbert()

### Community 19 - "GDELT Rate Limiting & Circuit Breaker"
Cohesion: 0.15
Nodes (7): GDELTRateLimiter, Process-wide thread-safe rate limiter for GDELT API requests. Coordinates…, Atomically reserves the next available request time slot under lock, then…, Resets the rate limiter state (useful for test isolation)., GDELTRateLimiter must enforce spacing using Lock + monotonic time., Multiple concurrent threads calling GDELTRateLimiter.wait() must never fire…, Lock must be held only to reserve target timestamp slot, not during sleep…

### Community 20 - "Reliance Entity Disambiguation"
Cohesion: 0.21
Nodes (8): _gdelt_item(), _mock_200(), < 250 results: no recursive split, single request only., Exactly 250 results must trigger recursive split into two sub-requests., 250-item result triggers one split; combined sub-results are returned., >500 articles requiring multiple recursive splits. Parent=250, left=250…, A sub-window API failure must raise RuntimeError rather than silently returning…, Minimal GDELT article dict for a clearly Reliance-relevant headline.

### Community 21 - "GDELT Rate Limiting & Circuit Breaker"
Cohesion: 0.21
Nodes (12): Connection, get_circuit_breaker_state(), get_connection(), get_unscored_articles(), Any, Returns a SQLite connection configured with WAL mode for concurrency., Saves a batch of raw articles into SQLite cache. Returns number of newly…, Retrieves articles that do not yet have FinBERT sentiment scores. (+4 more)

### Community 22 - "BigQuery GKG Extraction Engine"
Cohesion: 0.29
Nodes (7): export_articles_parquet(), get_period_status(), load_all_articles_df(), DataFrame, Loads all raw articles from cache as a pandas DataFrame., Exports raw articles cache to the immutable news_articles.parquet audit file., Returns period fetch record: status ('success', 'empty', 'failed'),…

### Community 23 - "Frontend Trading UI & Watchlist"
Cohesion: 0.31
Nodes (9): initStarGrid(), destroy(), drawBackground(), drawDotGrid(), drawLines(), drawVignette(), frame(), resize() (+1 more)

### Community 24 - "News Fetching & Entity Matching"
Cohesion: 0.22
Nodes (5): Any, Returns a snapshot of all diagnostic telemetry, including: - All stat counters…, Cleans headline string for duplicate detection., Generates a stable, canonical deduplication key for an article record. Returns…, Deduplicates article records using canonical deduplication keys. Primary key…

### Community 25 - "BigQuery GKG Extraction Engine"
Cohesion: 0.48
Nodes (6): generate_article_id(), Creates a deterministic SHA-1 hash for an article., check_cache_state(), fetch_nse_calendar(), get_git_commit(), run_live_poc()

### Community 26 - "Firebase-Auth Module"
Cohesion: 0.33
Nodes (4): app, auth, firebaseConfig, googleProvider

### Community 28 - "News Article Deduplication"
Cohesion: 0.83
Nodes (3): get_article_content(), get_sentiment(), update_all_sentiments()

### Community 30 - "L&T Entity Disambiguation"
Cohesion: 0.67
Nodes (3): _build_trading_calendar(), main(), Live GDELT validation probe for ambiguous tickers: ITC.NS, LT.NS, TITAN.NS,…

### Community 31 - "Reliance Entity Disambiguation"
Cohesion: 0.67
Nodes (3): _build_trading_calendar(), main(), Live GDELT validation probe -- RELIANCE.NS, January 2025. Purpose -------…

## Knowledge Gaps
- **11 isolated node(s):** `firebaseConfig`, `app`, `auth`, `googleProvider`, `currentAttributions` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 305 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `NewsFetcher` connect `Axis Bank Entity Disambiguation` to `Axis Bank Entity Disambiguation`, `Reliance Entity Disambiguation`, `BigQuery GKG Extraction Engine`, `Daily Sentiment Aggregation`, `Kotak Bank Entity Disambiguation`, `Bajaj Finance Entity Disambiguation`, `Bharti Airtel Entity Disambiguation`, `News Fetching & Entity Matching`, `Sentiment & News Precision Auditing`, `GDELT Rate Limiting & Circuit Breaker`, `GDELT Rate Limiting & Circuit Breaker`, `BigQuery GKG Extraction Engine`, `News Fetching & Entity Matching`, `BigQuery GKG Extraction Engine`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `L&T Entity Disambiguation`, `Reliance Entity Disambiguation`, `News Fetching & Entity Matching`?**
  _High betweenness centrality (0.379) - this node is a cross-community bridge._
- **Why does `TestNewsFetcher` connect `Reliance Entity Disambiguation` to `GDELT Rate Limiting & Circuit Breaker`, `Axis Bank Entity Disambiguation`, `Sentiment & News Precision Auditing`, `GDELT Rate Limiting & Circuit Breaker`, `News Fetching & Entity Matching`, `GDELT Rate Limiting & Circuit Breaker`, `GDELT Rate Limiting & Circuit Breaker`, `Reliance Entity Disambiguation`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `Sentiment & News Precision Auditing`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`, `News Fetching & Entity Matching`?**
  _High betweenness centrality (0.266) - this node is a cross-community bridge._
- **Why does `download_macro_data()` connect `Market Prediction & Indicators Pipeline` to `Sentiment & News Precision Auditing`?**
  _High betweenness centrality (0.190) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `TestNewsFetcher` (e.g. with `GDELTRateLimiter` and `GDELTRateLimitExhausted`) actually correct?**
  _`TestNewsFetcher` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `NewsFetcher` (e.g. with `BigQueryGKGExtractor` and `process_ticker_news_fetch()`) actually correct?**
  _`NewsFetcher` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `firebaseConfig`, `app`, `auth` to the rest of the system?**
  _11 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Market Prediction & Indicators Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.07213114754098361 - nodes in this community are weakly interconnected._