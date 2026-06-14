import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from main import train_single_model
import os

def test_shap_generation():
    ticker = "RELIANCE.NS"
    print(f"Running training for {ticker} to generate SHAP plots...")
    
    # Run the training pipeline
    try:
        train_single_model(ticker)
        
        # Check for output files
        results_dir = "../stock_models_optionB"
        fi_plot = os.path.join(results_dir, f"{ticker.replace('.', '_')}_feature_importance.png")
        shap_plot = os.path.join(results_dir, f"{ticker.replace('.', '_')}_shap_summary.png")
        
        if os.path.exists(fi_plot):
            print(f"✅ Feature Importance Plot generated: {fi_plot}")
        else:
            print(f"❌ Feature Importance Plot MISSING.")
            
        if os.path.exists(shap_plot):
            print(f"✅ SHAP Summary Plot generated: {shap_plot}")
        else:
            print(f"❌ SHAP Summary Plot MISSING (Did SHAP install correctly?).")
            
    except Exception as e:
        print(f"❌ Training failed: {e}")

if __name__ == "__main__":
    test_shap_generation()
