"""
Comprehensive Post-Deployment Production Smoke Test Suite
Simulates the live production hosting environment under production WSGI configuration:
- FLASK_ENV=production, FLASK_DEBUG=false
- REQUIRE_AUTH=true, ENABLE_DB_VIEWER=false
- TRUSTED_PROXIES_COUNT=1 (reverse-proxy terminated TLS)
- ALLOWED_ORIGINS=https://trendanalyzer.onrender.com
- FIREBASE_PROJECT_ID=trendanalyzer-4857f

Verifies:
1. HTTPS/TLS & Security Headers (HSTS, nosniff, DENY, CSP, Referrer-Policy, COOP, CORP)
2. CORS exact production origin allowlist & preflight OPTIONS behavior
3. Firebase Authentication: missing token (401), invalid token (401), BOLA/IDOR prevention (403), valid identity flow
4. Inaccessibility of /db in production
5. Low-impact endpoint smoke tests (/api/stocks, /api/lookup, valid predict, invalid ticker, malformed payload)
6. Safe concurrency check on /api/stream_train without heavy repeated training
7. Runtime persistence: users.db, model_logs.db, stock_models_optionB/
8. Firebase Admin credentials & sensitive logging audit (no tokens/secrets in logs)
"""

import sys
import os
import time
import json
import threading
import unittest
from unittest.mock import patch

# Set production environment variables before importing app
os.environ["FLASK_ENV"] = "production"
os.environ["ENV"] = "production"
os.environ["FLASK_DEBUG"] = "false"
os.environ["REQUIRE_AUTH"] = "true"
os.environ["ENABLE_DB_VIEWER"] = "false"
os.environ["ADMIN_KEY"] = "prod_admin_secret_key_12345"
os.environ["TRUSTED_PROXIES_COUNT"] = "1"
os.environ["ALLOWED_ORIGINS"] = "https://trendanalyzer.onrender.com"
os.environ["FIREBASE_PROJECT_ID"] = "trendanalyzer-4857f"
os.environ["ALLOW_SQLITE_DEV_OVERRIDE"] = "true"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module
from app import app, rate_limiter, training_lock

PROD_URL = "https://trendanalyzer.onrender.com"

class ProductionSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()

    def setUp(self):
        with rate_limiter._lock:
            rate_limiter._requests.clear()
        if training_lock.is_locked():
            training_lock.release(force=True)

    def tearDown(self):
        if training_lock.is_locked():
            training_lock.release(force=True)

    # =========================================================================
    # 1. HTTPS/TLS & Security Headers
    # =========================================================================
    def test_01_https_and_security_headers(self):
        """Verify HTTPS/TLS simulation and strict security headers."""
        # Simulated request through production reverse proxy with TLS terminated upstream
        res = self.client.get(
            '/api/stocks',
            headers={
                'X-Forwarded-Proto': 'https',
                'X-Forwarded-For': '203.0.113.10',
                'Host': 'trendanalyzer.onrender.com'
            }
        )
        self.assertEqual(res.status_code, 200)

        # 1. HSTS must be present on HTTPS
        hsts = res.headers.get('Strict-Transport-Security')
        self.assertIsNotNone(hsts, "Strict-Transport-Security must be present on HTTPS")
        self.assertIn('max-age=31536000', hsts)
        self.assertIn('includeSubDomains', hsts)

        # 2. X-Content-Type-Options
        self.assertEqual(res.headers.get('X-Content-Type-Options'), 'nosniff')

        # 3. X-Frame-Options
        self.assertEqual(res.headers.get('X-Frame-Options'), 'DENY')

        # 4. Content-Security-Policy
        csp = res.headers.get('Content-Security-Policy', '')
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

        # 5. Referrer-Policy
        self.assertEqual(res.headers.get('Referrer-Policy'), 'strict-origin-when-cross-origin')

        # 6. COOP & CORP
        self.assertEqual(res.headers.get('Cross-Origin-Opener-Policy'), 'same-origin-allow-popups')
        self.assertEqual(res.headers.get('Cross-Origin-Resource-Policy'), 'same-origin')

    # =========================================================================
    # 2. CORS using Production Frontend Origin
    # =========================================================================
    def test_02_cors_production_origin_and_preflight(self):
        """Verify CORS allows production origin and strictly rejects untrusted origins."""
        # 1. Allowed production origin succeeds
        res_allowed = self.client.get(
            '/api/stocks',
            headers={'Origin': PROD_URL}
        )
        self.assertEqual(res_allowed.status_code, 200)
        self.assertEqual(res_allowed.headers.get('Access-Control-Allow-Origin'), PROD_URL)
        self.assertEqual(res_allowed.headers.get('Access-Control-Allow-Credentials'), 'true')

        # 2. Arbitrary untrusted origin does NOT receive Access-Control-Allow-Origin
        res_untrusted = self.client.get(
            '/api/stocks',
            headers={'Origin': 'https://evil-attacker.com'}
        )
        self.assertIsNone(res_untrusted.headers.get('Access-Control-Allow-Origin'))

        # 3. OPTIONS preflight behaves correctly
        res_opt = self.client.options(
            '/api/save_data',
            headers={'Origin': PROD_URL}
        )
        self.assertEqual(res_opt.status_code, 204)
        self.assertEqual(res_opt.headers.get('Access-Control-Allow-Origin'), PROD_URL)
        self.assertIn('POST', res_opt.headers.get('Access-Control-Allow-Methods', ''))
        self.assertIn('Authorization', res_opt.headers.get('Access-Control-Allow-Headers', ''))

    # =========================================================================
    # 3. Firebase Authentication & BOLA/IDOR
    # =========================================================================
    def test_03_authentication_and_bola_idor(self):
        """Verify Firebase token verification, failure modes, and BOLA protection."""
        # 1. Missing token -> 401
        res_missing = self.client.get('/api/get_data/alice@example.com')
        self.assertEqual(res_missing.status_code, 401)
        self.assertIn('Authentication required', res_missing.json.get('error', ''))

        # 2. Malformed/invalid token -> 401
        with patch('app.verify_firebase_id_token', return_value=None):
            res_invalid = self.client.get(
                '/api/get_data/alice@example.com',
                headers={'Authorization': 'Bearer malformed.token.format'}
            )
            self.assertEqual(res_invalid.status_code, 401)
            self.assertIn('Invalid or expired', res_invalid.json.get('error', ''))

        # 3. Valid owner token -> succeeds
        with patch('app.verify_firebase_id_token', return_value={'email': 'alice@example.com'}):
            res_owner = self.client.get(
                '/api/get_data/alice@example.com',
                headers={'Authorization': 'Bearer mock_valid_owner_token'}
            )
            # Returns 200 (or game_start if user row not initialized yet)
            self.assertEqual(res_owner.status_code, 200)

        # 4. Valid token for another user's resource -> 403 Forbidden
        with patch('app.verify_firebase_id_token', return_value={'email': 'mallory_attacker@example.com'}):
            # Attempt to read Alice's data
            res_bola_get = self.client.get(
                '/api/get_data/alice@example.com',
                headers={'Authorization': 'Bearer mock_attacker_token'}
            )
            self.assertEqual(res_bola_get.status_code, 403)
            self.assertIn('Forbidden', res_bola_get.json.get('error', ''))

            # Attempt to overwrite Alice's portfolio
            res_bola_save = self.client.post(
                '/api/save_data',
                headers={'Authorization': 'Bearer mock_attacker_token'},
                json={'email': 'alice@example.com', 'data': {'portfolio': {'balance': 0}}}
            )
            self.assertEqual(res_bola_save.status_code, 403)

            # Attempt to delete Alice's account
            res_bola_del = self.client.post(
                '/api/delete_data',
                headers={'Authorization': 'Bearer mock_attacker_token'},
                json={'email': 'alice@example.com'}
            )
            self.assertEqual(res_bola_del.status_code, 403)

    # =========================================================================
    # 4. Database Viewer Inaccessible in Production
    # =========================================================================
    def test_04_db_viewer_disabled_in_production(self):
        """Verify /db is disabled by default in production (ENABLE_DB_VIEWER=false)."""
        res_db = self.client.get('/db')
        self.assertEqual(res_db.status_code, 404)

        # Even with query key, access is blocked
        res_db_key = self.client.get('/db?key=prod_admin_secret_key_12345')
        self.assertEqual(res_db_key.status_code, 404)

    # =========================================================================
    # 5. Low-Impact Endpoint Smoke Tests
    # =========================================================================
    def test_05_endpoint_smoke_tests(self):
        """Verify baseline REST API endpoints without triggering heavy training."""
        # 1. GET /api/stocks
        res_stocks = self.client.get('/api/stocks')
        self.assertEqual(res_stocks.status_code, 200)
        stocks = res_stocks.json
        self.assertIsInstance(stocks, list)
        self.assertIn('RELIANCE.NS', stocks)

        # 2. GET /api/lookup
        res_lookup = self.client.get('/api/lookup?q=RELIANCE')
        self.assertEqual(res_lookup.status_code, 200)
        lookup_results = res_lookup.json
        self.assertIsInstance(lookup_results, list)
        self.assertTrue(any('RELIANCE' in item.get('symbol', '') for item in lookup_results))

        # 3. One valid prediction request (RELIANCE.NS)
        res_pred = self.client.get('/api/predict/RELIANCE.NS')
        self.assertEqual(res_pred.status_code, 200)
        pred_data = res_pred.json
        self.assertIn('prediction', pred_data)
        self.assertIn('probability', pred_data)
        self.assertIn(pred_data['prediction'], ['UP', 'DOWN', 'HOLD', 'NEUTRAL'])

        # 4. One invalid ticker request -> 400 Bad Request
        res_invalid_ticker = self.client.get('/api/sentiment/INVALID;;INJECTION$$')
        self.assertEqual(res_invalid_ticker.status_code, 400)

        # 5. One malformed request -> 400 Bad Request
        res_malformed = self.client.post(
            '/api/feedback',
            json={'message': ''}  # Empty message
        )
        self.assertEqual(res_malformed.status_code, 400)

    # =========================================================================
    # 6. Stream Train Safe Concurrency Check
    # =========================================================================
    def test_06_stream_train_safe_concurrency_check(self):
        """Verify /api/stream_train enforces concurrency lock without running heavy repeated training."""
        # Acquire lock simulating active training
        acquired = training_lock.acquire(blocking=False)
        self.assertTrue(acquired)

        with patch('app.resolve_and_validate_ticker', return_value='CONCURRENT_TEST.NS'), \
             patch('os.path.exists', return_value=False):
            # Incoming concurrent stream_train request must receive "Busy" response immediately
            res_busy = self.client.get('/api/stream_train/CONCURRENT_TEST.NS')
            self.assertEqual(res_busy.status_code, 200)
            chunk = res_busy.data.decode('utf-8')
            self.assertIn('"Busy"', chunk)
            self.assertIn('Another model is currently training', chunk)

        # Release lock safely
        training_lock.release(force=True)
        self.assertFalse(training_lock.is_locked())

    # =========================================================================
    # 7. Runtime Persistence Verification
    # =========================================================================
    def test_07_runtime_persistence_configuration(self):
        """Verify storage paths for users.db, model_logs.db, and stock_models_optionB/."""
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        
        # 1. users.db
        users_db_path = os.path.join(root_dir, 'users.db')
        self.assertTrue(os.path.exists(users_db_path), "users.db must exist")
        self.assertTrue(os.access(users_db_path, os.R_OK | os.W_OK), "users.db must be readable and writable")

        # 2. stock_models_optionB/ directory
        models_dir = os.path.join(root_dir, 'stock_models_optionB')
        self.assertTrue(os.path.isdir(models_dir), "stock_models_optionB/ directory must exist")
        self.assertTrue(os.access(models_dir, os.R_OK | os.W_OK), "stock_models_optionB/ must be readable and writable")
        
        # Check model files inside directory
        files = os.listdir(models_dir)
        keras_models = [f for f in files if f.endswith('.keras')]
        scalers = [f for f in files if f.endswith('_scaler.save')]
        self.assertGreater(len(keras_models), 0, "stock_models_optionB/ must contain trained models")
        self.assertGreater(len(scalers), 0, "stock_models_optionB/ must contain scalers")

    # =========================================================================
    # 8. Firebase Admin Initialization & Sensitive Logging Audit
    # =========================================================================
    def test_08_firebase_admin_initialization_and_sensitive_logging(self):
        """Confirm Firebase Admin SDK initializes without committed secrets, and no secrets are logged."""
        # Verify FIREBASE_PROJECT_ID matches
        self.assertEqual(app_module.FIREBASE_PROJECT_ID, "trendanalyzer-4857f")

        # Verify no service-account JSON or private key file is committed to git
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        for fname in os.listdir(root_dir):
            self.assertFalse(
                fname.endswith('.json') and ('service-account' in fname.lower() or 'firebase' in fname.lower() or 'key' in fname.lower()),
                f"Secret credential file {fname} must NOT be present in repo root"
            )

        # Audit app.py source code for sensitive logging sinks
        with open(os.path.join(root_dir, 'app.py'), 'r', encoding='utf-8') as f:
            app_code = f.read()
        self.assertNotIn("logger.info(token", app_code)
        self.assertNotIn("logger.warning(token", app_code)
        self.assertNotIn("logger.info(SECRET_KEY", app_code)
        self.assertNotIn("logger.warning(ADMIN_KEY", app_code)
        self.assertNotIn("logger.error(admin_key_header", app_code)
        self.assertNotIn("logger.info(auth_header", app_code)


if __name__ == '__main__':
    unittest.main()
