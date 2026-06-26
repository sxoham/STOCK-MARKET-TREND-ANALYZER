import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLEAN_FILE = os.path.join(BASE_DIR, "..", "daily_sentiment.csv")
ORIG_FILE = os.path.join(BASE_DIR, "..", "daily_sentiment.csv.bak")
STOCKS_TO_CHECK = ["RELIANCE.NS", "TCS.NS", "INFY.NS"]

def audit_accuracy():
    print("--- 1. Distribution Check ---")
    if not os.path.exists(CLEAN_FILE):
        print(f"Error: {CLEAN_FILE} not found.")
        return
    
    clean_df = pd.read_csv(CLEAN_FILE)
    clean_df['Date'] = pd.to_datetime(clean_df['Date'])
    clean_df.set_index('Date', inplace=True)
    
    if os.path.exists(ORIG_FILE):
        print(f"Comparing with {ORIG_FILE}...")
        orig_df = pd.read_csv(ORIG_FILE)
        # Basic stats comparison for a sample column
        col = "RELIANCE.NS"
        if col in clean_df.columns and col in orig_df.columns:
            print(f"\nStats for {col}:")
            print(f"  Original Mean: {orig_df[col].mean():.4f}")
            print(f"  Cleaned Mean:  {clean_df[col].mean():.4f}")
            print(f"  Original Std:  {orig_df[col].std():.4f}")
            print(f"  Cleaned Std:   {clean_df[col].std():.4f}")
            
            # Check how much data was imputed
            orig_missing = orig_df[col].isna().sum()
            total_rows = len(orig_df)
            print(f"  Imputed Rows:  {orig_missing} / {total_rows} ({orig_missing/total_rows:.1%})")
    else:
        print("Original comparison file not found. Skipping distribution comparison.")

    print("\n--- 2. Market Correlation Check ---")
    print("Verifying if sentiment actually correlates with future price movement (predictive accuracy).")
    
    for ticker in STOCKS_TO_CHECK:
        if ticker not in clean_df.columns:
            continue
            
        print(f"\nEvaluating {ticker}...")
        # Download price data
        start_date = clean_df.index.min()
        end_date = clean_df.index.max()
        
        try:
            prices = yf.download(ticker, start=start_date, end=end_date, progress=False)
            if isinstance(prices.columns, pd.MultiIndex):
                prices.columns = prices.columns.get_level_values(0)
                
            if prices.empty:
                print("  No price data found.")
                continue
                
            # Calculate Next Day Return
            prices['Return_NextDay'] = prices['Close'].pct_change().shift(-1)
            
            # Join with sentiment
            # Ensure index match
            prices.index = pd.to_datetime(prices.index).normalize()
            
            merged = prices.join(clean_df[[ticker]], how='inner')
            merged.dropna(inplace=True)
            
            if len(merged) < 10:
                print("  Not enough overlapping data.")
                continue
                
            # Calculate Correlation
            corr = merged[ticker].corr(merged['Return_NextDay'])
            print(f"  Correlation (Sentiment vs Next Day Return): {corr:.4f}")
            
            if abs(corr) < 0.01:
                print("  ⚠️ Low correlation. Sentiment might be noise or lead/lag is different.")
            elif corr > 0:
                print("  ✅ Positive correlation. Higher sentiment -> Higher Return (Expected).")
            else:
                print("  ⚠️ Negative correlation. Higher sentiment -> Lower Return (Contrarian?).")
                
        except Exception as e:
            print(f"  Error fetching data: {e}")

if __name__ == "__main__":
    audit_accuracy()
