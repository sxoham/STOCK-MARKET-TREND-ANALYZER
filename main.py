import os
import random
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (accuracy_score, precision_score, recall_score,f1_score, confusion_matrix, roc_auc_score,precision_recall_curve, auc)
import tensorflow as tf
from keras.models import Sequential, Model
from keras.layers import LSTM, Dense, Dropout, BatchNormalization, Bidirectional, Input, MultiHeadAttention, LayerNormalization, GlobalAveragePooling1D, Conv1D
from keras.optimizers import Adam
from keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau
from collections import Counter
import joblib
import warnings
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier
from sklearn.feature_selection import RFE
from scipy.stats import entropy

warnings.filterwarnings("ignore")

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
tf.random.set_seed(SEED)

# 2. Config - choose your stocks here
STOCKS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", 
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", 
    "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "TITAN.NS", 
    "BAJFINANCE.NS", "SUNPHARMA.NS", "HCLTECH.NS", "TATASTEEL.NS", "NTPC.NS"
]
START_DATE = "2000-01-01"
import datetime
END_DATE = datetime.date.today().strftime("%Y-%m-%d")
WINDOW = 30
TEST_SPLIT_RATIO = 0.2

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_models_optionB")
os.makedirs(RESULTS_DIR, exist_ok=True)

FEATURE_COLS = [
    "RSI", "MACD_Norm", "Return", "Volatility", "Volume_Change", 
    "Dist_EMA20", "Dist_EMA50", "Dist_EMA200", "Rel_EMA20_50", # Trend Ratios
    "BBP", "Bandwidth", "ATR_Pct", "ROC", # Oscillators/Volatility
    "Return_1d", "Return_3d", "Return_5d", "Nifty_Return", "USD_Change", 
    "Gold_Change", "Oil_Change", "ADX", "CCI", "MFI", "OBV_Slope", "Sentiment_Score"
]

# 3. Utility functions
def download_macro_data(start: str, end: str) -> pd.DataFrame:
    """
    Downloads macro-economic data (Nifty, USD/INR, Gold, Oil) and calculates daily returns.
    
    Args:
        start (str): Start date string (YYYY-MM-DD).
        end (str): End date string (YYYY-MM-DD).
        
    Returns:
        pd.DataFrame: DataFrame containing macro indicators aligned to Nifty trading days.
    """
    try:
        nifty = yf.download("^NSEI", start=start, end=end, progress=False)
        usd = yf.download("INR=X", start=start, end=end, progress=False)
        gold = yf.download("GC=F", start=start, end=end, progress=False)
        oil = yf.download("CL=F", start=start, end=end, progress=False)
        
        if isinstance(nifty.columns, pd.MultiIndex): nifty.columns = nifty.columns.get_level_values(0)
        if isinstance(usd.columns, pd.MultiIndex): usd.columns = usd.columns.get_level_values(0)
        if isinstance(gold.columns, pd.MultiIndex): gold.columns = gold.columns.get_level_values(0)
        if isinstance(oil.columns, pd.MultiIndex): oil.columns = oil.columns.get_level_values(0)
        
        macro = pd.DataFrame(index=nifty.index)
        macro["Nifty_Return"] = nifty["Close"].pct_change()
        
        # Reindex others to match Nifty
        usd = usd.reindex(nifty.index, method='ffill')
        gold = gold.reindex(nifty.index, method='ffill')
        oil = oil.reindex(nifty.index, method='ffill')

        macro["USD_Change"] = usd["Close"].pct_change()
        macro["Gold_Change"] = gold["Close"].pct_change()
        macro["Oil_Change"] = oil["Close"].pct_change()
        return macro
    except Exception as e:
        print(f"Error downloading macro data: {e}")
        return pd.DataFrame()

def load_sentiment_data(ticker: str | None = None) -> pd.DataFrame:
    """
    Loads daily sentiment data from the local CSV file.
    
    Args:
        ticker (str, optional): The specific stock ticker to retrieve.
        
    Returns:
        pd.DataFrame: DataFrame with 'Sentiment_Score' column indexed by Date.
                      Returns empty DataFrame if file not found or ticker missing.
    """
    path = "daily_sentiment.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
        
    try:
        df = pd.read_csv(path)
        df['Date'] = pd.to_datetime(df['Date'])
        df.set_index('Date', inplace=True)
        
        if ticker and ticker in df.columns:
            # Extract column for this ticker and rename to 'Sentiment_Score'
            sentiment_df = df[[ticker]].rename(columns={ticker: 'Sentiment_Score'})
            return sentiment_df
        elif ticker is None:
            # Return empty if specific ticker not requested, to avoid dangerous broad joins
            return pd.DataFrame()
        else:
            return pd.DataFrame()
    except Exception as e:
        print(f"Error loading sentiment for {ticker}: {e}")
        return pd.DataFrame()

def download_stock(ticker: str, start: str = START_DATE, end: str | None = END_DATE) -> pd.DataFrame:
    """
    Downloads stock data from Yahoo Finance and calculates basic Stochastic indicators.
    
    Args:
        ticker (str): The stock symbol (e.g., 'RELIANCE.NS').
        start (str): Start date.
        end (str): End date.
        
    Returns:
        pd.DataFrame: Stock data with basic cleaning applied.
    """
    df = yf.download(ticker, start=start, end=end, progress=False)
    # Flatten MultiIndex columns if present (common in recent yfinance)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # Stochastic Oscillator
    low_min = df['Low'].rolling(window=14).min()
    high_max = df['High'].rolling(window=14).max()
    df['%K'] = (100 * (df['Close'] - low_min) / (high_max - low_min))
    df['%D'] = df['%K'].rolling(window=3).mean()

    df.dropna(inplace=True)
    return df

def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates and adds a comprehensive suite of technical indicators to the DataFrame.
    
    Indicators Included:
    - Trend: EMA20, EMA50, EMA200, MACD, ADX, CCI
    - Momentum: RSI, Momentum, Stochastic Oscillator (%K, %D computed in download)
    - Volatility: Bollinger Bands, ATR, Volatility (Std Dev of Returns)
    - Volume: Volume Change, OBV Slope, MFI
    
    Args:
        df (pd.DataFrame): Input DataFrame with 'Close', 'High', 'Low', 'Volume'.
        
    Returns:
        pd.DataFrame: DataFrame with added feature columns. Nan values are filled.
    """
    # Using pandas-only simple indicators so no extra dependency is required for core version.
    # For more advanced indicators install 'ta' and use it.
    df = df.copy()
    df["Return"] = df["Close"].pct_change()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
    
    # [STATIONARY] Trend Ratios
    # Distance from EMAs (Percentage)
    df["Dist_EMA20"] = (df["Close"] - df["EMA20"]) / df["EMA20"]
    df["Dist_EMA50"] = (df["Close"] - df["EMA50"]) / df["EMA50"]
    df["Dist_EMA200"] = (df["Close"] - df["EMA200"]) / df["EMA200"]
    
    # EMA Crossover proxy
    df["Rel_EMA20_50"] = (df["EMA20"] - df["EMA50"]) / df["EMA50"]
    
    # Simple RSI approximation
    delta = df["Close"].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(com=13, adjust=False).mean()
    ma_down = down.ewm(com=13, adjust=False).mean()
    rs = ma_up / (ma_down + 1e-9)
    df["RSI"] = 100 - (100 / (1 + rs))
    
    # MACD (fast EMA(12) - slow EMA(26))
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    # Normalize MACD by Close to make it comparable across price levels
    df["MACD"] = ema12 - ema26
    df["MACD_Norm"] = df["MACD"] / df["Close"]
    
    df["Volatility"] = df["Return"].rolling(window=10).std()
    
    # Bollinger Bands
    df["MA20"] = df["Close"].rolling(window=20).mean()
    std20 = df["Close"].rolling(window=20).std()
    df["UpperBB"] = df["MA20"] + (std20 * 2)
    df["LowerBB"] = df["MA20"] - (std20 * 2)
    
    # [STATIONARY] Bollinger Band Position (BBP) & Bandwidth
    # BBP: Where is price relative to bands? 0=Lower, 1=Upper, >1=Breakout
    df["BBP"] = (df["Close"] - df["LowerBB"]) / (df["UpperBB"] - df["LowerBB"] + 1e-9)
    # Bandwidth: Relative width of bands
    df["Bandwidth"] = (df["UpperBB"] - df["LowerBB"]) / df["MA20"]
    
    # ATR (Average True Range)
    high_low = df["High"] - df["Low"]
    high_close = np.abs(df["High"] - df["Close"].shift())
    low_close = np.abs(df["Low"] - df["Close"].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df["ATR"] = true_range.rolling(window=14).mean()
    
    # [STATIONARY] Normalized ATR
    df["ATR_Pct"] = df["ATR"] / df["Close"]
    
    # Momentum (10 days) - Replaced with ROC (Rate of Change)
    # df["Momentum"] = df["Close"] - df["Close"].shift(10)
    df["ROC"] = df["Close"].pct_change(periods=10)

    # [NEW] Volume Change
    # Raw volume is often non-stationary. % Change is better.
    df["Volume_Change"] = df["Volume"].pct_change()

    
    # --- Advanced Indicators ---
    
    # 1. CCI (Commodity Channel Index)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    ma_tp = tp.rolling(window=20).mean()
    md_tp = tp.rolling(window=20).apply(lambda x: np.abs(x - x.mean()).mean())
    df["CCI"] = (tp - ma_tp) / (0.015 * md_tp)
    
    # 2. ADX (Average Directional Index) - Simplified
    # TR is already calculated somewhat in ATR but let's be explicit
    tr = df["ATR"] # ATR is smoothed TR
    # Directional Movement
    up_move = df["High"].diff()
    down_move = df["Low"].diff().apply(lambda x: -x)
    
    pos_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    neg_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    
    # Smooth DM
    pos_dm_s = pos_dm.ewm(alpha=1/14, adjust=False).mean()
    neg_dm_s = neg_dm.ewm(alpha=1/14, adjust=False).mean()
    
    pos_di = 100 * (pos_dm_s / (df["ATR"] + 1e-9))
    neg_di = 100 * (neg_dm_s / (df["ATR"] + 1e-9))
    
    dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-9)
    df["ADX"] = dx.rolling(window=14).mean()

    # 3. OBV (On-Balance Volume) Slope
    obv = (np.sign(df["Close"].diff()) * df["Volume"]).fillna(0).cumsum()
    df["OBV_Slope"] = obv.diff(5) # 5-day slope of OBV

    # 4. MFI (Money Flow Index)
    # Typical Price
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    rmf = tp * df["Volume"] # Raw Money Flow
    
    # Split into positive and negative flow
    # If TP > TP_prev -> Positive Flow
    tp_diff = tp.diff()
    pos_mf = np.where(tp_diff > 0, rmf, 0)
    neg_mf = np.where(tp_diff < 0, rmf, 0)
    
    pos_mf_s = pd.Series(pos_mf, index=df.index).rolling(window=14).sum()
    neg_mf_s = pd.Series(neg_mf, index=df.index).rolling(window=14).sum()
    
    mfr = pos_mf_s / (neg_mf_s + 1e-9)
    df["MFI"] = 100 - (100 / (1 + mfr))

    # [NEW] Lag Features for Tree Models
    df["Return_1d"] = df["Return"].shift(1)
    df["Return_3d"] = df["Return"].shift(3)
    df["Return_5d"] = df["Return"].shift(5)

    # Handle infinite readings (e.g. from 0 volume)
    df.replace([np.inf, -np.inf], np.nan, inplace=True)

    # Forward-fill only (no bfill — avoids leaking future values into past rows)
    df.ffill(inplace=True)
    return df

def create_target(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generates the target variable for a 3-class classification problem (SELL, HOLD, BUY).
    
    Logic:
    - Calculates the 3-day future return.
    - Determines a dynamic threshold based on daily volatility (ATR/Close).
    - If Future Return > Threshold -> BUY (2)
    - If Future Return < -Threshold -> SELL (0)
    - Otherwise -> HOLD (1)
    
    Args:
        df (pd.DataFrame): DataFrame containing 'Close' and 'ATR'.
        
    Returns:
        pd.DataFrame: DataFrame with a new 'Target' column and 'Next_Return'.
    """
    # 3-Class Target: 0=SELL, 1=HOLD, 2=BUY
    # Dynamic Threshold: 0.5 * (ATR / Close) -> 0.5x daily volatility
    df = df.copy()
    
    # Ensure ATR is present (it should be)
    if "ATR" not in df.columns:
        # Fallback if ATR is missing (though add_technical_indicators adds it)
        high_low = df["High"] - df["Low"]
        high_close = np.abs(df["High"] - df["Close"].shift())
        low_close = np.abs(df["Low"] - df["Close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        df["ATR"] = true_range.rolling(window=14).mean()
        df["ATR"].ffill(inplace=True)

    # Calculate dynamic threshold (percentage)
    # Volatility % = ATR / Close
    # We require a move > 0.5 * Volatility to call it a Trend
    volatility_pct = df["ATR"] / df["Close"]
    threshold_series = volatility_pct * 0.35
    
    # Clip threshold to reasonable limits (e.g. min 0.3%, max 3%) to avoid craziness
    threshold_series = threshold_series.clip(lower=0.003, upper=0.03)

    # Calculate future return (3-day horizon)
    df["Next_Return"] = df["Close"].shift(-3) / df["Close"] - 1
    
    conditions = [
        (df["Next_Return"] < -threshold_series), # SELL
        (df["Next_Return"] > threshold_series)   # BUY
    ]
    choices = [0, 2]
    # Default is 1 (HOLD)
    
    df["Target"] = np.select(conditions, choices, default=1)
    df.dropna(inplace=True)
    return df

def create_sequences(features, target, window: int = WINDOW) -> tuple:
    """
    Creates temporal sequences for LSTM training.
    
    Args:
        features (np.ndarray): Scaled feature matrix (N, F).
        target (np.ndarray): Target vector (N,).
        window (int): Lookback window size (default: 30).
        
    Returns:
        tuple: (X, y)
            X (np.ndarray): 3D array of shape (N-window, window, F).
            y (np.ndarray): 1D array of shape (N-window,).
    """
    X, y = [], []
    for i in range(window, len(features)):
        X.append(features[i-window:i])
        y.append(target[i])
    X = np.array(X)
    y = np.array(y)
    return X, y

def time_based_split(X: np.ndarray, y: np.ndarray, test_ratio: float = 0.2, meta_ratio: float = 0.2) -> tuple:
    """
    Splits data into Train, Meta-Train, and Test sets based on time.
    
    Args:
        X (np.ndarray): Feature matrix.
        y (np.ndarray): Target vector.
        test_ratio (float): Proportion for Test set (default 0.2).
        meta_ratio (float): Proportion for Meta-Train set (default 0.2).
        
    Returns:
        tuple: (X_train, X_meta, X_test, y_train, y_meta, y_test)
    """
    n = len(X)
    test_split = int(n * (1 - test_ratio))
    meta_split = int(n * (1 - test_ratio - meta_ratio))
    
    # Train: 0 to meta_split
    # Meta: meta_split to test_split
    # Test: test_split to end
    
    X_train = X[:meta_split]
    y_train = y[:meta_split]
    
    X_meta = X[meta_split:test_split]
    y_meta = y[meta_split:test_split]
    
    X_test = X[test_split:]
    y_test = y[test_split:]
    
    return X_train, X_meta, X_test, y_train, y_meta, y_test

def tree_features(X: np.ndarray) -> np.ndarray:
    """Last timestep plus window mean — gives trees more sequence context."""
    last = X[:, -1, :]
    mean = X.mean(axis=1)
    return np.hstack([last, mean])

def stacker_features(p_rf, p_gb, p_xgb, p_lstm) -> np.ndarray:
    """Features for the learned stacking classifier."""
    p_avg = (p_rf + p_gb + p_xgb + p_lstm) / 4.0
    conf = np.max(p_avg, axis=1, keepdims=True)
    ent = entropy(p_avg, axis=1).reshape(-1, 1)
    return np.column_stack([p_rf, p_gb, p_xgb, p_lstm, conf, ent])

def meta_filter_features(p_prob, X_last) -> np.ndarray:
    conf = np.max(p_prob, axis=1, keepdims=True)
    ent = entropy(p_prob, axis=1).reshape(-1, 1)
    return np.column_stack((p_prob, conf, ent, X_last))

def sample_weights_from_counts(y: np.ndarray) -> np.ndarray:
    counts = Counter(y)
    total = len(y)
    weight_map = {k: total / (len(counts) * v) for k, v in counts.items()}
    return np.array([weight_map[yi] for yi in y])

def tune_meta_threshold(confidence: np.ndarray, y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Pick confidence threshold that maximizes accuracy on the meta set."""
    best_t, best_acc = 0.5, 0.0
    for t in np.arange(0.45, 0.86, 0.05):
        mask = confidence >= t
        if mask.sum() < 30:
            continue
        acc = accuracy_score(y_true[mask], y_pred[mask])
        if acc > best_acc:
            best_acc, best_t = acc, float(t)
    return best_t

def predict_ensemble_probs(
    rf, gb, xgb_base, lstm, stacker, X_seq: np.ndarray
) -> np.ndarray:
    """Stacked class probabilities; falls back to uniform average if no stacker."""
    X_last = tree_features(X_seq)
    p_rf = rf.predict_proba(X_last)
    p_gb = gb.predict_proba(X_last)
    p_xgb = xgb_base.predict_proba(X_last)
    p_lstm = lstm.predict(X_seq, verbose=0)
    X_st = stacker_features(p_rf, p_gb, p_xgb, p_lstm)
    if stacker is not None:
        return stacker.predict_proba(X_st)
    return (p_rf + p_gb + p_xgb + p_lstm) / 4.0

def build_lstm_model(input_shape: tuple) -> Model:
    """
    Constructs a Bidirectional LSTM model with Dropout and Batch Normalization.
    
    Args:
        input_shape (tuple): Shape of the input data (window_size, num_features).
        
    Returns:
        keras.models.Model: Compiled Keras model.
    """
    inputs = Input(shape=input_shape)
    
    # 1. GRU Layer 1
    x = Bidirectional(LSTM(64, return_sequences=True))(inputs)
    x = Dropout(0.3)(x)
    x = BatchNormalization()(x)
    
    # 2. GRU Layer 2
    x = LSTM(32, return_sequences=False)(x)
    x = Dropout(0.3)(x)
    x = BatchNormalization()(x)
    
    # 3. Dense Head
    x = Dense(32, activation="relu")(x)
    x = Dropout(0.2)(x)
    
    # Output: 3 classes
    outputs = Dense(3, activation="softmax")(x)
    
    model = Model(inputs=inputs, outputs=outputs)
    model.compile(optimizer=Adam(learning_rate=0.001), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model

# 4. Main loop: for each stock, prepare data -> train -> evaluate -> save
def train_single_model(ticker: str, force_rfe: bool = False) -> dict | None:
    """
    End-to-end training pipeline with Meta-Labeling.
    """
    print(f"\n===== Processing {ticker} =====")
    df = download_stock(ticker)
    if df.shape[0] < WINDOW + 100:
        print(f"Not enough data for {ticker}. Skipping.")
        return None

    df = add_technical_indicators(df)
    
    # Merge Macro Data
    macro = download_macro_data(start=pd.DatetimeIndex(df.index)[0].strftime('%Y-%m-%d'), end=pd.DatetimeIndex(df.index)[-1].strftime('%Y-%m-%d'))
    if not macro.empty:
        df = df.join(macro)
        df.ffill(inplace=True)
        df.fillna(0, inplace=True)
    
    # Merge Sentiment Data
    sentiment = load_sentiment_data(ticker)
    if not sentiment.empty:
        df = df.join(sentiment, how='left')
        df["Sentiment_Score"].fillna(0.0, inplace=True)
    else:
        df["Sentiment_Score"] = 0.0
    
    # Integrity check (ffill only — no backward fill before split)
    df.ffill(inplace=True)
    df.replace([np.inf, -np.inf], 0, inplace=True)

    df = create_target(df)

    feature_cols = FEATURE_COLS
    features = df[feature_cols].values
    target = df["Target"].values

    # Create sequences
    X_all, y_all = create_sequences(features, target, window=WINDOW)
    print(f"Total sequences: {len(X_all)}")

    # Time-based split (Train / Meta / Test)
    X_train, X_meta, X_test, y_train, y_meta, y_test = time_based_split(X_all, y_all)
    print(f"Train class distribution: {dict(Counter(y_train))}")
    
    # --- FEATURE SELECTION (RFE) on TRAIN Set ---
    feature_save_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_features.joblib")
    selected_features = []
    selected_indices = []
    
    if os.path.exists(feature_save_path) and not force_rfe:
        try:
            loaded_features = joblib.load(feature_save_path)
            selected_features = [f for f in loaded_features if f in FEATURE_COLS]
            
            # If we filtered out stale features, update the file on disk to prevent future mismatches
            if len(selected_features) != len(loaded_features):
                print(f"Updating feature file (removed {len(loaded_features) - len(selected_features)} stale features)...")
                joblib.dump(selected_features, feature_save_path)
                
            selected_indices = [FEATURE_COLS.index(f) for f in selected_features]
        except:
            selected_features = []
            
    if not selected_features:
        print("Running RFE on Train set...")
        X_train_last_raw = X_train[:, -1, :]
        selector = RFE(RandomForestClassifier(n_estimators=30, random_state=SEED, n_jobs=-1), n_features_to_select=20)
        selector.fit(X_train_last_raw, y_train)
        selected_indices = np.where(selector.support_)[0]
        selected_features = [FEATURE_COLS[i] for i in selected_indices]
        print(f"Selected Features: {selected_features}")
        joblib.dump(selected_features, feature_save_path)
    else:
        print(f"Using {len(selected_features)} Pre-selected Features.")

    # Apply selection
    X_train = X_train[:, :, selected_indices]
    X_meta = X_meta[:, :, selected_indices]
    X_test = X_test[:, :, selected_indices]
    
    # Fit scaler on TRAIN
    nsamples, ntime, nfeat = X_train.shape
    scaler = StandardScaler()
    X_train_2d = X_train.reshape(-1, nfeat)
    scaler.fit(X_train_2d)
    
    # Transform all
    X_train = scaler.transform(X_train_2d).reshape(nsamples, ntime, nfeat)
    X_meta = scaler.transform(X_meta.reshape(-1, nfeat)).reshape(X_meta.shape)
    X_test = scaler.transform(X_test.reshape(-1, nfeat)).reshape(X_test.shape)

    joblib.dump(scaler, os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_scaler.save"))

    # Class weights
    class_counts = Counter(y_train)
    total = len(y_train)
    class_weight = {k: total / (3 * v) for k, v in class_counts.items()}
    
    # --- TRAIN BASE MODELS (Train Set) ---
    X_train_tree = tree_features(X_train)
    sw = sample_weights_from_counts(y_train)
    
    print("Training Base Models...")
    rf = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=SEED, class_weight="balanced", n_jobs=-1)
    rf.fit(X_train_tree, y_train)
    
    gb = GradientBoostingClassifier(n_estimators=100, learning_rate=0.05, max_depth=3, random_state=SEED)
    gb.fit(X_train_tree, y_train, sample_weight=sw)
    
    xgb_base = XGBClassifier(n_estimators=100, learning_rate=0.05, max_depth=4, random_state=SEED, objective='multi:softprob', num_class=3)
    xgb_base.fit(X_train_tree, y_train, sample_weight=sw)
    
    inputs = Input(shape=(X_train.shape[1], X_train.shape[2]))
    x = Bidirectional(LSTM(64, return_sequences=True))(inputs)
    x = BatchNormalization()(x)
    x = Dropout(0.3)(x)
    x = LSTM(32, return_sequences=False)(x)
    x = BatchNormalization()(x)
    x = Dropout(0.3)(x)
    x = Dense(32, activation="relu")(x)
    outputs = Dense(3, activation="softmax")(x)
    lstm = Model(inputs=inputs, outputs=outputs)
    lstm.compile(optimizer=Adam(learning_rate=0.001), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    
    callbacks = [
        EarlyStopping(monitor="val_loss", patience=7, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=3, min_lr=1e-5),
    ]
    lstm.fit(X_train, y_train, validation_split=0.1, epochs=50, batch_size=32, callbacks=callbacks, verbose=0, class_weight=class_weight)
    
    # Save Base Models
    ticker_key = ticker.replace('.', '_')
    joblib.dump(rf, os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib"))
    joblib.dump(gb, os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib"))
    joblib.dump(xgb_base, os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib"))
    lstm.save(os.path.join(RESULTS_DIR, f"{ticker_key}_best_model.keras"))
    
    # --- STACKER (Meta Set) ---
    print("Training stacking classifier...")
    X_meta_tree = tree_features(X_meta)
    p_rf = rf.predict_proba(X_meta_tree)
    p_gb = gb.predict_proba(X_meta_tree)
    p_xgb = xgb_base.predict_proba(X_meta_tree)
    p_lstm = lstm.predict(X_meta, verbose=0)
    X_st_train = stacker_features(p_rf, p_gb, p_xgb, p_lstm)
    stacker = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=SEED)
    stacker.fit(X_st_train, y_meta)
    joblib.dump(stacker, os.path.join(RESULTS_DIR, f"{ticker_key}_stacker.joblib"))
    y_meta_stacked = stacker.predict(X_st_train)
    print(f"Meta-set stacked acc: {accuracy_score(y_meta, y_meta_stacked):.4f}")
    
    # --- META-MODEL (confidence filter) ---
    print("Training meta-model (confidence filter)...")
    p_meta = stacker.predict_proba(X_st_train)
    y_meta_pred = np.argmax(p_meta, axis=1)
    X_meta_input = meta_filter_features(p_meta, X_meta[:, -1, :])
    y_meta_target = (y_meta_pred == y_meta).astype(int)
    print(f"Meta-target balance (1=correct): {np.mean(y_meta_target):.4f}")
    
    meta_model = XGBClassifier(n_estimators=80, max_depth=4, learning_rate=0.05, eval_metric='logloss', random_state=SEED)
    meta_model.fit(X_meta_input, y_meta_target)
    joblib.dump(meta_model, os.path.join(RESULTS_DIR, f"{ticker_key}_meta.joblib"))
    
    meta_conf_train = meta_model.predict_proba(X_meta_input)[:, 1]
    meta_threshold = tune_meta_threshold(meta_conf_train, y_meta, y_meta_pred)
    joblib.dump(meta_threshold, os.path.join(RESULTS_DIR, f"{ticker_key}_meta_threshold.joblib"))
    print(f"Tuned meta confidence threshold: {meta_threshold:.2f}")
    
    # --- FINAL EVALUATION (Test Set) ---
    print("Evaluating on test set...")
    t_prob = predict_ensemble_probs(rf, gb, xgb_base, lstm, stacker, X_test)
    y_test_pred = np.argmax(t_prob, axis=1)
    
    X_test_meta = meta_filter_features(t_prob, X_test[:, -1, :])
    meta_confidence = meta_model.predict_proba(X_test_meta)[:, 1]
    
    acc = accuracy_score(y_test, y_test_pred)
    prec = precision_score(y_test, y_test_pred, average='weighted', zero_division=0)
    f1 = f1_score(y_test, y_test_pred, average='macro', zero_division=0)
    cm = confusion_matrix(y_test, y_test_pred)
    print(f"Confusion matrix (rows=true SELL/HOLD/BUY, cols=pred):\n{cm}")
    print(f"Unfiltered: Acc={acc:.4f}, Prec={prec:.4f}, Macro-F1={f1:.4f}")
    
    mask = meta_confidence >= meta_threshold
    n_trades = int(np.sum(mask))
    if n_trades > 0:
        acc_filt = accuracy_score(y_test[mask], y_test_pred[mask])
        prec_filt = precision_score(y_test[mask], y_test_pred[mask], average='weighted', zero_division=0)
        f1_filt = f1_score(y_test[mask], y_test_pred[mask], average='macro', zero_division=0)
        print(f"Filtered (Conf>={meta_threshold:.2f}): Trades={n_trades}/{len(y_test)}, Acc={acc_filt:.4f}, Prec={prec_filt:.4f}, Macro-F1={f1_filt:.4f}")
    else:
        acc_filt, prec_filt, f1_filt = 0.0, 0.0, 0.0
        print(f"Filtered (Conf>={meta_threshold:.2f}): no samples met criteria.")
    
    return {
        "ticker": ticker,
        "base_acc": acc,
        "base_prec": prec,
        "base_f1": f1,
        "filt_acc": acc_filt,
        "filt_prec": prec_filt,
        "filt_f1": f1_filt,
        "meta_threshold": meta_threshold,
        "trades": n_trades,
        "total_test": len(y_test),
    }

def train_models():
    summary = []
    for ticker in STOCKS:
        meta = train_single_model(ticker)
        if meta:
            summary.append(meta)

    # 5. Summary table
    if summary:
        summary_df = pd.DataFrame(summary)
        print("\n=== Summary of trained models ===")
        print(summary_df)
        summary_df.to_csv(os.path.join(RESULTS_DIR, "training_summary.csv"), index=False)

def backtest_model(ticker, model, scaler, window=WINDOW, days=365, stop_loss=0.01, take_profit=0.02):
    """
    Simulate trading over the last 'days' using the trained model.
    Strategy:
        - Predict next day's movement.
        - If Prob > 0.5 (UP) -> Buy/Hold.
        - Risk Management: 
            - If intraday LOW drops below (Entry * (1 - SL)) -> Stop Loss Exit.
            - If intraday HIGH goes above (Entry * (1 + TP)) -> Take Profit Exit.
        - If Prob <= 0.5 (DOWN) -> Sell/Cash (Exit at Close).
    Returns: DataFrame with signals and returns.
    """
    df = download_stock(ticker, start=str((pd.to_datetime(END_DATE) - pd.Timedelta(days=days*2)).date()), end=END_DATE)
    df = add_technical_indicators(df)
    
    # [NEW] Merge Macro Data for Backtest
    macro = download_macro_data(start=pd.DatetimeIndex(df.index)[0].strftime('%Y-%m-%d'), end=pd.DatetimeIndex(df.index)[-1].strftime('%Y-%m-%d'))
    if not macro.empty:
        df = df.join(macro)
        df.ffill(inplace=True)
        df.fillna(0, inplace=True)
    else:
        df["Nifty_Return"] = 0.0
        df["USD_Change"] = 0.0
        df["Gold_Change"] = 0.0
        df["Oil_Change"] = 0.0

    # [NEW] Merge Sentiment Data for Backtest
    sentiment = load_sentiment_data(ticker)
    if not sentiment.empty:
        df = df.join(sentiment, how='left')
        df["Sentiment_Score"].fillna(0.0, inplace=True)
    else:
        df["Sentiment_Score"] = 0.0

    df = create_target(df)
    
    # We need at least 'days' + 'window' data
    if len(df) < window + 10:
        return None

    # Take the last 'days' + 'window' for simulation
    sim_df = df.tail(days + window).copy()
    
    # Load selected features
    feature_save_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_features.joblib")
    if os.path.exists(feature_save_path):
        active_features = joblib.load(feature_save_path)
    else:
        # Fallback if no selection file
        active_features = FEATURE_COLS
        
    features = sim_df[active_features].values
    
    # Generate predictions
    # Batch prediction for speed
    X_seq, _ = create_sequences(features, np.zeros(len(features)), window=window)
    
    if len(X_seq) == 0:
        return None

    # Scale
    nsamples, ntime, nfeat = X_seq.shape
    X_seq_2d = X_seq.reshape(-1, nfeat)
    X_seq_scaled = scaler.transform(X_seq_2d).reshape(nsamples, ntime, nfeat)
    
    ticker_key = ticker.replace('.', '_')
    rf_path = os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib")
    gb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib")
    xgb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib")
    stacker_path = os.path.join(RESULTS_DIR, f"{ticker_key}_stacker.joblib")
    
    if os.path.exists(rf_path) and os.path.exists(gb_path) and os.path.exists(xgb_path):
        print(f"Using stacked ensemble for {ticker}...")
        rf = joblib.load(rf_path)
        gb = joblib.load(gb_path)
        xgb = joblib.load(xgb_path)
        stacker = joblib.load(stacker_path) if os.path.exists(stacker_path) else None
        y_probs = predict_ensemble_probs(rf, gb, xgb, model, stacker, X_seq_scaled)
    else:
        y_probs = model.predict(X_seq_scaled, verbose=0)
    
    start_idx = window
    dates = sim_df.index[start_idx:]
    
    # Slice to match length
    min_len = min(len(dates), len(y_probs))
    dates = dates[:min_len]
    y_probs = y_probs[:min_len]

    # Pre-fetch price arrays for speed
    opens = sim_df["Open"].values[start_idx : start_idx + min_len]
    closes = sim_df["Close"].values[start_idx : start_idx + min_len]
    highs = sim_df["High"].values[start_idx : start_idx + min_len]
    lows = sim_df["Low"].values[start_idx : start_idx + min_len]
    returns = sim_df["Return"].values[start_idx : start_idx + min_len]
    
    capital = 1.0
    strategy_curve = [1.0]
    market_curve = [1.0]
    
    signals_list = []
    
    strategy_daily_returns = []
    
    for i in range(min_len):
        today_open = opens[i]
        today_close = closes[i]
        today_high = highs[i]
        today_low = lows[i]
        today_ret = returns[i]
        
        # Signal: 2 = BUY
        # Use argmax to get class
        # probs[i] is [prob_sell, prob_hold, prob_buy]
        best_class = np.argmax(y_probs[i])
        
        signal = 1 if best_class == 2 else 0
        signals_list.append(signal)
        
        daily_strat_ret = 0.0
        
        if signal == 1:
            entry = today_open
            sl_price = entry * (1 - stop_loss)
            tp_price = entry * (1 + take_profit)
            
            # Conservative check: Check SL first
            if today_low <= sl_price:
                # Stopped out
                trade_ret = -stop_loss
            elif today_high >= tp_price:
                # Profit taken
                trade_ret = take_profit
            else:
                # Exit at Close
                trade_ret = (today_close - entry) / entry
            
            capital = capital * (1 + trade_ret)
            daily_strat_ret = trade_ret
        
        strategy_curve.append(capital)
        market_curve.append(float(market_curve[-1] * (1 + today_ret)))
        strategy_daily_returns.append(daily_strat_ret)

    result_df = pd.DataFrame({
        "Cum_Market_Return": market_curve[1:], 
        "Cum_Strategy_Return": strategy_curve[1:],
        "Return": returns[:min_len], # Market returns
        "Strategy_Daily_Return": strategy_daily_returns,
        "Signal": signals_list
    }, index=dates)
    
    return result_df

if __name__ == "__main__":
    train_models()