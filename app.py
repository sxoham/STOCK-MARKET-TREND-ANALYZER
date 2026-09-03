import os
import sys
import re
import html
import hmac
import time
import queue
import logging
import threading
import sqlite3
import json
import datetime
import socket
import uuid
import urllib.request
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
import pandas as pd
import numpy as np
import joblib
from keras.models import load_model
import tensorflow as tf
tf.get_logger().setLevel('ERROR')
from flask import Flask, render_template, jsonify, request, send_from_directory, redirect, Response, stream_with_context, abort
import main
import sentiment as sentiment_module

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [Security] %(message)s'
)
logger = logging.getLogger('trendanalyzer')

app = Flask(__name__)

# Request limit: 1 MB max payload
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'trendanalyzer-dev-secret-key-change-in-prod')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_DEBUG', 'false').lower() != 'true'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Environment & Deployment Mode
IS_PROD = (
    os.environ.get("FLASK_ENV", "").lower() in ["prod", "production"]
    or os.environ.get("ENV", "").lower() in ["prod", "production"]
    or os.environ.get("FLASK_DEBUG", "false").lower() != "true"
)
# Fail-closed: in production, auth is strictly mandatory
if IS_PROD:
    REQUIRE_AUTH = True
else:
    REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "false").lower() == "true"

# Config
DB_FILE = 'users.db'
MODEL_DB_FILE = 'model_logs.db'
RESULTS_DIR = main.RESULTS_DIR
STOCKS = main.STOCKS
ENABLE_DB_VIEWER = os.environ.get("ENABLE_DB_VIEWER", "false").lower() == "true"
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "trendanalyzer-4857f")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000").split(",") if o.strip()]

# Trusted Proxy Configuration
# Default is 0 (direct connection; forwarding headers are untrusted to prevent IP spoofing).
# In production with a reverse proxy (Nginx, AWS ALB, Render), set to exact number of proxy hops.
# NOTE: Gunicorn must bind to localhost or an internal private socket so it cannot be reached directly around the reverse proxy.
try:
    TRUSTED_PROXIES_COUNT = max(0, int(os.environ.get("TRUSTED_PROXIES_COUNT", "0").strip()))
except (ValueError, TypeError, AttributeError):
    TRUSTED_PROXIES_COUNT = 0

if TRUSTED_PROXIES_COUNT > 0:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=TRUSTED_PROXIES_COUNT,
        x_proto=TRUSTED_PROXIES_COUNT,
        x_host=TRUSTED_PROXIES_COUNT
    )

# CORS Preflight OPTIONS handler
@app.before_request
def handle_preflight_and_options():
    if request.method == 'OPTIONS':
        origin = request.headers.get('Origin')
        if origin and origin in ALLOWED_ORIGINS:
            response = Response(status=204)
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Admin-Key'
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Max-Age'] = '86400'
            return response

# Security headers middleware
@app.after_request
def apply_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), camera=(), microphone=()'
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'

    # HSTS: emitted only on verified HTTPS responses
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    # CORS Allowlist handling
    origin = request.headers.get('Origin')
    if origin and origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Admin-Key'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    
    # Content-Security-Policy tailored to Firebase Auth, Google Fonts, Plotly, and Chart.js
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.gstatic.com https://cdn.jsdelivr.net https://cdn.plot.ly https://apis.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self' https://www.gstatic.com https://*.googleapis.com https://*.firebaseio.com https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://*.firebaseapp.com; "
        "img-src 'self' data: https:; "
        "frame-src https://*.firebaseapp.com; "
        "frame-ancestors 'none';"
    )
    response.headers['Content-Security-Policy'] = csp
    return response

# Validation regexes
TICKER_REGEX = re.compile(r'^[A-Za-z0-9\.\-\^]{1,20}$')
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$')

def is_valid_ticker_format(ticker: str) -> bool:
    return bool(ticker and isinstance(ticker, str) and TICKER_REGEX.match(ticker.strip()))

def is_valid_email_format(email: str) -> bool:
    if not email or not isinstance(email, str) or len(email) > 255:
        return False
    email = email.strip()
    if '..' in email:
        return False
    return bool(EMAIL_REGEX.match(email))

# Cross-Process Concurrency and Memory-Bounded Rate Limiting
class CrossProcessLock:
    """
    Atomic OS file-based lock guaranteeing cross-worker mutual exclusion.
    Stores PID, hostname, creation timestamp, and unique ownership token in lockfile.
    Validates process liveness before reclaiming stale locks so that legitimately
    long-running training processes (>45 min) are never stolen while still active.
    Enforces that only the process/token that owns the lock can release it.
    """
    def __init__(self, lockfile_path: str, timeout_seconds: int = 2700):
        self.lockfile_path = lockfile_path
        self.timeout_seconds = timeout_seconds
        self._thread_lock = threading.Lock()
        self._fd = None
        self._owner_token = None

    @staticmethod
    def is_pid_alive(pid: int, hostname: str) -> bool:
        """
        Verify whether the owning process is still active on this host.
        Returns True if process is alive (or on foreign host), False if confirmed dead.
        """
        if not hostname or hostname != socket.gethostname():
            # If on another host (e.g. shared NFS mount), we cannot inspect foreign PID; assume alive
            return True
        if not pid or not isinstance(pid, int) or pid <= 0:
            return False

        if sys.platform == 'win32':
            try:
                import ctypes
                SYNCHRONIZE = 0x00100000
                handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
                if handle == 0:
                    err = ctypes.windll.kernel32.GetLastError()
                    # Error 5 (ERROR_ACCESS_DENIED) indicates process exists but access is restricted
                    return err == 5
                try:
                    # 258 is WAIT_TIMEOUT, meaning process is active (not signaled)
                    return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 258
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                return True
        else:
            try:
                os.kill(pid, 0)
                return True
            except PermissionError:
                return True
            except (ProcessLookupError, OSError):
                return False

    def acquire(self, blocking: bool = False) -> bool:
        if not self._thread_lock.acquire(blocking=blocking):
            return False
        try:
            # Check for existing lockfile
            if os.path.exists(self.lockfile_path):
                should_reclaim = False
                try:
                    with open(self.lockfile_path, 'r') as f:
                        data = json.load(f)
                    owner_pid = data.get("pid")
                    owner_host = data.get("hostname", "")
                    lock_time = data.get("timestamp", 0)
                    lock_age = time.time() - lock_time

                    if lock_age > self.timeout_seconds:
                        # Before removing lock, verify if owner process is dead!
                        # A long-running training process must NOT lose its lock merely due to age.
                        if not self.is_pid_alive(owner_pid, owner_host):
                            logger.warning(
                                f"Reclaiming stale lock from dead process (PID {owner_pid} on {owner_host}, age {int(lock_age)}s)"
                            )
                            should_reclaim = True
                        else:
                            logger.info(
                                f"Training lock age ({int(lock_age)}s) exceeds timeout, but owner PID {owner_pid} is still active. Lock will NOT be stolen."
                            )
                except Exception as ex:
                    logger.warning(f"Could not parse existing lockfile metadata: {ex}")
                    try:
                        mtime = os.path.getmtime(self.lockfile_path)
                        if time.time() - mtime > self.timeout_seconds:
                            should_reclaim = True
                    except OSError:
                        pass

                if should_reclaim:
                    try:
                        os.remove(self.lockfile_path)
                    except OSError:
                        pass

            # Generate unique token for this ownership session
            token = uuid.uuid4().hex

            # Atomic OS-level file creation (POSIX & Windows)
            self._fd = os.open(self.lockfile_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            pid_info = json.dumps({
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "timestamp": time.time(),
                "owner_token": token
            }).encode('utf-8')
            os.write(self._fd, pid_info)
            self._owner_token = token
            return True
        except (FileExistsError, OSError):
            self._thread_lock.release()
            return False

    def release(self, token: str = None, force: bool = False) -> bool:
        """
        Releases the lock. Only the owner with matching token can release it.
        If token is None, uses self._owner_token.
        """
        try:
            target_token = token or self._owner_token
            if not target_token and not force:
                return False

            if os.path.exists(self.lockfile_path) and not force:
                try:
                    with open(self.lockfile_path, 'r') as f:
                        data = json.load(f)
                    file_token = data.get("owner_token")
                    if file_token != target_token:
                        logger.warning(
                            f"Refusing to release lock: token mismatch (caller {target_token} != owner {file_token})"
                        )
                        return False
                except Exception:
                    pass

            # Close open file descriptor before removing file (required on Windows)
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None

            if os.path.exists(self.lockfile_path):
                try:
                    os.remove(self.lockfile_path)
                except OSError:
                    pass

            self._owner_token = None
            return True
        finally:
            if self._thread_lock.locked():
                self._thread_lock.release()

    def is_locked(self) -> bool:
        return self._thread_lock.locked() or os.path.exists(self.lockfile_path)

training_lock = CrossProcessLock(os.path.join(RESULTS_DIR, ".training.lock"))

# =============================================================================
# RATE LIMITING ARCHITECTURAL NOTE:
# This in-memory sliding-window rate limiter is worker-process-local.
# In a multi-worker deployment (e.g. Gunicorn with N workers), the effective
# aggregate throughput across all workers is N * configured_limit per IP.
# For single-node deployments this is appropriate; for horizontally scaled or
# multi-container clusters, a shared Redis-backed limiter (or reverse-proxy
# rate limiting at Nginx / Cloudflare / Envoy layer) must be deployed.
# =============================================================================
class SimpleRateLimiter:
    """Sliding-window rate limiter with memory bounding and stale key eviction."""
    def __init__(self, max_keys: int = 10000):
        self._requests = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys
    
    def is_allowed(self, key: str, max_requests: int, window_seconds: int = 60) -> bool:
        now = time.time()
        with self._lock:
            # Evict stale entries when tracking table grows large
            if len(self._requests) > self._max_keys:
                self._prune(now, window_seconds)

            timestamps = self._requests.get(key, [])
            timestamps = [t for t in timestamps if now - t < window_seconds]
            if len(timestamps) >= max_requests:
                self._requests[key] = timestamps
                return False
            timestamps.append(now)
            self._requests[key] = timestamps
            return True

    def _prune(self, now: float, window_seconds: int):
        stale_keys = [k for k, ts in self._requests.items() if not ts or (now - ts[-1] >= window_seconds)]
        for k in stale_keys:
            del self._requests[k]
        if len(self._requests) > self._max_keys:
            sorted_keys = sorted(self._requests.keys(), key=lambda k: self._requests[k][-1] if self._requests[k] else 0)
            for k in sorted_keys[:len(self._requests) - self._max_keys]:
                del self._requests[k]

rate_limiter = SimpleRateLimiter()

def limit_rate(max_requests: int, window_seconds: int = 60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Use request.remote_addr directly (safe under ProxyFix)
            client_ip = request.remote_addr or '127.0.0.1'
            key = f"{f.__name__}:{client_ip}"
            if not rate_limiter.is_allowed(key, max_requests, window_seconds):
                logger.warning(f"Rate limit exceeded for IP {client_ip} on {request.path}")
                return jsonify({"status": "error", "error": "Too many requests. Please slow down."}), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator

# =============================================================================
# FIREBASE ADMIN SDK PRODUCTION INITIALIZATION:
# In production, Firebase Admin SDK authenticates using Google Application
# Default Credentials (ADC). Set GOOGLE_APPLICATION_CREDENTIALS to the external
# file path of your service-account JSON, or rely on Workload Identity / Cloud IAM
# if hosted on GCP/GKE/Cloud Run. Never commit service account credentials.
# Verify that FIREBASE_PROJECT_ID matches the project ID of your Firebase Auth tenant.
# NEVER log ID tokens, service-account keys, SECRET_KEY, or ADMIN_KEY.
# =============================================================================
try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={'projectId': FIREBASE_PROJECT_ID})
    HAS_FIREBASE_ADMIN = True
except Exception as e:
    logger.warning(f"Firebase Admin SDK initialization deferred: {e}")
    HAS_FIREBASE_ADMIN = False

def verify_firebase_id_token(token: str):
    """
    Verifies a Firebase ID token using Firebase Admin SDK where feasible.
    Verifies signature, aud, iss, exp, and token validity.
    Falls back to Google's public tokeninfo endpoint if Firebase Admin credentials are unconfigured.
    Returns decoded token dict containing 'email' if valid, None otherwise.
    """
    if not token or not isinstance(token, str):
        return None
    token = token.strip()
    
    # 1. Prefer Firebase Admin SDK (signature, iss, aud, exp verified against Google certs)
    if HAS_FIREBASE_ADMIN:
        try:
            decoded = firebase_auth.verify_id_token(token, check_revoked=False)
            if decoded:
                return decoded
        except Exception as ex:
            # If invalid or expired token, fail immediately without fallback
            if "invalid" in str(ex).lower() or "expired" in str(ex).lower() or "revoked" in str(ex).lower():
                logger.warning(f"Firebase Admin SDK token verification rejected token: {ex}")
                return None
            logger.debug(f"Firebase Admin verification error (falling back): {ex}")

    # 2. Tokeninfo verification fallback
    try:
        url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrendAnalyzer-Backend/1.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode('utf-8'))
                aud = payload.get('aud')
                iss = payload.get('iss', '')
                if (aud and aud == FIREBASE_PROJECT_ID) or f"securetoken.google.com/{FIREBASE_PROJECT_ID}" in iss:
                    return payload
                logger.warning(f"Token aud '{aud}' did not match project '{FIREBASE_PROJECT_ID}'")
    except Exception as e:
        logger.warning(f"Token verification fallback failed: {e}")
    return None

def require_user_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        target_email = kwargs.get('email')
        if not target_email and request.is_json and request.get_json(silent=True):
            target_email = request.get_json(silent=True).get('email')
        
        auth_header = request.headers.get('Authorization', '')
        token = None
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1].strip()
            
        if not REQUIRE_AUTH and not token:
            request.auth_email = (target_email or 'dev@local').lower().strip()
            return f(*args, **kwargs)
            
        if not token:
            logger.warning(f"Unauthorized access attempt to {request.path} without token")
            return jsonify({"status": "error", "error": "Authentication required. Bearer token missing."}), 401
            
        token_payload = verify_firebase_id_token(token)
        if not token_payload:
            logger.warning(f"Invalid or expired token for {request.path}")
            return jsonify({"status": "error", "error": "Invalid or expired authentication token."}), 401
            
        auth_email = token_payload.get('email', '').lower().strip()
        if not auth_email:
            logger.warning(f"Token for {request.path} lacks email identity")
            return jsonify({"status": "error", "error": "Invalid token: missing email identity."}), 401

        # Strict BOLA / IDOR check: caller identity must match target resource identity
        if target_email and auth_email != target_email.lower().strip():
            logger.warning(f"BOLA/IDOR attempt: Authenticated '{auth_email}' attempted access to '{target_email}'")
            return jsonify({"status": "error", "error": "Forbidden: You do not have permission to access or modify this account."}), 403
            
        request.auth_email = auth_email
        return f(*args, **kwargs)
    return decorated

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_model_db_connection():
    conn = sqlite3.connect(MODEL_DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    return render_template('index.html')

# --- API Endpoints ---

import yfinance as yf

def resolve_and_validate_ticker(ticker):
    if not is_valid_ticker_format(ticker):
        return None
    ticker = ticker.strip().upper()
    # 1. Try to download a tiny slice of data to check if ticker is directly valid
    try:
        df = yf.download(ticker, period="5d", progress=False)
        if df is not None and not df.empty and 'Close' in df.columns:
            return ticker
    except:
        pass
    
    # 2. If directly downloading failed or returned empty, try searching Yahoo Finance
    try:
        search = yf.Search(ticker)
        if search.quotes:
            best_symbol = search.quotes[0]['symbol']
            # Double check if we can download the resolved symbol
            df = yf.download(best_symbol, period="5d", progress=False)
            if df is not None and not df.empty and 'Close' in df.columns:
                return best_symbol
    except:
        pass
        
    return None

@app.route('/api/stocks')
@limit_rate(max_requests=60, window_seconds=60)
def get_stocks():
    # Return local STOCKS plus any other trained tickers
    results = list(STOCKS)
    trained_tickers = []
    if os.path.exists(RESULTS_DIR):
        for filename in os.listdir(RESULTS_DIR):
            if filename.endswith('_best_model.keras') or filename.endswith('_final_model.keras'):
                name = filename.replace('_best_model.keras', '').replace('_final_model.keras', '')
                if name.endswith('_NS'):
                    ticker = name[:-3] + '.NS'
                elif name.endswith('_DE'):
                    ticker = name[:-3] + '.DE'
                elif name.endswith('_BO'):
                    ticker = name[:-3] + '.BO'
                else:
                    ticker = name
                trained_tickers.append(ticker)
    for ticker in trained_tickers:
        if ticker not in results:
            results.append(ticker)
    return jsonify(results)

@app.route('/api/lookup')
@limit_rate(max_requests=60, window_seconds=60)
def lookup_stock():
    raw_query = request.args.get('q', '')
    if not raw_query or len(raw_query) > 50:
        return jsonify([])
    query = raw_query.strip().upper()
    
    # 1. Local STOCKS filtration
    results = [
        {"symbol": s, "shortname": s.split('.')[0], "exchange": "NSE"} 
        for s in STOCKS if query in s
    ]
    
    # 2. Add other trained models in RESULTS_DIR
    trained_tickers = []
    if os.path.exists(RESULTS_DIR):
        for filename in os.listdir(RESULTS_DIR):
            if filename.endswith('_best_model.keras') or filename.endswith('_final_model.keras'):
                name = filename.replace('_best_model.keras', '').replace('_final_model.keras', '')
                if name.endswith('_NS'):
                    ticker = name[:-3] + '.NS'
                elif name.endswith('_DE'):
                    ticker = name[:-3] + '.DE'
                elif name.endswith('_BO'):
                    ticker = name[:-3] + '.BO'
                else:
                    ticker = name
                trained_tickers.append(ticker)
                
    for s in trained_tickers:
        if query in s.upper():
            # Avoid duplicate
            if not any(r['symbol'] == s for r in results):
                results.append({"symbol": s, "shortname": s.split('.')[0], "exchange": "US/Other"})

    # 3. If local/trained results are few, query yfinance Search
    if len(results) < 5:
        try:
            search = yf.Search(query)
            for quote in search.quotes:
                symbol = quote.get('symbol')
                if symbol:
                    # Skip duplicate
                    if any(r['symbol'] == symbol for r in results):
                        continue
                    shortname = quote.get('shortname') or quote.get('longname') or symbol
                    exchange = quote.get('exchDisp') or quote.get('exchange') or "Yahoo"
                    results.append({
                        "symbol": symbol,
                        "shortname": shortname,
                        "exchange": exchange
                    })
        except Exception as e:
            print(f"Yahoo Search error: {e}")
            
    return jsonify(results[:10])

@app.route('/api/get_data/<email>')
@limit_rate(max_requests=60, window_seconds=60)
@require_user_auth
def get_user_data(email):
    if not is_valid_email_format(email):
        return jsonify({"status": "error", "message": "Invalid email format"}), 400

    # Authoritative email identity from verified token
    if hasattr(request, 'auth_email') and request.auth_email:
        email = request.auth_email

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    
    if user:
        try:
            data = json.loads(user['data'])
            return jsonify({"status": "success", "data": data})
        except:
            return jsonify({"status": "error", "message": "Corrupt data"})
    else:
        return jsonify({"status": "game_start", "message": "User not found"})

@app.route('/api/save_data', methods=['POST'])
@limit_rate(max_requests=60, window_seconds=60)
@require_user_auth
def save_user_data():
    try:
        req_data = request.get_json()
        if not req_data or not isinstance(req_data, dict):
            return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

        email = req_data.get('email')
        data = req_data.get('data')
        
        if not email or data is None:
            return jsonify({"status": "error", "message": "Missing email or data"}), 400
            
        if not is_valid_email_format(email):
            return jsonify({"status": "error", "message": "Invalid email format"}), 400

        # Authoritative identity check: never trust client body over verified token
        if hasattr(request, 'auth_email') and request.auth_email:
            if email.lower().strip() != request.auth_email:
                logger.warning(f"BOLA attempt in save_user_data: body '{email}' != auth '{request.auth_email}'")
                return jsonify({"status": "error", "error": "Forbidden: Target email does not match authenticated user."}), 403
            email = request.auth_email

        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Data payload must be a JSON object"}), 400

        # Enforce serialized size boundary (max 512KB)
        serialized_data = json.dumps(data)
        if len(serialized_data) > 512 * 1024:
            return jsonify({"status": "error", "message": "User data payload too large (max 512KB)"}), 413

        conn = get_db_connection()
        # Upsert
        conn.execute('''
            INSERT INTO users (email, data, is_verified, subscription_tier) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET data=excluded.data
        ''', (email, serialized_data, 0, 'free'))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"Error saving user data: {e}")
        return jsonify({"status": "error", "message": "Internal error saving user data"}), 500

@app.route('/api/delete_data', methods=['POST'])
@limit_rate(max_requests=10, window_seconds=60)
@require_user_auth
def delete_user_data():
    req_data = request.get_json()
    if not req_data or not isinstance(req_data, dict):
        return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

    email = req_data.get('email')
    if not email or not is_valid_email_format(email):
        return jsonify({"status": "error", "message": "Missing or invalid email"}), 400
        
    # Authoritative identity check: never trust client body over verified token
    if hasattr(request, 'auth_email') and request.auth_email:
        if email.lower().strip() != request.auth_email:
            logger.warning(f"BOLA attempt in delete_user_data: body '{email}' != auth '{request.auth_email}'")
            return jsonify({"status": "error", "error": "Forbidden: Target email does not match authenticated user."}), 403
        email = request.auth_email

    conn = get_db_connection()
    conn.execute('DELETE FROM users WHERE email = ?', (email,))
    conn.commit()
    conn.close()
    logger.info(f"User account and data deleted for {email}")
    return jsonify({"status": "success"})

@app.route('/api/feedback', methods=['POST'])
@limit_rate(max_requests=20, window_seconds=60)
def handle_feedback():
    """Accepts user feedback with length bounds and rate limiting."""
    try:
        req_data = request.get_json() or {}
        email = req_data.get('email', 'anonymous')
        message = req_data.get('message', '')
        rating = req_data.get('rating', 5)

        if not message or not isinstance(message, str) or len(message.strip()) == 0:
            return jsonify({"status": "error", "error": "Feedback message cannot be empty"}), 400

        if len(message) > 2000:
            return jsonify({"status": "error", "error": "Feedback message exceeds 2000 characters limit"}), 400

        try:
            rating = int(rating)
            if not 1 <= rating <= 5:
                rating = 5
        except (ValueError, TypeError):
            rating = 5

        sanitized_email = html.escape(str(email)[:255].strip())
        logger.info(f"Feedback received from {sanitized_email} (Rating: {rating}/5)")
        return jsonify({"status": "success", "message": "Feedback received successfully"})
    except Exception as e:
        logger.error(f"Error handling feedback: {e}")
        return jsonify({"status": "error", "error": "Failed to submit feedback"}), 500

@app.route('/db')
def view_database():
    """Interactive visual database viewer for all tables in users.db (Development/Debug only)."""
    if not ENABLE_DB_VIEWER:
        abort(404)

    # In production, require strong ADMIN_KEY of >= 16 chars
    if IS_PROD and (not ADMIN_KEY or len(ADMIN_KEY) < 16):
        logger.error("ENABLE_DB_VIEWER is enabled in production but ADMIN_KEY is absent or < 16 chars. Blocking access.")
        abort(404)

    # Strictly disallow ?key= query parameters to avoid access log leakage
    if 'key' in request.args:
        logger.warning("Rejected /db access using query parameter. Secret must be in header.")
        abort(403)

    # Accept header only: X-Admin-Key or Authorization: Bearer <key>
    admin_key_header = request.headers.get('X-Admin-Key', '')
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        admin_key_header = auth_header.split(' ', 1)[1].strip()

    if not ADMIN_KEY or not admin_key_header or not hmac.compare_digest(admin_key_header, ADMIN_KEY):
        logger.warning(f"Unauthorized access attempt to /db from {request.remote_addr}")
        abort(403)

    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Users Table
    cur.execute("SELECT email, data, is_verified, subscription_tier FROM users")
    users_raw = cur.fetchall()
    users_list = []
    for u in users_raw:
        email = html.escape(str(u['email'] or ''))
        verified = bool(u['is_verified'])
        tier = html.escape(str(u['subscription_tier'] or ''))
        try:
            pdata = json.loads(u['data']) if u['data'] else {}
        except:
            pdata = {}
        portfolio = pdata.get('portfolio', {})
        balance = portfolio.get('balance', 0)
        holdings = portfolio.get('holdings', {})
        profile = portfolio.get('profile', {})
        watchlist = pdata.get('watchlist', [])
        
        escaped_holdings = html.escape(json.dumps(holdings, indent=2))
        escaped_profile = html.escape(json.dumps(profile, indent=2))
        escaped_watchlist = html.escape(", ".join(watchlist)) if watchlist else "None"
        formatted_balance = html.escape(f"₹{balance:,.2f}" if isinstance(balance, (int, float)) else str(balance))

        users_list.append({
            'email': email,
            'balance': formatted_balance,
            'holdings': escaped_holdings,
            'profile': escaped_profile,
            'watchlist': escaped_watchlist,
            'verified': "Yes" if verified else "No",
            'tier': tier
        })
        
    # 2. Alerts Table
    try:
        cur.execute("SELECT * FROM alerts")
        alerts_list = [dict(row) for row in cur.fetchall()]
    except:
        alerts_list = []
        
    conn.close()
    
    html_page = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Database Viewer — TrendAnalyzer</title>
      <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
      <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Inter', sans-serif; background: #050b14; color: #f8fafc; padding: 32px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #1e293b; }}
        h1 {{ font-size: 1.5rem; font-weight: 700; color: #38bdf8; }}
        .btn-back {{ background: #1e293b; color: #94a3b8; padding: 8px 16px; border-radius: 8px; text-decoration: none; font-size: 0.875rem; font-weight: 600; border: 1px solid #334155; }}
        .btn-back:hover {{ color: #ffffff; background: #334155; }}
        .section-title {{ font-size: 1.1rem; font-weight: 700; color: #f1f5f9; margin: 24px 0 12px; display: flex; align-items: center; gap: 8px; }}
        .badge {{ font-size: 0.75rem; background: rgba(56, 189, 248, 0.15); color: #38bdf8; padding: 2px 8px; border-radius: 9999px; }}
        table {{ width: 100%; border-collapse: collapse; background: #0a1120; border: 1px solid #1e293b; border-radius: 12px; overflow: hidden; margin-bottom: 32px; }}
        th, td {{ padding: 12px 16px; text-align: left; font-size: 0.8125rem; border-bottom: 1px solid #1e293b; }}
        th {{ background: #0f172a; color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em; }}
        tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}
        pre {{ font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #38bdf8; background: #050b14; padding: 6px 10px; border-radius: 6px; border: 1px solid #1e293b; max-width: 320px; white-space: pre-wrap; }}
        .balance-pill {{ font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #4ade80; background: rgba(34, 197, 94, 0.12); padding: 4px 8px; border-radius: 6px; display: inline-block; }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <div>
            <h1>TrendAnalyzer Database Inspector</h1>
            <p style="color: #64748b; font-size: 0.875rem; margin-top: 4px;">File: <code>users.db</code> in project root</p>
          </div>
          <a href="/dashboard" class="btn-back">← Back to Dashboard</a>
        </div>

        <div class="section-title">
          <span>Users & Portfolios</span>
          <span class="badge">{len(users_list)} Users</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Cash Balance</th>
              <th>Stock Holdings</th>
              <th>Profile Metadata</th>
              <th>Watchlist</th>
              <th>Verified</th>
              <th>Tier</th>
            </tr>
          </thead>
          <tbody>
            {"".join([f'''
            <tr>
              <td style="font-weight: 600; color: #f8fafc;">{u['email']}</td>
              <td><span class="balance-pill">{u['balance']}</span></td>
              <td><pre>{u['holdings']}</pre></td>
              <td><pre>{u['profile']}</pre></td>
              <td style="color: #94a3b8;">{u['watchlist']}</td>
              <td><span style="color: {'#4ade80' if u['verified'] == 'Yes' else '#94a3b8'};">{u['verified']}</span></td>
              <td style="color: #94a3b8;">{u['tier']}</td>
            </tr>
            ''' for u in users_list])}
          </tbody>
        </table>

        <div class="section-title">
          <span>Price Alerts</span>
          <span class="badge">{len(alerts_list)} Alerts</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Email</th>
              <th>Ticker</th>
              <th>Target Price</th>
              <th>Condition</th>
              <th>Active</th>
              <th>Created At</th>
            </tr>
          </thead>
          <tbody>
            {"".join([f'''
            <tr>
              <td>{html.escape(str(a.get('id', '')))}</td>
              <td style="font-weight: 500;">{html.escape(str(a.get('email', '')))}</td>
              <td style="color: #38bdf8; font-weight: 600;">{html.escape(str(a.get('ticker', '')))}</td>
              <td>₹{a.get('target_price', 0):,.2f}</td>
              <td>{html.escape(str(a.get('condition', '')))}</td>
              <td>{'Active' if a.get('is_active') == 1 else 'Inactive'}</td>
              <td style="color: #64748b;">{html.escape(str(a.get('created_at', '')))}</td>
            </tr>
            ''' for a in alerts_list]) if alerts_list else '<tr><td colspan="7" style="text-align: center; color: #64748b; padding: 24px;">No price alerts set yet.</td></tr>'}
          </tbody>
        </table>
      </div>
    </body>
    </html>
    """
    return html_page

@app.route('/api/sentiment/<ticker>')
@limit_rate(max_requests=60, window_seconds=60)
def get_sentiment(ticker):
    if not is_valid_ticker_format(ticker):
        return jsonify({"error": "Invalid ticker symbol format", "label": "Neutral", "score": 0, "headlines": []}), 400
    try:
        result = sentiment_module.get_news_sentiment(ticker)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error fetching sentiment for {ticker}: {e}")
        return jsonify({"error": "Failed to fetch sentiment", "label": "Neutral", "score": 0, "headlines": []})

@app.route('/api/predict/<ticker>')
@limit_rate(max_requests=30, window_seconds=60)
def get_prediction(ticker):
    resolved_ticker = resolve_and_validate_ticker(ticker)
    if not resolved_ticker:
        return jsonify({"error": f"Ticker symbol '{ticker}' not found on Yahoo Finance"}), 400
    ticker = resolved_ticker
    
    horizon_param = request.args.get('horizon', '1d').lower()
    if horizon_param in ['5d', '5']:
        horizon = 5
    elif horizon_param in ['1m', '20d', '20']:
        horizon = 20
    else:
        horizon = 1
        
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    prediction = None
    probability = 0
    top_drivers = []
    all_attributions = []
    
    # 1. Check DB for existing prediction (only for 1d default)
    if horizon == 1:
        conn = get_model_db_connection()
        row = conn.execute('SELECT prediction, probability FROM predictions WHERE ticker = ? AND date = ?', (ticker, today_str)).fetchone()
        conn.close()
        if row:
            prediction = row['prediction']
            probability = row['probability']

    # 2. Generate on-the-fly if missing or non-standard horizon
    if not prediction:
        try:
            prediction, probability, top_drivers, all_attributions = generate_live_prediction(ticker, horizon=horizon)
        except Exception as e:
            print(f"Prediction error for {ticker} (horizon={horizon}d): {e}")
            prediction = "NEUTRAL"
            probability = 0.5
            top_drivers = []
            all_attributions = []
    else:
        # DB row found but need drivers
        try:
            _, _, top_drivers, all_attributions = generate_live_prediction(ticker, horizon=horizon)
        except Exception:
            pass
            
    # 3. Get History for Charts
    try:
        # Download last 1 year for charts
        end_date = datetime.date.today().strftime("%Y-%m-%d")
        start_date = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        df = main.download_stock(ticker, start=start_date, end=end_date)
        
        if df is None or df.empty:
            history = {}
            technical_analysis = {"score": 0, "rating": "NEUTRAL"}
        else:
            df = main.add_technical_indicators(df)
            
            # Format for JSON
            dates = pd.DatetimeIndex(df.index).strftime('%Y-%m-%d').tolist()
            history = {
                "dates": dates,
                "open": df["Open"].tolist(),
                "high": df["High"].tolist(),
                "low": df["Low"].tolist(),
                "close": df["Close"].tolist(),
                "volume": df["Volume"].tolist(),
                "rsi": df["RSI"].fillna(0).tolist(),
                "macd": df["MACD"].fillna(0).tolist(),
                "ema50": df["EMA50"].fillna(0).tolist(),
                "ema200": df["EMA200"].fillna(0).tolist(),
                "stoch_k": df["%K"].fillna(0).tolist(),
                "stoch_d": df["%D"].fillna(0).tolist()
            }
            
            # Technical Analysis Score (6-indicator aggregate: RSI, MACD, EMA20, EMA50, EMA200, Stochastic %K)
            last = df.iloc[-1]
            tech_score = 0
            
            rsi_val = last["RSI"] if not pd.isna(last.get("RSI")) else 50
            macd_val = last["MACD"] if not pd.isna(last.get("MACD")) else 0
            close_val = last["Close"] if not pd.isna(last.get("Close")) else 0
            ema20_val = last["EMA20"] if "EMA20" in last and not pd.isna(last["EMA20"]) else close_val
            ema50_val = last["EMA50"] if "EMA50" in last and not pd.isna(last["EMA50"]) else close_val
            ema200_val = last["EMA200"] if "EMA200" in last and not pd.isna(last["EMA200"]) else close_val
            stoch_k_val = last["%K"] if "%K" in last and not pd.isna(last["%K"]) else 50
            
            # 1. RSI
            if rsi_val > 70: tech_score -= 1
            elif rsi_val < 30: tech_score += 1
            
            # 2. MACD
            if macd_val > 0: tech_score += 1
            else: tech_score -= 1
            
            # 3. Close vs EMA20
            if close_val > ema20_val: tech_score += 1
            else: tech_score -= 1
            
            # 4. Close vs EMA50
            if close_val > ema50_val: tech_score += 1
            else: tech_score -= 1
            
            # 5. Close vs EMA200
            if close_val > ema200_val: tech_score += 1
            else: tech_score -= 1
            
            # 6. Stochastic %K
            if stoch_k_val > 80: tech_score -= 1
            elif stoch_k_val < 20: tech_score += 1
            
            rating = "NEUTRAL"
            if tech_score >= 2: rating = "BUY"
            if tech_score >= 4: rating = "STRONG BUY"
            if tech_score <= -2: rating = "SELL"
            if tech_score <= -4: rating = "STRONG SELL"
            
            technical_analysis = {
                "score": tech_score,
                "rating": rating
            }
        
    except Exception as e:
        print(f"History error for {ticker}: {e}")
        history = {}
        technical_analysis = {"score": 0, "rating": "NEUTRAL"}
        
    return jsonify({
        "ticker": ticker,
        "horizon": f"{horizon}d" if horizon != 20 else "1m",
        "horizon_days": horizon,
        "prediction": prediction,
        "probability": probability,
        "top_drivers": top_drivers,
        "all_attributions": all_attributions,
        "history": history,
        "technical_analysis": technical_analysis
    })

def generate_live_prediction(ticker, horizon: int = 1):
    ticker_key = ticker.replace('.', '_') if horizon == 1 else f"{ticker.replace('.', '_')}_h{horizon}"
    model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_best_model.keras")
    if not os.path.exists(model_path):
         model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_final_model.keras")
    
    scaler_path = os.path.join(RESULTS_DIR, f"{ticker_key}_scaler.save")
    feature_path = os.path.join(RESULTS_DIR, f"{ticker_key}_features.joblib")
    
    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        print(f"Model for {ticker} (horizon={horizon}d) not found. Training model on demand...")
        try:
            main.train_single_model(ticker, horizon=horizon)
            model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_best_model.keras")
            if not os.path.exists(model_path):
                model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_final_model.keras")
            scaler_path = os.path.join(RESULTS_DIR, f"{ticker_key}_scaler.save")
        except Exception as e:
            print(f"Error training model for {ticker}: {e}")
            return "TRAINING", 0.0, [], []
            
        if not os.path.exists(model_path) or not os.path.exists(scaler_path):
            return "TRAINING", 0.0, [], []
        
    try:
        model = load_model(model_path)
    except Exception as ex:
        print(f"Model load with compile warning: {ex}. Retrying load_model without compile...")
        model = load_model(model_path, compile=False)
        
    scaler = joblib.load(scaler_path)
    
    if os.path.exists(feature_path):
        active_features = joblib.load(feature_path)
    else:
        active_features = main.FEATURE_COLS

    # Get Data
    start_date = (datetime.date.today() - datetime.timedelta(days=365)).strftime('%Y-%m-%d')
    df = main.download_stock(ticker, start=start_date, end=None)
    
    if len(df) < main.WINDOW + 50:
         return "NEUTRAL", 0.0, [], []
         
    df = main.add_technical_indicators(df)
    
    # Macro
    try:
        start_str = str(pd.DatetimeIndex(df.index)[0].strftime('%Y-%m-%d'))
        end_str = str(pd.DatetimeIndex(df.index)[-1].strftime('%Y-%m-%d'))
        macro = main.download_macro_data(start=start_str, end=end_str)
        if not macro.empty:
            df = df.join(macro)
            df.ffill(inplace=True)
            df.fillna(0, inplace=True)
        else:
            df["Nifty_Return"] = 0.0; df["USD_Change"] = 0.0; df["Gold_Change"] = 0.0; df["Oil_Change"] = 0.0
    except:
        df["Nifty_Return"] = 0.0; df["USD_Change"] = 0.0; df["Gold_Change"] = 0.0; df["Oil_Change"] = 0.0

    # Sentiment
    sentiment = main.load_sentiment_data(ticker)
    if not sentiment.empty:
        df = df.join(sentiment, how='left')
        df["Sentiment_Score"].fillna(0.0, inplace=True)
    else:
        df["Sentiment_Score"] = 0.0
        
    if hasattr(scaler, 'n_features_in_') and scaler.n_features_in_ != len(active_features):
        if scaler.n_features_in_ == len(main.FEATURE_COLS):
            active_features = main.FEATURE_COLS

    features = df[active_features].tail(main.WINDOW).values
    if len(features) < main.WINDOW:
        return "NEUTRAL", 0.0, [], []
        
    try:
        features_scaled = scaler.transform(features)
    except Exception as e:
        print(f"Scaler transform warning for {ticker}: {e}. Refitting scaler dynamically.")
        from sklearn.preprocessing import StandardScaler
        features_scaled = StandardScaler().fit_transform(features)
        
    X_input = features_scaled.reshape(1, main.WINDOW, len(active_features))
    
    rf_path = os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib")
    gb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib")
    xgb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib")
    stacker_path = os.path.join(RESULTS_DIR, f"{ticker_key}_stacker.joblib")
    meta_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta.joblib")
    threshold_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta_threshold.joblib")
    
    if os.path.exists(rf_path) and os.path.exists(gb_path) and os.path.exists(xgb_path):
        try:
            rf = joblib.load(rf_path)
            gb = joblib.load(gb_path)
            xgb = joblib.load(xgb_path)
            stacker = joblib.load(stacker_path) if os.path.exists(stacker_path) else None
            probs = main.predict_ensemble_probs(rf, gb, xgb, model, stacker, X_input)[0]
            
            if os.path.exists(meta_path) and os.path.exists(threshold_path):
                meta_model = joblib.load(meta_path)
                meta_threshold = joblib.load(threshold_path)
                X_meta_input = main.meta_filter_features(probs.reshape(1, -1), X_input[:, -1, :])
                meta_confidence = float(meta_model.predict_proba(X_meta_input)[0, 1])
                
                best_class = int(np.argmax(probs))
                effective_threshold = min(float(meta_threshold), 0.60)
                # Soft-margin: if argmax is HOLD but directional class is within 5%, prefer directional signal
                if best_class == 1:
                    directional = int(np.argmax([probs[0], -1, probs[2]]))  # 0=SELL or 2=BUY
                    directional_class = 0 if probs[0] > probs[2] else 2
                    if float(probs[directional_class]) >= float(probs[1]) - 0.05:
                        best_class = directional_class
                # Only force HOLD if meta-confidence is low AND ensemble probability is under 45%
                if meta_confidence < effective_threshold and float(probs[best_class]) < 0.45:
                    best_class = 1
                    prob = float(probs[1])
                else:
                    prob = float(probs[best_class])
            else:
                best_class = int(np.argmax(probs))
                prob = float(probs[best_class])
        except Exception as e:
            print(f"Failed to load ensemble for predict ({ticker}): {e}. Falling back to base model.")
            if model is None:
                return "NEUTRAL", 0.0, [], []
            try:
                if callable(model):
                    preds = model(X_input, training=False)
                else:
                    preds = model.predict(X_input, verbose=0)
                probs = np.asarray(preds)[0]
            except Exception:
                probs = np.asarray(model.predict(X_input, verbose=0))[0]
            best_class = int(np.argmax(probs))
            prob = float(probs[best_class])
    else:
        if model is None:
            return "NEUTRAL", 0.0, [], []
        try:
            if callable(model):
                preds = model(X_input, training=False)
            else:
                preds = model.predict(X_input, verbose=0)
            probs = np.asarray(preds)[0]
        except Exception:
            probs = np.asarray(model.predict(X_input, verbose=0))[0]
        best_class = int(np.argmax(probs))
        prob = float(probs[best_class])
        
    if best_class == 2:
        prediction = "UP"
    elif best_class == 0:
        prediction = "DOWN"
    else:
        prediction = "HOLD"
        
    # Generate XAI drivers
    try:
        last_scaled_vec: np.ndarray = np.asarray(features_scaled[-1])
        xai_res = main.explain_prediction(ticker, last_scaled_vec, list(active_features), best_class, horizon=horizon, return_dict=True)
        if isinstance(xai_res, dict):
            top_drivers = xai_res.get("top_drivers", [])
            all_attributions = xai_res.get("all_attributions", [])
        else:
            top_drivers = xai_res
            all_attributions = []
    except Exception as ex:
        print(f"XAI driver extraction warning: {ex}")
        top_drivers = []
        all_attributions = []

    return prediction, prob, top_drivers, all_attributions

@app.route('/api/backtest/<ticker>')
@limit_rate(max_requests=30, window_seconds=60)
def backtest_endpoint(ticker):
    if not is_valid_ticker_format(ticker):
        return jsonify({"error": "Invalid ticker symbol format"}), 400

    try:
        # Load model and scaler
        safe_ticker = os.path.basename(ticker.replace('.', '_'))
        model_path = os.path.join(RESULTS_DIR, f"{safe_ticker}_best_model.keras")
        if not os.path.exists(model_path):
             model_path = os.path.join(RESULTS_DIR, f"{safe_ticker}_final_model.keras")
        scaler_path = os.path.join(RESULTS_DIR, f"{safe_ticker}_scaler.save")
        
        if not os.path.exists(model_path) or not os.path.exists(scaler_path):
            return jsonify({"error": "Model not trained yet"}), 404
            
        model = load_model(model_path)
        scaler = joblib.load(scaler_path)
        
        result_df = main.backtest_model(ticker, model, scaler, days=365)
        
        if result_df is None or result_df.empty:
            return jsonify({"error": "Not enough data for backtest"}), 400
        
        # --- Metric Calculations ---
        final_strategy = result_df["Cum_Strategy_Return"].iloc[-1]
        final_market = result_df["Cum_Market_Return"].iloc[-1]
        total_return = (final_strategy - 1.0) * 100
        market_return = (final_market - 1.0) * 100
        
        buy_days = result_df[result_df["Signal"] == 1]
        total_trades = len(buy_days)
        wins = len(buy_days[buy_days["Strategy_Daily_Return"] > 0])
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        strat_curve = result_df["Cum_Strategy_Return"]
        rolling_max = strat_curve.cummax()
        drawdown = (strat_curve - rolling_max) / rolling_max
        max_drawdown = float(drawdown.min() * 100)
        
        return jsonify({
            "metrics": {
                "total_return": round(total_return, 2),
                "market_return": round(market_return, 2),
                "win_rate": round(win_rate, 2),
                "total_trades": total_trades,
                "max_drawdown": round(max_drawdown, 2)
            },
            "chart": {
                "dates": pd.DatetimeIndex(result_df.index).strftime('%Y-%m-%d').tolist(),
                "strategy": result_df["Cum_Strategy_Return"].tolist(),
                "market": result_df["Cum_Market_Return"].tolist()
            }
        })
        
    except Exception as e:
        logger.error(f"Backtest error for {ticker}: {e}")
        return jsonify({"error": "Failed to execute backtest"}), 500

@app.route('/api/stream_train/<ticker>')
@limit_rate(max_requests=10, window_seconds=60)
def stream_train(ticker):
    resolved_ticker = resolve_and_validate_ticker(ticker)
    if not resolved_ticker:
        return jsonify({"error": f"Ticker symbol '{ticker}' not found on Yahoo Finance"}), 400
    ticker = resolved_ticker

    horizon_param = request.args.get('horizon', '1d').lower()
    if horizon_param in ['5d', '5']:
        horizon = 5
    elif horizon_param in ['1m', '20d', '20']:
        horizon = 20
    else:
        horizon = 1

    ticker_key = ticker.replace('.', '_') if horizon == 1 else f"{ticker.replace('.', '_')}_h{horizon}"
    safe_ticker_key = os.path.basename(ticker_key)
    model_path = os.path.join(RESULTS_DIR, f"{safe_ticker_key}_best_model.keras")
    if not os.path.exists(model_path):
        model_path = os.path.join(RESULTS_DIR, f"{safe_ticker_key}_final_model.keras")
    scaler_path = os.path.join(RESULTS_DIR, f"{safe_ticker_key}_scaler.save")

    # If model is already trained, return immediately without locking
    if os.path.exists(model_path) and os.path.exists(scaler_path):
        def generate_immediate():
            payload = json.dumps({"step": "Completed", "progress": 100, "message": f"Model for {ticker} is already trained and ready!"})
            yield f"data: {payload}\n\n"
        return Response(stream_with_context(generate_immediate()), mimetype='text/event-stream')

    # Acquire concurrency lock to prevent CPU/memory exhaustion
    if not training_lock.acquire(blocking=False):
        logger.warning(f"Concurrent training rejected for {ticker}")
        def generate_busy():
            payload = json.dumps({"step": "Busy", "progress": 0, "message": "Another model is currently training. Please wait a moment and retry."})
            yield f"data: {payload}\n\n"
        return Response(stream_with_context(generate_busy()), mimetype='text/event-stream')

    def generate():
        msg_queue = queue.Queue()

        def progress_cb(step, progress, message):
            msg_queue.put({"step": step, "progress": progress, "message": message})

        def run_training():
            try:
                main.train_single_model(ticker, horizon=horizon, progress_callback=progress_cb)
            except Exception as e:
                logger.error(f"SSE training error for {ticker}: {e}")
                msg_queue.put({"step": "Error", "progress": 100, "message": str(e)})
            finally:
                if training_lock.is_locked():
                    training_lock.release()
                msg_queue.put(None)

        t = threading.Thread(target=run_training)
        t.start()

        while True:
            item = msg_queue.get()
            if item is None:
                break
            payload = json.dumps(item)
            yield f"data: {payload}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/watchlist_alerts', methods=['GET', 'POST'])
@limit_rate(max_requests=30, window_seconds=60)
def watchlist_alerts():
    if request.method == 'POST':
        req = request.get_json() or {}
        raw_list = req.get('watchlist', [])
    else:
        raw_list_str = request.args.get('tickers', '')
        raw_list = [t.strip() for t in raw_list_str.split(',') if t.strip()]

    # Limit to maximum 20 tickers to prevent resource exhaustion
    watchlist = [t.strip().upper() for t in raw_list if isinstance(t, str) and is_valid_ticker_format(t)][:20]

    if not watchlist:
        return jsonify([])

    alerts = []
    for ticker in watchlist:
        try:
            prediction, prob, top_drivers, _ = generate_live_prediction(ticker, horizon=1)
            conf_pct = int(round(prob * 100))
            if prediction in ["UP", "BUY"] and conf_pct >= 80:
                driver_text = top_drivers[0]["name"] if top_drivers else "Strong Indicators"
                alerts.append({
                    "ticker": ticker,
                    "prediction": prediction,
                    "confidence": conf_pct,
                    "driver": driver_text,
                    "message": f"🚀 High-Confidence BUY Signal ({conf_pct}%) on {ticker}!"
                })
        except Exception as e:
            logger.warning(f"Watchlist alert check failed for {ticker}: {e}")
            continue

    return jsonify(alerts)

if __name__ == '__main__':
    # Create DB if not exists (users)
    if not os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        conn.execute('''
            CREATE TABLE users (
                email TEXT PRIMARY KEY,
                data TEXT,
                is_verified INTEGER DEFAULT 0,
                subscription_tier TEXT DEFAULT 'free',
                subscription_expiry DATETIME,
                start_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        ''')
        conn.close()
        
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(debug=debug_mode, host=host, port=port)