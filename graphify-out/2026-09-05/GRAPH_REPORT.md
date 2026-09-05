# Graph Report - STOCK MARKET TREND ANALYZER  (2026-09-05)

## Corpus Check
- 258 files · ~1,949,312 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2333 nodes · 2905 edges · 186 communities (130 shown, 40 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 65 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1f9badbe`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- main.py
- script.js
- TestAxisBankMatcherValidation
- TestNewsFetcher
- TestBigQueryGKGExtractor
- patch
- generate_sentiment.py
- generate_stratified_sample
- TestKotakBankMatcherValidation
- TestBajajFinanceMatcherValidation
- TestBhartiAirtelMatcherValidation
- NewsFetcher
- app.py
- TestICICIBankMatcherValidation
- .fetch_gdelt_window
- process_ticker_news_fetch
- _make_article
- test_modal_rendering.js
- FinBertAnalyzer
- GDELTRateLimiter
- _gdelt_item
- get_connection
- run-evals.js
- initStarGrid
- test_news_fetcher.py
- Worked example: Agent Teams for competing-hypothesis debugging
- firebase-auth.js
- Skill Evals
- Security and Hardening
- Code Review and Quality
- Test-Driven Development
- Performance Checklist
- ProductionSmokeTest
- test_p0_readiness.py
- Git Workflow and Versioning
- sdd-cache hook
- API and Interface Design
- Browser Testing with DevTools
- cache.py
- Performance Optimization
- ._compile_matchers
- get_sentiment
- Shipping and Launch
- skill-lint.js
- CI/CD and Automation
- Constraint-Driven Development
- Deprecation and Migration
- Frontend UI Engineering
- SecurityHardeningTests
- agent-skills/README.md
- Context Engineering
- Incremental Implementation
- Code Simplification
- Debugging and Error Recovery
- Documentation and ADRs
- Agent Skills
- Skill Anatomy
- Planning and Task Breakdown
- Using agent-skills with Cursor
- ReOrder: Keep Your Regulars Ordering Direct
- Interview Me
- 📈 Stock Market Trend Analyzer
- Getting Started with agent-skills
- Accessibility Checklist
- Security Checklist
- How agent-skills compares
- OpenCode Setup
- Doubt-Driven Development
- Web Performance Auditor
- Path A | Greenfield: full lifecycle from day one
- Using agent-skills with Antigravity CLI (agy)
- validate-commands-test.js
- Idea Refine
- Process
- Using Agent Skills
- Contributing to Agent Skills
- Using agent-skills with Gemini CLI
- Testing Patterns Reference (JavaScript/TypeScript)
- Spec-Driven Development
- Review Framework
- Review Scope
- Agent Personas
- validate-artifact-paths-test.js
- validate-reference-links-test.js
- Refinement & Evaluation Criteria
- Approach
- The Standing Checklist
- Observability Checklist
- validate-commands.js
- OpenCode Integration
- Setup
- Developer Onboarding
- apply_entries
- Ideation Frameworks Reference
- Stock Market Sentiment Generator (FinBERT & Historical News Engine)
- agent-skills
- marketplace.json
- benchmark.js
- simplify-ignore hook
- simplify-ignore-test.sh
- validate-artifact-paths.js
- TestP0ProductionReadiness
- Using agent-skills with Windsurf
- test-driven-development/package.json
- simplify-ignore.sh
- validate-reference-links.js
- validate-versions-test.js
- Using agent-skills with Command Code
- ci-cd-and-automation/package.json
- slug.test.js
- config-parser.test.js
- pagination.test.js
- app.test.js
- reports.test.js
- webhook.test.js
- split.test.js
- validate-versions.js
- handle_preflight_and_options
- CrossProcessLock
- ship.md
- Using agent-skills with Codex
- split-payment
- sdd-cache-post.sh
- Web Interface Guidelines
- build.md
- webperf.md
- server.js
- sdd-cache-pre.sh
- service-brief.md
- browser-testing-with-devtools/README.md
- context-audit.md
- time-pressure.md
- api-inventory.md
- decision-context.md
- migration-plan.md
- Button.tsx
- design-system.md
- scenario.md
- tasks/plan.md
- operations.md
- notifications-spec.md
- authority-pressure.md
- launch-status.md
- framework-task.md
- billing-brief.md
- portal-brief.md
- BUG.md
- test-driven-development-ecosystem/README.md
- incident.md
- session-start.sh
- session-start-test.sh
- idea-refine.sh
- rules/graphify.md
- workflows/graphify.md
- get_unresolved_failed_periods
- ._article_dedupe_key
- ._has_financial_context
- probe_ambiguous_tickers.py
- SimpleRateLimiter
- ._get_session
- probe_live_gdelt.py
- apply_security_headers
- .test_budget_exhaustion_raises_runtime_error
- get_db_connection
- .test_first_429_retries_once_and_succeeds
- .test_missing_title_and_url_telemetry
- .test_http_200_textual_rate_limit_exhaustion_raises_gdelt_rate_limit_exhausted
- .test_active_cooldown_prevents_all_http_calls
- .test_generic_service_unavailable_text_routes_through_transient_error_path
- .test_successful_request_clears_expired_breaker

## God Nodes (most connected - your core abstractions)
1. `TestNewsFetcher` - 79 edges
2. `NewsFetcher` - 56 edges
3. `get_db_connection()` - 22 edges
4. `TestAxisBankMatcherValidation` - 22 edges
5. `train_single_model()` - 21 edges
6. `SecurityHardeningTests` - 21 edges
7. `run_pipeline()` - 21 edges
8. `process_ticker_news_fetch()` - 20 edges
9. `Code Review and Quality` - 19 edges
10. `TestBigQueryGKGExtractor` - 18 edges

## Surprising Connections (you probably didn't know these)
- `get_user_data()` --calls--> `get_db_connection()`  [EXTRACTED]
  app.py → db.py
- `save_user_data()` --calls--> `get_db_connection()`  [EXTRACTED]
  app.py → db.py
- `delete_user_data()` --calls--> `get_db_connection()`  [EXTRACTED]
  app.py → db.py
- `view_database()` --calls--> `get_db_connection()`  [EXTRACTED]
  app.py → db.py
- `get_prediction()` --calls--> `get_model_db_connection()`  [EXTRACTED]
  app.py → db.py

## Import Cycles
- None detected.

## Communities (186 total, 40 thin omitted)

### Community 0 - "main.py"
Cohesion: 0.07
Nodes (52): generate_live_prediction(), Callback, add_technical_indicators(), backtest_model(), build_lstm_model(), create_sequences(), create_target(), download_macro_data() (+44 more)

### Community 1 - "script.js"
Cohesion: 0.07
Nodes (52): checkWatchlistAlerts(), closeTradeModal(), confirmTrade(), createTradeSuccessContent(), CURRENCY_CONFIG, CURRENCY_KEYS, currentAttributions, escapeHtml() (+44 more)

### Community 2 - "TestAxisBankMatcherValidation"
Cohesion: 0.05
Nodes (20): Adversarial and boundary test suite for AXISBANK.NS (Axis Bank Limited) entity…, Q3/quarterly results and key financial metrics explicitly about Axis Bank., Analyst recommendations and share price movements directly on Axis Bank., RBI actions, SEBI/legal matters, and governance events involving Axis Bank., Axis Bank product launches, partnerships, credit cards, and operational news., Bare 'Axis' shorthand recoverable when co-occurring with named banking peers., UTI Bank (historical name before 2007 rebranding) maps to Axis Bank., Subsidiary activity that materially involves the parent bank (stake, merger,… (+12 more)

### Community 3 - "TestNewsFetcher"
Cohesion: 0.03
Nodes (22): Monday 15:30:01 IST -> next trading day (one second after close)., Validates the hardened RELIANCE.NS disambiguation rules: - True: Reliance…, Monday 18:45 IST -> next trading day., Validates the refined Mukesh Ambani policy: - Positives: Requires co-occurring…, Saturday 11:00 IST -> following Monday., YYYYMMDD timestamps must raise LowPrecisionTimestampError (ValueError subclass)., 9–13 digit counts are not date-only (8) nor canonical full (14) — plain…, A string that yields >14 numeric digits after stripping non-numeric characters… (+14 more)

### Community 4 - "TestBigQueryGKGExtractor"
Cohesion: 0.07
Nodes (12): BigQueryGKGExtractor, Any, Parses and validates a single BigQuery GKG row into a normalized raw article…, Ingestion adapter for GDELT Global Knowledge Graph (GKG) 2.0 via Google…, Parses, validates, filters, and deduplicates a batch of BigQuery GKG records., Exports the candidate articles to an isolated staging Parquet file for auditing., Constructs an optimized, partition-pruned BigQuery SQL query for a specific…, Validates whether a URL represents a genuine article rather than a… (+4 more)

### Community 5 - "patch"
Cohesion: 0.08
Nodes (13): Verifies a Firebase ID token with authoritative cryptographic RS256 signature,…, verify_firebase_id_token(), patch, Prove production verifies via google.oauth2.id_token without synthetic…, Prove Mode 1 uses firebase_auth.verify_id_token when real credentials exist., TestFirebaseAuthHardening, Second consecutive 429 response raises GDELTRateLimitExhausted and executes no…, HTTP-200 with textual rate limit message retries once and successfully returns… (+5 more)

### Community 6 - "generate_sentiment.py"
Cohesion: 0.11
Nodes (26): aggregate_daily_sentiment(), generate_coverage_report(), Any, DataFrame, Aggregates article-level FinBERT sentiment for a single stock on a single…, Generates: 1. Per-ticker coverage report (sentiment_coverage.csv) 2. Per-year…, export_articles_parquet(), get_failed_period_diagnostics() (+18 more)

### Community 7 - "generate_stratified_sample"
Cohesion: 0.10
Nodes (19): calculate_precision_metrics(), categorize_headline(), generate_stratified_sample(), main(), Any, DataFrame, Precision Audit Tool for GDELT BigQuery Accepted Articles. Modes: 1. Generate…, Computes strict and usable precision metrics from human labels. (+11 more)

### Community 8 - "TestKotakBankMatcherValidation"
Cohesion: 0.08
Nodes (13): Comprehensive adversarial test suite for Kotak Mahindra Bank Limited…, Quarterly profit, NII, NIM, PAT, asset quality for Kotak Mahindra Bank., Brokerage upgrades, target prices, analyst recommendations., RBI supervisory directives, approvals, and penalties., Uday Kotak in bank governance, succession, leadership, and promoter context., Parent bank stake sales, acquisitions, or Zurich deal involving insurance…, Personal biography, net worth, family, wedding, lifestyle articles are NOT bank…, Kotak Securities third-party stock recommendations are NOT parent bank relevant. (+5 more)

### Community 9 - "TestBajajFinanceMatcherValidation"
Cohesion: 0.08
Nodes (13): Focused adversarial test suite for Bajaj Finance Limited (BAJFINANCE.NS)…, Quarterly profit, AUM growth, loan additions, and asset quality., Brokerage ratings, target prices, stock movements., RBI regulatory directives and corporate NCD fundraising., Holding company stake changes or subsidiary IPO plans involving Bajaj Finance., Standalone Bajaj Finserv operations are NOT parent Bajaj Finance relevant., Standalone Bajaj Housing Finance operational news., Bajaj Allianz Life / General Insurance products. (+5 more)

### Community 10 - "TestBhartiAirtelMatcherValidation"
Cohesion: 0.08
Nodes (13): Focused adversarial test suite for Bharti Airtel Limited (BHARTIARTL.NS)…, Quarterly results, ARPU growth, revenue, and subscriber additions., Brokerage ratings, target prices, stock movements., Corporate tariff hikes and 5G network rollout., Parent corporate transactions involving Hexacom IPO and Nxtra capex., Retail consumer prepaid/postpaid recharge plan comparisons and OTT bundles., Standalone regional Africa operations without parent corporate action., Standalone banking products. (+5 more)

### Community 11 - "NewsFetcher"
Cohesion: 0.12
Nodes (9): NewsFetcher, Production-grade historical news fetcher for Indian equities (NSE) using GDELT…, Determines whether a headline/text is genuinely relevant to RELIANCE.NS…, Determines whether a headline/text is genuinely relevant to INFY.NS (Infosys…, Determines whether a headline/text is genuinely relevant to ICICIBANK.NS (ICICI…, Determines whether a headline/text is genuinely relevant to AXISBANK.NS (Axis…, Specialized entity matching for Kotak Mahindra Bank Limited (KOTAKBANK.NS).…, Specialized entity matching for Bajaj Finance Limited (BAJFINANCE.NS).… (+1 more)

### Community 12 - "app.py"
Cohesion: 0.15
Nodes (29): backtest_endpoint(), contact(), dashboard(), delete_user_data(), disclaimer(), get_prediction(), get_stocks(), get_user_data() (+21 more)

### Community 13 - "TestICICIBankMatcherValidation"
Cohesion: 0.09
Nodes (12): Adversarial and boundary test suite for ICICIBANK.NS (ICICI Bank Limited)…, Explicit ICICI Bank financial results, NIM, NII, asset quality, and shares., ICICI Bank regulatory, governance, digital banking, and operational…, Subsidiary actions materially involving parent bank (merger, delisting, parent…, Bare ICICI references with strong banking, market mover, or peer signals., Current and former leadership in corporate/legal/governance contexts., Standalone subsidiary products, earnings, and operations without parent bank…, Automated 13F and SEC foreign portfolio filing notices. (+4 more)

### Community 14 - ".fetch_gdelt_window"
Cohesion: 0.14
Nodes (10): date, datetime, Thread-safe increment of diagnostic counters., Builds a GDELT OR query from the primary company name and configured aliases,…, Determines whether a headline is genuinely relevant to the target company,…, Parses a GDELT seendate string (UTC) into a 4-tuple: (source_timestamp,…, Finds the earliest NSE trading day strictly after cal_date. Returns None if no…, Maps an article's IST timestamp to the correct NSE trading session date.… (+2 more)

### Community 15 - "process_ticker_news_fetch"
Cohesion: 0.18
Nodes (11): Event, Records the outcome of a period fetch. status: 'success' | 'empty' | 'failed', record_fetch_period(), process_ticker_news_fetch(), Any, Fetches all period windows for a single ticker., GDELTRateLimitExhausted, Raised when all dedicated GDELT HTTP 429 rate-limit retries are exhausted.… (+3 more)

### Community 16 - "_make_article"
Cohesion: 0.11
Nodes (10): _make_article(), Articles with BOTH empty URL and empty headline must never be collapsed…, Domain normalisation must strip 'www.' as a prefix (not character-set lstrip).…, Two URLs differing only in tracking params must share a canonical URL key., Two records sharing a normalised URL (tracking params stripped) -> one kept., Headline-based deduplication only fires when the normalized URL is empty. Two…, Two articles with different URL paths on the same domain use the URL as their…, Same headline syndicated to two different publishers should NOT be merged,… (+2 more)

### Community 17 - "test_modal_rendering.js"
Cohesion: 0.13
Nodes (9): assert, createTradeSuccessContent(), elements, formatWithCurrency(), MockDocumentFragment, MockElement, MockNode, MockText (+1 more)

### Community 18 - "FinBertAnalyzer"
Cohesion: 0.21
Nodes (8): RuntimeError, FinBertAnalyzer, Any, Scores a list of article dicts, attaching finbert_label, finbert_confidence,…, Financial sentiment analyzer using ProsusAI/finbert. Strictly FinBERT only --…, Runs batch sentiment classification on headlines using ProsusAI/finbert.…, Live validation test for FinBertAnalyzer: 1. Positive headlines -> +confidence…, test_finbert()

### Community 19 - "GDELTRateLimiter"
Cohesion: 0.15
Nodes (7): GDELTRateLimiter, Process-wide thread-safe rate limiter for GDELT API requests. Coordinates…, Atomically reserves the next available request time slot under lock, then…, Resets the rate limiter state (useful for test isolation)., GDELTRateLimiter must enforce spacing using Lock + monotonic time., Multiple concurrent threads calling GDELTRateLimiter.wait() must never fire…, Lock must be held only to reserve target timestamp slot, not during sleep…

### Community 20 - "_gdelt_item"
Cohesion: 0.21
Nodes (8): _gdelt_item(), _mock_200(), < 250 results: no recursive split, single request only., Exactly 250 results must trigger recursive split into two sub-requests., 250-item result triggers one split; combined sub-results are returned., >500 articles requiring multiple recursive splits. Parent=250, left=250…, A sub-window API failure must raise RuntimeError rather than silently returning…, Minimal GDELT article dict for a clearly Reliance-relevant headline.

### Community 21 - "get_connection"
Cohesion: 0.14
Nodes (17): Connection, clear_circuit_breaker_state(), get_circuit_breaker_state(), get_connection(), get_unscored_articles(), init_db(), Any, Returns a SQLite connection configured with WAL mode for concurrency. (+9 more)

### Community 22 - "run-evals.js"
Cohesion: 0.07
Nodes (36): buildCorpus(), CASES_DIR, cosine(), EVAL_KINDS, { execFileSync }, FIXTURES_DIR, fs, loadCases() (+28 more)

### Community 23 - "initStarGrid"
Cohesion: 0.31
Nodes (9): initStarGrid(), destroy(), drawBackground(), drawDotGrid(), drawLines(), drawVignette(), frame(), resize() (+1 more)

### Community 24 - "test_news_fetcher.py"
Cohesion: 0.22
Nodes (9): is_article_url(), _is_gdelt_rate_limit_text(), LowPrecisionTimestampError, Checks if a response body contains GDELT rate-limit or service throttling text.…, Raised by parse_gdelt_timestamp() when a GDELT timestamp carries only date-…, # NOTE: bare "mia" is intentionally excluded — it is too short and generic, # IMPORTANT: recursive calls must pass the SAME _request_budget list object., Validates whether a URL represents a genuine news article rather than a static… (+1 more)

### Community 25 - "Worked example: Agent Teams for competing-hypothesis debugging"
Cohesion: 0.06
Nodes (31): 1. Direct invocation (no orchestration), 2. Single-persona slash command, 3. Parallel fan-out with merge, 4. Sequential pipeline as user-driven slash commands, 5. Research isolation (context preservation), A. Router persona ("meta-orchestrator"), Anti-pattern in this scenario, Anti-patterns (+23 more)

### Community 26 - "firebase-auth.js"
Cohesion: 0.33
Nodes (4): app, auth, firebaseConfig, googleProvider

### Community 27 - "Skill Evals"
Cohesion: 0.29
Nodes (7): Adding a skill, Eval case format, Metrics to watch, Prior art (and what we adopted), Running, Skill Evals, The three tiers

### Community 28 - "Security and Hardening"
Cohesion: 0.06
Nodes (30): Always Do (No Exceptions), Ask First (Requires Human Approval), Broken Access Control, Broken Authentication, Common Rationalizations, Cross-Site Scripting (XSS), Data Privacy & Compliance, File Upload Safety (+22 more)

### Community 29 - "Code Review and Quality"
Cohesion: 0.07
Nodes (29): 1. Correctness, 2. Readability & Simplicity, 3. Architecture, 4. Security, 5. Performance, Change Descriptions, Change Sizing, Code Review and Quality (+21 more)

### Community 30 - "Test-Driven Development"
Cohesion: 0.07
Nodes (29): Browser Testing with DevTools, Common Rationalizations, DAMP Over DRY in Tests, Decision Guide, Discover the Stack First, Name Tests Descriptively, One Assertion Per Concept, Overview (+21 more)

### Community 31 - "Performance Checklist"
Cohesion: 0.07
Nodes (26): API, Backend Checklist, Cache checklist, Caching Strategies, Common Anti-Patterns, Connection pooling, Core Web Vitals Targets, CSS (+18 more)

### Community 32 - "ProductionSmokeTest"
Cohesion: 0.09
Nodes (10): ProductionSmokeTest, Comprehensive Post-Deployment Production Smoke Test Suite Simulates the live…, Verify CORS allows production origin and strictly rejects untrusted origins., Verify Firebase token verification, failure modes, and BOLA protection., Verify /db is disabled by default in production (ENABLE_DB_VIEWER=false)., Verify baseline REST API endpoints without triggering heavy training., Verify /api/stream_train enforces concurrency lock without running heavy…, Verify storage paths for users.db, model_logs.db, and stock_models_optionB/. (+2 more)

### Community 38 - "test_p0_readiness.py"
Cohesion: 0.42
Nodes (8): handle_forbidden(), handle_not_found(), handle_server_error(), handle_too_many_requests(), handle_unexpected_exception(), _wants_json_error(), errorhandler, Production-Readiness P0 Verification Suite Validates all launch-critical P0…

### Community 39 - "Git Workflow and Versioning"
Cohesion: 0.07
Nodes (26): 1. Commit Early, Commit Often, 2. Atomic Commits, 3. Descriptive Messages, 4. Keep Concerns Separate, 5. Size Your Changes, Branch Naming, Branching Strategy, Change Summaries (+18 more)

### Community 40 - "sdd-cache hook"
Cohesion: 0.08
Nodes (24): 1. Smoke test the scripts directly, 2. End-to-end in a real session, 3. Freshness verification, 4. Debugging, How it works, Known limitations, Local testing, Mental model (+16 more)

### Community 41 - "API and Interface Design"
Cohesion: 0.08
Nodes (24): 1. Contract First, 2. Consistent Error Semantics, 3. Validate at Boundaries, 4. Prefer Addition Over Modification, 5. Predictable Naming, 6. Honouring an Idempotency Key, API and Interface Design, Common Rationalizations (+16 more)

### Community 42 - "Browser Testing with DevTools"
Cohesion: 0.08
Nodes (24): Accessibility Verification with DevTools, Available Tools, Browser Testing with DevTools, Clean Console Standard, Common Rationalizations, Console Analysis Patterns, Content Boundary Markers, For Network Issues (+16 more)

### Community 43 - "cache.py"
Cohesion: 0.25
Nodes (10): generate_article_id(), get_period_status(), Saves a batch of raw articles into SQLite cache. Returns number of newly…, Creates a deterministic SHA-1 hash for an article., Returns period fetch record: status ('success', 'empty', 'failed'),…, save_raw_articles(), check_cache_state(), fetch_nse_calendar() (+2 more)

### Community 44 - "Performance Optimization"
Cohesion: 0.08
Nodes (24): Common Rationalizations, Connection Pool Exhaustion, Core Web Vitals Targets, Large Bundle Size, Log every attempt, including the reverted ones, Missing Caching (Backend), Missing Image Optimization (Frontend), N+1 Queries (Backend) (+16 more)

### Community 46 - "get_sentiment"
Cohesion: 0.50
Nodes (3): get_sentiment(), get_news_sentiment(), Fetches news headlines for a ticker and calculates average sentiment. Returns:…

### Community 50 - "Shipping and Launch"
Cohesion: 0.08
Nodes (24): Accessibility, Code Quality, Common Rationalizations, Documentation, Error Reporting, Feature Flag Strategy, Infrastructure, Monitoring and Observability (+16 more)

### Community 51 - "skill-lint.js"
Cohesion: 0.11
Nodes (20): extractSkillReferences(), fs, lintSkill(), lintSkillContent(), parseFrontmatter(), path, REQUIRED_SECTIONS, SECTION_EXEMPT_SKILLS (+12 more)

### Community 52 - "CI/CD and Automation"
Cohesion: 0.08
Nodes (23): Automation Beyond CI, Basic CI Pipeline, Build Cop Role, CI/CD and Automation, CI Optimization, Common Rationalizations, Dependabot / Renovate, Deployment Strategies (+15 more)

### Community 53 - "Constraint-Driven Development"
Cohesion: 0.08
Nodes (22): Adapting it, Contract, Floor guard: reference implementation, Reference (Node, ~stack-agnostic patterns), Common Rationalizations, Constraint-Driven Development, Escalation Path, Loading Constraints (+14 more)

### Community 54 - "Deprecation and Migration"
Cohesion: 0.08
Nodes (23): Adapter Pattern, Code Is a Liability, Common Rationalizations, Compulsory vs Advisory Deprecation, Core Principles, Database Schema Migrations (Expand/Contract), Deprecation and Migration, Deprecation Planning Starts at Design Time (+15 more)

### Community 55 - "Frontend UI Engineering"
Cohesion: 0.08
Nodes (23): Accessibility (WCAG 2.1 AA), ARIA Labels, Avoid the AI Aesthetic, Color, Common Rationalizations, Component Architecture, Component Patterns, Design System Adherence (+15 more)

### Community 56 - "SecurityHardeningTests"
Cohesion: 0.05
Nodes (19): Verify malformed email formats are rejected on user data routes., Verify /api/feedback validates message content and bounds., Verify that payloads exceeding 512KB are rejected with 413., Verify that when REQUIRE_AUTH=True, unauthorized callers and BOLA attacks are…, Verify that when IS_PROD is active, auth cannot be bypassed., Verify rapid repeated requests trigger HTTP 429 Too Many Requests., Verify that the rate limiter evicts stale keys and bounds memory., Verify that when TRUSTED_PROXIES_COUNT=0 (direct connection / untrusted… (+11 more)

### Community 58 - "Context Engineering"
Cohesion: 0.09
Nodes (22): Anti-Patterns, Common Rationalizations, Confusion Management, Context Engineering, Context Packing Strategies, Level 1: Rules Files, Level 2: Specs and Architecture, Level 3: Relevant Source Files (+14 more)

### Community 59 - "Incremental Implementation"
Cohesion: 0.09
Nodes (22): Common Rationalizations, Contract-First Slicing, Implementation Rules, Increment Checklist, Incremental Implementation, Overview, Red Flags, Risk-First Slicing (+14 more)

### Community 60 - "Code Simplification"
Cohesion: 0.09
Nodes (21): 1. Preserve Behavior Exactly, 2. Follow Project Conventions, 3. Prefer Clarity Over Cleverness, 4. Maintain Balance, 5. Scope to What Changed, Code Simplification, Common Rationalizations, Language-Specific Guidance (+13 more)

### Community 61 - "Debugging and Error Recovery"
Cohesion: 0.09
Nodes (21): Build Failure Triage, Common Rationalizations, Debugging and Error Recovery, Error-Specific Patterns, Instrumentation Guidelines, Overview, Red Flags, Runtime Error Triage (+13 more)

### Community 62 - "Documentation and ADRs"
Cohesion: 0.09
Nodes (21): ADR Lifecycle, ADR Template, API Documentation, Architecture Decision Records (ADRs), Changelog Maintenance, Common Rationalizations, Document Known Gotchas, Documentation and ADRs (+13 more)

### Community 63 - "Agent Skills"
Cohesion: 0.10
Nodes (21): Adoption, Agent Personas, Agent Skills, All 24 Skills, Build - Write the code, Commands, Contributing, Define - Clarify what to build (+13 more)

### Community 64 - "Skill Anatomy"
Cohesion: 0.10
Nodes (20): Common Rationalizations, Context Efficiency, Core Process, Cross-Skill References, File Location, Frontmatter (Required), Naming Conventions, Overview (+12 more)

### Community 65 - "Planning and Task Breakdown"
Cohesion: 0.11
Nodes (18): Common Rationalizations, Output Files, Overview, Parallelization Opportunities, Plan Document Template, Planning and Task Breakdown, Red Flags, See Also (+10 more)

### Community 66 - "Using agent-skills with Cursor"
Cohesion: 0.11
Nodes (18): 1. Install skills into `.cursor/skills/`, 2. Add minimal project rules (optional but useful), 3. User-level skills (optional), 4. Verify, `agents/` directory, Checklist (new project), Context tips, How agents should use skills (+10 more)

### Community 67 - "ReOrder: Keep Your Regulars Ordering Direct"
Cohesion: 0.11
Nodes (17): Example 1: Vague Early-Stage Concept (Full 3-Phase Session), Example 2: Feature Idea Within an Existing Product (Codebase-Aware), Example 3: Process/Workflow Idea (Non-Product), Ideation Session Examples, Key Assumptions to Validate, MVP Scope, Not Doing (and Why), Open Questions (+9 more)

### Community 68 - "Interview Me"
Cohesion: 0.11
Nodes (17): Common Rationalizations, Example, Interaction with Other Skills, Interview Me, Loading Constraints, Output, Overview, Red Flags (+9 more)

### Community 69 - "📈 Stock Market Trend Analyzer"
Cohesion: 0.11
Nodes (17): 1. Clone the repository, 2. Set up virtual environment, 3. Install dependencies, 4. Train models & run the web server, 🔌 API Reference, 👤 Author, ⚠️ Disclaimer, 🌟 Key Features (+9 more)

### Community 70 - "Getting Started with agent-skills"
Cohesion: 0.12
Nodes (17): 1. Clone the repository, 2. Choose a skill, 3. Load the skill into your agent, 4. Use the meta-skill for discovery, Context-Aware Loading, Full Lifecycle, Getting Started with agent-skills, How Skills Work (+9 more)

### Community 71 - "Accessibility Checklist"
Cohesion: 0.12
Nodes (16): Accessibility Checklist, Accessible Lists, ARIA Roles, Buttons vs. Links, Common Anti-Patterns, Common HTML Patterns, Content, Essential Checks (+8 more)

### Community 72 - "Security Checklist"
Cohesion: 0.12
Nodes (16): AI / LLM Security, Authentication, Authorization, CORS Configuration, Data Protection, Dependency Security, Error Handling, Input Validation (+8 more)

### Community 73 - "How agent-skills compares"
Cohesion: 0.12
Nodes (15): A real head-to-head: Superpowers vs. agent-skills, agent-skills (this project), At a glance, Combining them, Concrete scenarios, How agent-skills compares, How to decide what to use, Matt Pocock's skills (+7 more)

### Community 74 - "OpenCode Setup"
Cohesion: 0.09
Nodes (22): 1. Skill Discovery, 2. Automatic Skill Invocation, 3. Lifecycle Mapping (Implicit Commands), Agent Expectations, Copy the optional slash commands, Cross-compatible paths, Example 1: Feature Development, Example 2: Bug Fix (+14 more)

### Community 75 - "Doubt-Driven Development"
Cohesion: 0.12
Nodes (15): Common Rationalizations, Cross-model escalation, Doubt-Driven Development, Interaction with Other Skills, Loading Constraints, Overview, Red Flags, Step 1: CLAIM — Surface what stands (+7 more)

### Community 76 - "Web Performance Auditor"
Cohesion: 0.13
Nodes (15): 1. Core Web Vitals, 2. Loading, 3. Rendering / JavaScript, 4. Network, Composition, Deep mode (activated when tool artifacts or live measurement are available), Metric-Honesty Rule, Operating Modes (+7 more)

### Community 77 - "Path A | Greenfield: full lifecycle from day one"
Cohesion: 0.13
Nodes (15): Add as the project grows, Adoption Guide: New Projects vs. Established Codebases, Brownfield anti-patterns, Day 0 | Define before you build, Day 0 | Install and wire up, From the start, treat these as always-on, Greenfield anti-patterns, Path A | Greenfield: full lifecycle from day one (+7 more)

### Community 78 - "Using agent-skills with Antigravity CLI (agy)"
Cohesion: 0.13
Nodes (14): 1. On-Demand Skill Activation, 2. Specialized Agent Personas, Configuration & Customization, How It Works, Option 1: Native Plugin Installation (Recommended), Option 2: Import from Gemini CLI, Project-Specific Enforcements (`AGENTS.md`), Sandbox Mode (+6 more)

### Community 79 - "validate-commands-test.js"
Cohesion: 0.17
Nodes (12): { afterEach, test }, assert, fs, os, path, sandboxes, { spawnSync }, VALIDATOR (+4 more)

### Community 80 - "Idea Refine"
Cohesion: 0.13
Nodes (14): Anti-patterns to Avoid, Detailed Instructions, How It Works, Idea Refine, Output, Phase 1: Understand & Expand (Divergent), Phase 2: Evaluate & Converge, Phase 3: Sharpen & Ship (+6 more)

### Community 81 - "Process"
Cohesion: 0.13
Nodes (14): 1. Define "working" before instrumenting, 2. Pick the right signal for each question, 3. Structured logging, 4. Metrics, 5. Distributed tracing, 6. Alerting, 7. Verify the telemetry itself, Common Rationalizations (+6 more)

### Community 82 - "Using Agent Skills"
Cohesion: 0.13
Nodes (14): 1. Surface Assumptions, 2. Manage Confusion Actively, 3. Push Back When Warranted, 4. Enforce Simplicity, 5. Maintain Scope Discipline, 6. Verify, Don't Assume, Core Operating Behaviors, Failure Modes to Avoid (+6 more)

### Community 83 - "Contributing to Agent Skills"
Cohesion: 0.14
Nodes (14): Adding a New Skill, Before proposing a new skill, Contributing to Agent Skills, Creating the skill, License, Modifying Existing Skills, Repo-scoped files, Reporting Issues (+6 more)

### Community 84 - "Using agent-skills with Gemini CLI"
Cohesion: 0.14
Nodes (13): Advanced Configuration, Always-On (GEMINI.md), Explicit Context Loading, MCP Integration, On-Demand (Skills), Option 1: Install as Skills (Recommended), Option 2: GEMINI.md (Persistent Context), Recommended Configuration (+5 more)

### Community 85 - "Testing Patterns Reference (JavaScript/TypeScript)"
Cohesion: 0.14
Nodes (13): API / Integration Testing, Common Assertions, E2E Testing (Playwright), Mock at Boundaries Only, Mock Functions, Mock Modules, Mocking Patterns, React/Component Testing (+5 more)

### Community 86 - "Spec-Driven Development"
Cohesion: 0.14
Nodes (13): Common Rationalizations, Keeping the Spec Alive, Overview, Phase 0: Scope Check, Phase 1: Specify, Phase 2: Plan, Phase 3: Tasks, Phase 4: Implement (+5 more)

### Community 87 - "Review Framework"
Cohesion: 0.18
Nodes (11): 1. Correctness, 2. Readability, 3. Architecture, 4. Security, 5. Performance, Composition, Output Format, Review Framework (+3 more)

### Community 88 - "Review Scope"
Cohesion: 0.17
Nodes (12): 1. Input Handling, 2. Authentication & Authorization, 3. Data Protection, 4. Infrastructure, 5. Third-Party Integrations, 6. AI / LLM Features (if present), Composition, Output Format (+4 more)

### Community 89 - "Agent Personas"
Cohesion: 0.17
Nodes (12): Adding a new persona, Agent Personas, Claude Code interop, Decision matrix, Direct persona invocation, How personas relate to skills and commands, Rules for personas, Slash command (orchestrator — fan-out) (+4 more)

### Community 90 - "validate-artifact-paths-test.js"
Cohesion: 0.17
Nodes (8): { afterEach, test }, assert, fs, os, path, sandboxes, { spawnSync }, VALIDATOR

### Community 91 - "validate-reference-links-test.js"
Cohesion: 0.17
Nodes (8): { afterEach, test }, assert, fs, os, path, sandboxes, { spawnSync }, VALIDATOR

### Community 92 - "Refinement & Evaluation Criteria"
Cohesion: 0.17
Nodes (11): 1. User Value, 2. Feasibility, 3. Differentiation, Assumption Audit, Core Evaluation Dimensions, Decision Framework, Might Be True (Nice to Have), Must Be True (Dealbreakers) (+3 more)

### Community 93 - "Approach"
Cohesion: 0.20
Nodes (10): 1. Analyze Before Writing, 2. Test at the Right Level, 3. Follow the Prove-It Pattern for Bugs, 4. Write Descriptive Tests, 5. Cover These Scenarios, Approach, Composition, Output Format (+2 more)

### Community 94 - "The Standing Checklist"
Cohesion: 0.18
Nodes (10): Correctness, Definition of Done, Definition of Done vs. Acceptance Criteria, Documentation, How to Apply, Integration, Quality, Red Flags (+2 more)

### Community 95 - "Observability Checklist"
Cohesion: 0.18
Nodes (10): Alerting, Dashboards, Distributed Tracing, Metrics, Observability Checklist, On-Call Questions (Start Here), Pre-Launch Gate, Structured Logging (+2 more)

### Community 96 - "validate-commands.js"
Cohesion: 0.24
Nodes (10): descriptionFromMd(), descriptionFromToml(), DIRS, fs, loadCommands(), main(), NAME_MAP, NAME_MAP_REVERSE (+2 more)

### Community 97 - "OpenCode Integration"
Cohesion: 0.20
Nodes (9): Anti-Rationalization, Core Rules, Creating a New Skill, Execution Model, Intent → Skill Mapping, Lifecycle Mapping (Implicit Commands), OpenCode Integration, Orchestration: Personas, Skills, and Commands (+1 more)

### Community 98 - "Setup"
Cohesion: 0.20
Nodes (9): Agent Personas (*.agent.md), Copilot Instructions, Custom Instructions (User Level), .github/copilot-instructions.md, Recommended Configuration, Setup, Specialized Agents, Usage Tips (+1 more)

### Community 99 - "Developer Onboarding"
Cohesion: 0.20
Nodes (10): 1. The mental model, 2. Local setup, 3. The verification loop, 4. Contribution paths, 5. Pre-PR checklist, 6. Suggested reading order, Developer Onboarding, Path 1: Fixing or improving an existing skill (most common, best first PR) (+2 more)

### Community 100 - "apply_entries"
Cohesion: 0.33
Nodes (4): apply_entries(), Simple in-memory ledger utilities., Apply entries to a starting balance and return the result. Entries are (kind,…, ApplyEntriesTest

### Community 101 - "Ideation Frameworks Reference"
Cohesion: 0.22
Nodes (8): Analogous Inspiration, Constraint-Based Ideation, First Principles Thinking, How Might We (HMW), Ideation Frameworks Reference, Jobs to Be Done (JTBD), Pre-mortem, SCAMPER

### Community 102 - "Stock Market Sentiment Generator (FinBERT & Historical News Engine)"
Cohesion: 0.22
Nodes (8): Cache Failure Retry Rules, CLI Usage, Macro vs. Ticker Sentiment, Module Architecture, Prediction Timing & Look-Ahead Bias Protection, Production Validation Hard Gate, Sentiment Aggregation & No-News Handling, Stock Market Sentiment Generator (FinBERT & Historical News Engine)

### Community 103 - "agent-skills"
Cohesion: 0.25
Nodes (8): agent-skills, Boundaries, Commands, Contributing, Conventions, Project Structure, Pull Requests, Skills by Phase

### Community 104 - "marketplace.json"
Cohesion: 0.25
Nodes (7): description, name, owner, name, url, plugins, $schema

### Community 105 - "benchmark.js"
Cohesion: 0.29
Nodes (6): output, { performance }, products, { renderProducts }, start, renderProducts()

### Community 106 - "simplify-ignore hook"
Cohesion: 0.25
Nodes (7): Annotation syntax, Crash recovery, How it works, Known limitations, Requirements, Setup, simplify-ignore hook

### Community 107 - "simplify-ignore-test.sh"
Cohesion: 0.36
Nodes (6): assert_eq(), block_hash(), CACHE, file_id(), hash_cmd(), simplify-ignore-test.sh script

### Community 108 - "validate-artifact-paths.js"
Cohesion: 0.29
Nodes (7): ARTIFACT_ALLOWLIST, findViolations(), fs, GUARDED_FILES, main(), path, ROOT

### Community 109 - "TestP0ProductionReadiness"
Cohesion: 0.11
Nodes (9): Verify login.html includes password reset trigger, modal, and non-enumerating…, Verify browser unknown route returns branded HTML 404; API unknown route…, Verify 403 error handler returns branded HTML for browser and JSON for API., Trigger an actual endpoint protected by production @limit_rate and verify 429…, Verify 500 responses never leak stack traces, file paths, secrets, or internal…, Confirm /privacy, /terms, /disclaimer, /contact return 200 without auth in…, Verify login, register, and dashboard templates include required legal links., Verify register.html has an unchecked acknowledgement checkbox and dual-flow JS… (+1 more)

### Community 110 - "Using agent-skills with Windsurf"
Cohesion: 0.29
Nodes (6): Global Rules, Project Rules, Recommended Configuration, Setup, Usage Tips, Using agent-skills with Windsurf

### Community 111 - "test-driven-development/package.json"
Cohesion: 0.29
Nodes (6): description, name, private, scripts, test, version

### Community 112 - "simplify-ignore.sh"
Cohesion: 0.57
Nodes (6): block_hash(), escape_glob(), file_id(), filter_file(), hash_cmd(), simplify-ignore.sh script

### Community 113 - "validate-reference-links.js"
Cohesion: 0.33
Nodes (6): findViolations(), fs, main(), path, ROOT, SKILLS_DIR

### Community 114 - "validate-versions-test.js"
Cohesion: 0.29
Nodes (5): assert, { execFileSync }, manifestPaths, { readFileSync }, test

### Community 115 - "Using agent-skills with Command Code"
Cohesion: 0.33
Nodes (5): Install, Manage, Usage, Using agent-skills with Command Code, Where skills live

### Community 116 - "ci-cd-and-automation/package.json"
Cohesion: 0.33
Nodes (5): name, private, scripts, lint, test

### Community 117 - "slug.test.js"
Cohesion: 0.40
Nodes (4): slugify(), assert, { slugify }, test

### Community 118 - "config-parser.test.js"
Cohesion: 0.40
Nodes (4): parseConfig(), assert, { parseConfig }, test

### Community 119 - "pagination.test.js"
Cohesion: 0.40
Nodes (4): paginate(), assert, { paginate }, test

### Community 120 - "app.test.js"
Cohesion: 0.40
Nodes (4): assert, test, { total }, total()

### Community 121 - "reports.test.js"
Cohesion: 0.40
Nodes (4): assert, test, { visibleReports }, visibleReports()

### Community 122 - "webhook.test.js"
Cohesion: 0.40
Nodes (4): previewWebhook(), assert, { previewWebhook }, test

### Community 123 - "split.test.js"
Cohesion: 0.40
Nodes (4): splitCents(), assert, { splitCents }, test

### Community 124 - "validate-versions.js"
Cohesion: 0.33
Nodes (4): { execFileSync }, expectedVersion, manifestPaths, { readFileSync }

### Community 126 - "CrossProcessLock"
Cohesion: 0.25
Nodes (4): CrossProcessLock, Atomic OS file-based lock guaranteeing cross-worker mutual exclusion. Stores…, Verify whether the owning process is still active on this host. Returns True if…, Releases the lock. Only the owner with matching token can release it. If token…

### Community 127 - "ship.md"
Cohesion: 0.40
Nodes (4): Phase A — Parallel fan-out, Phase B — Merge in main context, Phase C — Decision and rollback, Rules

### Community 128 - "Using agent-skills with Codex"
Cohesion: 0.40
Nodes (4): How it works, Install, Usage, Using agent-skills with Codex

### Community 129 - "split-payment"
Cohesion: 0.40
Nodes (4): API, Invariants, split-payment, Tests

### Community 130 - "sdd-cache-post.sh"
Cohesion: 0.70
Nodes (4): dbg(), extract_header(), hash_key(), sdd-cache-post.sh script

### Community 131 - "Web Interface Guidelines"
Cohesion: 0.40
Nodes (4): Guidelines Source, How It Works, Usage, Web Interface Guidelines

### Community 132 - "build.md"
Cohesion: 0.50
Nodes (3): Autonomous: the whole plan (`/build auto`), Default: one task, Modes

### Community 133 - "webperf.md"
Cohesion: 0.50
Nodes (3): Determine the mode, Output, Run the audit

### Community 134 - "server.js"
Cohesion: 0.50
Nodes (3): fs, http, path

### Community 135 - "sdd-cache-pre.sh"
Cohesion: 0.83
Nodes (3): dbg(), hash_key(), sdd-cache-pre.sh script

### Community 164 - "get_unresolved_failed_periods"
Cohesion: 0.40
Nodes (5): get_unresolved_failed_periods(), Returns list of periods that are currently in 'failed' status in the cache., Any, Comprehensive validation gate for the generated sentiment dataset. Returns:…, validate_production_dataset()

### Community 165 - "._article_dedupe_key"
Cohesion: 0.17
Nodes (6): Any, Returns a snapshot of all diagnostic telemetry, including: - All stat counters…, Normalizes a URL by stripping tracking query params, fragments, trailing…, Cleans headline string for duplicate detection., Generates a stable, canonical deduplication key for an article record. Returns…, Deduplicates article records using canonical deduplication keys. Primary key…

### Community 167 - "probe_ambiguous_tickers.py"
Cohesion: 0.67
Nodes (3): _build_trading_calendar(), main(), Live GDELT validation probe for ambiguous tickers: ITC.NS, LT.NS, TITAN.NS,…

### Community 176 - "probe_live_gdelt.py"
Cohesion: 0.67
Nodes (3): _build_trading_calendar(), main(), Live GDELT validation probe -- RELIANCE.NS, January 2025. Purpose -------…

### Community 179 - "get_db_connection"
Cohesion: 0.06
Nodes (36): init_databases(), Ensure database tables exist upon startup (PostgreSQL or SQLite)., DatabaseConnectionWrapper, DictRowWrapper, get_db_connection(), get_model_db_connection(), get_sqlite_path(), init_all_tables() (+28 more)

## Knowledge Gaps
- **1014 isolated node(s):** `$schema`, `name`, `description`, `name`, `url` (+1009 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1469 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **40 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `NewsFetcher` connect `NewsFetcher` to `TestAxisBankMatcherValidation`, `TestNewsFetcher`, `TestBigQueryGKGExtractor`, `._article_dedupe_key`, `generate_sentiment.py`, `._has_financial_context`, `probe_ambiguous_tickers.py`, `._get_session`, `TestBajajFinanceMatcherValidation`, `cache.py`, `TestBhartiAirtelMatcherValidation`, `._compile_matchers`, `.fetch_gdelt_window`, `process_ticker_news_fetch`, `probe_live_gdelt.py`, `TestICICIBankMatcherValidation`, `TestKotakBankMatcherValidation`, `test_news_fetcher.py`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Why does `TestNewsFetcher` connect `TestNewsFetcher` to `patch`, `._article_dedupe_key`, `NewsFetcher`, `process_ticker_news_fetch`, `_make_article`, `.test_budget_exhaustion_raises_runtime_error`, `GDELTRateLimiter`, `_gdelt_item`, `.test_first_429_retries_once_and_succeeds`, `.test_http_200_textual_rate_limit_exhaustion_raises_gdelt_rate_limit_exhausted`, `.test_active_cooldown_prevents_all_http_calls`, `test_news_fetcher.py`, `.test_missing_title_and_url_telemetry`, `get_connection`, `.test_successful_request_clears_expired_breaker`, `.test_generic_service_unavailable_text_routes_through_transient_error_path`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Why does `TestBajajFinanceMatcherValidation` connect `TestBajajFinanceMatcherValidation` to `test_news_fetcher.py`, `NewsFetcher`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `TestNewsFetcher` (e.g. with `GDELTRateLimiter` and `GDELTRateLimitExhausted`) actually correct?**
  _`TestNewsFetcher` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `NewsFetcher` (e.g. with `BigQueryGKGExtractor` and `process_ticker_news_fetch()`) actually correct?**
  _`NewsFetcher` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `patch` (e.g. with `.test_02_postgresql_production_selection()` and `.test_expired_token_returns_401()`) actually correct?**
  _`patch` has 16 INFERRED edges - model-reasoned connections that need verification._
- **What connects `$schema`, `name`, `description` to the rest of the system?**
  _1014 weakly-connected nodes found - possible documentation gaps or missing edges._