"""
Live validation test for FinBertAnalyzer:
1. Positive headlines -> +confidence
2. Neutral headlines  -> 0.0
3. Negative headlines -> -confidence
4. All scores strictly in [-1.0, 1.0]
"""

import sys
from sentiment_generator.finbert_sentiment import FinBertAnalyzer


def test_finbert():
    print("=" * 70)
    print("  FINBERT SENTIMENT ANALYZER -- Live Test")
    print("=" * 70)

    analyzer = FinBertAnalyzer()

    test_cases = [
        ("Reliance Industries quarterly net profit surges 28% beating analyst estimates", "positive"),
        ("Tata Consultancy Services signs $1.5 billion digital transformation deal", "positive"),
        ("ITC Ltd reports decline in cigarette volumes as Q3 net profit falls 12%", "negative"),
        ("Larsen & Toubro faces major contract cancellation and margin compression", "negative"),
        ("State Bank of India announces board meeting schedule for next week", "neutral"),
        ("Titan Company opens new corporate office in Bengaluru", "neutral"),
    ]

    texts = [t[0] for t in test_cases]
    expected = [t[1] for t in test_cases]

    results = analyzer.analyze_batch(texts)

    all_passed = True
    print("\n  RESULTS:")
    for i, (text, exp, res) in enumerate(zip(texts, expected, results), 1):
        if res is None:
            print(f"  [{i:02d}] FAILED: Inference returned None for: {text[:60]}")
            all_passed = False
            continue

        label = res["finbert_label"]
        conf = res["finbert_confidence"]
        score = res["sentiment_score"]

        # Validate label matches or is reasonable
        label_ok = (label == exp)
        score_ok = (-1.0 <= score <= 1.0)
        
        # Validate mathematical sign mapping
        if label == "positive":
            sign_ok = (score == conf and score > 0)
        elif label == "negative":
            sign_ok = (score == -conf and score < 0)
        else:
            sign_ok = (score == 0.0)

        status = "OK" if (label_ok and score_ok and sign_ok) else "MISMATCH"
        if not (label_ok and score_ok and sign_ok):
            all_passed = False

        print(f"  [{i:02d}] [{status}] Label: {label:<8} (Expected: {exp:<8}) Conf: {conf:.4f} Score: {score:+.4f}")
        print(f"       Headline: {text[:80]}")

    print("\n" + "=" * 70)
    if all_passed:
        print("  VERDICT: PASS -- FinBERT sentiment scoring is verified!")
    else:
        print("  VERDICT: !! REVIEW -- Some checks failed")
    print("=" * 70)


if __name__ == "__main__":
    test_finbert()
