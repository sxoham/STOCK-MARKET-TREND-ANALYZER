import sys
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score, confusion_matrix
from sklearn.preprocessing import StandardScaler
from collections import Counter

# Add parent directory to path to import from main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import download_stock, add_technical_indicators, create_target, FEATURE_COLS, download_macro_data, load_sentiment_data

def diagnose_ticker(ticker="RELIANCE.NS"):
    print(f"--- DIAGNOSING {ticker} ---")
    
    # 1. Data Fetching
    print("Fetching data...")
    df = download_stock(ticker, start="2015-01-01")
    print(f"Raw data shape: {df.shape}")
    
    # 2. Feature Engineering
    print("Adding indicators...")
    df = add_technical_indicators(df)
    
    # Merge Macro Data
    print("Fetching macro data...")
    macro = download_macro_data(start=df.index[0], end=df.index[-1])
    if not macro.empty:
        df = df.join(macro)
        df.ffill(inplace=True)
        df.fillna(0, inplace=True)
    else:
        # Mock macro data if download fails
        for col in ["Nifty_Return", "USD_Change", "Gold_Change", "Oil_Change"]:
            df[col] = 0.0

    # Merge Sentiment Data
    print("Fetching sentiment data...")
    sentiment = load_sentiment_data(ticker)
    if not sentiment.empty:
        df = df.join(sentiment, how='left')
        df["Sentiment_Score"].fillna(0.0, inplace=True)
    else:
        df["Sentiment_Score"] = 0.0
    
    # Check for NaNs/Inf
    nans = df[FEATURE_COLS].isna().sum().sum()
    infs = np.isinf(df[FEATURE_COLS].select_dtypes(include=np.number)).sum().sum()
    print(f"NaNs in features: {nans}")
    print(f"Infs in features: {infs}")
    
    df.bfill(inplace=True)
    df.ffill(inplace=True)
    df.replace([np.inf, -np.inf], 0, inplace=True)
    
    # 3. Target Creation
    print("Creating target...")
    df = create_target(df)
    
    print("\nTarget Distribution:")
    print(df["Target"].value_counts(normalize=True))
    
    # Check correlations
    # Ensure all feature cols exist
    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        print(f"Missing columns: {missing}")
        for c in missing:
            df[c] = 0.0

    # 4. Correlation Check
    print("\nTop 5 Correlated Features with Next_Return:")
    corrs = df[FEATURE_COLS + ['Next_Return']].corr()['Next_Return'].abs().sort_values(ascending=False)
    print(corrs.head(6))
    
    # 5. Simple Model Test (Random Forest)
    print("\nTraining Baseline Random Forest...")
    X = df[FEATURE_COLS].values
    y = df["Target"].values
    
    # Standard Split (Shuffle=True to see if it's learnable at all)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)
    
    model = RandomForestClassifier(n_estimators=100, class_weight='balanced', random_state=42)
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    
    print("\nBaseline Model Performance (Random Split):")
    print(classification_report(y_test, y_pred))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, y_pred))
    
    # 6. Time-Series Split Test
    print("\nTraining Baseline Random Forest (Time-Series Split)...")
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    
    # Class weights for time split
    cw = dict(zip(*np.unique(y_train, return_counts=True)))
    cw = {k: len(y_train)/(3*v) for k,v in cw.items()}
    print(f"Time Split Class Weights: {cw}")

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)
    
    model = RandomForestClassifier(n_estimators=100, class_weight='balanced', random_state=42)
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    
    print("\nBaseline Model Performance (Time Split):")
    print(classification_report(y_test, y_pred))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, y_pred))

    # 7. Binary Classification Test (UP vs DOWN) to check for ANY signal
    print("\n--- BINARY CLASSIFICATION TEST (UP/DOWN) ---")
    # 0=SELL (DOWN), 1=HOLD, 2=BUY (UP). 
    # Let's drop HOLD or merge.
    # Option A: Drop HOLD
    mask = y != 1
    X_bin = X[mask]
    y_bin = y[mask]
    y_bin = np.where(y_bin == 2, 1, 0) # 2->1 (UP), 0->0 (DOWN)
    
    print(f"Binary Data Shape: {X_bin.shape}")
    print(f"Class Balance: {np.mean(y_bin)} (1=UP)")
    
    X_train_b, X_test_b, y_train_b, y_test_b = train_test_split(X_bin, y_bin, test_size=0.2, random_state=42, stratify=y_bin)
    
    scaler_b = StandardScaler()
    X_train_b = scaler_b.fit_transform(X_train_b)
    X_test_b = scaler_b.transform(X_test_b)
    
    model_b = RandomForestClassifier(n_estimators=100, class_weight='balanced', random_state=42)
    model_b.fit(X_train_b, y_train_b)
    y_pred_b = model_b.predict(X_test_b)
    
    print("\nBinary Random Forest Performance (Random Split):")
    print(classification_report(y_test_b, y_pred_b))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test_b, y_pred_b))

if __name__ == "__main__":
    t = "RELIANCE.NS"
    if len(sys.argv) > 1:
        t = sys.argv[1]
    diagnose_ticker(t)
