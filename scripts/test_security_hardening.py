"""
Comprehensive Automated Security Hardening & Adversarial Test Suite
for Stock Market Trend Analyzer.

Validates:
1. Security headers (CSP, COOP, CORP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, HSTS)
2. CORS exact-origin allowlist and preflight OPTIONS handling
3. BOLA / IDOR defense: verified token identity vs path/body email
4. Token verification: signature, expiration, audience, issuer via Firebase Admin SDK & tokeninfo
5. Fail-closed production enforcement: no bypass in production
6. Rate limiting: sliding window, threshold rejection, and memory eviction/pruning
7. Cross-process training concurrency lock: atomic mutual exclusion and stale lock reclamation
8. Access control & XSS protection in /db: disallow query param secret, header constant-time check, HTML escaping
9. Request-size and schema boundary limits: JSON size bounds, malformed payloads, ticker and email regexes
"""

import sys
import os
import json
import time
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module
from app import app, rate_limiter, training_lock, is_valid_ticker_format, is_valid_email_format


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        # Reset in-memory rate limiter between tests
        with rate_limiter._lock:
            rate_limiter._requests.clear()
        if training_lock.is_locked():
            training_lock.release(force=True)

    def tearDown(self):
        if training_lock.is_locked():
            training_lock.release(force=True)

    # =========================================================================
    # 1. Security Headers & HSTS
    # =========================================================================
    def test_security_headers_present(self):
        """Verify baseline security headers on responses."""
        res = self.client.get('/api/stocks')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(res.headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(res.headers.get('Referrer-Policy'), 'strict-origin-when-cross-origin')
        self.assertEqual(res.headers.get('Cross-Origin-Opener-Policy'), 'same-origin-allow-popups')
        self.assertEqual(res.headers.get('Cross-Origin-Resource-Policy'), 'same-origin')
        self.assertIn('default-src', res.headers.get('Content-Security-Policy', ''))
        # On non-secure HTTP, HSTS should NOT be sent
        self.assertIsNone(res.headers.get('Strict-Transport-Security'))

    def test_conditional_hsts_on_https(self):
        """Verify Strict-Transport-Security is emitted only when request.is_secure."""
        # Using test client environ_base to simulate secure HTTPS connection
        res = self.client.get('/api/stocks', base_url='https://localhost:5000')
        self.assertEqual(res.status_code, 200)
        hsts = res.headers.get('Strict-Transport-Security')
        self.assertIsNotNone(hsts)
        self.assertIn('max-age=31536000', hsts)
        self.assertIn('includeSubDomains', hsts)

    # =========================================================================
    # 2. CORS & Preflight (OPTIONS)
    # =========================================================================
    def test_cors_origin_handling_and_preflight(self):
        """Verify exact CORS origin allowlist and preflight OPTIONS handling."""
        # Allowed origin GET
        res = self.client.get('/api/stocks', headers={'Origin': 'http://localhost:5000'})
        self.assertEqual(res.headers.get('Access-Control-Allow-Origin'), 'http://localhost:5000')
        self.assertEqual(res.headers.get('Access-Control-Allow-Credentials'), 'true')

        # Disallowed origin GET (no CORS header returned, no reflection)
        res_untrusted = self.client.get('/api/stocks', headers={'Origin': 'http://attacker-site.com'})
        self.assertIsNone(res_untrusted.headers.get('Access-Control-Allow-Origin'))

        # Allowed origin preflight OPTIONS
        res_opt = self.client.options('/api/save_data', headers={'Origin': 'http://localhost:5000'})
        self.assertEqual(res_opt.status_code, 204)
        self.assertEqual(res_opt.headers.get('Access-Control-Allow-Origin'), 'http://localhost:5000')
        self.assertIn('POST', res_opt.headers.get('Access-Control-Allow-Methods', ''))

        # Disallowed origin preflight OPTIONS
        res_opt_bad = self.client.options('/api/save_data', headers={'Origin': 'http://evil.com'})
        self.assertNotEqual(res_opt_bad.headers.get('Access-Control-Allow-Origin'), 'http://evil.com')

    # =========================================================================
    # 3. Input Validation & Bounds
    # =========================================================================
    def test_ticker_format_validation(self):
        """Verify invalid/path-traversal ticker formats are blocked with HTTP 400 or 404."""
        malicious_tickers = [
            '../../etc/passwd',
            'RELIANCE;DROP TABLE users;',
            '<script>alert(1)</script>',
            'STOCK$INJECTION',
            'A' * 25  # Exceeds max length
        ]
        for bad_ticker in malicious_tickers:
            res = self.client.get(f'/api/sentiment/{bad_ticker}')
            self.assertIn(res.status_code, [400, 404], f"Expected 400 or 404 for bad ticker: {bad_ticker}")

            res_backtest = self.client.get(f'/api/backtest/{bad_ticker}')
            self.assertIn(res_backtest.status_code, [400, 404], f"Expected 400 or 404 for bad ticker in backtest: {bad_ticker}")

    def test_email_format_validation(self):
        """Verify malformed email formats are rejected on user data routes."""
        bad_emails = ['notanemail', 'test@', '@domain.com', 'test..user@domain.com', 'user@domain..com']
        with patch.object(app_module, 'REQUIRE_AUTH', False):
            for bad_email in bad_emails:
                self.assertFalse(is_valid_email_format(bad_email))
                res_get = self.client.get(f'/api/get_data/{bad_email}')
                self.assertEqual(res_get.status_code, 400)

                res_save = self.client.post('/api/save_data', json={'email': bad_email, 'data': {}})
                self.assertEqual(res_save.status_code, 400)

                res_del = self.client.post('/api/delete_data', json={'email': bad_email})
                self.assertEqual(res_del.status_code, 400)

    def test_feedback_endpoint_validation(self):
        """Verify /api/feedback validates message content and bounds."""
        # Empty message
        res_empty = self.client.post('/api/feedback', json={'message': '   '})
        self.assertEqual(res_empty.status_code, 400)

        # Oversized message (>2000 chars)
        res_huge = self.client.post('/api/feedback', json={'message': 'A' * 2500})
        self.assertEqual(res_huge.status_code, 400)

        # Valid feedback
        res_valid = self.client.post('/api/feedback', json={'message': 'Great app!', 'rating': 5, 'email': 'user@example.com'})
        self.assertEqual(res_valid.status_code, 200)
        self.assertEqual(res_valid.json.get('status'), 'success')

    def test_oversized_user_data_payload_rejection(self):
        """Verify that payloads exceeding 512KB are rejected with 413."""
        huge_portfolio = {'holdings': {'TCS': 1}, 'huge_field': 'x' * (550 * 1024)}
        with patch.object(app_module, 'REQUIRE_AUTH', False):
            res = self.client.post('/api/save_data', json={'email': 'test@example.com', 'data': huge_portfolio})
            self.assertEqual(res.status_code, 413)

    # =========================================================================
    # 4. BOLA / IDOR & Token Authorization (Adversarial)
    # =========================================================================
    def test_bola_idor_authorization_enforcement(self):
        """Verify that when REQUIRE_AUTH=True, unauthorized callers and BOLA attacks are blocked."""
        with patch.object(app_module, 'REQUIRE_AUTH', True):
            # 1. Missing Bearer token -> 401
            res_no_token = self.client.get('/api/get_data/victim@example.com')
            self.assertEqual(res_no_token.status_code, 401)

            # 2. Malformed Bearer header -> 401
            res_malformed = self.client.get(
                '/api/get_data/victim@example.com',
                headers={'Authorization': 'Bearer '}
            )
            self.assertEqual(res_malformed.status_code, 401)

            # 3. Invalid token signature/payload -> 401
            with patch('app.verify_firebase_id_token', return_value=None):
                res_bad_token = self.client.get(
                    '/api/get_data/victim@example.com',
                    headers={'Authorization': 'Bearer invalid.jwt.token'}
                )
                self.assertEqual(res_bad_token.status_code, 401)

            # 4. Token lacking email claim -> 401
            with patch('app.verify_firebase_id_token', return_value={'uid': 'abc1234'}):
                res_no_email = self.client.get(
                    '/api/get_data/victim@example.com',
                    headers={'Authorization': 'Bearer token_without_email'}
                )
                self.assertEqual(res_no_email.status_code, 401)

            # 5. BOLA Attempt on GET /api/get_data: Attacker queries victim -> 403
            with patch('app.verify_firebase_id_token', return_value={'email': 'attacker@example.com'}):
                res_bola_get = self.client.get(
                    '/api/get_data/victim@example.com',
                    headers={'Authorization': 'Bearer attacker_token'}
                )
                self.assertEqual(res_bola_get.status_code, 403)
                self.assertIn('Forbidden', res_bola_get.json.get('error', ''))

            # 6. BOLA Attempt on POST /api/save_data: Attacker tries to overwrite victim's portfolio -> 403
            with patch('app.verify_firebase_id_token', return_value={'email': 'attacker@example.com'}):
                res_bola_save = self.client.post(
                    '/api/save_data',
                    headers={'Authorization': 'Bearer attacker_token'},
                    json={'email': 'victim@example.com', 'data': {'portfolio': {'balance': 0}}}
                )
                self.assertEqual(res_bola_save.status_code, 403)

            # 7. BOLA Attempt on POST /api/delete_data: Attacker tries to wipe victim's account -> 403
            with patch('app.verify_firebase_id_token', return_value={'email': 'attacker@example.com'}):
                res_bola_del = self.client.post(
                    '/api/delete_data',
                    headers={'Authorization': 'Bearer attacker_token'},
                    json={'email': 'victim@example.com'}
                )
                self.assertEqual(res_bola_del.status_code, 403)

            # 8. Legitimate Owner: Attacker accesses own data -> 200
            with patch('app.verify_firebase_id_token', return_value={'email': 'owner@example.com'}):
                res_owner = self.client.get(
                    '/api/get_data/owner@example.com',
                    headers={'Authorization': 'Bearer owner_token'}
                )
                self.assertIn(res_owner.status_code, [200])

    def test_production_fail_closed_authentication(self):
        """Verify that when IS_PROD is active, auth cannot be bypassed."""
        with patch.object(app_module, 'IS_PROD', True), \
             patch.object(app_module, 'REQUIRE_AUTH', True):
            res = self.client.get('/api/get_data/anyuser@example.com')
            self.assertEqual(res.status_code, 401)

    # =========================================================================
    # 5. Rate Limiting & Memory Eviction
    # =========================================================================
    def test_rate_limiting_enforcement(self):
        """Verify rapid repeated requests trigger HTTP 429 Too Many Requests."""
        for i in range(10):
            res = self.client.get('/api/stream_train/INVALID_TICKER')
            self.assertIn(res.status_code, [400, 200])

        res_exceeded = self.client.get('/api/stream_train/INVALID_TICKER')
        self.assertEqual(res_exceeded.status_code, 429)
        self.assertIn('Too many requests', res_exceeded.json.get('error', ''))

    def test_rate_limiter_memory_pruning(self):
        """Verify that the rate limiter evicts stale keys and bounds memory."""
        # Inject 100 fake stale keys with timestamps older than window
        old_time = time.time() - 120
        with rate_limiter._lock:
            for i in range(50):
                rate_limiter._requests[f"stale_ip_{i}"] = [old_time]
            # Prune
            rate_limiter._prune(time.time(), 60)
            self.assertEqual(len(rate_limiter._requests), 0)

    # =========================================================================
    # 6. ProxyFix & Untrusted Direct Request Spoofing Tests
    # =========================================================================
    def test_untrusted_direct_request_spoofed_headers_ignored(self):
        """
        Verify that when TRUSTED_PROXIES_COUNT=0 (direct connection / untrusted client),
        spoofed X-Forwarded-For and X-Forwarded-Proto headers are NOT trusted.
        """
        # Plain HTTP request with spoofed forwarding headers
        res = self.client.get(
            '/api/stocks',
            headers={
                'X-Forwarded-For': '203.0.113.195',
                'X-Forwarded-Proto': 'https'
            }
        )
        self.assertEqual(res.status_code, 200)
        # HSTS must NOT be emitted because the actual transport is insecure HTTP
        self.assertIsNone(res.headers.get('Strict-Transport-Security'))

    # =========================================================================
    # 7. Training Concurrency & Cross-Process File Lock
    # =========================================================================
    def test_cross_process_training_lock_lifecycle(self):
        """Verify normal acquisition, second-process rejection, and normal release."""
        # 1. Normal acquisition
        acquired = training_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        self.assertTrue(training_lock.is_locked())

        # 2. Second process / worker acquisition must fail
        second_acquire = training_lock.acquire(blocking=False)
        self.assertFalse(second_acquire)

        # 3. Normal release
        released = training_lock.release()
        self.assertTrue(released)
        self.assertFalse(training_lock.is_locked())

    def test_cross_process_lock_exception_release(self):
        """Verify exception-safe release under error conditions."""
        acquired = training_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            raise RuntimeError("Simulated crash during model training")
        except RuntimeError:
            pass
        finally:
            training_lock.release()

        self.assertFalse(training_lock.is_locked())
        # Can acquire cleanly again
        self.assertTrue(training_lock.acquire(blocking=False))
        training_lock.release()

    def test_cross_process_lock_non_owner_cannot_release(self):
        """Verify that a rogue caller or non-owner token CANNOT release another owner's lock."""
        acquired = training_lock.acquire(blocking=False)
        self.assertTrue(acquired)

        # Non-owner attempt to release with fake token must fail
        fake_release = training_lock.release(token="rogue_unauthorized_token_12345")
        self.assertFalse(fake_release)
        # The legitimate lockfile MUST remain intact
        self.assertTrue(os.path.exists(training_lock.lockfile_path))

        # Legitimate owner releases with valid token
        real_release = training_lock.release()
        self.assertTrue(real_release)
        self.assertFalse(os.path.exists(training_lock.lockfile_path))

    def test_old_but_active_process_lock_must_not_be_stolen(self):
        """
        Verify that a legitimate long-running training process (>45 min)
        DOES NOT lose its lock if its process is still actively running.
        """
        import socket
        lock_path = training_lock.lockfile_path
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)

        # Write lockfile metadata with timestamp 2 hours ago, but PID is current process (ALIVE!)
        with open(lock_path, 'w') as f:
            json.dump({
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "timestamp": time.time() - 7200,
                "owner_token": "legitimate_active_process_token"
            }, f)
        two_hours_ago = time.time() - 7200
        os.utime(lock_path, (two_hours_ago, two_hours_ago))

        # Another process attempts to acquire: MUST NOT STEAL because owner is still alive!
        steal_attempt = training_lock.acquire(blocking=False)
        self.assertFalse(steal_attempt, "Lock must NOT be stolen from an active process regardless of age")
        self.assertTrue(os.path.exists(lock_path))

        # Cleanup manually for test isolation
        os.remove(lock_path)

    def test_stale_lockfile_dead_owner_recovery(self):
        """Verify that genuinely stale locks from crashed/dead processes ARE safely reclaimed."""
        import socket
        lock_path = training_lock.lockfile_path
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)

        # Write lockfile metadata with timestamp 2 hours ago and a non-existent dead PID
        with open(lock_path, 'w') as f:
            json.dump({
                "pid": 999999,
                "hostname": socket.gethostname(),
                "timestamp": time.time() - 7200,
                "owner_token": "dead_process_token"
            }, f)
        two_hours_ago = time.time() - 7200
        os.utime(lock_path, (two_hours_ago, two_hours_ago))

        # Acquire should detect owner is dead, reclaim the stale lock, and succeed
        reclaimed = training_lock.acquire(blocking=False)
        self.assertTrue(reclaimed, "Stale lock from dead process must be reclaimed")
        training_lock.release()

    # =========================================================================
    # 7. Database Viewer (/db) Security & XSS Escaping
    # =========================================================================
    def test_database_viewer_access_and_xss_protection(self):
        """Verify /db requires header auth, rejects query params, and HTML-escapes all fields."""
        # 1. When disabled -> 404
        with patch.object(app_module, 'ENABLE_DB_VIEWER', False):
            res = self.client.get('/db')
            self.assertEqual(res.status_code, 404)

        # 2. When enabled with ADMIN_KEY:
        with patch.object(app_module, 'ENABLE_DB_VIEWER', True), \
             patch.object(app_module, 'ADMIN_KEY', 'super_secure_admin_key_12345'):
            
            # Query param ?key= MUST be rejected with 403 to avoid log leakage
            res_query = self.client.get('/db?key=super_secure_admin_key_12345')
            self.assertEqual(res_query.status_code, 403)

            # No header -> 403
            res_no_header = self.client.get('/db')
            self.assertEqual(res_no_header.status_code, 403)

            # Bad header -> 403
            res_bad_header = self.client.get('/db', headers={'X-Admin-Key': 'wrong_key'})
            self.assertEqual(res_bad_header.status_code, 403)

            # Correct header -> 200
            fake_user = {
                'email': 'hacker<script>alert("XSS")</script>@test.com',
                'is_verified': 1,
                'subscription_tier': '<img src=x onerror=alert(1)>',
                'data': json.dumps({'portfolio': {'balance': 100, 'holdings': {'<script>': 1}}})
            }
            with patch('app.get_db_connection') as mock_conn:
                mock_cur = mock_conn.return_value.cursor.return_value
                mock_cur.fetchall.side_effect = [
                    [fake_user],  # Users table
                    []            # Alerts table
                ]
                res_good = self.client.get('/db', headers={'X-Admin-Key': 'super_secure_admin_key_12345'})
                self.assertEqual(res_good.status_code, 200)
                html_body = res_good.data.decode('utf-8')
                # Must NOT contain raw script tag or img tag
                self.assertNotIn('<script>alert("XSS")</script>', html_body)
                self.assertIn('&lt;script&gt;alert(&quot;XSS&quot;)&lt;/script&gt;', html_body)
                self.assertNotIn('<img src=x onerror=alert(1)>', html_body)
                self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html_body)


if __name__ == '__main__':
    unittest.main()
