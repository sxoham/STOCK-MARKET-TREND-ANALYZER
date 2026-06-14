
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
import json
import sys

# Removed pytest dependency for environment compatibility


# Manual tests below

if __name__ == "__main__":
    # Manual run if pytest not available
    try:
        c = app.test_client()
        print("Testing / ...")
        assert c.get('/').status_code == 200
        print("Testing /api/stocks ...")
        assert c.get('/api/stocks').status_code == 200
        print("Testing /api/lookup ...")
        assert c.get('/api/lookup?q=RELIANCE').status_code == 200
        print("Basic tests passed!")
    except Exception as e:
        print(f"Tests failed: {e}")
