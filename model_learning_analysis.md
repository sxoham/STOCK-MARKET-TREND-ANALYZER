# Model Learning Analysis & Diagnostics

After diagnosing your data pipeline, model training curves, and test evaluation metrics, the short answer is: **No, the model is not learning a real predictive signal.** It is currently memorizing noise during training and performing no better than random guessing on validation and test data.

Below is a detailed breakdown of the structural, mathematical, and algorithmic reasons for this behavior, along with evidence from the codebase and suggestions to resolve it.

---

## 1. Key Diagnostic Evidence

### A. Epoch-by-Epoch Training Divergence (Overfitting)
We isolated and tracked the LSTM's loss and accuracy metrics over training epochs. While the training loss steadily decreases, the validation loss diverges:

| Epoch | Training Loss | Training Acc | Val Loss | Val Acc |
| :--- | :--- | :--- | :--- | :--- |
| **01** | 1.1976 | 34.1% | 1.1357 | 37.0% |
| **05** | 1.0661 | 42.7% | 1.0938 | 38.2% |
| **10** | 1.0096 | 48.5% | 1.1783 | 34.7% |
| **15** | 0.9767 | 49.2% | 1.1960 | 37.0% |

> [!WARNING]
> **Classic Overfitting:** Training loss goes down from `1.19` to `0.97` (the model is successfully adapting to train data), but validation loss *increases* from `1.13` to `1.20` and validation accuracy remains flat. The model is memorizing historical noise and fails to generalize.

### B. Test Evaluation Metrics
Looking at your `full_model_report.csv`, the test performance for almost all models is near random:
* **ROC AUC:** Consistently between `0.49` and `0.52` across all tickers (an ROC AUC of `0.50` indicates pure random guessing).
* **Test Accuracy:** Often lower than `30%` (e.g., `RELIANCE.NS` has `27.9%` accuracy, `MARUTI.NS` has `24.6%` accuracy). Since the majority class (`HOLD`) accounts for `54.5%` of the dataset, a naive model that predicts `HOLD` everyday would get `54.5%` accuracy. The trained models are performing far worse than this baseline.

---

## 2. Root Causes Identified

### Root Cause 1: Alignment Mismatch (The Shift Bug)
There is a 1-day alignment mismatch between the input sequences and the target labels.

In [main.py](file:///f:/Project/STOCK%20MARKET%20TREND%20ANALYZER/main.py#L350-L370):
```python
def create_sequences(features, target, window: int = WINDOW) -> tuple:
    X, y = [], []
    for i in range(window, len(features)):
        X.append(features[i-window:i])
        y.append(target[i])
    X = np.array(X)
    y = np.array(y)
    return X, y
```
1. `features[i-window:i]` spans indices `i-window` up to `i-1` (the last feature row is from day $t-1$).
2. `target[i]` represents `Next_Return` for day $t$, which is the return from the close of day $t$ to the close of day $t+1$.
3. **The Gap:** The model is given features ending at day $t-1$, and is asked to predict the return from day $t$ to $t+1$. This introduces a **1-day gap** where the return of day $t$ (from close of $t-1$ to close of $t$) is not in the features, making the prediction horizon 2 days out instead of 1.
4. **Backtest Mismatch:** In the backtester, the model's prediction at index `i` is used to trade day $t$ (open to close), but the model was trained on `target[i]` (return of day $t+1$).

### Root Cause 2: Extreme Model Complexity vs. Small Data
The LSTM architecture ([main.py](file:///f:/Project/STOCK%20MARKET%20TREND%20ANALYZER/main.py#L455-L486)) is highly complex:
* Bidirectional LSTM (64 units)
* Multi-Head Attention (4 heads, 32 key dim)
* Layer Normalization
* Global Average Pooling & Dense heads
* **Total Parameters:** ~80k–100k.
* **Training Sample Size:** Only ~1500 sequences.

In daily financial markets, the signal-to-noise ratio is extremely low. A model with this many parameters will immediately overfit and memorize training samples instead of learning generalizable features.

### Root Cause 3: Over-Penalization of the Majority Class
Because `HOLD` is the majority class (~54.5%), class weights are applied to balance the dataset:
* `class_weight = {k: total / (3 * v) for k, v in class_counts.items()}`
This weights `BUY` and `SELL` classes roughly **2.5 times** heavier than `HOLD`.
Without a true signal in the data, the model attempts to minimize loss by guessing `BUY` or `SELL` (since predicting them correctly yields higher rewards, and guessing them wrong is penalized the same as missing them). This results in predicting highly volatile signals on test days that are actually stable `HOLD` days, dropping accuracy to `<30%`.

### Root Cause 4: Low Feature Correlation
A correlation check on `RELIANCE.NS` shows that the highest correlated features with `Next_Return` are volatility measures (like `ATR_Pct` and `Volatility` at ~`0.06`). These capture return magnitude but contain zero directional information. All directional indicators (e.g. `RSI`, `MACD`, lag returns) have correlations close to 0.

---

## 3. Recommended Actions

To fix these issues, we need to restructure both the data alignment and the model architecture.

### Step 1: Fix the Sequence Alignment
Change `y.append(target[i])` in `create_sequences` to `y.append(target[i-1])` to align features ending at day $t$ with the target return from day $t$ close to day $t+1$ close.

```diff
 def create_sequences(features, target, window: int = WINDOW) -> tuple:
     X, y = [], []
     for i in range(window, len(features)):
         X.append(features[i-window:i])
-        y.append(target[i])
+        y.append(target[i-1])
     X = np.array(X)
     y = np.array(y)
     return X, y
```

### Step 2: Simplify the Model Architecture
Replace the complex Attention + Bidirectional LSTM with a simple feedforward or lightweight LSTM model with heavy regularization (e.g., L2 activity regularization, lower unit size, and high dropout) to force it to learn simple, robust features instead of memorizing noise:
```python
def build_lstm_model(input_shape: tuple) -> Model:
    inputs = Input(shape=input_shape)
    x = LSTM(16, dropout=0.4, recurrent_dropout=0.4)(inputs)
    outputs = Dense(3, activation="softmax")(x)
    model = Model(inputs=inputs, outputs=outputs)
    model.compile(optimizer=Adam(learning_rate=0.0005), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model
```

### Step 3: Regularize Class Weights
Instead of full inverse frequency weighting (which multiplies loss by 2.5x), use a smoothed class weighting scheme (e.g., square root frequency weighting) so the model doesn't over-predict `BUY` and `SELL` signals in search of high-weighted loss reductions:
```python
# Smoothed weights
class_weight = {k: (total / v) ** 0.5 for k, v in class_counts.items()}
```
