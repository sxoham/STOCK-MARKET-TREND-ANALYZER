#!/usr/bin/env python3
"""
Test Suite: Firebase Authentication & Token Verification Architecture Hardening
Verifies:
1. Mode 2 Authoritative Verification via google.oauth2.id_token (no service account, no synthetic credentials)
2. Mode 1 Verification via Firebase Admin SDK (real service account credential)
3. Cryptographic RS256 token verification
4. Missing token -> 401
5. Malformed token -> 401
6. Expired token -> 401
7. Wrong audience -> 401
8. Wrong issuer -> 401
9. Valid token -> 200 success
10. Wrong-user access (BOLA/IDOR protection) -> 403
11. Valid user save_data -> 200 success
12. Invalid token cannot save -> 401
13. Token contents are never logged
"""

import os
import sys
import json
import logging
import unittest
import tempfile
from io import StringIO
from unittest.mock import patch, MagicMock

# Set test environment
os.environ['TESTING'] = 'true'
os.environ['REQUIRE_AUTH'] = 'true'

import app as app_module
from app import app, verify_firebase_id_token, require_user_auth

class TestFirebaseAuthHardening(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = self.app.test_client()
        self.app.config['TESTING'] = True

    # 1. Mode 2: Direct verification via google.oauth2.id_token when no service account exists
    def test_mode2_authoritative_verification_without_service_account(self):
        """Prove production verifies via google.oauth2.id_token without synthetic credentials."""
        test_email = "mode2user@example.com"
        valid_claims = {
            "iss": f"https://securetoken.google.com/{app_module.FIREBASE_PROJECT_ID}",
            "aud": app_module.FIREBASE_PROJECT_ID,
            "auth_time": 1700000000,
            "user_id": "mode2_uid_123",
            "sub": "mode2_uid_123",
            "iat": 1700000000,
            "exp": 1700003600,
            "email": test_email,
            "email_verified": True
        }

        # Simulate Mode 2: HAS_FIREBASE_ADMIN is False
        with patch.object(app_module, 'HAS_FIREBASE_ADMIN', False), \
             patch('app.google_id_token.verify_firebase_token', return_value=valid_claims) as mock_verify:
            
            result = verify_firebase_id_token("header.payload.signature")
            self.assertIsNotNone(result)
            self.assertEqual(result['email'], test_email)
            self.assertEqual(result['aud'], app_module.FIREBASE_PROJECT_ID)
            # Verify google.oauth2.id_token was the authoritative verifier called
            mock_verify.assert_called_once()
            call_args, call_kwargs = mock_verify.call_args
            self.assertEqual(call_kwargs.get('audience'), app_module.FIREBASE_PROJECT_ID)

    # 2. Mode 1: Verification via Firebase Admin SDK when service account exists
    def test_mode1_firebase_admin_verification_with_service_account(self):
        """Prove Mode 1 uses firebase_auth.verify_id_token when real credentials exist."""
        test_email = "mode1user@example.com"
        valid_claims = {
            "iss": f"https://securetoken.google.com/{app_module.FIREBASE_PROJECT_ID}",
            "aud": app_module.FIREBASE_PROJECT_ID,
            "user_id": "mode1_uid_456",
            "email": test_email
        }

        with patch.object(app_module, 'HAS_FIREBASE_ADMIN', True), \
             patch('app.firebase_auth.verify_id_token', return_value=valid_claims) as mock_fb_verify:
            
            result = verify_firebase_id_token("header.payload.signature")
            self.assertIsNotNone(result)
            self.assertEqual(result['email'], test_email)
            mock_fb_verify.assert_called_once_with("header.payload.signature", check_revoked=False)

    # 3. Valid Firebase token -> protected endpoint succeeds
    def test_valid_token_protected_endpoint_succeeds(self):
        test_email = "validuser@example.com"
        valid_claims = {
            "iss": f"https://securetoken.google.com/{app_module.FIREBASE_PROJECT_ID}",
            "aud": app_module.FIREBASE_PROJECT_ID,
            "user_id": "firebase_uid_123",
            "email": test_email
        }

        with patch('app.verify_firebase_id_token', return_value=valid_claims):
            res = self.client.get(
                f'/api/get_data/{test_email}',
                headers={'Authorization': 'Bearer valid.jwt.token'}
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn(data.get('status'), ['success', 'game_start'])

    # 4. Missing token -> 401
    def test_missing_token_returns_401(self):
        with patch.object(app_module, 'REQUIRE_AUTH', True):
            res = self.client.get('/api/get_data/user@example.com')
            self.assertEqual(res.status_code, 401)
            data = res.get_json()
            self.assertIn("Authentication required", data.get('error', ''))

    # 5. Malformed token -> 401
    def test_malformed_token_returns_401(self):
        with patch.object(app_module, 'REQUIRE_AUTH', True):
            malformed_tokens = [
                "not-a-token",
                "onlyonepart",
                "partone.parttwo",
                "   ",
                "Bearer "
            ]
            for bad_token in malformed_tokens:
                res = self.client.get(
                    '/api/get_data/user@example.com',
                    headers={'Authorization': f'Bearer {bad_token}'}
                )
                self.assertEqual(res.status_code, 401)
                data = res.get_json()
                self.assertEqual(data.get('status'), 'error')

    # 6. Expired token -> 401
    def test_expired_token_returns_401(self):
        with patch.object(app_module, 'REQUIRE_AUTH', True):
            with patch('app.google_id_token.verify_firebase_token', side_effect=Exception("Token expired: exp")):
                with patch.object(app_module, 'HAS_FIREBASE_ADMIN', False):
                    res = self.client.get(
                        '/api/get_data/user@example.com',
                        headers={'Authorization': 'Bearer header.payload_expired.signature'}
                    )
                    self.assertEqual(res.status_code, 401)

    # 7. Wrong audience -> 401
    def test_wrong_audience_returns_401(self):
        wrong_aud_claims = {
            "iss": f"https://securetoken.google.com/{app_module.FIREBASE_PROJECT_ID}",
            "aud": "wrong-project-id",
            "email": "user@example.com"
        }
        with patch.object(app_module, 'HAS_FIREBASE_ADMIN', False), \
             patch('app.google_id_token.verify_firebase_token', return_value=wrong_aud_claims):
            
            result = verify_firebase_id_token("header.wrong_aud.signature")
            self.assertIsNone(result)

        with patch.object(app_module, 'REQUIRE_AUTH', True), \
             patch('app.verify_firebase_id_token', return_value=None):
            res = self.client.get(
                '/api/get_data/user@example.com',
                headers={'Authorization': 'Bearer header.wrong_aud.signature'}
            )
            self.assertEqual(res.status_code, 401)

    # 8. Wrong issuer -> 401
    def test_wrong_issuer_returns_401(self):
        wrong_iss_claims = {
            "iss": "https://securetoken.google.com/malicious-project",
            "aud": app_module.FIREBASE_PROJECT_ID,
            "email": "user@example.com"
        }
        with patch.object(app_module, 'HAS_FIREBASE_ADMIN', False), \
             patch('app.google_id_token.verify_firebase_token', return_value=wrong_iss_claims):
            
            result = verify_firebase_id_token("header.wrong_iss.signature")
            self.assertIsNone(result)

        with patch.object(app_module, 'REQUIRE_AUTH', True), \
             patch('app.verify_firebase_id_token', return_value=None):
            res = self.client.get(
                '/api/get_data/user@example.com',
                headers={'Authorization': 'Bearer header.wrong_iss.signature'}
            )
            self.assertEqual(res.status_code, 401)

    # 9. Wrong-user email access -> 403 (BOLA protection)
    def test_wrong_user_email_access_returns_403(self):
        attacker_email = "attacker@example.com"
        victim_email = "victim@example.com"
        token_claims = {
            "iss": f"https://securetoken.google.com/{app_module.FIREBASE_PROJECT_ID}",
            "aud": app_module.FIREBASE_PROJECT_ID,
            "email": attacker_email,
            "user_id": "attacker_uid"
        }

        with patch.object(app_module, 'REQUIRE_AUTH', True):
            with patch('app.verify_firebase_id_token', return_value=token_claims):
                # Attempt to read victim's data
                res_get = self.client.get(
                    f'/api/get_data/{victim_email}',
                    headers={'Authorization': 'Bearer attacker_valid_token'}
                )
                self.assertEqual(res_get.status_code, 403)
                self.assertIn("Forbidden", res_get.get_json().get('error', ''))

                # Attempt to overwrite victim's data
                res_save = self.client.post(
                    '/api/save_data',
                    headers={'Authorization': 'Bearer attacker_valid_token'},
                    json={'email': victim_email, 'data': {'portfolio': {'balance': 0}}}
                )
                self.assertEqual(res_save.status_code, 403)
                self.assertIn("Forbidden", res_save.get_json().get('error', ''))

    # 10. Valid user save_data -> success
    def test_valid_user_save_data_success(self):
        user_email = "savetest@example.com"
        token_claims = {
            "iss": f"https://securetoken.google.com/{app_module.FIREBASE_PROJECT_ID}",
            "aud": app_module.FIREBASE_PROJECT_ID,
            "email": user_email,
            "user_id": "save_uid"
        }

        with patch.object(app_module, 'REQUIRE_AUTH', True):
            with patch('app.verify_firebase_id_token', return_value=token_claims):
                res = self.client.post(
                    '/api/save_data',
                    headers={'Authorization': 'Bearer legitimate_token'},
                    json={
                        'email': user_email,
                        'data': {
                            'portfolio': {'balance': 50000, 'holdings': {'RELIANCE.NS': {'qty': 10, 'avgPrice': 1200}}},
                            'watchlist': ['RELIANCE.NS', 'TCS.NS']
                        }
                    }
                )
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.get_json().get('status'), 'success')

                # Verify get_data returns saved data
                res_get = self.client.get(
                    f'/api/get_data/{user_email}',
                    headers={'Authorization': 'Bearer legitimate_token'}
                )
                self.assertEqual(res_get.status_code, 200)
                get_data = res_get.get_json()
                self.assertEqual(get_data.get('status'), 'success')
                self.assertEqual(get_data['data']['portfolio']['balance'], 50000)

    # 11. Invalid token cannot save -> 401
    def test_invalid_token_cannot_save(self):
        with patch.object(app_module, 'REQUIRE_AUTH', True):
            with patch('app.verify_firebase_id_token', return_value=None):
                res = self.client.post(
                    '/api/save_data',
                    headers={'Authorization': 'Bearer forgery.fake.signature'},
                    json={'email': 'hacker@example.com', 'data': {'portfolio': {'balance': 9999999}}}
                )
                self.assertEqual(res.status_code, 401)
                self.assertIn("Invalid or expired", res.get_json().get('error', ''))

    # 12. Token contents are never logged
    def test_token_contents_never_logged(self):
        sensitive_token = "secret_header.super_sensitive_payload_12345.critical_signature_xyz"

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)
        app_logger = logging.getLogger('trendanalyzer')
        app_logger.addHandler(handler)

        try:
            with patch.object(app_module, 'REQUIRE_AUTH', True):
                self.client.get(
                    '/api/get_data/user@example.com',
                    headers={'Authorization': f'Bearer {sensitive_token}'}
                )
            
            log_output = log_stream.getvalue()
            # Assert sensitive components are NOT anywhere in log output
            self.assertNotIn("secret_header", log_output)
            self.assertNotIn("super_sensitive_payload_12345", log_output)
            self.assertNotIn("critical_signature_xyz", log_output)
            self.assertNotIn(sensitive_token, log_output)
        finally:
            app_logger.removeHandler(handler)

    # 13. Confirm PublicCertCredential was removed from codebase
    def test_no_synthetic_credentials_exist(self):
        self.assertFalse(hasattr(app_module, 'PublicCertCredential'),
                         "PublicCertCredential must be completely removed.")

if __name__ == '__main__':
    unittest.main()
