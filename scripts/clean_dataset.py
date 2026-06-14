import pandas as pd
import numpy as np
from sklearn.impute import KNNImputer
import os

INPUT_FILE = "../daily_sentiment.csv"
OUTPUT_FILE = "../daily_sentiment_cleaned.csv"

def clean_dataset():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found.")
        return

    print(f"Loading {INPUT_FILE}...")
    df = pd.read_csv(INPUT_FILE)
    
    # --- 1. Structural Checks ---
    print("\n--- Structural Checks ---")
    # Date Handling
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df.dropna(subset=['Date'], inplace=True)
        df.set_index('Date', inplace=True)
        df.sort_index(inplace=True)
        print("✓ Date column processed and set as index.")
    else:
        print("Error: 'Date' column missing.")
        return

    # Duplicates
    initial_len = len(df)
    df = df[~df.index.duplicated(keep='last')]
    if len(df) < initial_len:
        print(f"✓ Removed {initial_len - len(df)} duplicate dates.")
    else:
        print("✓ No duplicate dates found.")

    # Numeric Conversion
    # Drop non-ticker columns if any (except index). 'Sentiment_Score' looks like a global avg, we should keep it or recalculate it?
    # Looking at the csv file content from previous turns, there is a 'Sentiment_Score' column and then ticker columns.
    # We will treat all columns as sentiment scores.
    cols_to_clean = df.columns
    for col in cols_to_clean:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    print("✓ Coerced non-numeric values to NaN.")

    # --- 2. Outlier Management ---
    print("\n--- Outlier Management ---")
    # Range Enforcement (VADER -1 to 1)
    out_of_range = ((df < -1.0) | (df > 1.0)).sum().sum()
    if out_of_range > 0:
        print(f"⚠ Found {out_of_range} values outside [-1, 1]. Clipping...")
        df = df.clip(lower=-1.0, upper=1.0)
    else:
        print("✓ All values within VADER range [-1, 1].")

    # Statistical Outliers (Z-score > 3)
    # We won't remove them, just log them.
    z_scores = (df - df.mean()) / df.std()
    outliers = (z_scores.abs() > 3).sum().sum()
    if outliers > 0:
        print(f"ℹ Detected {outliers} statistical outliers (Z-score > 3). Keeping them as potential market signals.")
    else:
        print("✓ No extreme statistical outliers detected.")

    # --- 3. Missing Value Imputation (KNN) ---
    print("\n--- Imputation (KNN) ---")
    missing_before = df.isna().sum().sum()
    print(f"Missing values before imputation: {missing_before}")

    if missing_before > 0:
        # KNN Imputer
        # n_neighbors=5 is a reasonable default. 
        # We need to impute column-wise? No, KNNImputer works on the whole matrix.
        # It finds similar rows (days) to fill in gaps.
        imputer = KNNImputer(n_neighbors=5, weights='uniform')
        
        # KNNImputer returns a numpy array, we need to put it back into DataFrame
        df_imputed_array = imputer.fit_transform(df)
        df_imputed = pd.DataFrame(df_imputed_array, columns=df.columns, index=df.index)
        
        # If KNN fails (e.g. valid neighbors not found), fallback to ffill/bfill
        if df_imputed.isna().sum().sum() > 0:
            print("⚠ KNN left some gaps. Applying Forward/Backward Fill fallback.")
            df_imputed.ffill(inplace=True)
            df_imputed.bfill(inplace=True)
            df_imputed.fillna(0.0, inplace=True) # Final fallback
        
        df = df_imputed
        print(f"✓ Imputation complete. Missing values remaining: {df.isna().sum().sum()}")
    else:
        print("✓ No missing values to impute.")

    # --- 4. Save ---
    print(f"\nSaving cleaned dataset to {OUTPUT_FILE}...")
    # Round to reasonable precision
    df = df.round(4)
    # Reset index to save Date as column
    df.reset_index(inplace=True)
    df.to_csv(OUTPUT_FILE, index=False)
    print("Done.")

if __name__ == "__main__":
    clean_dataset()
