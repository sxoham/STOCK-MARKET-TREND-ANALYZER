import requests
from bs4 import BeautifulSoup
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
import urllib.parse

# Download VADER lexicon if not present
try:
    nltk.data.find('sentiment/vader_lexicon.zip')
except LookupError:
    nltk.download('vader_lexicon', quiet=True)

def get_news_sentiment(ticker):
    """
    Fetches news headlines for a ticker and calculates average sentiment.
    Returns: dict with 'score' (-1 to 1), 'label' (Positive/Neutral/Negative), and 'headlines'.
    """
    try:
        # Clean ticker for search (e.g., "RELIANCE.NS" -> "RELIANCE stock news")
        query = f"{ticker.split('.')[0]} stock news"
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        response = requests.get(url, timeout=5)
        soup = BeautifulSoup(response.content, features="xml")
        items = soup.findAll('item')[:5] # Top 5 headlines
        
        if not items:
            return {"score": 0, "label": "Neutral", "headlines": []}

        sia = SentimentIntensityAnalyzer()
        total_score = 0
        headlines = []
        
        for item in items:
            title = item.title.text
            score = sia.polarity_scores(title)['compound']
            total_score += score
            headlines.append({"title": title, "score": score, "link": item.link.text})
            
        avg_score = total_score / len(items)
        
        if avg_score > 0.05:
            label = "Positive"
        elif avg_score < -0.05:
            label = "Negative"
        else:
            label = "Neutral"
            
        return {
            "score": avg_score,
            "label": label,
            "headlines": headlines
        }
        
    except Exception as e:
        print(f"Error fetching sentiment for {ticker}: {e}")
        return {"score": 0, "label": "Neutral", "headlines": []}

if __name__ == "__main__":
    # Test
    print(get_news_sentiment("RELIANCE.NS"))
