import sys
import os

# Add parent to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import train_single_model

if __name__ == "__main__":
    print("Testing training for RELIANCE.NS...")
    train_single_model("RELIANCE.NS", force_rfe=True)
