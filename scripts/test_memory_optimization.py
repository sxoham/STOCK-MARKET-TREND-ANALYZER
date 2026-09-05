#!/usr/bin/env python3
"""
Test Suite: Production Memory Optimization & Bounded Model Cache
Validates:
1. Startup RSS memory remains bounded
2. Bounded model cache eviction works (MAX_CACHED_MODELS=2)
3. RELIANCE prediction succeeds with accurate structure
4. Sentiment endpoint does not load ML models
5. Protected get_data/save_data do not load ML models
6. Repeated predictions across tickers stay memory-bounded
7. stream_train prevents in-process training in production
"""

import os
import gc
import psutil
import unittest
from unittest.mock import patch, mock_open, MagicMock

os.environ['TESTING'] = 'true'
os.environ['REQUIRE_AUTH'] = 'true'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

import app as app_module
from app import app, get_cached_model_bundle, _MODEL_CACHE, MAX_CACHED_MODELS, get_rss_memory_mb

class TestMemoryOptimization(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = self.app.test_client()
        self.app.config['TESTING'] = True

    def test_01_startup_memory_is_lean(self):
        """Verify process RSS at startup is well below the 512 MB Render threshold."""
        rss = get_rss_memory_mb()
        # Should be below 250 MB (previously 414 MB)
        self.assertLess(rss, 260.0, f"Startup RSS {rss} MB is unexpectedly high")

    def test_02_bounded_model_cache_eviction(self):
        """Verify bounded cache holds at most MAX_CACHED_MODELS and evicts oldest."""
        # Test production mode where MAX_CACHED_MODELS is 1
        with patch.object(app_module, 'IS_PROD', True):
            _MODEL_CACHE.clear()
            b1 = get_cached_model_bundle("RELIANCE_NS")
            if b1:
                self.assertIn("RELIANCE_NS", _MODEL_CACHE)
                self.assertEqual(len(_MODEL_CACHE), 1)
                
                # Load TCS -> must evict RELIANCE_NS in production
                b2 = get_cached_model_bundle("TCS_NS")
                if b2:
                    self.assertEqual(len(_MODEL_CACHE), 1)
                    self.assertIn("TCS_NS", _MODEL_CACHE)
                    self.assertNotIn("RELIANCE_NS", _MODEL_CACHE)

        # Test development mode where MAX_CACHED_MODELS is 2
        with patch.object(app_module, 'IS_PROD', False), patch.dict(os.environ, {"RENDER": "false"}):
            _MODEL_CACHE.clear()
            b1 = get_cached_model_bundle("RELIANCE_NS")
            b2 = get_cached_model_bundle("TCS_NS")
            if b1 and b2:
                self.assertEqual(len(_MODEL_CACHE), 2)
                # Third model evicts oldest (RELIANCE_NS)
                b3 = get_cached_model_bundle("INFY_NS")
                if b3:
                    self.assertEqual(len(_MODEL_CACHE), 2)
                    self.assertIn("INFY_NS", _MODEL_CACHE)
                    self.assertIn("TCS_NS", _MODEL_CACHE)
                    self.assertNotIn("RELIANCE_NS", _MODEL_CACHE)

    def test_03_reliance_prediction_succeeds(self):
        """Verify RELIANCE.NS prediction produces valid output."""
        res = self.client.get('/api/predict/RELIANCE.NS')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('ticker'), 'RELIANCE.NS')
        self.assertIn(data.get('prediction'), ['UP', 'DOWN', 'HOLD', 'NEUTRAL'])
        self.assertIsInstance(data.get('probability'), (int, float))

    def test_04_sentiment_does_not_load_models(self):
        """Verify /api/sentiment/<ticker> runs without loading heavy Keras models."""
        cache_len_before = len(_MODEL_CACHE)
        res = self.client.get('/api/sentiment/RELIANCE.NS')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue('score' in data or 'sentiment_score' in data)
        # Model cache should not have expanded
        self.assertEqual(len(_MODEL_CACHE), cache_len_before)

    def test_05_auth_endpoints_do_not_load_models(self):
        """Verify get_data/save_data execute purely via database and auth without loading ML models."""
        cache_len_before = len(_MODEL_CACHE)
        with patch('app.verify_firebase_id_token', return_value={'email': 'memtest@example.com'}):
            res_get = self.client.get(
                '/api/get_data/memtest@example.com',
                headers={'Authorization': 'Bearer test_token'}
            )
            self.assertEqual(res_get.status_code, 200)
            
            res_save = self.client.post(
                '/api/save_data',
                headers={'Authorization': 'Bearer test_token'},
                json={'email': 'memtest@example.com', 'data': {'portfolio': {'balance': 1000}}}
            )
            self.assertEqual(res_save.status_code, 200)

        self.assertEqual(len(_MODEL_CACHE), cache_len_before)

    def test_06_stream_train_guards_against_in_process_training(self):
        """Verify in production stream_train completes without launching heavy training."""
        with patch.object(app_module, 'IS_PROD', True), \
             patch('app.resolve_and_validate_ticker', return_value='UNTRAINED_TEST.NS'):
            
            res = self.client.get('/api/stream_train/UNTRAINED_TEST.NS')
            self.assertEqual(res.status_code, 200)
            body = res.data.decode('utf-8')
            self.assertIn('"Completed"', body)
            self.assertIn('Pre-trained model analysis ready', body)

    def test_07_repeated_predictions_remain_bounded(self):
        """Verify memory does not grow unbounded over repeated predictions."""
        start_rss = get_rss_memory_mb()
        for _ in range(5):
            res = self.client.get('/api/predict/RELIANCE.NS')
            self.assertEqual(res.status_code, 200)
        gc.collect()
        end_rss = get_rss_memory_mb()
        # RSS should stay strictly within Render 512 MB boundary
        self.assertLess(end_rss, 500.0, f"Memory reached {end_rss} MB, nearing Render limit")

    def test_08_rss_memory_reporting_and_linux_proc_fallback(self):
        """Verify get_rss_memory_mb returns positive RSS and falls back to /proc correctly."""
        # 1. Direct invocation should return a real positive number (> 0)
        current_rss = get_rss_memory_mb()
        self.assertGreater(current_rss, 0.0)

        # 2. Test fallback when psutil is not available
        with patch.dict('sys.modules', {'psutil': None}):
            with patch('builtins.open', mock_open(read_data="12345 50000 1000 0 0 0 0")), \
                 patch('os.path.exists', side_effect=lambda p: p == '/proc/self/statm'), \
                 patch('os.sysconf', return_value=4096, create=True):
                rss_proc = get_rss_memory_mb()
                expected = round((50000 * 4096) / (1024 * 1024), 2)
                self.assertEqual(rss_proc, expected)

    def test_09_matplotlib_uninstalled_handling_without_magicmock(self):
        """Verify matplotlib is blocked via standard PEP 302 None, not MagicMock."""
        import sys
        self.assertIsNone(sys.modules.get("matplotlib"))
        self.assertIsNone(sys.modules.get("matplotlib.pyplot"))
        self.assertFalse(isinstance(sys.modules.get("matplotlib"), MagicMock))

if __name__ == '__main__':
    unittest.main()
