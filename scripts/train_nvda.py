import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
print("Starting training for NVDA...")
main.train_single_model("NVDA")
print("Training completed.")
