import os
import pandas as pd
import datetime
import requests
from bs4 import BeautifulSoup
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
import urllib.parse
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from main import STOCKS

# Ensure VADER lexicon is downloaded
try:
    nltk.data.find('sentiment/vader_lexicon.zip')
except LookupError:
    nltk.download('vader_lexicon', quiet=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def get_article_content(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code != 200:
            return ""
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text = " ".join([p.get_text() for p in paragraphs])
        return text[:5000]
    except:
        return ""

def get_sentiment(ticker):
    print(f"  Fetching news for {ticker}...")
    query = f"{ticker.split('.')[0]} stock news"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.content, features="xml")
        items = soup.findAll('item')[:5]
        
        if not items:
            return 0.0
            
        sia = SentimentIntensityAnalyzer()
        total_score = 0
        
        for item in items:
            title = item.title.text
            link = item.link.text
            headline_score = sia.polarity_scores(title)['compound']
            
            # Deep fetch
            try:
                # Some RSS links are redirects (google news), requests usually handles them
                body_text = get_article_content(link)
                if len(body_text) > 100:
                    body_score = sia.polarity_scores(body_text)['compound']
                    item_score = (headline_score * 0.4) + (body_score * 0.6)
                else:
                    item_score = headline_score
            except:
                item_score = headline_score
                
            total_score += item_score
            
        return total_score / len(items)
        
    except Exception as e:
        print(f"Error: {e}")
        return 0.0

def update_all_sentiments():
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"Updating sentiment for {today}...")
    
    # Load existing wide-format CSV or create new
    csv_file = "daily_sentiment.csv"
    if os.path.exists(csv_file):
        df = pd.read_csv(csv_file)
        df['Date'] = pd.to_datetime(df['Date'])
    else:
        df = pd.DataFrame(columns=["Date"])
    
    # Ensure Date column is standard
    if 'Date' not in df.columns:
        df['Date'] = pd.to_datetime(df.index) if df.index.name == 'Date' else pd.to_datetime([])

    # Create a dictionary for today's row
    today_row = {"Date": pd.to_datetime(today)}
    
    for ticker in STOCKS:
        score = get_sentiment(ticker)
        print(f"  {ticker}: {score:.4f}")
        today_row[ticker] = score
        
    # Check if today exists
    # We need to make sure 'Date' is datetime for comparison
    match_mask = df['Date'] == pd.to_datetime(today)
    
    if match_mask.any():
        # Update existing row
        idx = df.index[match_mask][0]
        for col, val in today_row.items():
            if col != "Date":
                df.at[idx, col] = val
    else:
        # Append new row
        new_df = pd.DataFrame([today_row])
        df = pd.concat([df, new_df], ignore_index=True)
        
    # Sort by date just in case
    df.sort_values("Date", inplace=True)
    df.to_csv(csv_file, index=False)
    print(f"Saved consolidated sentiment to {csv_file}")

if __name__ == "__main__":
    update_all_sentiments()
