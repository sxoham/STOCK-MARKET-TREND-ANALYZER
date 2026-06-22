import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
from flask import Flask, render_template, jsonify, request, send_from_directory
import sqlite3
import json
import datetime
import pandas as pd
import numpy as np
import joblib
from keras.models import load_model
import tensorflow as tf
tf.get_logger().setLevel('ERROR')
import main
import sentiment as sentiment_module

app = Flask(__name__)

# Config
DB_FILE = 'users.db'
MODEL_DB_FILE = 'model_logs.db'
RESULTS_DIR = main.RESULTS_DIR
STOCKS = main.STOCKS

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_model_db_connection():
    conn = sqlite3.connect(MODEL_DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    return render_template('index.html')

# --- API Endpoints ---

import yfinance as yf

def resolve_and_validate_ticker(ticker):
    # 1. Try to download a tiny slice of data to check if ticker is directly valid
    try:
        df = yf.download(ticker, period="5d", progress=False)
        if not df.empty and 'Close' in df.columns:
            return ticker
    except:
        pass
    
    # 2. If directly downloading failed or returned empty, try searching Yahoo Finance
    try:
        search = yf.Search(ticker)
        if search.quotes:
            best_symbol = search.quotes[0]['symbol']
            # Double check if we can download the resolved symbol
            df = yf.download(best_symbol, period="5d", progress=False)
            if not df.empty and 'Close' in df.columns:
                return best_symbol
    except:
        pass
        
    return None

@app.route('/api/stocks')
def get_stocks():
    # Return local STOCKS plus any other trained tickers
    results = list(STOCKS)
    trained_tickers = []
    if os.path.exists(RESULTS_DIR):
        for filename in os.listdir(RESULTS_DIR):
            if filename.endswith('_best_model.keras') or filename.endswith('_final_model.keras'):
                name = filename.replace('_best_model.keras', '').replace('_final_model.keras', '')
                if name.endswith('_NS'):
                    ticker = name[:-3] + '.NS'
                elif name.endswith('_DE'):
                    ticker = name[:-3] + '.DE'
                elif name.endswith('_BO'):
                    ticker = name[:-3] + '.BO'
                else:
                    ticker = name
                trained_tickers.append(ticker)
    for ticker in trained_tickers:
        if ticker not in results:
            results.append(ticker)
    return jsonify(results)

@app.route('/api/lookup')
def lookup_stock():
    query = request.args.get('q', '').upper()
    if not query:
        return jsonify([])
    
    # 1. Local STOCKS filtration
    results = [
        {"symbol": s, "shortname": s.split('.')[0], "exchange": "NSE"} 
        for s in STOCKS if query in s
    ]
    
    # 2. Add other trained models in RESULTS_DIR
    trained_tickers = []
    if os.path.exists(RESULTS_DIR):
        for filename in os.listdir(RESULTS_DIR):
            if filename.endswith('_best_model.keras') or filename.endswith('_final_model.keras'):
                name = filename.replace('_best_model.keras', '').replace('_final_model.keras', '')
                if name.endswith('_NS'):
                    ticker = name[:-3] + '.NS'
                elif name.endswith('_DE'):
                    ticker = name[:-3] + '.DE'
                elif name.endswith('_BO'):
                    ticker = name[:-3] + '.BO'
                else:
                    ticker = name
                trained_tickers.append(ticker)
                
    for s in trained_tickers:
        if query in s.upper():
            # Avoid duplicate
            if not any(r['symbol'] == s for r in results):
                results.append({"symbol": s, "shortname": s.split('.')[0], "exchange": "US/Other"})

    # 3. If local/trained results are few, query yfinance Search
    if len(results) < 5:
        try:
            search = yf.Search(query)
            for quote in search.quotes:
                symbol = quote.get('symbol')
                if symbol:
                    # Skip duplicate
                    if any(r['symbol'] == symbol for r in results):
                        continue
                    shortname = quote.get('shortname') or quote.get('longname') or symbol
                    exchange = quote.get('exchDisp') or quote.get('exchange') or "Yahoo"
                    results.append({
                        "symbol": symbol,
                        "shortname": shortname,
                        "exchange": exchange
                    })
        except Exception as e:
            print(f"Yahoo Search error: {e}")
            
    return jsonify(results[:10])

@app.route('/api/get_data/<email>')
def get_user_data(email):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    
    if user:
        try:
            data = json.loads(user['data'])
            return jsonify({"status": "success", "data": data})
        except:
            return jsonify({"status": "error", "message": "Corrupt data"})
    else:
        return jsonify({"status": "game_start", "message": "User not found"})

@app.route('/api/save_data', methods=['POST'])
def save_user_data():
    try:
        req_data = request.get_json()
        email = req_data.get('email')
        data = req_data.get('data')
        
        if not email or not data:
            return jsonify({"status": "error", "message": "Missing email or data"}), 400
            
        conn = get_db_connection()
        # Upsert
        conn.execute('''
            INSERT INTO users (email, data, is_verified, subscription_tier) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET data=excluded.data
        ''', (email, json.dumps(data), 0, 'free'))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/delete_data', methods=['POST'])
def delete_user_data():
    req_data = request.get_json()
    email = req_data.get('email')
    
    if not email:
        return jsonify({"status": "error", "message": "Missing email"}), 400
        
    conn = get_db_connection()
    conn.execute('DELETE FROM users WHERE email = ?', (email,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/sentiment/<ticker>')
def get_sentiment(ticker):
    try:
        result = sentiment_module.get_news_sentiment(ticker)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "label": "Neutral", "score": 0, "headlines": []})

@app.route('/api/predict/<ticker>')
def get_prediction(ticker):
    resolved_ticker = resolve_and_validate_ticker(ticker)
    if not resolved_ticker:
        return jsonify({"error": f"Ticker symbol '{ticker}' not found on Yahoo Finance"}), 400
    ticker = resolved_ticker
        
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    
    # 1. Check DB for existing prediction
    conn = get_model_db_connection()
    row = conn.execute('SELECT prediction, probability FROM predictions WHERE ticker = ? AND date = ?', (ticker, today_str)).fetchone()
    conn.close()
    
    prediction = None
    probability = 0
    
    if row:
        prediction = row['prediction']
        probability = row['probability']
    else:
        # 2. Generate on-the-fly if missing
        try:
            prediction, probability = generate_live_prediction(ticker)
        except Exception as e:
            print(f"Prediction error for {ticker}: {e}")
            prediction = "NEUTRAL"
            probability = 0.5
            
    # 3. Get History for Charts
    try:
        # Download last 1 year for charts
        end_date = datetime.date.today().strftime("%Y-%m-%d")
        start_date = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        df = main.download_stock(ticker, start=start_date, end=end_date)
        df = main.add_technical_indicators(df)
        
        # Format for JSON
        dates = pd.DatetimeIndex(df.index).strftime('%Y-%m-%d').tolist()
        history = {
            "dates": dates,
            "open": df["Open"].tolist(),
            "high": df["High"].tolist(),
            "low": df["Low"].tolist(),
            "close": df["Close"].tolist(),
            "volume": df["Volume"].tolist(),
            "rsi": df["RSI"].fillna(0).tolist(),
            "macd": df["MACD"].fillna(0).tolist(),
            "ema50": df["EMA50"].fillna(0).tolist(),
            "ema200": df["EMA200"].fillna(0).tolist(),
            "stoch_k": df["%K"].fillna(0).tolist(),
            "stoch_d": df["%D"].fillna(0).tolist()
        }
        
        # Technical Analysis Score (simple aggregate)
        # Using last row values
        last = df.iloc[-1]
        tech_score = 0
        if last["RSI"] > 70: tech_score -= 1
        elif last["RSI"] < 30: tech_score += 1
        if last["MACD"] > 0: tech_score += 1
        else: tech_score -= 1
        if last["Close"] > last["EMA50"]: tech_score += 1
        else: tech_score -= 1
        if last["Close"] > last["EMA200"]: tech_score += 1
        else: tech_score -= 1
        
        rating = "NEUTRAL"
        if tech_score >= 2: rating = "BUY"
        if tech_score >= 3: rating = "STRONG BUY"
        if tech_score <= -2: rating = "SELL"
        if tech_score <= -3: rating = "STRONG SELL"
        
        technical_analysis = {
            "score": tech_score,
            "rating": rating
        }
        
    except Exception as e:
        print(f"History error: {e}")
        history = {}
        technical_analysis = {"score": 0, "rating": "NEUTRAL"}
        
    return jsonify({
        "ticker": ticker,
        "prediction": prediction,
        "probability": probability,
        "history": history,
        "technical_analysis": technical_analysis
    })

def generate_live_prediction(ticker):
    # Logic adapted from make_daily_predictions.py
    # Load Model & Assets
    model_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_best_model.keras")
    if not os.path.exists(model_path):
         model_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_final_model.keras")
    
    scaler_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_scaler.save")
    feature_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_features.joblib")
    
    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        return "NEUTRAL", 0.0
        
    model = load_model(model_path)
    scaler = joblib.load(scaler_path)
    
    if os.path.exists(feature_path):
        active_features = joblib.load(feature_path)
    else:
        active_features = main.FEATURE_COLS

    # Get Data (enough for window)
    start_date = (datetime.date.today() - datetime.timedelta(days=365)).strftime('%Y-%m-%d')
    df = main.download_stock(ticker, start=start_date, end=None)
    
    if len(df) < main.WINDOW + 50:
         return "NEUTRAL", 0.0
         
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
        
    features = df[active_features].tail(main.WINDOW).values
    if len(features) < main.WINDOW:
        return "NEUTRAL", 0.0
        
    features_scaled = scaler.transform(features)
    X_input = features_scaled.reshape(1, main.WINDOW, len(active_features))
    
    ticker_key = ticker.replace('.', '_')
    rf_path = os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib")
    gb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib")
    xgb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib")
    stacker_path = os.path.join(RESULTS_DIR, f"{ticker_key}_stacker.joblib")
    if os.path.exists(rf_path) and os.path.exists(gb_path) and os.path.exists(xgb_path):
        rf = joblib.load(rf_path)
        gb = joblib.load(gb_path)
        xgb = joblib.load(xgb_path)
        stacker = joblib.load(stacker_path) if os.path.exists(stacker_path) else None
        probs = main.predict_ensemble_probs(rf, gb, xgb, model, stacker, X_input)[0]
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
    return prediction, prob

@app.route('/api/backtest/<ticker>')
def backtest_endpoint(ticker):
    try:
        # Load model and scaler
        model_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_best_model.keras")
        if not os.path.exists(model_path):
             model_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_final_model.keras")
        scaler_path = os.path.join(RESULTS_DIR, f"{ticker.replace('.', '_')}_scaler.save")
        
        if not os.path.exists(model_path) or not os.path.exists(scaler_path):
            return jsonify({"error": "Model not trained yet"}), 404
            
        model = load_model(model_path)
        scaler = joblib.load(scaler_path)
        
        result_df = main.backtest_model(ticker, model, scaler, days=365)
        
        if result_df is None or result_df.empty:
            return jsonify({"error": "Not enough data for backtest"}), 400
            
        # Calculate Metrics
        initial = result_df["Cum_Strategy_Return"].iloc[0]
        final = result_df["Cum_Strategy_Return"].iloc[-1]
        total_return = (final - initial) / initial * 100
        
        m_initial = result_df["Cum_Market_Return"].iloc[0]
        m_final = result_df["Cum_Market_Return"].iloc[-1]
        market_return = (m_final - m_initial) / m_initial * 100
        
        wins = result_df[result_df["Strategy_Daily_Return"] > 0]
        total_trades = result_df[result_df["Signal"] != 0] # Only days we held? Or strictly trades?
        # Backtest strategy in main.py holds 1 for BUY/HOLD. 
        # The 'Signal' in result_df seems to be 1 for BUY/HOLD days.
        # Win rate should be positive return days / total active days
        active_days = len(result_df[result_df["Signal"] == 1])
        win_rate = (len(wins) / active_days * 100) if active_days > 0 else 0
        
        return jsonify({
            "metrics": {
                "total_return": total_return,
                "market_return": market_return,
                "win_rate": win_rate
            },
            "chart": {
                "dates": pd.DatetimeIndex(result_df.index).strftime('%Y-%m-%d').tolist(),
                "strategy": result_df["Cum_Strategy_Return"].tolist(),
                "market": result_df["Cum_Market_Return"].tolist()
            }
        })
        
    except Exception as e:
        print(f"Backtest error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Create DB if not exists (users)
    if not os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        conn.execute('''
            CREATE TABLE users (
                email TEXT PRIMARY KEY,
                data TEXT,
                is_verified INTEGER DEFAULT 0,
                subscription_tier TEXT DEFAULT 'free',
                subscription_expiry DATETIME,
                start_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        ''')
        conn.close()
        
    app.run(debug=True, port=5000)