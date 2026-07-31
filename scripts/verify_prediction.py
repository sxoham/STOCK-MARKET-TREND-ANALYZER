import sqlite3
import pandas as pd
import yfinance as yf
import datetime
import os
import sys

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import main

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Resolves to the model_logs.db in the parent (root) directory
LOG_DB_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "model_logs.db"))


def get_next_run_date():
    today_date = datetime.date.today()
    weekday = today_date.weekday()

    if weekday == 4:      # Friday
        days_to_add = 3
    elif weekday == 5:    # Saturday
        days_to_add = 2
    else:
        days_to_add = 1

    return (
        today_date + datetime.timedelta(days=days_to_add)
    ).strftime("%Y-%m-%d")


def migrate_database(conn):
    """Automatically adds missing actual_move and actual_return columns to predictions table."""
    c = conn.cursor()
    c.execute("PRAGMA table_info(predictions)")
    columns = [row[1] for row in c.fetchall()]

    if "actual_move" not in columns:
        print("Migrating database: Adding actual_move column to predictions table...")
        c.execute("ALTER TABLE predictions ADD COLUMN actual_move TEXT")
        conn.commit()

    if "actual_return" not in columns:
        print("Migrating database: Adding actual_return column to predictions table...")
        c.execute("ALTER TABLE predictions ADD COLUMN actual_return REAL")
        conn.commit()


def verify_and_update():
    print("Connecting to prediction log database...")

    conn = sqlite3.connect(LOG_DB_FILE)
    # Run DB migration to ensure actual_move and actual_return columns exist
    migrate_database(conn)
    
    c = conn.cursor()

    today = datetime.date.today().strftime("%Y-%m-%d")

    # Select all details including predicted_date
    c.execute("""
        SELECT id, ticker, date, predicted_date, prediction, start_price
        FROM predictions
        WHERE is_correct IS NULL
        AND date < ?
    """, (today,))

    rows = c.fetchall()

    if not rows:
        c.execute("""
            SELECT COUNT(*)
            FROM predictions
            WHERE is_correct IS NULL
            AND date >= ?
        """, (today,))

        pending_future = c.fetchone()[0]

        if pending_future > 0:
            print("No pending predictions to verify from previous days.")
            print(f"There are {pending_future} active predictions for today/future.")
            print(
                f"NEXT RUN: Run verify_prediction.py after 16:00 IST on {get_next_run_date()}."
            )
        else:
            print("No pending predictions to verify.")
            print("NEXT STEP: Run make_daily_predictions.py.")

        conn.close()
        return

    print(f"Found {len(rows)} pending predictions to verify.")

    # Group pending predictions by ticker to batch/cache downloads
    predictions_by_ticker = {}
    for row in rows:
        pred_id, ticker, date_made, predicted_date, prediction, start_price = row
        predictions_by_ticker.setdefault(ticker, []).append({
            "id": pred_id,
            "date_made": date_made,
            "predicted_date": predicted_date,
            "prediction": prediction,
            "start_price": start_price
        })

    tickers_to_update = set()
    results = []  # Collect all verified results for the final summary

    for ticker, preds in predictions_by_ticker.items():
        print(f"\nProcessing ticker: {ticker} ({len(preds)} pending predictions)")

        # Determine download date range
        dates_made = [p["date_made"] for p in preds]
        predicted_dates = [p["predicted_date"] for p in preds]

        min_date = min(dates_made)
        # End date: max predicted date + 7 days buffer
        max_pred_ts = pd.Timestamp(max(predicted_dates))
        end_date = (max_pred_ts + pd.Timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            print(f"Downloading historical data for {ticker} from {min_date} to {end_date}...")
            df = yf.download(
                ticker,
                start=min_date,
                end=end_date,
                progress=False,
                auto_adjust=False
            )

            if df.empty:
                print(f"Not enough market data downloaded for {ticker}. Skipping predictions.")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.index = pd.to_datetime(df.index).normalize()

            for p in preds:
                pred_id = p["id"]
                date_made = p["date_made"]
                predicted_date = p["predicted_date"]
                prediction = p["prediction"]
                start_price = float(p["start_price"])

                print(f"  Verifying prediction ID {pred_id} made on {date_made} targeting {predicted_date}...")

                predicted_ts = pd.Timestamp(predicted_date).normalize()
                # Find the first trading day on or after the predicted date
                future_df = df[df.index >= predicted_ts]

                if future_df.empty:
                    print(f"    Target trading day data (on/after {predicted_date}) not available yet.")
                    continue

                next_close = float(future_df.iloc[0]["Close"])
                pct_change = (next_close - start_price) / start_price

                # 3-class movement
                if pct_change > 0.015:
                    actual_move = "UP"
                elif pct_change < -0.015:
                    actual_move = "DOWN"
                else:
                    actual_move = "HOLD"

                if prediction == "NEUTRAL":
                    prediction = "HOLD"

                is_correct = 1 if actual_move == prediction else 0
                actual_return = pct_change * 100

                print(f"    Prediction : {prediction}")
                print(f"    Actual     : {actual_move}")
                print(f"    Start Price: {start_price:.2f}")
                print(f"    Next Close : {next_close:.2f} (on {future_df.index[0].strftime('%Y-%m-%d')})")
                print(f"    Return     : {actual_return:.2f}%")
                print(f"    Result     : {'CORRECT' if is_correct else 'WRONG'}")

                c.execute("""
                    UPDATE predictions
                    SET actual_price = ?,
                        actual_move = ?,
                        actual_return = ?,
                        is_correct = ?
                    WHERE id = ?
                """, (next_close, actual_move, actual_return, is_correct, pred_id))

                tickers_to_update.add(ticker)

                results.append({
                    "id": pred_id,
                    "ticker": ticker,
                    "date_made": date_made,
                    "predicted_date": future_df.index[0].strftime('%Y-%m-%d'),
                    "prediction": prediction,
                    "actual": actual_move,
                    "start_price": start_price,
                    "next_close": next_close,
                    "return_pct": actual_return,
                    "is_correct": is_correct,
                })

        except Exception as e:
            print(f"Error processing ticker {ticker}: {e}")
            continue

    conn.commit()
    conn.close()

    # Retrain updated models
    if tickers_to_update and "--no-retrain" not in sys.argv:
        print("\nRetraining models...")

        for ticker in tickers_to_update:
            try:
                print(f"--- Retraining {ticker} ---")
                main.train_single_model(ticker)
            except Exception as e:
                print(f"Retraining failed for {ticker}: {e}")
    elif tickers_to_update:
        print("\nSkipping retraining (deferred to next pipeline step)...")

    print("\nVerification and update complete.")

    # ── Final Summary Table ──────────────────────────────────────────────────
    if results:
        print("\n" + "=" * 90)
        print(" PREDICTION VERIFICATION SUMMARY")
        print("=" * 90)
        header = (
            f"{'ID':>4}  {'Ticker':<8}  {'Date Made':<12}  {'Target Date':<12}  "
            f"{'Predicted':<10}  {'Actual':<7}  {'Start':>8}  {'Close':>8}  "
            f"{'Return':>8}  {'Result':<8}"
        )
        print(header)
        print("-" * 90)
        correct_count = 0
        for r in results:
            result_label = "✔ CORRECT" if r["is_correct"] else "✘ WRONG"
            if r["is_correct"]:
                correct_count += 1
            row = (
                f"{r['id']:>4}  {r['ticker']:<8}  {r['date_made']:<12}  {r['predicted_date']:<12}  "
                f"{r['prediction']:<10}  {r['actual']:<7}  {r['start_price']:>8.2f}  "
                f"{r['next_close']:>8.2f}  {r['return_pct']:>+7.2f}%  {result_label:<8}"
            )
            print(row)
        print("-" * 90)
        accuracy = (correct_count / len(results)) * 100
        print(f"  Total: {len(results)} predictions  |  Correct: {correct_count}  |  Accuracy: {accuracy:.1f}%")
        print("=" * 90)

    if len(tickers_to_update) == 0 and rows:
        print(
            f"NEXT RUN: Run verify_prediction.py after 16:00 IST on {get_next_run_date()}."
        )


if __name__ == "__main__":
    verify_and_update()
