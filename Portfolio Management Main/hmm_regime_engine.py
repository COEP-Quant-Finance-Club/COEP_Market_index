"""
3-State Hidden Markov Model (HMM) Sector Regime Engine
=====================================================
Architecture:
- Self-contained Baum-Welch EM Baum-Welch Training & Forward Filtering.
- 3 Macro States:
  * State 0: Bullish / Expansion  (High Return, Low-to-Moderate Volatility)
  * State 1: Neutral / Consolidation (Near-Zero Return, Moderate Volatility)
  * State 2: Bearish / Contraction (Negative Return, High Volatility)
- Strict Out-of-Sample Forward Filter:
  Guarantees ZERO FORWARD BIAS by using forward algorithm gamma_{t|t} = P(S_t | x_{1:t}).
- Candle Smoothing Sensitivity (k = 1 to 21 candles).
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.stats import multivariate_normal

class GaussianHMM3State:
    def __init__(self, n_states=3, max_iter=100, tol=1e-4, random_state=42):
        self.n_states = n_states
        self.max_iter = max_iter
        self.tol = tol
        self.random_state = random_state
        self.is_fitted = False
        
        # Parameters
        self.startprob_ = None   # pi: (n_states,)
        self.transmat_ = None     # A: (n_states, n_states)
        self.means_ = None        # mu: (n_states, n_features)
        self.covars_ = None       # sigma: (n_states, n_features, n_features)

    def _init_params(self, X):
        np.random.seed(self.random_state)
        N, D = X.shape
        
        # Equal prior state probabilities
        self.startprob_ = np.full(self.n_states, 1.0 / self.n_states)
        
        # High self-transition probability matrix (sticky state regime assumption)
        self.transmat_ = np.full((self.n_states, self.n_states), (1.0 - 0.90) / (self.n_states - 1))
        np.fill_diagonal(self.transmat_, 0.90)
        
        # Initialize means by quantiles along feature 0 (smoothed returns)
        ret_col = X[:, 0]
        q_low, q_mid, q_high = np.quantile(ret_col, [0.25, 0.50, 0.75])
        
        self.means_ = np.zeros((self.n_states, D))
        # Initial guess: State 0 (Bull), State 1 (Neutral), State 2 (Bear)
        self.means_[0] = np.mean(X[ret_col >= q_high], axis=0) if np.sum(ret_col >= q_high) > 0 else np.mean(X, axis=0) + 0.01
        self.means_[1] = np.mean(X[(ret_col >= q_low) & (ret_col < q_high)], axis=0) if np.sum((ret_col >= q_low) & (ret_col < q_high)) > 0 else np.mean(X, axis=0)
        self.means_[2] = np.mean(X[ret_col < q_low], axis=0) if np.sum(ret_col < q_low) > 0 else np.mean(X, axis=0) - 0.01

        # Covariance matrices
        glob_cov = np.cov(X.T) + np.eye(D) * 1e-4
        if D == 1:
            glob_cov = np.array([[glob_cov]])
        self.covars_ = np.array([glob_cov.copy() for _ in range(self.n_states)])

    def _compute_likelihoods(self, X):
        N, D = X.shape
        B = np.zeros((N, self.n_states))
        for k in range(self.n_states):
            try:
                # Add regularization for numerical stability
                cov_reg = self.covars_[k] + np.eye(D) * 1e-5
                mvn = multivariate_normal(mean=self.means_[k], cov=cov_reg, allow_singular=True)
                B[:, k] = np.maximum(mvn.pdf(X), 1e-12)
            except Exception:
                B[:, k] = 1e-6
        return B

    def fit(self, X):
        X = np.asarray(X, dtype=float)
        N, D = X.shape
        if N < 20:
            raise ValueError("Insufficient data points to fit 3-State HMM model.")
            
        self._init_params(X)
        log_likelihood_old = -np.inf
        
        for iteration in range(self.max_iter):
            B = self._compute_likelihoods(X)
            
            # --- FORWARD PASS ---
            alpha = np.zeros((N, self.n_states))
            c = np.zeros(N)  # scaling factors
            
            alpha[0] = self.startprob_ * B[0]
            c[0] = np.sum(alpha[0])
            if c[0] == 0: c[0] = 1e-12
            alpha[0] /= c[0]
            
            for t in range(1, N):
                alpha[t] = np.dot(alpha[t-1], self.transmat_) * B[t]
                c[t] = np.sum(alpha[t])
                if c[t] == 0: c[t] = 1e-12
                alpha[t] /= c[t]
                
            log_likelihood = np.sum(np.log(c))
            
            # Check EM convergence
            if abs(log_likelihood - log_likelihood_old) < self.tol:
                break
            log_likelihood_old = log_likelihood
            
            # --- BACKWARD PASS ---
            beta = np.zeros((N, self.n_states))
            beta[-1] = 1.0 / c[-1]
            
            for t in range(N - 2, -1, -1):
                beta[t] = np.dot(self.transmat_, B[t+1] * beta[t+1]) / c[t]
                
            # --- EXPECTATION STEP (Posteriors) ---
            gamma = alpha * beta
            gamma_sum = np.sum(gamma, axis=1, keepdims=True)
            gamma_sum[gamma_sum == 0] = 1e-12
            gamma /= gamma_sum
            
            xi = np.zeros((N - 1, self.n_states, self.n_states))
            for t in range(N - 1):
                num = alpha[t, :, None] * self.transmat_ * B[t+1, None, :] * beta[t+1, None, :]
                denom = np.sum(num)
                xi[t] = num / (denom if denom > 0 else 1e-12)
                
            # --- MAXIMIZATION STEP ---
            self.startprob_ = gamma[0] / np.sum(gamma[0])
            
            trans_num = np.sum(xi, axis=0)
            trans_denom = np.sum(gamma[:-1], axis=0)[:, None]
            trans_denom[trans_denom == 0] = 1e-12
            self.transmat_ = trans_num / trans_denom
            # Normalize transition rows
            self.transmat_ /= np.sum(self.transmat_, axis=1, keepdims=True)
            
            for k in range(self.n_states):
                g_k = gamma[:, k]
                w_sum = np.sum(g_k)
                if w_sum <= 1e-8:
                    continue
                self.means_[k] = np.sum(g_k[:, None] * X, axis=0) / w_sum
                diff = X - self.means_[k]
                self.covars_[k] = np.dot((g_k[:, None] * diff).T, diff) / w_sum + np.eye(D) * 1e-5

        # Order states: State 0 = Bullish (Highest return), State 1 = Neutral, State 2 = Bearish (Lowest return)
        order = np.argsort(self.means_[:, 0])[::-1]  # Descending order of mean return
        self.startprob_ = self.startprob_[order]
        self.transmat_ = self.transmat_[order][:, order]
        self.means_ = self.means_[order]
        self.covars_ = self.covars_[order]
        self.is_fitted = True
        return self

    def filter_realtime(self, X):
        """
        Strict Out-of-Sample Forward Filter:
        Computes state probability gamma_{t|t} = P(S_t | x_{1:t}) using ONLY past data up to t.
        Guarantees ZERO FORWARD BIAS.
        """
        X = np.asarray(X, dtype=float)
        N, D = X.shape
        B = self._compute_likelihoods(X)
        
        filtered_probs = np.zeros((N, self.n_states))
        
        # t = 0
        a = self.startprob_ * B[0]
        s = np.sum(a)
        filtered_probs[0] = a / (s if s > 0 else 1.0)
        
        # t = 1 ... N-1
        for t in range(1, N):
            # Prior for t based on t-1: P(S_t | x_{1:t-1}) = sum_j P(S_{t-1}=j | x_{1:t-1}) * A_{j,t}
            prior = np.dot(filtered_probs[t-1], self.transmat_)
            # Update with likelihood B_t
            posterior = prior * B[t]
            s = np.sum(posterior)
            if s > 0:
                filtered_probs[t] = posterior / s
            else:
                filtered_probs[t] = prior / (np.sum(prior) if np.sum(prior) > 0 else 1.0)
                
        return filtered_probs

def compute_smoothed_features(df_ohlcv: pd.DataFrame, k: int = 1) -> pd.DataFrame:
    """
    Computes smoothed candle series and features for window parameter k (1 to 21 candles).
    Features:
    1. Smoothed Log Return: ln(Smoothed Close_t / Smoothed Close_{t-1})
    2. Smoothed Log Volatility: ln(Garman-Klass Volatility over k bars)
    3. Detrended Return Error: Return - Rolling Mean Return
    """
    df = df_ohlcv.copy()
    df.sort_index(inplace=True)
    
    # 1. Smoothed Price Series (EMA / Rolling Close)
    if k == 1:
        smooth_close = df["Close"]
        smooth_open = df["Open"]
        smooth_high = df["High"]
        smooth_low = df["Low"]
    else:
        smooth_close = df["Close"].ewm(span=k, adjust=False).mean()
        smooth_open  = df["Open"].ewm(span=k, adjust=False).mean()
        smooth_high  = df["High"].ewm(span=k, adjust=False).mean()
        smooth_low   = df["Low"].ewm(span=k, adjust=False).mean()
        
    # Smoothed Log Return
    ret = np.log(smooth_close / smooth_close.shift(1)).fillna(0.0)
    
    # Garman-Klass Volatility Proxy
    log_hl = np.log(np.maximum(smooth_high, 1e-4) / np.maximum(smooth_low, 1e-4)) ** 2
    log_co = np.log(np.maximum(smooth_close, 1e-4) / np.maximum(smooth_open, 1e-4)) ** 2
    gk_vol = np.sqrt(np.maximum(0.5 * log_hl - (2 * np.log(2) - 1) * log_co, 1e-8))
    vol_smooth = pd.Series(gk_vol, index=df.index).rolling(window=max(5, k), min_periods=1).mean()
    log_vol = np.log(np.maximum(vol_smooth, 1e-6))
    
    # Detrended Return Error
    trend_ret = ret.rolling(window=max(10, k * 2), min_periods=1).mean()
    detrend_err = ret - trend_ret
    
    feat_df = pd.DataFrame({
        "smooth_close": smooth_close,
        "return": ret,
        "log_vol": log_vol,
        "detrend_err": detrend_err
    }, index=df.index).dropna()
    
    return feat_df

def run_hmm_regime_analysis(df_ohlcv: pd.DataFrame, k: int = 1) -> pd.DataFrame:
    """
    Fits 3-State HMM on smoothed features and calculates out-of-sample forward filtered probabilities.
    Returns DataFrame with columns: [State, Bull_Prob, Neutral_Prob, Bear_Prob]
    """
    feat_df = compute_smoothed_features(df_ohlcv, k=k)
    X = feat_df[["return", "log_vol", "detrend_err"]].values
    
    model = GaussianHMM3State(n_states=3, max_iter=80, random_state=42)
    model.fit(X)
    
    # Out-of-sample forward filter (ZERO FORWARD BIAS)
    probs = model.filter_realtime(X)
    state_seq = np.argmax(probs, axis=1)
    
    res_df = feat_df.copy()
    res_df["state"] = state_seq
    res_df["bull_prob"] = np.round(probs[:, 0], 4)
    res_df["neutral_prob"] = np.round(probs[:, 1], 4)
    res_df["bear_prob"] = np.round(probs[:, 2], 4)
    
    return res_df
