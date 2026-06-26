import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
import sqlite3
import datetime
import pandas as pd
import numpy as np
import joblib
from tensorflow.keras.models import load_model
import tensorflow as tf
tf.get_logger().setLevel('ERROR')
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import main

# Configuration
LOG_DB_FILE = 'model_logs.db'
RESULTS_DIR = main.RESULTS_DIR
STOCKS = main.STOCKS
WINDOW = main.WINDOW

def make_predictions():
    print(f"Starting daily predictions for {len(STOCKS)} stocks...")
    
    # 1. Connect to DB
    conn = sqlite3.connect(LOG_DB_FILE)
    c = conn.cursor()
    
    # Ensure table exists (just in case)
    c.execute('''
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            date TEXT,
            predicted_date TEXT,
            prediction TEXT,
            probability REAL,
            start_price REAL,
            actual_price REAL,
            is_correct INTEGER
        )
    ''')
    
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    
    # Calculate next likely trading day (simple +1 day)
    # Verification script handles exact matching, so precise date here is for reference
    next_day = (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    
    count = 0
    
    for ticker in STOCKS:
        print(f"\nProcessing {ticker}...")
        try:
            # 2. Load Model & Assets
            model_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_best_model.keras")
            if not os.path.exists(model_path):
                 model_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_final_model.keras")
            
            scaler_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_scaler.save")
            feature_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_features.joblib")
            
            if not os.path.exists(model_path) or not os.path.exists(scaler_path):
                print(f"  Model/Scaler not found for {ticker}. Skipping.")
                continue
                
            model = load_model(model_path)
            scaler = joblib.load(scaler_path)
            
            # Load selected features
            if os.path.exists(feature_path):
                active_features = joblib.load(feature_path)
            else:
                active_features = main.FEATURE_COLS

            # 3. Get Data
            # Helper: Download enough data to create 1 sequence (plus buffer for indicators)
            # 1 year should be plenty
            start_date = (datetime.date.today() - datetime.timedelta(days=365)).strftime('%Y-%m-%d')
            df = main.download_stock(ticker, start=start_date, end=None)
            
            if len(df) < WINDOW + 50:
                print(f"  Not enough data for {ticker}.")
                continue
                
            # 4. Preprocess
            df = main.add_technical_indicators(df)
            
            # Macro
            try:
                macro = main.download_macro_data(start=pd.DatetimeIndex(df.index)[0].strftime('%Y-%m-%d'), end=pd.DatetimeIndex(df.index)[-1].strftime('%Y-%m-%d'))
                if not macro.empty:
                    df = df.join(macro)
                    df.ffill(inplace=True)
                    df.fillna(0, inplace=True)
                else:
                    df["Nifty_Return"] = 0.0; df["USD_Change"] = 0.0; df["Gold_Change"] = 0.0; df["Oil_Change"] = 0.0
            except:
                df["Nifty_Return"] = 0.0; df["USD_Change"] = 0.0; df["Gold_Change"] = 0.0; df["Oil_Change"] = 0.0

            # Sentiment
            sentiment = main.load_sentiment_data(ticker)
            if not sentiment.empty:
                df = df.join(sentiment, how='left')
                df["Sentiment_Score"].fillna(0.0, inplace=True)
            else:
                df["Sentiment_Score"] = 0.0
                
            # 5. Create Sequence
            # We need the LAST valid window
            # Ensure we select only the FEATURES the model expects
            try:
                features = df[active_features].tail(WINDOW).values
            except KeyError as ke:
                print(f"  Feature mismatch: {ke}")
                continue
                
            if len(features) < WINDOW:
                print("  Not enough features generated.")
                continue
                
            # Scale
            # The scaler was fit on (N, n_features), so we reshape our (WINDOW, n_features) to (WINDOW, n_features)
            # Wait, standard scaler expects 2D.
            features_scaled = scaler.transform(features)
            
            # Reshape for LSTM: (1, WINDOW, n_features)
            X_input = features_scaled.reshape(1, WINDOW, len(active_features))
            
            # 6. Predict
            ticker_key = ticker.replace('.', '_')
            rf_path = os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib")
            gb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib")
            xgb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib")
            stacker_path = os.path.join(RESULTS_DIR, f"{ticker_key}_stacker.joblib")
            meta_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta.joblib")
            threshold_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta_threshold.joblib")
            
            if os.path.exists(rf_path) and os.path.exists(gb_path) and os.path.exists(xgb_path):
                rf = joblib.load(rf_path)
                gb = joblib.load(gb_path)
                xgb = joblib.load(xgb_path)
                stacker = joblib.load(stacker_path) if os.path.exists(stacker_path) else None
                probs = main.predict_ensemble_probs(rf, gb, xgb, model, stacker, X_input)[0]
                
                if os.path.exists(meta_path) and os.path.exists(threshold_path):
                    meta_model = joblib.load(meta_path)
                    meta_threshold = joblib.load(threshold_path)
                    X_meta_input = main.meta_filter_features(probs.reshape(1, -1), X_input[:, -1, :])
                    meta_confidence = float(meta_model.predict_proba(X_meta_input)[0, 1])
                    
                    best_class = int(np.argmax(probs))
                    if meta_confidence < meta_threshold:
                        best_class = 1
                        prob = meta_confidence
                    else:
                        prob = float(probs[best_class])
                else:
                    best_class = int(np.argmax(probs))
                    prob = float(probs[best_class])
            else:
                probs = model(X_input, training=False).numpy()[0]
                best_class = int(np.argmax(probs))
                prob = float(probs[best_class])
                
            if best_class == 2:
                prediction = "UP"
            elif best_class == 0:
                prediction = "DOWN"
            else:
                prediction = "HOLD"
                
            start_price = float(df.iloc[-1]["Close"])
            
            print(f"  Prediction: {prediction} ({prob:.2%}) @ {start_price:.2f}")
            
            # 7. Insert into DB
            # Check if we already predicted for this ticker & date to avoid duplicates
            c.execute("SELECT id FROM predictions WHERE ticker=? AND date=?", (ticker, today_str))
            existing = c.fetchone()
            
            if existing:
                print("  Updating existing record...")
                c.execute('''
                    UPDATE predictions 
                    SET prediction=?, probability=?, start_price=?, predicted_date=?
                    WHERE id=?
                ''', (prediction, float(prob), start_price, next_day, existing[0]))
            else:
                c.execute('''
                    INSERT INTO predictions (ticker, date, predicted_date, prediction, probability, start_price)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (ticker, today_str, next_day, prediction, float(prob), start_price))
            
            count += 1
            conn.commit()
            
        except Exception as e:
            print(f"  Error processing {ticker}: {e}")
            import traceback
            traceback.print_exc()

        # Clear session to prevent retracing warnings and memory leaks
        tf.keras.backend.clear_session()

    conn.close()
    print(f"\nDone. Generated predictions for {count}/{len(STOCKS)} stocks.")

if __name__ == "__main__":
    make_predictions()
