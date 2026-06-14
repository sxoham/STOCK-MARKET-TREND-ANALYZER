import sqlite3
import pandas as pd
import yfinance as yf
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import main
import os
import datetime

LOG_DB_FILE = 'model_logs.db'

def verify_and_update():
    print("Connecting to prediction log database...")
    conn = sqlite3.connect(LOG_DB_FILE)
    c = conn.cursor()
    
    # 1. Fetch pending predictions (older than today)
    # We look for predictions made yesterday or before, that don't have a result yet.
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    # Select columns: id, ticker, date, prediction, start_price
    # 'date' in DB is the date prediction was made (e.g. 2023-10-27)
    # The 'prediction' targets the next trading day. 
    # We can verify if we have data for a date > 'date'.
    
    c.execute("SELECT id, ticker, date, prediction, start_price FROM predictions WHERE is_correct IS NULL AND date < ?", (today,))
    rows = c.fetchall()
    
    if not rows:
        # Check if there are any active predictions for today or future
        # (i.e. we made predictions but can't verify them yet)
        c.execute("SELECT count(*) FROM predictions WHERE is_correct IS NULL AND date >= ?", (today,))
        pending_future = c.fetchone()[0]
        
        if pending_future > 0:
            print("No pending predictions to verify from previous days.")
            print(f"There are {pending_future} active predictions for today/future.")
            
            # Calculate next likely trading day
            today_date = datetime.date.today()
            weekday = today_date.weekday() # 0=Mon, 6=Sun
            
            if weekday == 4: # Friday
                days_to_add = 3 # Next Monday
            elif weekday == 5: # Saturday
                days_to_add = 2 # Next Monday
            else:
                days_to_add = 1 # Tomorrow
                
            next_run_date = (today_date + datetime.timedelta(days=days_to_add)).strftime("%Y-%m-%d")
            
            print(f"NEXT RUN: Run 'verify_prediction.py' after 16:00 IST on {next_run_date} (or next trading day).")
        else:
            print("No pending predictions to verify, and no active predictions found for today.")
            print("NEXT STEP: Please run 'make_daily_predictions.py' to generate new predictions.")
            
        conn.close()
        return

    print(f"Found {len(rows)} pending predictions to verify.")
    
    tickers_to_update = set()
    
    for row in rows:
        pred_id, ticker, date_made, prediction, start_price = row
        print(f"\nVerifying prediction for {ticker} made on {date_made}...")
        
        # 2. Get Actual Data
        # We need data from date_made + a few days to find the "Next Close"
        # Download recent data
        df = yf.download(ticker, start=date_made, progress=False)
        
        if df.empty or len(df) < 2:
            print(f"Not enough data yet for {ticker}. (Found {len(df)} days from {date_made}, need at least 2 to verify next-day target)")
            continue
            
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # Find the row for date_made
        # Find the row for next trading day
        try:
            # Normalize dates to ensure accurate comparison
            df.index = pd.to_datetime(df.index).normalize()
            made_date_ts = pd.Timestamp(date_made).normalize()
            
            # We need the first trading day strictly AFTER date_made
            future_df = df[df.index > made_date_ts]
            
            if future_df.empty:
                print(f"Next day data not available yet for {ticker}.")
                continue
            
            next_close = future_df.iloc[0]["Close"]
            # 3-Class logic for verification
            # Matches the lower bound of dynamic threshold (0.5%)
            pct_change = (next_close - start_price) / start_price
            
            if pct_change > 0.005:
                actual_move = "UP"
            elif pct_change < -0.005:
                actual_move = "DOWN"
            else:
                actual_move = "HOLD"
            
            # Map "NEUTRAL" to "HOLD" if saved differently
            if prediction == "NEUTRAL": prediction = "HOLD"
            
            is_correct = 1 if actual_move == prediction else 0
            
            print(f"  Prediction: {prediction} (Start: {start_price:.2f})")
            print(f"  Actual: {actual_move} (Next Close: {next_close:.2f})")
            print(f"  Result: {'CORRECT' if is_correct else 'WRONG'}")
            
            # 3. Update Database
            c.execute('''
                UPDATE predictions 
                SET actual_price = ?, is_correct = ? 
                WHERE id = ?
            ''', (next_close, is_correct, pred_id))
            conn.commit()
            
            # Queue for update
            tickers_to_update.add(ticker)
            
        except KeyError:
            print(f"  Date {date_made} not found in market data. Market might have been closed.")
            continue
        except Exception as e:
            print(f"  Error processing {ticker}: {e}")
            continue

    conn.close()
    
    # 4. Trigger Retraining (Update Model) once per ticker
    if tickers_to_update:
        print(f"\nTraining models for updated tickers: {list(tickers_to_update)}")
        for ticker in tickers_to_update:
            print(f"--- Retraining {ticker} ---")
            # Uses cached features by default now (stable training)
            main.train_single_model(ticker)
            
    print("\nVerification and update complete.")
    
    if len(tickers_to_update) == 0 and rows:
        print("\nNo pending predictions could be verified yet (likely waiting for market data).")
        # Calculate next likely trading day
        today_date = datetime.date.today()
        weekday = today_date.weekday() # 0=Mon, 6=Sun
        
        if weekday == 4: # Friday
            days_to_add = 3 # Next Monday
        elif weekday == 5: # Saturday
            days_to_add = 2 # Next Monday
        else:
            days_to_add = 1 # Tomorrow
            
        next_run_date = (today_date + datetime.timedelta(days=days_to_add)).strftime("%Y-%m-%d")
        
        print(f"NEXT RUN: Run 'verify_prediction.py' after 16:00 IST on {next_run_date} (or next trading day).")

if __name__ == "__main__":
    verify_and_update()
