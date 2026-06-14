import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from main import load_sentiment_data
import pandas as pd

def test_load():
    ticker = "RELIANCE.NS"
    print(f"Testing load_sentiment_data for {ticker}...")
    try:
        df = load_sentiment_data(ticker)
        if df.empty:
            print("❌ Returned empty DataFrame.")
        else:
            print(f"✅ Successfully loaded {len(df)} rows.")
            print("First 5 rows:")
            print(df.head())
            
            # Check for NaNs
            nans = df.isna().sum().sum()
            if nans == 0:
                print("✅ No NaNs found in loaded data.")
            else:
                print(f"⚠️ Found {nans} NaNs in loaded data (might be expected if dates don't match, but check logic).")
                
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_load()
