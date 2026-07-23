# Stock Market Trend Analyzer

AI-powered stock market analysis platform for Indian equities (NSE). Predicts short-term BUY / SELL / HOLD signals using an ensemble of deep learning and tree-based models, with a Flask web dashboard for live predictions, backtesting, and sentiment analysis.

## Features

- **Ensemble ML pipeline** — Bidirectional LSTM + Random Forest + Gradient Boosting + XGBoost with logistic stacking and meta-labeling confidence filter
- **24+ engineered features** — Technical indicators (RSI, MACD, Bollinger Bands, ADX, OBV, Stochastic %K/%D), macro data (Nifty, USD/INR, gold, oil), and news sentiment
- **20 NSE blue-chip stocks** — RELIANCE, TCS, HDFC Bank, ICICI, Infosys, and more
- **Web dashboard** — Interactive charts (Plotly/Chart.js), watchlist, portfolio simulation, stock search
- **Technical Rating Gauge** — 6-indicator aggregate scoring system (RSI, MACD, EMA20, EMA50, EMA200, Stochastic %K) with needle gauge display
- **News sentiment** — VADER NLP on Google News headlines
- **Backtesting** — 365-day strategy simulation with stop-loss and take-profit rules

## Tech Stack

Python · TensorFlow/Keras · scikit-learn · XGBoost · Flask · SQLite · pandas · yfinance · NLTK · Plotly · Chart.js

---

## Screenshots & Dashboard Showcase

### Web Interface & Dashboard

| Main Dashboard & Technical Rating Gauge | Login Page |
| :---: | :---: |
| ![Dashboard Overview](images/dashboard.png) | ![Login Screen](images/login_screenshot.png) |

### Model Evaluation & Explainability

| SHAP Feature Attribution | Feature Importance Ranking |
| :---: | :---: |
| ![SHAP Summary](images/shap_summary.png) | ![Feature Importance](images/feature_importance.png) |

| Confusion Matrix | Training Loss & Accuracy History |
| :---: | :---: |
| ![Confusion Matrix](images/confusion_matrix.png) | ![Training History](images/training_history.png) |

---

## Setup & Local Installation

```bash
# Clone the repository
git clone https://github.com/sxoham/Stock-Market-Trend-Analyzer.git
cd Stock-Market-Trend-Analyzer

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt
pip install xgboost

# Train models (optional — pre-trained models included)
python main.py

# Run the web app
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

---

## Project Structure

```
├── main.py              # ML training pipeline & backtesting engine
├── app.py               # Flask REST API & web server
├── sentiment.py         # News sentiment analyzer (VADER NLP)
├── requirements.txt     # Python dependencies
├── images/              # Dashboard screenshots & model evaluation plots
│   ├── dashboard.png
│   ├── login_screenshot.png
│   ├── confusion_matrix.png
│   ├── feature_importance.png
│   ├── shap_summary.png
│   └── training_history.png
├── scripts/
│   ├── make_daily_predictions.py
│   ├── diagnose_model.py
│   └── clean_dataset.py
├── templates/           # HTML templates (index.html, login.html, register.html)
├── static/              # CSS, JS, Chart.js modules
└── stock_models_optionB/  # Trained model weights & evaluation reports
```

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /login` | User Authentication Page |
| `GET /dashboard` | Main Prediction & Charting Dashboard |
| `GET /api/predict/<ticker>` | Live BUY/SELL/HOLD prediction & Technical Rating |
| `GET /api/backtest/<ticker>` | 365-day backtest strategy simulation |
| `GET /api/sentiment/<ticker>` | News sentiment analysis breakdown |
| `GET /api/stocks` | Available stock tickers |
| `GET /api/lookup?q=` | Live stock symbol search |

---

## Disclaimer

This project is for educational and research purposes only. It is not financial advice. Past performance does not guarantee future results.

## Author

[sxoham](https://github.com/sxoham)
