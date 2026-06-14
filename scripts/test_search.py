import yfinance as yf

def resolve_and_validate_ticker(ticker):
    # 1. Try to download a tiny slice of data to check if ticker is directly valid
    try:
        df = yf.download(ticker, period="5d", progress=False)
        if not df.empty and 'Close' in df.columns:
            return ticker
    except Exception as e:
        print(f"Direct download error for {ticker}: {e}")
    
    # 2. If directly downloading failed or returned empty, try searching Yahoo Finance
    try:
        search = yf.Search(ticker)
        if search.quotes:
            best_symbol = search.quotes[0]['symbol']
            # Double check if we can download the resolved symbol
            df = yf.download(best_symbol, period="5d", progress=False)
            if not df.empty and 'Close' in df.columns:
                return best_symbol
    except Exception as e:
        print(f"Search/resolve error for {ticker}: {e}")
        
    return None

print("Resolving 'NVDA':", resolve_and_validate_ticker('NVDA'))
print("Resolving 'NVIDIA':", resolve_and_validate_ticker('NVIDIA'))
print("Resolving 'NVDIA':", resolve_and_validate_ticker('NVDIA'))
print("Resolving 'RELIANCE.NS':", resolve_and_validate_ticker('RELIANCE.NS'))
