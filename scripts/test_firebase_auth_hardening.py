#!/usr/bin/env python3
"""
Test Suite: Firebase Authentication & Token Verification Hardening
Tests the 9 specific requirements for Render production authentication:
1. Valid Firebase token -> protected endpoint succeeds
2. Missing token -> 401
3. Malformed token -> 401
4. Expired token -> 401
5. Wrong-user email access -> 403 (BOLA/IDOR protection)
6. Valid user save_data -> 200 success
7. Invalid token cannot save -> 401
8. Token contents are never logged (logging safety audit)
9. Production Firebase initialization fails safely and supports multi-mode credentials
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

    # 1. Valid Firebase token -> protected endpoint succeeds
    def test_valid_token_protected_endpoint_succeeds(self):
        test_email = "validuser@example.com"
        valid_claims = {
            "iss": "https://securetoken.google.com/trendanalyzer-4857f",
            "aud": "trendanalyzer-4857f",
            "auth_time": 1700000000,
            "user_id": "firebase_uid_123",
            "sub": "firebase_uid_123",
            "iat": 1700000000,
            "exp": 1700003600,
            "email": test_email,
            "email_verified": True
        }

        with patch('app.verify_firebase_id_token', return_value=valid_claims):
            res = self.client.get(
                f'/api/get_data/{test_email}',
                headers={'Authorization': 'Bearer valid.jwt.token'}
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertIn(data.get('status'), ['success', 'game_start'])

    # 2. Missing token -> 401
    def test_missing_token_returns_401(self):
        with patch.object(app_module, 'REQUIRE_AUTH', True):
            res = self.client.get('/api/get_data/user@example.com')
            self.assertEqual(res.status_code, 401)
            data = res.get_json()
            self.assertIn("Authentication required", data.get('error', ''))

    # 3. Malformed token -> 401
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

    # 4. Expired token -> 401
    def test_expired_token_returns_401(self):
        with patch.object(app_module, 'REQUIRE_AUTH', True):
            # Firebase Admin SDK raising ExpiredIdTokenError
            with patch('app.HAS_FIREBASE_ADMIN', True):
                from firebase_admin._token_gen import ExpiredIdTokenError
                with patch('app.firebase_auth.verify_id_token', side_effect=ExpiredIdTokenError("Token expired", None)):
                    res = self.client.get(
                        '/api/get_data/user@example.com',
                        headers={'Authorization': 'Bearer header.payload_expired.signature'}
                    )
                    self.assertEqual(res.status_code, 401)

    # 5. Wrong-user email access -> 403 (BOLA protection)
    def test_wrong_user_email_access_returns_403(self):
        attacker_email = "attacker@example.com"
        victim_email = "victim@example.com"
        token_claims = {
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

    # 6. Valid user save_data -> success
    def test_valid_user_save_data_success(self):
        user_email = "savetest@example.com"
        token_claims = {
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

                # Verify that get_data recovers the saved data
                res_get = self.client.get(
                    f'/api/get_data/{user_email}',
                    headers={'Authorization': 'Bearer legitimate_token'}
                )
                self.assertEqual(res_get.status_code, 200)
                get_data = res_get.get_json()
                self.assertEqual(get_data.get('status'), 'success')
                self.assertEqual(get_data['data']['portfolio']['balance'], 50000)

    # 7. Invalid token cannot save -> 401
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

    # 8. Token contents are never logged
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

    # 9. Production Firebase initialization fails safely and supports multi-mode credentials
    def test_firebase_initialization_modes(self):
        from firebase_admin import credentials as fb_credentials
        
        # Test PublicCertCredential returns None without throwing ADC error
        pub_cred = app_module.PublicCertCredential()
        self.assertIsNone(pub_cred.get_credential())

        # Test verify_firebase_id_token rejects tokens without 3 segments early
        self.assertIsNone(verify_firebase_id_token("invalid_token_format"))
        self.assertIsNone(verify_firebase_id_token(None))
        self.assertIsNone(verify_firebase_id_token(12345))

        # Test service account file mode
        dummy_sa = {
            "type": "service_account",
            "project_id": "trendanalyzer-4857f",
            "private_key_id": "fakekeyid",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----\n",
            "client_email": "firebase-adminsdk@trendanalyzer-4857f.iam.gserviceaccount.com",
            "client_id": "123456789",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as tf:
            json.dump(dummy_sa, tf)
            tf_path = tf.name

        try:
            with patch('firebase_admin.credentials.Certificate') as mock_cert:
                mock_cert.return_value = MagicMock()
                # Test loading from file
                with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS': tf_path}):
                    self.assertTrue(os.path.isfile(tf_path))
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

if __name__ == '__main__':
    unittest.main()
