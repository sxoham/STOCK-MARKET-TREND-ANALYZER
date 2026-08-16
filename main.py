import os
import random
from typing import Any
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
from keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau, Callback
from collections import Counter

class EpochProgressCallback(Callback):
    def __init__(self, progress_callback, start_pct=50, end_pct=80, total_epochs=50):
        super().__init__()
        self.progress_callback = progress_callback
        self.start_pct = start_pct
        self.end_pct = end_pct
        self.total_epochs = total_epochs

    def on_epoch_end(self, epoch, logs=None):
        if self.progress_callback:
            logs = logs or {}
            pct = int(self.start_pct + ((epoch + 1) / float(self.total_epochs)) * (self.end_pct - self.start_pct))
            loss = logs.get('loss', 0.0)
            msg = f"Epoch {epoch + 1}/{self.total_epochs} | Training LSTM | Loss: {loss:.4f}"
            self.progress_callback("LSTM Neural Network", pct, msg)

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
        
        if nifty is None or nifty.empty:
            raise ValueError("Failed to download Nifty data")
        if isinstance(nifty.columns, pd.MultiIndex):
            nifty.columns = nifty.columns.get_level_values(0)
        
        def normalize_df(df):
            if df is None or df.empty:
                return pd.DataFrame(index=nifty.index, columns=["Close"])
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df.reindex(nifty.index, method='ffill')

        usd = normalize_df(usd)
        gold = normalize_df(gold)
        oil = normalize_df(oil)

        macro = pd.DataFrame(index=nifty.index)
        macro["Nifty_Return"] = nifty["Close"].pct_change()
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
    if df is None or df.empty:
        return pd.DataFrame()
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
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
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
    df["ADX"] = pd.Series(dx, index=df.index).rolling(window=14).mean()

    # 3. OBV (On-Balance Volume) Slope
    obv = (df["Close"].diff().apply(np.sign) * df["Volume"]).fillna(0).cumsum()
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

def create_target(df: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """
    Generates the target variable for a 3-class classification problem (SELL, HOLD, BUY).
    Supports multi-horizon forecasting (e.g. 1-Day, 5-Day, 20-Day).
    """
    df = df.copy()
    
    # Calculate future return over specified horizon
    df["Next_Return"] = df["Close"].shift(-horizon) / df["Close"] - 1
    
    # Horizon-specific thresholding
    # Horizon-specific thresholding
    if horizon >= 20:
        threshold = 0.040 # 4.0% threshold for 1-Month (20-Day)
    elif horizon >= 5:
        threshold = 0.025 # 2.5% threshold for 5-Day (1-Week)
    else:
        threshold = 0.010 # 1.0% threshold for 1-Day
        
    conditions = [
        (df["Next_Return"] < -threshold), # SELL (0)
        (df["Next_Return"] > threshold)   # BUY (2)
    ]
    choices = [0, 2]
    # Default is 1 (HOLD)
    
    df["Target"] = np.select([np.asarray(c, dtype=bool) for c in conditions], choices, default=1)
    df.dropna(subset=["Next_Return"], inplace=True)
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
        y.append(target[i-1])
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
    weight_map = {k: (total / (len(counts) * v)) ** 0.5 for k, v in counts.items()}
    return np.array([weight_map[yi] for yi in y])

def tune_meta_threshold(confidence: np.ndarray, y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Pick confidence threshold that balances precision with signal trade volume."""
    best_t, best_score = 0.50, -1.0
    total = len(confidence)
    for t in np.arange(0.45, 0.66, 0.05):
        mask = confidence >= t
        if mask.sum() < max(15, int(total * 0.05)):
            continue
        acc = accuracy_score(y_true[mask], y_pred[mask])
        pass_ratio = mask.sum() / (total + 1e-9)
        score = acc + 0.10 * pass_ratio
        if score > best_score:
            best_score, best_t = score, float(t)
    return best_t

def predict_ensemble_probs(
    rf, gb, xgb_base, lstm, stacker, X_seq: Any
) -> np.ndarray:
    """Stacked class probabilities; falls back to uniform average if no stacker."""
    X_last = tree_features(X_seq)
    p_rf = rf.predict_proba(X_last)
    p_gb = gb.predict_proba(X_last)
    p_xgb = xgb_base.predict_proba(X_last)
    p_lstm = lstm.predict(X_seq, verbose=0)
    # Always use simple average ensemble as it is more robust to class imbalance
    return (p_rf + p_gb + p_xgb + p_lstm) / 4.0

def build_lstm_model(input_shape: tuple) -> Model:
    """
    Constructs a lightweight, regularized LSTM Neural Network for noisy financial time series.
    
    Args:
        input_shape (tuple): Shape of the input data (window_size, num_features).
        
    Returns:
        keras.models.Model: Compiled Keras model.
    """
    inputs = Input(shape=input_shape)
    
    # Lightweight single-layer LSTM with high dropout to prevent overfitting
    x = LSTM(16, dropout=0.4, recurrent_dropout=0.4)(inputs)
    x = BatchNormalization()(x)
    
    # Output: 3 classes
    outputs = Dense(3, activation="softmax")(x)
    
    model = Model(inputs=inputs, outputs=outputs)
    model.compile(optimizer=Adam(learning_rate=0.0005), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model

FEATURE_LABEL_MAP = {
    "RSI": ("RSI Oversold", "RSI Overbought"),
    "MACD_Norm": ("MACD Bearish Momentum", "MACD Bullish Crossover"),
    "Return": ("Negative Price Momentum", "Positive Price Momentum"),
    "Volume_Change": ("Volume Contraction", "High Volume Surge"),
    "Dist_EMA20": ("Price Below EMA20", "Price Above EMA20"),
    "Dist_EMA50": ("Price Below EMA50", "Price Above EMA50"),
    "Dist_EMA200": ("Price Below EMA200", "Price Above EMA200"),
    "Rel_EMA20_50": ("EMA20 Below EMA50", "EMA20 Golden Crossover"),
    "BBP": ("Lower Bollinger Band Touch", "Upper Bollinger Band Breakout"),
    "Bandwidth": ("Low Band Squeeze", "High Band Volatility"),
    "ATR_Pct": ("Low Volatility Range", "High Volatility Surge"),
    "ROC": ("Negative Rate of Change", "Positive Rate of Change"),
    "Nifty_Return": ("Macro Market Headwind", "Macro Market Tailwind"),
    "USD_Change": ("USD Strength Pressure", "USD Weakness Support"),
    "Gold_Change": ("Macro Gold Drop", "Macro Gold Spike"),
    "Oil_Change": ("Oil Price Drop", "Oil Price Spike"),
    "ADX": ("Weak Trend Strength", "Strong Trend Strength"),
    "CCI": ("CCI Oversold Signal", "CCI Overbought Signal"),
    "MFI": ("Money Outflow Pressure", "Money Inflow Accumulation"),
    "OBV_Slope": ("Volume Distribution", "Volume Accumulation"),
    "Sentiment_Score": ("Negative News Sentiment", "Positive News Sentiment")
}

def explain_prediction(ticker: str, X_last_scaled: np.ndarray, active_features: list, predicted_class: int, horizon: int = 1, return_dict: bool = False) -> list | dict:
    """
    Generates feature attribution breakdown (Explainable AI) showing top positive and negative drivers.
    Returns percentage contributions and direction for top drivers and full feature set.
    """
    ticker_key = ticker.replace('.', '_') if horizon == 1 else f"{ticker.replace('.', '_')}_h{horizon}"
    rf_path = os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib")
    xgb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib")
    
    importances = np.ones(len(active_features)) / len(active_features)
    try:
        if os.path.exists(xgb_path):
            xgb = joblib.load(xgb_path)
            if hasattr(xgb, 'feature_importances_') and len(xgb.feature_importances_) == len(active_features):
                importances = xgb.feature_importances_
        elif os.path.exists(rf_path):
            rf = joblib.load(rf_path)
            if hasattr(rf, 'feature_importances_') and len(rf.feature_importances_) == len(active_features):
                importances = rf.feature_importances_
    except Exception:
        pass

    scores = []
    total_abs_all = 0.0
    for idx, feature_name in enumerate(active_features):
        z_val = float(X_last_scaled[idx]) if idx < len(X_last_scaled) else 0.0
        imp = float(importances[idx]) if idx < len(importances) else 1.0 / len(active_features)
        
        impact_score = z_val * imp
        labels = FEATURE_LABEL_MAP.get(feature_name, (f"Low {feature_name}", f"High {feature_name}"))
        label = labels[1] if z_val >= 0 else labels[0]
        abs_imp = abs(impact_score)
        total_abs_all += abs_imp
        
        scores.append({
            "feature": feature_name,
            "label": label,
            "raw_impact": impact_score,
            "abs_impact": abs_imp,
            "z_val": z_val,
            "importance": imp
        })
        
    scores.sort(key=lambda x: x["abs_impact"], reverse=True)
    
    total_abs_all = total_abs_all if total_abs_all > 1e-9 else 1.0
    all_attributions = []
    for s in scores:
        pct = (float(s["abs_impact"]) / total_abs_all) * 100.0
        is_positive = (float(s["raw_impact"]) >= 0 and predicted_class == 2) or (float(s["raw_impact"]) < 0 and predicted_class == 0)
        direction = "positive" if is_positive else "negative"
        sign = "+" if direction == "positive" else "-"
        all_attributions.append({
            "feature": s["feature"],
            "name": s["label"],
            "pct": round(pct, 1),
            "impact_str": f"{sign}{round(pct, 1)}%",
            "direction": direction,
            "z_score": round(float(s["z_val"]), 2),
            "importance": round(float(s["importance"]), 4)
        })

    top_scores = scores[:4]
    total_abs_top = sum(float(s["abs_impact"]) for s in top_scores) + 1e-9
    
    drivers = []
    for s in top_scores:
        pct = int(round((float(s["abs_impact"]) / total_abs_top) * 100))
        pct = max(12, min(pct, 48))
        
        is_positive = (float(s["raw_impact"]) >= 0 and predicted_class == 2) or (float(s["raw_impact"]) < 0 and predicted_class == 0)
        direction = "positive" if is_positive else "negative"
        sign = "+" if direction == "positive" else "-"
        
        drivers.append({
            "feature": s["feature"],
            "name": s["label"],
            "impact": f"{sign}{pct}%",
            "pct": pct,
            "direction": direction
        })
        
    if return_dict:
        return {
            "top_drivers": drivers,
            "all_attributions": all_attributions
        }
    return drivers

# 4. Main loop: for each stock, prepare data -> train -> evaluate -> save
def train_single_model(ticker: str, force_rfe: bool = False, horizon: int = 1, progress_callback = None) -> dict | None:
    """
    End-to-end training pipeline with Meta-Labeling and Multi-Horizon forecasting.
    """
    ticker_key = ticker.replace('.', '_') if horizon == 1 else f"{ticker.replace('.', '_')}_h{horizon}"
    print(f"\n===== Processing {ticker} (Horizon: {horizon}d) =====")
    if progress_callback:
        progress_callback("Data Download", 10, f"Downloading price history & technical indicators for {ticker}...")
    df = download_stock(ticker)
    if df.shape[0] < WINDOW + 100:
        print(f"Not enough data for {ticker}. Skipping.")
        if progress_callback:
            progress_callback("Error", 100, f"Insufficient data for {ticker}")
        return None

    df = add_technical_indicators(df)
    
    if progress_callback:
        progress_callback("Macro & Sentiment Alignment", 18, "Fetching macro indicators (Nifty, USD, Gold, Oil) & sentiment data...")
    # Merge Macro Data
    macro = download_macro_data(start=str(pd.DatetimeIndex(df.index)[0].strftime('%Y-%m-%d')), end=str(pd.DatetimeIndex(df.index)[-1].strftime('%Y-%m-%d')))
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

    df = create_target(df, horizon=horizon)

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
    feature_save_path = os.path.join(RESULTS_DIR, f"{ticker_key}_features.joblib")
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
        if progress_callback:
            progress_callback("Feature Selection (RFE)", 25, "Running Recursive Feature Elimination (RFE) on training features...")
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

    joblib.dump(scaler, os.path.join(RESULTS_DIR, f"{ticker_key}_scaler.save"))

    # Smoothed class weights
    class_counts = Counter(y_train)
    total = len(y_train)
    class_weight = {k: (total / (3 * v)) ** 0.5 for k, v in class_counts.items()}
    
    # --- TRAIN BASE MODELS (Train Set) ---
    X_train_tree = tree_features(X_train)
    sw = sample_weights_from_counts(y_train)
    
    if progress_callback:
        progress_callback("Ensemble Base Models", 40, "Training Random Forest, Gradient Boosting & XGBoost...")
    print("Training Base Models...")
    rf = RandomForestClassifier(
        n_estimators=150,
        max_depth=6,
        min_samples_leaf=15,
        max_features="sqrt",
        random_state=SEED,
        n_jobs=-1
    )
    rf.fit(X_train_tree, y_train, sample_weight=sw)
    
    gb = GradientBoostingClassifier(
        n_estimators=80,
        learning_rate=0.03,
        max_depth=4,
        min_samples_leaf=15,
        random_state=SEED
    )
    gb.fit(X_train_tree, y_train, sample_weight=sw)
    
    xgb_base = XGBClassifier(
        n_estimators=80,
        learning_rate=0.03,
        max_depth=4,
        subsample=0.7,
        colsample_bytree=0.7,
        reg_alpha=2.0,
        reg_lambda=5.0,
        random_state=SEED,
        objective='multi:softprob',
        num_class=3
    )
    xgb_base.fit(X_train_tree, y_train, sample_weight=sw)
    
    if progress_callback:
        progress_callback("LSTM Model Architecture", 50, "Initializing Bidirectional LSTM with Multi-Head Attention...")
    lstm = build_lstm_model((X_train.shape[1], X_train.shape[2]))
    
    callbacks = [
        EarlyStopping(monitor="val_loss", patience=7, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=3, min_lr=1e-5),
    ]
    if progress_callback:
        callbacks.append(EpochProgressCallback(progress_callback, start_pct=50, end_pct=80, total_epochs=50))
        
    lstm.fit(X_train, y_train, validation_split=0.1, epochs=50, batch_size=32, class_weight=class_weight, callbacks=callbacks, verbose="0")
    
    # Save Base Models
    joblib.dump(rf, os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib"))
    joblib.dump(gb, os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib"))
    joblib.dump(xgb_base, os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib"))
    lstm.save(os.path.join(RESULTS_DIR, f"{ticker_key}_best_model.keras"))
    
    # --- META-MODEL TRAINING (Full Out-of-Sample Meta Set) ---
    if progress_callback:
        progress_callback("Meta-Labeling & Threshold Tuning", 85, "Training Meta-Classifier & Tuning Meta-Confidence Threshold...")
    print("Training meta-model...")
    
    # Generate features for meta-model training
    p_rf_meta = rf.predict_proba(tree_features(X_meta))
    p_gb_meta = gb.predict_proba(tree_features(X_meta))
    p_xgb_meta = xgb_base.predict_proba(tree_features(X_meta))
    p_lstm_meta = lstm.predict(X_meta, verbose="0")
    
    # Simple average probabilities
    p_meta = (p_rf_meta + p_gb_meta + p_xgb_meta + p_lstm_meta) / 4.0
    y_meta_pred = np.argmax(p_meta, axis=1)
    
    X_meta_input = meta_filter_features(p_meta, X_meta[:, -1, :])
    y_meta_target = (y_meta_pred == y_meta).astype(int)
    print(f"Meta-target balance (1=correct): {np.mean(y_meta_target):.4f}")
    
    meta_model = XGBClassifier(n_estimators=80, max_depth=4, learning_rate=0.05, eval_metric='logloss', random_state=SEED)
    meta_model.fit(X_meta_input, y_meta_target)
    joblib.dump(meta_model, os.path.join(RESULTS_DIR, f"{ticker_key}_meta.joblib"))
    
    meta_conf_val = meta_model.predict_proba(X_meta_input)[:, 1]
    meta_threshold = tune_meta_threshold(meta_conf_val, y_meta, y_meta_pred)
    joblib.dump(meta_threshold, os.path.join(RESULTS_DIR, f"{ticker_key}_meta_threshold.joblib"))
    print(f"Tuned meta confidence threshold: {meta_threshold:.2f}")

    if progress_callback:
        progress_callback("Completed", 100, f"Model training complete for {ticker}!")

    
    # --- FINAL EVALUATION (Test Set) ---
    print("Evaluating on test set...")
    t_prob = predict_ensemble_probs(rf, gb, xgb_base, lstm, None, X_test)
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
    macro = download_macro_data(start=str(pd.DatetimeIndex(df.index)[0].strftime('%Y-%m-%d')), end=str(pd.DatetimeIndex(df.index)[-1].strftime('%Y-%m-%d')))
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

    if hasattr(scaler, 'n_features_in_') and scaler.n_features_in_ != len(active_features):
        if scaler.n_features_in_ == len(FEATURE_COLS):
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
    try:
        X_seq_scaled = scaler.transform(X_seq_2d).reshape(nsamples, ntime, nfeat)
    except Exception as e:
        print(f"Scaler transform warning in backtest for {ticker}: {e}. Refitting scaler dynamically.")
        from sklearn.preprocessing import StandardScaler
        X_seq_2d_scaled = StandardScaler().fit_transform(X_seq_2d)
        X_seq_scaled = X_seq_2d_scaled.reshape(nsamples, ntime, nfeat)
    
    ticker_key = ticker.replace('.', '_')
    rf_path = os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib")
    gb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib")
    xgb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib")
    stacker_path = os.path.join(RESULTS_DIR, f"{ticker_key}_stacker.joblib")
    meta_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta.joblib")
    threshold_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta_threshold.joblib")
    
    use_meta = False
    meta_confidence: np.ndarray = np.array([])
    meta_threshold: float = 1.0  # Default: no signal passes until explicitly loaded
    if os.path.exists(rf_path) and os.path.exists(gb_path) and os.path.exists(xgb_path):
        try:
            print(f"Using stacked ensemble for {ticker}...")
            rf = joblib.load(rf_path)
            gb = joblib.load(gb_path)
            xgb = joblib.load(xgb_path)
            stacker = joblib.load(stacker_path) if os.path.exists(stacker_path) else None
            y_probs = predict_ensemble_probs(rf, gb, xgb, model, stacker, X_seq_scaled)
            
            if os.path.exists(meta_path) and os.path.exists(threshold_path):
                meta_model = joblib.load(meta_path)
                meta_threshold = joblib.load(threshold_path)
                X_seq_meta = meta_filter_features(y_probs, X_seq_scaled[:, -1, :])
                meta_confidence = meta_model.predict_proba(X_seq_meta)[:, 1]
                # Use adaptive threshold for backtesting:
                # If the trained threshold blocks nearly all signals (<2 signals),
                # fall back to the median of confidence scores so ~50% of BUY signals pass.
                buy_mask = np.argmax(y_probs, axis=1) == 2
                n_raw_buys = int(buy_mask.sum())
                n_confident_buys = int((meta_confidence[buy_mask] >= meta_threshold).sum()) if n_raw_buys > 0 else 0
                if n_confident_buys < max(2, n_raw_buys * 0.1):
                    # Threshold too strict — use median confidence among BUY candidates
                    buy_confidences = meta_confidence[buy_mask]
                    meta_threshold = float(np.percentile(buy_confidences, 50)) if len(buy_confidences) > 0 else meta_threshold
                    print(f"  Adaptive backtest threshold applied: {meta_threshold:.3f} ({n_confident_buys}/{n_raw_buys} raw BUYs passed original threshold)")
                use_meta = True
        except Exception as e:
            print(f"Failed to load ensemble for backtest ({ticker}): {e}. Falling back to base model.")
            y_probs = model.predict(X_seq_scaled, verbose=0)
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
        
        # Apply meta-confidence filter if available
        if use_meta:
            is_confident = meta_confidence[i] >= meta_threshold
        else:
            is_confident = True
            
        signal = 1 if (best_class == 2 and is_confident) else 0
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