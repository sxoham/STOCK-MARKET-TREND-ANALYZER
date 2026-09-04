"""
Production-Readiness P0 Verification Suite
Validates all launch-critical P0 remediation items:
1. Public legal & contact routes accessible under strict production REQUIRE_AUTH=true
2. Global responsive footer links presence
3. Registration acknowledgement requirements for both Email and Google OAuth signup
4. Password recovery non-enumeration and input validation
5. HTTP 404, 403, 429, and 500 error handling (Branded HTML vs API JSON)
6. Real rate-limiter exhaustion triggering 429 with retry instructions
7. Error 500 sanitization (zero leak of stack traces, paths, secrets, or tokens)
"""

import sys
import os
import re
import json
import unittest
from unittest.mock import patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module
from app import app, rate_limiter, handle_forbidden, handle_not_found, handle_too_many_requests, handle_server_error


class TestP0ProductionReadiness(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        # Save original auth config
        self._orig_require_auth = app_module.REQUIRE_AUTH
        self._orig_is_prod = app_module.IS_PROD
        # Clear rate limiter
        with rate_limiter._lock:
            rate_limiter._requests.clear()

    def tearDown(self):
        app_module.REQUIRE_AUTH = self._orig_require_auth
        app_module.IS_PROD = self._orig_is_prod
        with rate_limiter._lock:
            rate_limiter._requests.clear()

    # =========================================================================
    # 1. Public Legal & Contact Routes Under Strict Production Config
    # =========================================================================
    def test_01_public_legal_routes_accessible_under_production_auth(self):
        """Confirm /privacy, /terms, /disclaimer, /contact return 200 without auth in production mode."""
        app_module.REQUIRE_AUTH = True
        app_module.IS_PROD = True

        public_endpoints = ['/privacy', '/terms', '/disclaimer', '/contact']
        for ep in public_endpoints:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, 200, f"Public endpoint {ep} failed with status {res.status_code}")
            html_text = res.get_data(as_text=True)
            self.assertIn("TrendAnalyzer", html_text)
            # Ensure footer links are present
            self.assertIn('href="/privacy"', html_text)
            self.assertIn('href="/terms"', html_text)
            self.assertIn('href="/disclaimer"', html_text)
            self.assertIn('href="/contact"', html_text)

    def test_02_dashboard_and_auth_pages_have_footer_links(self):
        """Verify login, register, and dashboard templates include required legal links."""
        for ep in ['/login', '/register', '/dashboard']:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, 200)
            html_text = res.get_data(as_text=True)
            self.assertIn('href="/privacy"', html_text, f"{ep} missing /privacy link")
            self.assertIn('href="/terms"', html_text, f"{ep} missing /terms link")
            self.assertIn('href="/disclaimer"', html_text, f"{ep} missing /disclaimer link")
            self.assertIn('href="/contact"', html_text, f"{ep} missing /contact link")

    # =========================================================================
    # 2. Registration Acknowledgement Verification
    # =========================================================================
    def test_03_registration_acknowledgement_elements(self):
        """Verify register.html has an unchecked acknowledgement checkbox and dual-flow JS guards."""
        res = self.client.get('/register')
        html_text = res.get_data(as_text=True)

        # Checkbox must exist and NOT be pre-checked
        self.assertIn('id="termsCheckbox"', html_text)
        self.assertNotIn('checked', re.findall(r'<input[^>]*id="termsCheckbox"[^>]*>', html_text)[0])

        # Must link to terms, privacy, and disclaimer
        self.assertIn('href="/terms"', html_text)
        self.assertIn('href="/privacy"', html_text)
        self.assertIn('href="/disclaimer"', html_text)

        # Verify JS prevents email/password registration if unchecked
        self.assertIn('if (!termsCheckbox.checked)', html_text)

        # Verify JS prevents Google OAuth registration if unchecked
        self.assertIn('googleBtn', html_text)
        google_guard_match = re.search(r"googleBtn.*addEventListener\('click'.*if \(!termsCheckbox\.checked\)", html_text, re.DOTALL)
        self.assertIsNotNone(google_guard_match, "Google button handler must validate termsCheckbox.checked before signing in")

    # =========================================================================
    # 3. Password Recovery UX & Safe Enumeration Defense
    # =========================================================================
    def test_04_password_reset_ui_and_enumeration_defense(self):
        """Verify login.html includes password reset trigger, modal, and non-enumerating safe handler."""
        res = self.client.get('/login')
        html_text = res.get_data(as_text=True)

        self.assertIn('id="forgotPasswordBtn"', html_text)
        self.assertIn('id="forgotPasswordModal"', html_text)
        self.assertIn('id="resetEmail"', html_text)
        self.assertIn('id="sendResetBtn"', html_text)
        self.assertIn('sendPasswordResetEmail', html_text)

        # Must display uniform message regardless of user existence
        self.assertIn('If an account exists for this email', html_text)

    # =========================================================================
    # 4. HTTP Error Handling (Branded HTML vs API JSON)
    # =========================================================================
    def test_05_http_404_handling(self):
        """Verify browser unknown route returns branded HTML 404; API unknown route returns JSON 404."""
        # Browser request
        res_browser = self.client.get('/unknown_page_path_test', headers={'Accept': 'text/html,application/xhtml+xml'})
        self.assertEqual(res_browser.status_code, 404)
        self.assertIn('text/html', res_browser.headers.get('Content-Type', ''))
        html = res_browser.get_data(as_text=True)
        self.assertIn('404', html)
        self.assertIn('Page Not Found', html)

        # API request
        res_api = self.client.get('/api/nonexistent_route_test', headers={'Accept': 'application/json'})
        self.assertEqual(res_api.status_code, 404)
        self.assertIn('application/json', res_api.headers.get('Content-Type', ''))
        data = res_api.get_json()
        self.assertEqual(data.get('status'), 'error')
        self.assertEqual(data.get('code'), 404)

    def test_06_http_403_handling(self):
        """Verify 403 error handler returns branded HTML for browser and JSON for API."""
        with app.test_request_context('/some_browser_page', headers={'Accept': 'text/html'}):
            res_browser, code = handle_forbidden(None)
            self.assertEqual(code, 403)
            self.assertIn('Access Forbidden', res_browser)

        with app.test_request_context('/api/protected_resource', headers={'Accept': 'application/json'}):
            res_api = handle_forbidden(None)
            self.assertEqual(res_api.status_code, 403)
            self.assertIn('application/json', res_api.headers.get('Content-Type', ''))
            data = res_api.get_json()
            self.assertEqual(data.get('status'), 'error')
            self.assertEqual(data.get('code'), 403)

    # =========================================================================
    # 5. Real Rate-Limit Exhaustion (429)
    # =========================================================================
    def test_07_real_rate_limiter_exhaustion_returns_json_429(self):
        """Trigger an actual endpoint protected by production @limit_rate and verify 429 response."""
        # /api/stream_train/<ticker> is protected by @limit_rate(max_requests=10, window_seconds=60)
        # Mock resolve_and_validate_ticker to bypass yfinance network delay
        endpoint = '/api/stream_train/TESTTICKER'
        with patch('app.resolve_and_validate_ticker', return_value=None):
            for _ in range(10):
                res = self.client.get(endpoint)
                self.assertIn(res.status_code, [400, 200])

            # The 11th request must exhaust the real limiter and return 429 JSON
            res_exceeded = self.client.get(endpoint)
            self.assertEqual(res_exceeded.status_code, 429)
            self.assertIn('application/json', res_exceeded.headers.get('Content-Type', ''))
            data = res_exceeded.get_json()
            self.assertEqual(data.get('status'), 'error')
            self.assertEqual(data.get('code'), 429)
            self.assertIn("Too many requests", data.get('error', ''))
            self.assertIn("slow down", data.get('error', '').lower())

    # =========================================================================
    # 6. HTTP 500 Sanitization & Diagnostic Protection
    # =========================================================================
    def test_08_http_500_sanitization_no_leaks(self):
        """Verify 500 responses never leak stack traces, file paths, secrets, or internal variables."""
        # Simulate an unexpected crash inside an API route by patching is_valid_ticker_format
        with patch('app.is_valid_ticker_format', side_effect=RuntimeError("SecretKey=trendanalyzer-prod-key-12345 token=eyJhbGciOi file=/etc/shadow")):
            # API 500
            res_api = self.client.get('/api/sentiment/RELIANCE', headers={'Accept': 'application/json'})
            self.assertEqual(res_api.status_code, 500)
            self.assertIn('application/json', res_api.headers.get('Content-Type', ''))
            data = res_api.get_json()
            self.assertEqual(data.get('status'), 'error')
            self.assertEqual(data.get('code'), 500)
            # Ensure generic sanitized error message
            self.assertEqual(data.get('error'), 'Internal server error. The incident has been logged.')
            # Ensure NO sensitive leak
            raw_text = res_api.get_data(as_text=True)
            self.assertNotIn('trendanalyzer-prod-key', raw_text)
            self.assertNotIn('eyJhbGciOi', raw_text)
            self.assertNotIn('/etc/shadow', raw_text)
            self.assertNotIn('Traceback', raw_text)

        # Directly invoke handle_server_error in browser context to verify HTML sanitization
        with app.test_request_context('/dashboard', headers={'Accept': 'text/html'}):
            exc = RuntimeError("Database pass=supersecretpass file=/etc/shadow host=10.0.0.1")
            res_html, code = handle_server_error(exc)
            self.assertEqual(code, 500)
            self.assertIn('Server Error', res_html)
            # Ensure NO sensitive leak
            self.assertNotIn('supersecretpass', res_html)
            self.assertNotIn('/etc/shadow', res_html)
            self.assertNotIn('RuntimeError', res_html)
            self.assertNotIn('Traceback', res_html)


if __name__ == '__main__':
    unittest.main()
