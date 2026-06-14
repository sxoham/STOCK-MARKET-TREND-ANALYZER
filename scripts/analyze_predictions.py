import sqlite3
import pandas as pd

LOG_DB_FILE = '../model_logs.db'

def analyze():
    print("Connecting to prediction log database...")
    try:
        conn = sqlite3.connect(LOG_DB_FILE)
        
        # Check if table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='predictions';")
        if not cursor.fetchone():
            print("Table 'predictions' does not exist in the database.")
            return

        query = "SELECT * FROM predictions"
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if df.empty:
            print("No predictions found in the database.")
            return
            
        print(f"Total Predictions Logged: {len(df)}")
        
        # Analyze verified predictions
        verified = df[df['is_correct'].notnull()]
        pending = df[df['is_correct'].isnull()]
        
        print(f"Verified Predictions: {len(verified)}")
        print(f"Pending Verification: {len(pending)}")
        
        if not verified.empty:
            correct = verified[verified['is_correct'] == 1]
            accuracy = (len(correct) / len(verified)) * 100
            print(f"Overall Accuracy: {accuracy:.2f}%")
            
            print("\nRecent Verified Predictions:")
            print(verified.tail(10)[['ticker', 'date', 'prediction', 'actual_price', 'is_correct']])
            
            # Group by ticker accuracy
            print("\nAccuracy by Ticker:")
            ticker_stats = verified.groupby('ticker')['is_correct'].mean() * 100
            print(ticker_stats)
            
        else:
            print("No verified predictions to analyze accuracy.")
            
        if not pending.empty:
            print("\nPending Predictions (Next 5):")
            print(pending.head(5)[['ticker', 'date', 'prediction', 'start_price']])

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    analyze()
