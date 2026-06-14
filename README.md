# Stock Market Trend Analyzer

AI-powered stock market analysis platform for Indian equities (NSE). Predicts short-term BUY / SELL / HOLD signals using an ensemble of deep learning and tree-based models, with a Flask web dashboard for live predictions, backtesting, and sentiment analysis.

## Features

- **Ensemble ML pipeline** — Bidirectional LSTM + Random Forest + Gradient Boosting + XGBoost with logistic stacking and meta-labeling confidence filter
- **24+ engineered features** — Technical indicators (RSI, MACD, Bollinger Bands, ADX, OBV, etc.), macro data (Nifty, USD/INR, gold, oil), and news sentiment
- **20 NSE blue-chip stocks** — RELIANCE, TCS, HDFC Bank, ICICI, Infosys, and more
- **Web dashboard** — Interactive charts (Plotly/Chart.js), watchlist, portfolio simulation, stock search
- **News sentiment** — VADER NLP on Google News headlines
- **Backtesting** — 365-day strategy simulation with stop-loss and take-profit rules

## Tech Stack

Python · TensorFlow/Keras · scikit-learn · XGBoost · Flask · SQLite · pandas · yfinance · NLTK · Plotly

## Setup

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

# Train models (optional — takes time)
python main.py

# Run the web app
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

## Project Structure

```
├── main.py              # ML training pipeline & backtesting
├── app.py               # Flask REST API & web server
├── sentiment.py         # News sentiment (VADER NLP)
├── requirements.txt
├── scripts/
│   ├── make_daily_predictions.py
│   ├── diagnose_model.py
│   └── clean_dataset.py
├── templates/           # HTML templates
├── static/              # CSS, JS, charts
└── stock_models_optionB/  # Trained models & training reports
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/predict/<ticker>` | Live BUY/SELL/HOLD prediction |
| `GET /api/backtest/<ticker>` | 365-day backtest results |
| `GET /api/sentiment/<ticker>` | News sentiment analysis |
| `GET /api/stocks` | List of available stocks |
| `GET /api/lookup?q=` | Stock symbol search |

## Disclaimer

This project is for educational and research purposes only. It is not financial advice. Past performance does not guarantee future results.

## Author

[sxoham](https://github.com/sxoham)
