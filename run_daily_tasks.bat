@echo off
echo ===========================================
echo      STOCK MARKET TREND ANALYZER (AUTO)
echo ===========================================
echo.

cd /d "F:\Project\STOCK MARKET TREND ANALYZER"

echo [1/4] Verifying past predictions...
.venv\Scripts\python.exe scripts/verify_prediction.py

echo.

echo [2/4] Updating sentiment data...
.venv\Scripts\python.exe scripts/update_sentiment.py
echo.

echo [3/4] Retraining models (main.py)...
.venv\Scripts\python.exe main.py
echo.

echo [4/4] Generating new predictions...
.venv\Scripts\python.exe scripts/make_daily_predictions.py
echo.

echo ===========================================
echo      ALL TASKS COMPLETED SUCCESSFULLY
echo ===========================================
pause