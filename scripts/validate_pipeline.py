import yfinance as yf
import pandas as pd
import numpy as np
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from main import download_stock, add_technical_indicators

# "Interview-Safe" Validation Script
# Purpose: Prove that the internal data pipeline does not deviate from the source of truth (Yahoo Finance)
# and that feature engineering (technical indicators) is mathematically sound.

def validate_pipeline(ticker="RELIANCE.NS"):
    print(f"--- VALIDATING PIPELINE FOR {ticker} ---")
    
    # 1. Fetch Source of Truth
    print("1. Fetching Raw Data (Source of Truth)...")
    raw = yf.download(ticker, period="2y", progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw['Close_Return'] = raw['Close'].pct_change()
    
    # 2. Run Internal Pipeline
    print("2. Running Internal Pipeline...")
    processed = download_stock(ticker) # Should match raw logic
    processed = add_technical_indicators(processed)
    
    # 3. integrity Checks
    print("\n--- INTEGRITY CHECKS ---")
    
    # Check 1: Price Consistency
    # Align dates
    common_dates = raw.index.intersection(processed.index)
    
    price_diff = np.abs(raw.loc[common_dates, 'Close'] - processed.loc[common_dates, 'Close']).sum()
    max_diff = np.abs(raw.loc[common_dates, 'Close'] - processed.loc[common_dates, 'Close']).max()
    
    # Relaxed tolerance check: focus on max single difference being small enough
    if max_diff < 1e-3:
        print(f"[PASS] Price Consistency: PASS (Max Diff: {max_diff:.6f})")
    else:
        print(f"[FAIL] Price Consistency: FAIL (Total Diff: {price_diff:.6f}, Max Single Diff: {max_diff:.6f})")
        # Show top 5 mismatches
        diff_series = np.abs(raw.loc[common_dates, 'Close'] - processed.loc[common_dates, 'Close'])
        mismatches = diff_series[diff_series > 1e-4].sort_values(ascending=False).head(5)
        if not mismatches.empty:
            print("   Top Mismatches:")
            for date, val in mismatches.items():
                print(f"   - {date.date()}: {val:.6f} (Raw: {raw.loc[date, 'Close']:.2f} vs Processed: {processed.loc[date, 'Close']:.2f})")
        
    # Check 2: Indicator Sanity (RSI Range)
    if 'RSI' in processed.columns:
        rsi_min = processed['RSI'].min()
        rsi_max = processed['RSI'].max()
        if rsi_min >= 0 and rsi_max <= 100:
            print(f"[PASS] RSI Range Check: PASS (0 <= {rsi_min:.2f} ... {rsi_max:.2f} <= 100)")
        else:
            print(f"[FAIL] RSI Range Check: FAIL (Range: {rsi_min} - {rsi_max})")
            
    # Check 3: Data Lookahead (Leakage)
    print("\n[CHECK] Data Leakage (Lookahead Verification)...")
    
    cutoff = -50
    if len(processed) > abs(cutoff):
        # Create truncated dataset (ending at cutoff)
        df_full = processed.copy()
        date_at_cutoff = df_full.index[cutoff]
        
        # We need raw columns to re-calculate indicators
        raw_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        if 'Adj Close' in processed.columns: raw_cols.append('Adj Close')
        
        # Handle cases where processed might have extra columns or processed was constructed differently
        # We use the 'processed' DF as source for raw values to avoid download mismatches, 
        # assuming 'processed' preserves original raw values in those columns accurately.
        df_trunc_input = df_full.iloc[:cutoff+1][['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        
        # Re-calculate indicators on truncated data
        df_trunc_processed = add_technical_indicators(df_trunc_input)
        
        # Compare last row of truncated vs same row in full
        # exclude obvious differences like sequences or next_return if they exist (they shouldn't be in add_tech yet)
        
        row_full = df_full.iloc[cutoff]
        row_trunc = df_trunc_processed.iloc[-1]
        
        leakage_cols = []
        possible_leakage_cols = [c for c in df_trunc_processed.columns if c not in ['Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close']]

        for col in possible_leakage_cols:
            if col not in row_full.index: continue
            
            val_full = row_full[col]
            val_trunc = row_trunc[col]
            
            # Handling NaNs
            if pd.isna(val_full) and pd.isna(val_trunc): continue
            if pd.isna(val_full) != pd.isna(val_trunc):
                leakage_cols.append((col, val_full, val_trunc))
                continue

            # Numeric comparison
            if isinstance(val_full, (int, float, np.number)):
                if abs(val_full - val_trunc) > 1e-4:
                    leakage_cols.append((col, val_full, val_trunc))
            
        if not leakage_cols:
             print(f"[PASS] No Data Leakage detected at {date_at_cutoff.date()}.")
        else:
             print(f"[FAIL] Data Leakage Detected! The following features changed when future data was removed:")
             for c, vf, vt in leakage_cols:
                 print(f"   - {c}: Full={vf:.4f} vs Truncated={vt:.4f} (Diff: {abs(vf-vt):.4f})")
    else:
        print("[WARN] Not enough data to check leakage.")

    print("\nValidation Complete.")

if __name__ == "__main__":
    validate_pipeline()
