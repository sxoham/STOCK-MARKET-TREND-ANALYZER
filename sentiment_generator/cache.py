import sqlite3
import hashlib
import os
import threading
import datetime
from typing import List, Dict, Any, Optional
import pandas as pd
from .config import CACHE_DB_PATH, ARTICLES_PARQUET

_db_lock = threading.Lock()

def get_connection() -> sqlite3.Connection:
    """Returns a SQLite connection configured with WAL mode for concurrency."""
    os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    """Initializes tables and performs schema migrations if necessary."""
    with _db_lock:
        conn = get_connection()
        c = conn.cursor()
        
        # Schema migration: check raw_articles
        c.execute("PRAGMA table_info(raw_articles)")
        existing_cols = {row[1] for row in c.fetchall()}
        
        # If legacy schema exists in raw_articles or articles, recreate cleanly
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='articles'")
        has_legacy_articles = c.fetchone() is not None
        if has_legacy_articles:
            c.execute("DROP TABLE articles")
            
        if existing_cols and not {"article_id", "seen_at", "source_timestamp"}.issubset(existing_cols):
            print("  [Cache] Migrating raw_articles schema to include audit timestamps...")
            c.execute("DROP TABLE raw_articles")
            
        c.execute('''
            CREATE TABLE IF NOT EXISTS raw_articles (
                article_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                company TEXT NOT NULL,
                headline TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT,
                published_at TEXT,
                seen_at TEXT,
                source_timestamp TEXT,
                trading_date TEXT NOT NULL,
                finbert_label TEXT,
                finbert_confidence REAL,
                sentiment_score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ticker, trading_date, headline)
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS fetch_periods (
                ticker TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 1,
                last_attempt TEXT NOT NULL,
                article_count INTEGER DEFAULT 0,
                error_message TEXT,
                PRIMARY KEY (ticker, period_start, period_end)
            )
        ''')
        
        conn.commit()
        conn.close()

def generate_article_id(ticker: str, trading_date: str, headline: str, url: Optional[str] = None) -> str:
    """Creates a deterministic SHA-1 hash for an article."""
    raw = f"{ticker}|{trading_date}|{headline.strip().lower()}|{(url or '').strip()}".encode('utf-8')
    return hashlib.sha1(raw).hexdigest()

def get_period_status(ticker: str, period_start: str, period_end: str) -> Optional[Dict[str, Any]]:
    """Returns period fetch record: status ('success', 'empty', 'failed'), article_count, etc."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT status, attempt_count, last_attempt, article_count, error_message
        FROM fetch_periods
        WHERE ticker = ? AND period_start = ? AND period_end = ?
    """, (ticker, period_start, period_end))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "status": row[0],
            "attempt_count": row[1],
            "last_attempt": row[2],
            "article_count": row[3],
            "error_message": row[4]
        }
    return None

def record_fetch_period(
    ticker: str,
    period_start: str,
    period_end: str,
    status: str,
    article_count: int = 0,
    error_message: Optional[str] = None
):
    """
    Records the outcome of a period fetch.
    status: 'success' | 'empty' | 'failed'
    """
    with _db_lock:
        conn = get_connection()
        c = conn.cursor()
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Check existing attempts
        c.execute("""
            SELECT attempt_count FROM fetch_periods
            WHERE ticker = ? AND period_start = ? AND period_end = ?
        """, (ticker, period_start, period_end))
        row = c.fetchone()
        attempts = (row[0] + 1) if row else 1
        
        c.execute("""
            INSERT OR REPLACE INTO fetch_periods 
            (ticker, period_start, period_end, status, attempt_count, last_attempt, article_count, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, period_start, period_end, status, attempts, now_str, article_count, error_message))
        conn.commit()
        conn.close()

def save_raw_articles(articles: List[Dict[str, Any]]) -> int:
    """Saves a batch of raw articles into SQLite cache. Returns number of newly inserted articles."""
    if not articles:
        return 0
    with _db_lock:
        conn = get_connection()
        c = conn.cursor()
        inserted = 0
        for a in articles:
            art_id = a.get("article_id") or generate_article_id(
                a["ticker"], a["trading_date"], a["headline"], a.get("url")
            )
            try:
                c.execute("""
                    INSERT OR IGNORE INTO raw_articles 
                    (article_id, ticker, company, headline, source, url, published_at, seen_at, source_timestamp, trading_date, finbert_label, finbert_confidence, sentiment_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    art_id,
                    a["ticker"],
                    a["company"],
                    a["headline"],
                    a["source"],
                    a.get("url"),
                    a.get("published_at"),
                    a.get("seen_at"),
                    a.get("source_timestamp"),
                    a["trading_date"],
                    a.get("finbert_label"),
                    a.get("finbert_confidence"),
                    a.get("sentiment_score")
                ))
                if c.rowcount > 0:
                    inserted += 1
            except Exception as e:
                pass
        conn.commit()
        conn.close()
        return inserted

def get_unscored_articles(ticker: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves articles that do not yet have FinBERT sentiment scores."""
    conn = get_connection()
    c = conn.cursor()
    if ticker:
        c.execute("""
            SELECT article_id, headline, ticker, trading_date
            FROM raw_articles
            WHERE sentiment_score IS NULL AND ticker = ?
        """, (ticker,))
    else:
        c.execute("""
            SELECT article_id, headline, ticker, trading_date
            FROM raw_articles
            WHERE sentiment_score IS NULL
        """)
    rows = c.fetchall()
    conn.close()
    return [{
        "article_id": r[0],
        "headline": r[1],
        "ticker": r[2],
        "trading_date": r[3]
    } for r in rows]

def update_article_sentiments(scored_results: List[Dict[str, Any]]):
    """Updates FinBERT sentiment score and label for articles."""
    if not scored_results:
        return
    with _db_lock:
        conn = get_connection()
        c = conn.cursor()
        c.executemany("""
            UPDATE raw_articles
            SET finbert_label = ?, finbert_confidence = ?, sentiment_score = ?
            WHERE article_id = ?
        """, [
            (r["finbert_label"], r["finbert_confidence"], r["sentiment_score"], r["article_id"])
            for r in scored_results
        ])
        conn.commit()
        conn.close()

def load_all_articles_df(ticker: Optional[str] = None) -> pd.DataFrame:
    """Loads all raw articles from cache as a pandas DataFrame."""
    conn = get_connection()
    if ticker:
        df = pd.read_sql_query("SELECT * FROM raw_articles WHERE ticker = ? ORDER BY trading_date ASC", conn, params=(ticker,))
    else:
        df = pd.read_sql_query("SELECT * FROM raw_articles ORDER BY trading_date ASC, ticker ASC", conn)
    conn.close()
    return df

def export_articles_parquet(parquet_path: str = ARTICLES_PARQUET):
    """Exports raw articles cache to the immutable news_articles.parquet audit file."""
    df = load_all_articles_df()
    expected_cols = [
        "article_id", "ticker", "company", "headline", "source", "url",
        "published_at", "seen_at", "source_timestamp", "trading_date",
        "finbert_label", "finbert_confidence", "sentiment_score"
    ]
    if df.empty:
        empty_df = pd.DataFrame(columns=expected_cols)
        empty_df.to_parquet(parquet_path, index=False)
        print(f"  [Audit] Created empty Parquet audit dataset: {parquet_path}")
        return
        
    for col in expected_cols:
        if col not in df.columns:
            df[col] = None
            
    df = df[expected_cols]
    os.makedirs(os.path.dirname(parquet_path), exist_ok=True)
    df.to_parquet(parquet_path, index=False)
    print(f"  [Audit] Exported {len(df)} immutable raw articles -> {parquet_path}")
