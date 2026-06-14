import requests

base_url = "http://127.0.0.1:5000"

def test_endpoint(path):
    url = f"{base_url}{path}"
    try:
        response = requests.get(url)
        print(f"\n--- Testing {path} ---")
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                print(f"Received list with {len(data)} items:")
                for item in data[:3]:
                    print(f"  {item}")
                if len(data) > 3:
                    print("  ...")
            else:
                print("Keys in response:", list(data.keys()))
                if 'prediction' in data:
                    print(f"  prediction: {data['prediction']}")
                    print(f"  probability: {data['probability']}")
                    print(f"  ticker: {data['ticker']}")
                    print(f"  history length: {len(data['history'].get('close', []))}")
                    print(f"  technical_analysis: {data['technical_analysis']}")
        else:
            print("Error message:", response.text)
    except Exception as e:
        print(f"Request failed for {path}: {e}")

# Wait a moment to make sure Flask has reloaded
import time
time.sleep(2)

test_endpoint("/api/stocks")
test_endpoint("/api/lookup?q=NVIDIA")
test_endpoint("/api/lookup?q=RELIANCE")
test_endpoint("/api/predict/NVIDIA")
test_endpoint("/api/predict/NVDIA")
