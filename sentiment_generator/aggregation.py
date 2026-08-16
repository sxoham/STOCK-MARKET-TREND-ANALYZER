import pandas as pd
import numpy as np
from typing import List, Dict, Any, Tuple

def aggregate_daily_sentiment(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregates article-level FinBERT sentiment for a single stock on a single trading day.
    
    Returns:
        dict containing:
            - daily_sentiment: float in [-1.0, +1.0] (0.0 if no news)
            - article_count: int
            - pos_count: int
            - neu_count: int
            - neg_count: int
            - avg_sentiment: float
            - news_available: bool (True only when article_count > 0)
    """
    if not articles:
        return {
            "daily_sentiment": 0.0000,
            "article_count": 0,
            "pos_count": 0,
            "neu_count": 0,
            "neg_count": 0,
            "avg_sentiment": 0.0000,
            "news_available": False
        }

    scores = [a["sentiment_score"] for a in articles if a.get("sentiment_score") is not None]
    labels = [a.get("finbert_label", "neutral").lower() for a in articles]

    if not scores:
        return {
            "daily_sentiment": 0.0000,
            "article_count": len(articles),
            "pos_count": sum(1 for l in labels if l == "positive"),
            "neu_count": sum(1 for l in labels if l == "neutral"),
            "neg_count": sum(1 for l in labels if l == "negative"),
            "avg_sentiment": 0.0000,
            "news_available": len(articles) > 0
        }

    pos_count = sum(1 for l in labels if l == "positive")
    neu_count = sum(1 for l in labels if l == "neutral")
    neg_count = sum(1 for l in labels if l == "negative")
    article_count = len(articles)

    avg_sent = float(np.mean(scores))
    daily_sent = float(np.clip(avg_sent, -1.0, 1.0))

    return {
        "daily_sentiment": round(daily_sent, 4),
        "article_count": article_count,
        "pos_count": pos_count,
        "neu_count": neu_count,
        "neg_count": neg_count,
        "avg_sentiment": round(avg_sent, 4),
        "news_available": article_count > 0
    }

def generate_coverage_report(
    trading_dates: List[str],
    metadata_df: pd.DataFrame,
    ticker_keys: List[str]
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generates:
    1. Per-ticker coverage report (sentiment_coverage.csv)
    2. Per-year coverage report
    """
    total_trading_days = len(trading_dates)
    coverage_rows = []

    for ticker in ticker_keys:
        t_meta = metadata_df[metadata_df["Ticker"] == ticker] if not metadata_df.empty else pd.DataFrame()
        
        if not t_meta.empty:
            news_days = t_meta[t_meta["Article_Count"] > 0]
            days_with_news = len(news_days)
            days_without_news = total_trading_days - days_with_news
            total_articles = int(t_meta["Article_Count"].sum())
            first_news = news_days["Date"].min() if not news_days.empty else "N/A"
            last_news = news_days["Date"].max() if not news_days.empty else "N/A"
        else:
            days_with_news = 0
            days_without_news = total_trading_days
            total_articles = 0
            first_news = "N/A"
            last_news = "N/A"

        cov_pct = round(100.0 * days_with_news / total_trading_days, 2) if total_trading_days > 0 else 0.0

        coverage_rows.append({
            "Ticker": ticker,
            "First_News_Date": first_news,
            "Last_News_Date": last_news,
            "Articles": total_articles,
            "Trading_Days": total_trading_days,
            "Days_With_News": days_with_news,
            "Days_Without_News": days_without_news,
            "Coverage_Percentage": cov_pct
        })

    df_ticker_cov = pd.DataFrame(coverage_rows)

    # Yearly coverage summary
    yearly_rows = []
    if not metadata_df.empty:
        meta_copy = metadata_df.copy()
        meta_copy["Year"] = pd.to_datetime(meta_copy["Date"]).dt.year
        
        for yr, grp in meta_copy.groupby("Year"):
            unique_dates = grp["Date"].nunique()
            total_arts = grp["Article_Count"].sum()
            days_with_news = grp[grp["Article_Count"] > 0]["Date"].nunique()
            possible_slots = unique_dates * len(ticker_keys)
            slot_coverage = round(100.0 * len(grp[grp["Article_Count"] > 0]) / possible_slots, 2) if possible_slots > 0 else 0.0

            yearly_rows.append({
                "Year": yr,
                "Trading_Days": unique_dates,
                "Total_Articles": int(total_arts),
                "Ticker_Day_Coverage_Pct": slot_coverage
            })
            
    df_yearly_cov = pd.DataFrame(yearly_rows)
    return df_ticker_cov, df_yearly_cov
