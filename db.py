"""
TrendAnalyzer Unified Database Abstraction Layer
Supports:
1. Production PostgreSQL via DATABASE_URL (psycopg2)
2. Local SQLite fallback for development mode (users.db and model_logs.db)
3. Strict Fail-Closed Invariant: In production (IS_PROD=True), startup fails if DATABASE_URL is missing or invalid.
4. Safe SQL parameter translation (? -> %s) outside literals and comments.
5. Transactional commit on success, explicit rollback on exception.
6. Idempotent schema initialization.
"""

import os
import sys
import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger('trendanalyzer.db')

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
# Normalize postgres:// to postgresql:// for psycopg2 compatibility
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

# Determine environment mode
IS_PROD = (
    os.environ.get("FLASK_ENV", "").lower() in ["prod", "production"]
    or os.environ.get("ENV", "").lower() in ["prod", "production"]
    or os.environ.get("FLASK_DEBUG", "false").lower() != "true"
)

def get_sqlite_path(target: str = "users") -> str:
    """Dynamically resolves SQLite path, respecting DATA_DIR and test overrides."""
    data_dir = os.environ.get("DATA_DIR", "").strip()
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, 'users.db' if target == "users" else 'model_logs.db')
    if target == "users":
        return os.environ.get("DATABASE_PATH", "users.db").strip()
    else:
        return os.environ.get("MODEL_DB_PATH", "model_logs.db").strip()



def is_postgres_configured() -> bool:
    """Returns True if DATABASE_URL is set and starts with postgresql://"""
    url = os.environ.get("DATABASE_URL", "").strip()
    return bool(url and (url.startswith("postgresql://") or url.startswith("postgres://")))


def verify_production_database_config():
    """Fail-closed invariant: In production, application MUST NOT start on ephemeral SQLite."""
    if os.environ.get("ALLOW_SQLITE_DEV_OVERRIDE", "").lower() == "true":
        return

    current_is_prod = (
        os.environ.get("RENDER", "").lower() == "true"
        or os.environ.get("FLASK_ENV", "").lower() in ["prod", "production"]
        or os.environ.get("ENV", "").lower() in ["prod", "production"]
        or (os.environ.get("FLASK_DEBUG", "").lower() == "false" and os.environ.get("FLASK_ENV") is not None)
    )
    if current_is_prod and not is_postgres_configured():
        msg = (
            "CRITICAL STARTUP FAILURE: Production deployment detected (IS_PROD=True) "
            "but DATABASE_URL is missing or unconfigured. Ephemeral SQLite storage is "
            "strictly forbidden in production to prevent user data loss."
        )
        logger.critical(msg)
        raise RuntimeError(msg)


def translate_query(sql: str) -> str:
    """
    Safely translates SQLite '?' parameter placeholders to PostgreSQL '%s'.
    Preserves '?' characters inside string literals, quoted identifiers, and comments.
    """
    result = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(sql)
    while i < n:
        char = sql[i]
        next_char = sql[i + 1] if i + 1 < n else ''

        if in_line_comment:
            if char == '\n':
                in_line_comment = False
            result.append(char)
        elif in_block_comment:
            if char == '*' and next_char == '/':
                result.append('*/')
                i += 1
                in_block_comment = False
            else:
                result.append(char)
        elif in_single_quote:
            result.append(char)
            if char == '\\' and next_char:
                result.append(next_char)
                i += 1
            elif char == "'" and next_char == "'":
                result.append("'")
                i += 1
            elif char == "'":
                in_single_quote = False
        elif in_double_quote:
            result.append(char)
            if char == '\\' and next_char:
                result.append(next_char)
                i += 1
            elif char == '"' and next_char == '"':
                result.append('"')
                i += 1
            elif char == '"':
                in_double_quote = False
        else:
            if char == '-' and next_char == '-':
                in_line_comment = True
                result.append('--')
                i += 1
            elif char == '/' and next_char == '*':
                in_block_comment = True
                result.append('/*')
                i += 1
            elif char == "'":
                in_single_quote = True
                result.append(char)
            elif char == '"':
                in_double_quote = True
                result.append(char)
            elif char == '?':
                result.append('%s')
            else:
                result.append(char)
        i += 1
    return "".join(result)


class DictRowWrapper(dict):
    """Row wrapper allowing both dictionary and attribute/index-style access."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class WrappedCursor:
    """Cursor wrapper that translates placeholders and returns DictRowWrapper instances."""
    def __init__(self, raw_cursor, is_pg: bool):
        self._cur = raw_cursor
        self._is_pg = is_pg

    def execute(self, query: str, params: Optional[Union[Tuple, List]] = None):
        if self._is_pg:
            translated = translate_query(query)
            if params is not None:
                self._cur.execute(translated, params)
            else:
                self._cur.execute(translated)
        else:
            if params is not None:
                self._cur.execute(query, params)
            else:
                self._cur.execute(query)
        return self

    def fetchone(self) -> Optional[DictRowWrapper]:
        row = self._cur.fetchone()
        if row is None:
            return None
        if self._is_pg:
            # psycopg2 RealDictCursor returns dict-like
            return DictRowWrapper(row)
        else:
            # sqlite3.Row
            return DictRowWrapper({k: row[k] for k in row.keys()})

    def fetchall(self) -> List[DictRowWrapper]:
        rows = self._cur.fetchall()
        if not rows:
            return []
        if self._is_pg:
            return [DictRowWrapper(r) for r in rows]
        else:
            return [DictRowWrapper({k: r[k] for k in r.keys()}) for r in rows]

    def close(self):
        self._cur.close()


class DatabaseConnectionWrapper:
    """Connection wrapper providing unified execute, cursor, commit, rollback, close."""
    def __init__(self, raw_conn, is_pg: bool):
        self._conn = raw_conn
        self._is_pg = is_pg

    def cursor(self) -> WrappedCursor:
        if self._is_pg:
            import psycopg2.extras
            raw_cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            raw_cur = self._conn.cursor()
        return WrappedCursor(raw_cur, is_pg=self._is_pg)

    def execute(self, query: str, params: Optional[Union[Tuple, List]] = None) -> WrappedCursor:
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


def get_db_connection(target: str = "users") -> DatabaseConnectionWrapper:
    """
    Returns a unified connection for either PostgreSQL (if DATABASE_URL configured)
    or the appropriate SQLite database file ('users' -> users.db, 'models' -> model_logs.db).
    """
    if is_postgres_configured():
        import psycopg2
        url = os.environ.get("DATABASE_URL", "").strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        raw_conn = psycopg2.connect(url)
        return DatabaseConnectionWrapper(raw_conn, is_pg=True)
    else:
        # SQLite Development Fallback
        verify_production_database_config()
        path = get_sqlite_path(target)
        raw_conn = sqlite3.connect(path, timeout=30.0)
        raw_conn.row_factory = sqlite3.Row
        return DatabaseConnectionWrapper(raw_conn, is_pg=False)


def get_model_db_connection() -> DatabaseConnectionWrapper:
    """Helper returning connection to the model prediction database."""
    return get_db_connection(target="models")


def init_all_tables():
    """
    Idempotently initializes all application schemas.
    Never drops or truncates existing tables.
    """
    if is_postgres_configured():
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            # 1. users table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    email VARCHAR(255) PRIMARY KEY,
                    data TEXT,
                    is_verified INTEGER DEFAULT 0,
                    subscription_tier VARCHAR(50) DEFAULT 'free',
                    subscription_expiry TIMESTAMP,
                    start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status VARCHAR(50) DEFAULT 'active'
                );
            """)

            # 2. feedback table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255),
                    message TEXT,
                    rating INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 3. alerts table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255),
                    ticker VARCHAR(20),
                    target_price DOUBLE PRECISION,
                    condition VARCHAR(10),
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 4. subscriptions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255),
                    tier VARCHAR(50),
                    start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    end_date TIMESTAMP,
                    status VARCHAR(50) DEFAULT 'active'
                );
            """)

            # 5. predictions table (NO destructive unique constraint on ticker+date to preserve separate runs)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    ticker VARCHAR(20),
                    date VARCHAR(20),
                    predicted_date VARCHAR(20),
                    prediction VARCHAR(20),
                    probability DOUBLE PRECISION,
                    start_price DOUBLE PRECISION,
                    actual_price DOUBLE PRECISION,
                    is_correct INTEGER,
                    actual_move VARCHAR(20),
                    actual_return DOUBLE PRECISION,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 6. user_data compatibility view
            cur.execute("""
                CREATE OR REPLACE VIEW user_data AS
                SELECT email, data, is_verified, subscription_tier, subscription_expiry
                FROM users;
            """)
            conn.commit()
            logger.info("PostgreSQL tables and user_data view verified successfully.")
        finally:
            conn.close()
    else:
        # SQLite local development tables
        conn_u = get_db_connection(target="users")
        try:
            conn_u.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    data TEXT,
                    is_verified INTEGER DEFAULT 0,
                    subscription_tier TEXT DEFAULT 'free',
                    subscription_expiry DATETIME,
                    start_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active'
                )
            ''')
            conn_u.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    message TEXT,
                    rating INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn_u.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    ticker TEXT,
                    target_price REAL,
                    condition TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn_u.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    tier TEXT,
                    start_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    end_date DATETIME,
                    status TEXT DEFAULT 'active'
                )
            ''')
            conn_u.commit()
        finally:
            conn_u.close()

        conn_m = get_db_connection(target="models")
        try:
            conn_m.execute('''
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT,
                    date TEXT,
                    predicted_date TEXT,
                    prediction TEXT,
                    probability REAL,
                    start_price REAL,
                    actual_price REAL,
                    is_correct INTEGER,
                    actual_move TEXT,
                    actual_return REAL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn_m.commit()
        finally:
            conn_m.close()
        logger.info("Local SQLite tables verified successfully.")


def sync_sequences(conn: DatabaseConnectionWrapper):
    """
    Synchronizes PostgreSQL sequences to MAX(id) for all tables with serial primary keys.
    Ensures that next inserted row generates id > MAX(id) without collision.
    """
    if not conn._is_pg:
        return
    tables = ['feedback', 'alerts', 'subscriptions', 'predictions']
    cur = conn.cursor()
    for table in tables:
        try:
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1), (SELECT MAX(id) FROM {table}) IS NOT NULL);")
        except Exception as e:
            logger.warning(f"Could not synchronize sequence for {table}: {e}")

