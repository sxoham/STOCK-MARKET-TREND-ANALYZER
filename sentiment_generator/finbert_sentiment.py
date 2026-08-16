import os
import sys
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
torch_lib_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'torch', 'lib')
if os.path.exists(torch_lib_dir):
    os.environ['PATH'] = torch_lib_dir + os.pathsep + os.environ.get('PATH', '')
    if hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(torch_lib_dir)
        except Exception:
            pass

import logging
import time
from typing import List, Dict, Any, Optional
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline
from .config import FINBERT_MODEL_NAME

logger = logging.getLogger(__name__)

class FinBertAnalyzer:
    """
    Financial sentiment analyzer using ProsusAI/finbert.
    Strictly FinBERT only -- no fallback models or synthetic scores permitted.
    """
    def __init__(self, model_name: str = FINBERT_MODEL_NAME):
        self.model_name = model_name
        self.pipeline = None
        self.inference_failures = 0
        self._init_model()

    def _init_model(self):
        try:
            device = 0 if torch.cuda.is_available() else -1
            print(f"  [FinBERT] Initializing {self.model_name} on device={device}...")
            
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            
            self.pipeline = pipeline(
                "text-classification",
                model=model,
                tokenizer=tokenizer,
                device=device,
                top_k=None,
                truncation=True,
                max_length=512
            )
            print("  [FinBERT] Model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load FinBERT model: {e}")
            raise RuntimeError(
                f"ProsusAI/finbert initialization failed ({e}). "
                "No fallback models are allowed per requirements."
            )

    def analyze_batch(self, texts: List[str], batch_size: int = 32) -> List[Optional[Dict[str, Any]]]:
        """
        Runs batch sentiment classification on headlines using ProsusAI/finbert.
        
        Conversion rules:
        - positive -> +confidence
        - neutral  -> 0.0
        - negative -> -confidence
        
        If inference fails, retries up to 3 times before returning None for failed items.
        """
        if not texts:
            return []

        if not self.pipeline:
            raise RuntimeError("FinBERT pipeline is not initialized.")

        # Attempt batch inference with retries
        for attempt in range(3):
            try:
                outputs = self.pipeline(texts, batch_size=batch_size)
                processed = []
                for out in outputs:
                    top_pred = max(out, key=lambda x: x['score'])
                    label = top_pred['label'].lower()
                    conf = float(top_pred['score'])

                    if label == 'positive':
                        sentiment = conf
                    elif label == 'negative':
                        sentiment = -conf
                    else:  # neutral
                        sentiment = 0.0

                    processed.append({
                        "finbert_label": label,
                        "finbert_confidence": round(conf, 4),
                        "sentiment_score": round(sentiment, 4)
                    })
                return processed
            except Exception as e:
                logger.warning(f"FinBERT batch inference attempt {attempt + 1} failed ({e}).")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    # Try smaller batch size on retry
                    batch_size = max(1, batch_size // 2)

        # If all retries fail, log failures without generating synthetic sentiment
        self.inference_failures += len(texts)
        logger.error(f"FinBERT inference permanently failed for {len(texts)} texts.")
        return [None] * len(texts)

    def score_articles(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Scores a list of article dicts, attaching finbert_label, finbert_confidence, sentiment_score.
        """
        if not articles:
            return []

        texts = [a["headline"] for a in articles]
        results = self.analyze_batch(texts)

        scored = []
        for a, res in zip(articles, results):
            if res is not None:
                item = dict(a)
                item["finbert_label"] = res["finbert_label"]
                item["finbert_confidence"] = res["finbert_confidence"]
                item["sentiment_score"] = res["sentiment_score"]
                scored.append(item)
        return scored
