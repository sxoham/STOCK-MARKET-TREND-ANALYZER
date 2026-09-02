import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
from flask import Flask, render_template, jsonify, request, send_from_directory, redirect, Response, stream_with_context
import queue
import threading
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
    return redirect('/login')

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
        if df is not None and not df.empty and 'Close' in df.columns:
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
            if df is not None and not df.empty and 'Close' in df.columns:
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

@app.route('/db')
def view_database():
    """Interactive visual database viewer for all tables in users.db."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Users Table
    cur.execute("SELECT email, data, is_verified, subscription_tier FROM users")
    users_raw = cur.fetchall()
    users_list = []
    for u in users_raw:
        email = u['email']
        verified = bool(u['is_verified'])
        tier = u['subscription_tier']
        try:
            pdata = json.loads(u['data']) if u['data'] else {}
        except:
            pdata = {}
        portfolio = pdata.get('portfolio', {})
        balance = portfolio.get('balance', 0)
        holdings = portfolio.get('holdings', {})
        profile = portfolio.get('profile', {})
        watchlist = pdata.get('watchlist', [])
        users_list.append({
            'email': email,
            'balance': f"₹{balance:,.2f}" if isinstance(balance, (int, float)) else str(balance),
            'holdings': json.dumps(holdings, indent=2),
            'profile': json.dumps(profile, indent=2),
            'watchlist': ", ".join(watchlist) if watchlist else "None",
            'verified': "Yes" if verified else "No",
            'tier': tier
        })
        
    # 2. Alerts Table
    try:
        cur.execute("SELECT * FROM alerts")
        alerts_list = [dict(row) for row in cur.fetchall()]
    except:
        alerts_list = []
        
    conn.close()
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Database Viewer — TrendAnalyzer</title>
      <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
      <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Inter', sans-serif; background: #050b14; color: #f8fafc; padding: 32px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid #1e293b; }}
        h1 {{ font-size: 1.5rem; font-weight: 700; color: #38bdf8; }}
        .btn-back {{ background: #1e293b; color: #94a3b8; padding: 8px 16px; border-radius: 8px; text-decoration: none; font-size: 0.875rem; font-weight: 600; border: 1px solid #334155; }}
        .btn-back:hover {{ color: #ffffff; background: #334155; }}
        .section-title {{ font-size: 1.1rem; font-weight: 700; color: #f1f5f9; margin: 24px 0 12px; display: flex; align-items: center; gap: 8px; }}
        .badge {{ font-size: 0.75rem; background: rgba(56, 189, 248, 0.15); color: #38bdf8; padding: 2px 8px; border-radius: 9999px; }}
        table {{ width: 100%; border-collapse: collapse; background: #0a1120; border: 1px solid #1e293b; border-radius: 12px; overflow: hidden; margin-bottom: 32px; }}
        th, td {{ padding: 12px 16px; text-align: left; font-size: 0.8125rem; border-bottom: 1px solid #1e293b; }}
        th {{ background: #0f172a; color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em; }}
        tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}
        pre {{ font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #38bdf8; background: #050b14; padding: 6px 10px; border-radius: 6px; border: 1px solid #1e293b; max-width: 320px; white-space: pre-wrap; }}
        .balance-pill {{ font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #4ade80; background: rgba(34, 197, 94, 0.12); padding: 4px 8px; border-radius: 6px; display: inline-block; }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <div>
            <h1>TrendAnalyzer Database Inspector</h1>
            <p style="color: #64748b; font-size: 0.875rem; margin-top: 4px;">File: <code>users.db</code> in project root</p>
          </div>
          <a href="/dashboard" class="btn-back">← Back to Dashboard</a>
        </div>

        <div class="section-title">
          <span>Users & Portfolios</span>
          <span class="badge">{len(users_list)} Users</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Cash Balance</th>
              <th>Stock Holdings</th>
              <th>Profile Metadata</th>
              <th>Watchlist</th>
              <th>Verified</th>
            </tr>
          </thead>
          <tbody>
            {"".join([f'''
            <tr>
              <td style="font-weight: 600; color: #f8fafc;">{u['email']}</td>
              <td><span class="balance-pill">{u['balance']}</span></td>
              <td><pre>{u['holdings']}</pre></td>
              <td><pre>{u['profile']}</pre></td>
              <td style="color: #94a3b8;">{u['watchlist']}</td>
              <td><span style="color: {'#4ade80' if u['verified'] == 'Yes' else '#94a3b8'};">{u['verified']}</span></td>
            </tr>
            ''' for u in users_list])}
          </tbody>
        </table>

        <div class="section-title">
          <span>Price Alerts</span>
          <span class="badge">{len(alerts_list)} Alerts</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Email</th>
              <th>Ticker</th>
              <th>Target Price</th>
              <th>Condition</th>
              <th>Active</th>
              <th>Created At</th>
            </tr>
          </thead>
          <tbody>
            {"".join([f'''
            <tr>
              <td>{a.get('id', '')}</td>
              <td style="font-weight: 500;">{a.get('email', '')}</td>
              <td style="color: #38bdf8; font-weight: 600;">{a.get('ticker', '')}</td>
              <td>₹{a.get('target_price', 0):,.2f}</td>
              <td>{a.get('condition', '')}</td>
              <td>{'Active' if a.get('is_active') == 1 else 'Inactive'}</td>
              <td style="color: #64748b;">{a.get('created_at', '')}</td>
            </tr>
            ''' for a in alerts_list]) if alerts_list else '<tr><td colspan="7" style="text-align: center; color: #64748b; padding: 24px;">No price alerts set yet.</td></tr>'}
          </tbody>
        </table>
      </div>
    </body>
    </html>
    """
    return html

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
    
    horizon_param = request.args.get('horizon', '1d').lower()
    if horizon_param in ['5d', '5']:
        horizon = 5
    elif horizon_param in ['1m', '20d', '20']:
        horizon = 20
    else:
        horizon = 1
        
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    prediction = None
    probability = 0
    top_drivers = []
    all_attributions = []
    
    # 1. Check DB for existing prediction (only for 1d default)
    if horizon == 1:
        conn = get_model_db_connection()
        row = conn.execute('SELECT prediction, probability FROM predictions WHERE ticker = ? AND date = ?', (ticker, today_str)).fetchone()
        conn.close()
        if row:
            prediction = row['prediction']
            probability = row['probability']

    # 2. Generate on-the-fly if missing or non-standard horizon
    if not prediction:
        try:
            prediction, probability, top_drivers, all_attributions = generate_live_prediction(ticker, horizon=horizon)
        except Exception as e:
            print(f"Prediction error for {ticker} (horizon={horizon}d): {e}")
            prediction = "NEUTRAL"
            probability = 0.5
            top_drivers = []
            all_attributions = []
    else:
        # DB row found but need drivers
        try:
            _, _, top_drivers, all_attributions = generate_live_prediction(ticker, horizon=horizon)
        except Exception:
            pass
            
    # 3. Get History for Charts
    try:
        # Download last 1 year for charts
        end_date = datetime.date.today().strftime("%Y-%m-%d")
        start_date = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        df = main.download_stock(ticker, start=start_date, end=end_date)
        
        if df is None or df.empty:
            history = {}
            technical_analysis = {"score": 0, "rating": "NEUTRAL"}
        else:
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
            
            # Technical Analysis Score (6-indicator aggregate: RSI, MACD, EMA20, EMA50, EMA200, Stochastic %K)
            last = df.iloc[-1]
            tech_score = 0
            
            rsi_val = last["RSI"] if not pd.isna(last.get("RSI")) else 50
            macd_val = last["MACD"] if not pd.isna(last.get("MACD")) else 0
            close_val = last["Close"] if not pd.isna(last.get("Close")) else 0
            ema20_val = last["EMA20"] if "EMA20" in last and not pd.isna(last["EMA20"]) else close_val
            ema50_val = last["EMA50"] if "EMA50" in last and not pd.isna(last["EMA50"]) else close_val
            ema200_val = last["EMA200"] if "EMA200" in last and not pd.isna(last["EMA200"]) else close_val
            stoch_k_val = last["%K"] if "%K" in last and not pd.isna(last["%K"]) else 50
            
            # 1. RSI
            if rsi_val > 70: tech_score -= 1
            elif rsi_val < 30: tech_score += 1
            
            # 2. MACD
            if macd_val > 0: tech_score += 1
            else: tech_score -= 1
            
            # 3. Close vs EMA20
            if close_val > ema20_val: tech_score += 1
            else: tech_score -= 1
            
            # 4. Close vs EMA50
            if close_val > ema50_val: tech_score += 1
            else: tech_score -= 1
            
            # 5. Close vs EMA200
            if close_val > ema200_val: tech_score += 1
            else: tech_score -= 1
            
            # 6. Stochastic %K
            if stoch_k_val > 80: tech_score -= 1
            elif stoch_k_val < 20: tech_score += 1
            
            rating = "NEUTRAL"
            if tech_score >= 2: rating = "BUY"
            if tech_score >= 4: rating = "STRONG BUY"
            if tech_score <= -2: rating = "SELL"
            if tech_score <= -4: rating = "STRONG SELL"
            
            technical_analysis = {
                "score": tech_score,
                "rating": rating
            }
        
    except Exception as e:
        print(f"History error for {ticker}: {e}")
        history = {}
        technical_analysis = {"score": 0, "rating": "NEUTRAL"}
        
    return jsonify({
        "ticker": ticker,
        "horizon": f"{horizon}d" if horizon != 20 else "1m",
        "horizon_days": horizon,
        "prediction": prediction,
        "probability": probability,
        "top_drivers": top_drivers,
        "all_attributions": all_attributions,
        "history": history,
        "technical_analysis": technical_analysis
    })

def generate_live_prediction(ticker, horizon: int = 1):
    ticker_key = ticker.replace('.', '_') if horizon == 1 else f"{ticker.replace('.', '_')}_h{horizon}"
    model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_best_model.keras")
    if not os.path.exists(model_path):
         model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_final_model.keras")
    
    scaler_path = os.path.join(RESULTS_DIR, f"{ticker_key}_scaler.save")
    feature_path = os.path.join(RESULTS_DIR, f"{ticker_key}_features.joblib")
    
    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        print(f"Model for {ticker} (horizon={horizon}d) not found. Training model on demand...")
        try:
            main.train_single_model(ticker, horizon=horizon)
            model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_best_model.keras")
            if not os.path.exists(model_path):
                model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_final_model.keras")
            scaler_path = os.path.join(RESULTS_DIR, f"{ticker_key}_scaler.save")
        except Exception as e:
            print(f"Error training model for {ticker}: {e}")
            return "TRAINING", 0.0, [], []
            
        if not os.path.exists(model_path) or not os.path.exists(scaler_path):
            return "TRAINING", 0.0, [], []
        
    try:
        model = load_model(model_path)
    except Exception as ex:
        print(f"Model load with compile warning: {ex}. Retrying load_model without compile...")
        model = load_model(model_path, compile=False)
        
    scaler = joblib.load(scaler_path)
    
    if os.path.exists(feature_path):
        active_features = joblib.load(feature_path)
    else:
        active_features = main.FEATURE_COLS

    # Get Data
    start_date = (datetime.date.today() - datetime.timedelta(days=365)).strftime('%Y-%m-%d')
    df = main.download_stock(ticker, start=start_date, end=None)
    
    if len(df) < main.WINDOW + 50:
         return "NEUTRAL", 0.0, [], []
         
    df = main.add_technical_indicators(df)
    
    # Macro
    try:
        start_str = str(pd.DatetimeIndex(df.index)[0].strftime('%Y-%m-%d'))
        end_str = str(pd.DatetimeIndex(df.index)[-1].strftime('%Y-%m-%d'))
        macro = main.download_macro_data(start=start_str, end=end_str)
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
        
    if hasattr(scaler, 'n_features_in_') and scaler.n_features_in_ != len(active_features):
        if scaler.n_features_in_ == len(main.FEATURE_COLS):
            active_features = main.FEATURE_COLS

    features = df[active_features].tail(main.WINDOW).values
    if len(features) < main.WINDOW:
        return "NEUTRAL", 0.0, [], []
        
    try:
        features_scaled = scaler.transform(features)
    except Exception as e:
        print(f"Scaler transform warning for {ticker}: {e}. Refitting scaler dynamically.")
        from sklearn.preprocessing import StandardScaler
        features_scaled = StandardScaler().fit_transform(features)
        
    X_input = features_scaled.reshape(1, main.WINDOW, len(active_features))
    
    rf_path = os.path.join(RESULTS_DIR, f"{ticker_key}_rf.joblib")
    gb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_gb.joblib")
    xgb_path = os.path.join(RESULTS_DIR, f"{ticker_key}_xgb.joblib")
    stacker_path = os.path.join(RESULTS_DIR, f"{ticker_key}_stacker.joblib")
    meta_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta.joblib")
    threshold_path = os.path.join(RESULTS_DIR, f"{ticker_key}_meta_threshold.joblib")
    
    if os.path.exists(rf_path) and os.path.exists(gb_path) and os.path.exists(xgb_path):
        try:
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
                effective_threshold = min(float(meta_threshold), 0.60)
                # Soft-margin: if argmax is HOLD but directional class is within 5%, prefer directional signal
                if best_class == 1:
                    directional = int(np.argmax([probs[0], -1, probs[2]]))  # 0=SELL or 2=BUY
                    directional_class = 0 if probs[0] > probs[2] else 2
                    if float(probs[directional_class]) >= float(probs[1]) - 0.05:
                        best_class = directional_class
                # Only force HOLD if meta-confidence is low AND ensemble probability is under 45%
                if meta_confidence < effective_threshold and float(probs[best_class]) < 0.45:
                    best_class = 1
                    prob = float(probs[1])
                else:
                    prob = float(probs[best_class])
            else:
                best_class = int(np.argmax(probs))
                prob = float(probs[best_class])
        except Exception as e:
            print(f"Failed to load ensemble for predict ({ticker}): {e}. Falling back to base model.")
            if model is None:
                return "NEUTRAL", 0.0, [], []
            try:
                if callable(model):
                    preds = model(X_input, training=False)
                else:
                    preds = model.predict(X_input, verbose=0)
                probs = np.asarray(preds)[0]
            except Exception:
                probs = np.asarray(model.predict(X_input, verbose=0))[0]
            best_class = int(np.argmax(probs))
            prob = float(probs[best_class])
    else:
        if model is None:
            return "NEUTRAL", 0.0, [], []
        try:
            if callable(model):
                preds = model(X_input, training=False)
            else:
                preds = model.predict(X_input, verbose=0)
            probs = np.asarray(preds)[0]
        except Exception:
            probs = np.asarray(model.predict(X_input, verbose=0))[0]
        best_class = int(np.argmax(probs))
        prob = float(probs[best_class])
        
    if best_class == 2:
        prediction = "UP"
    elif best_class == 0:
        prediction = "DOWN"
    else:
        prediction = "HOLD"
        
    # Generate XAI drivers
    try:
        last_scaled_vec: np.ndarray = np.asarray(features_scaled[-1])
        xai_res = main.explain_prediction(ticker, last_scaled_vec, list(active_features), best_class, horizon=horizon, return_dict=True)
        if isinstance(xai_res, dict):
            top_drivers = xai_res.get("top_drivers", [])
            all_attributions = xai_res.get("all_attributions", [])
        else:
            top_drivers = xai_res
            all_attributions = []
    except Exception as ex:
        print(f"XAI driver extraction warning: {ex}")
        top_drivers = []
        all_attributions = []

    return prediction, prob, top_drivers, all_attributions

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
        
        # --- Metric Calculations ---
        # Cumulative return curves start at 1.0, so total return = (final - 1) * 100
        final_strategy = result_df["Cum_Strategy_Return"].iloc[-1]
        final_market = result_df["Cum_Market_Return"].iloc[-1]
        total_return = (final_strategy - 1.0) * 100
        market_return = (final_market - 1.0) * 100
        
        # Win rate: positive-return days among all BUY days
        buy_days = result_df[result_df["Signal"] == 1]
        total_trades = len(buy_days)
        wins = len(buy_days[buy_days["Strategy_Daily_Return"] > 0])
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Max Drawdown on strategy curve
        strat_curve = result_df["Cum_Strategy_Return"]
        rolling_max = strat_curve.cummax()
        drawdown = (strat_curve - rolling_max) / rolling_max
        max_drawdown = float(drawdown.min() * 100)
        
        return jsonify({
            "metrics": {
                "total_return": round(total_return, 2),
                "market_return": round(market_return, 2),
                "win_rate": round(win_rate, 2),
                "total_trades": total_trades,
                "max_drawdown": round(max_drawdown, 2)
            },
            "chart": {
                "dates": pd.DatetimeIndex(result_df.index).strftime('%Y-%m-%d').tolist(),
                "strategy": result_df["Cum_Strategy_Return"].tolist(),
                "market": result_df["Cum_Market_Return"].tolist()
            }
        })
        
    except Exception as e:
        print(f"Backtest error: {e}")
        import traceback; traceback.print_exc()
@app.route('/api/stream_train/<ticker>')
def stream_train(ticker):
    resolved_ticker = resolve_and_validate_ticker(ticker)
    if not resolved_ticker:
        return jsonify({"error": f"Ticker symbol '{ticker}' not found on Yahoo Finance"}), 400
    ticker = resolved_ticker

    horizon_param = request.args.get('horizon', '1d').lower()
    if horizon_param in ['5d', '5']:
        horizon = 5
    elif horizon_param in ['1m', '20d', '20']:
        horizon = 20
    else:
        horizon = 1

    ticker_key = ticker.replace('.', '_') if horizon == 1 else f"{ticker.replace('.', '_')}_h{horizon}"
    model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_best_model.keras")
    if not os.path.exists(model_path):
        model_path = os.path.join(RESULTS_DIR, f"{ticker_key}_final_model.keras")
    scaler_path = os.path.join(RESULTS_DIR, f"{ticker_key}_scaler.save")

    def generate():
        # If model is already trained, emit complete status immediately
        if os.path.exists(model_path) and os.path.exists(scaler_path):
            payload = json.dumps({"step": "Completed", "progress": 100, "message": f"Model for {ticker} is already trained and ready!"})
            yield f"data: {payload}\n\n"
            return

        msg_queue = queue.Queue()

        def progress_cb(step, progress, message):
            msg_queue.put({"step": step, "progress": progress, "message": message})

        def run_training():
            try:
                main.train_single_model(ticker, horizon=horizon, progress_callback=progress_cb)
            except Exception as e:
                print(f"SSE training error for {ticker}: {e}")
                msg_queue.put({"step": "Error", "progress": 100, "message": str(e)})
            finally:
                msg_queue.put(None)

        t = threading.Thread(target=run_training)
        t.start()

        while True:
            item = msg_queue.get()
            if item is None:
                break
            payload = json.dumps(item)
            yield f"data: {payload}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/watchlist_alerts', methods=['GET', 'POST'])
def watchlist_alerts():
    if request.method == 'POST':
        req = request.get_json() or {}
        watchlist = req.get('watchlist', [])
    else:
        raw_list = request.args.get('tickers', '')
        watchlist = [t.strip() for t in raw_list.split(',') if t.strip()]

    if not watchlist:
        return jsonify([])

    alerts = []
    for ticker in watchlist:
        try:
            prediction, prob, top_drivers, _ = generate_live_prediction(ticker, horizon=1)
            conf_pct = int(round(prob * 100))
            if prediction in ["UP", "BUY"] and conf_pct >= 80:
                driver_text = top_drivers[0]["name"] if top_drivers else "Strong Indicators"
                alerts.append({
                    "ticker": ticker,
                    "prediction": prediction,
                    "confidence": conf_pct,
                    "driver": driver_text,
                    "message": f"🚀 High-Confidence BUY Signal ({conf_pct}%) on {ticker}!"
                })
        except Exception as e:
            print(f"Watchlist alert check failed for {ticker}: {e}")
            continue

    return jsonify(alerts)

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