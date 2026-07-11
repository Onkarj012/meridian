# NIFTY Futures Router v0 — Port Inventory

Repo inspected: `/Users/onkarj012/Projects/market/intranet_optinet`  
Scope: read-only. All claims below cite file:line. Items not found explicitly are flagged **MISSING**.

---

## 1. Trained model artifact `final_long.lgb`

### Artifact location
- **File:** `models/router_v0/futures/final_long.lgb` (verified present, 11,530 bytes)

### Code that loads it
- `src/engine/strategies/futures.py:87-90`
- `scripts/research/backtest_futures.py:389`
- `scripts/research/forward_walk.py:553`
- `scripts/trading/paper_trade.py:414`
- `scripts/trading/live_signal.py:40`
- `scripts/research/tier1_validate.py:36`

All use the same pattern: `lgb.Booster(model_file=str(MODEL_PATH))`.

### LightGBM params / training code that produced it

**Verified model file facts** (loaded directly):
- `num_trees: 1`
- `objective: binary`
- `metric: binary_logloss`
- `learning_rate: 0.05`
- `num_leaves: 63`
- `min_data_in_leaf: 200`
- `feature_fraction: 0.85`
- `bagging_fraction: 0.85`
- `bagging_freq: 5`
- `lambda_l1: 0.1`
- `lambda_l2: 1.0`
- `is_unbalance: True`
- Feature count: 39 (see §2)

**Training params declared in code:**
- `scripts/research/backtest_futures.py:74-80` defines `LGBM_PARAMS` with the exact values above.
- `archive/scripts/train_futures_engine.py:120-134` defines `train_fold()` using these params; `train_final()` at `archive/scripts/train_futures_engine.py:169-178` saves `final_long.lgb` after training on all data before 2024-01-01.
- `archive/scripts/train_futures_engine.py:42` consumes `cache/router_v0/futures_features.parquet` and `models/router_v0/futures/futures_barrier_labels.parquet`.

**Note:** the archived training script `archive/scripts/train_futures_engine.py:54` contains a garbled import line (`from optinet_router.futures_features import FUTURES_FEATURES, add_regime` followed immediately by the dict literal), so the file as stored will not run verbatim. The effective training recipe is reconstructed from `backtest_futures.py:74-80` plus `train_futures_engine.py:120-178`.

**Training target:** binary `long_label` = 1 iff +0.40% is reached before −0.30% within 60 minutes. Label construction logic is in `archive/scripts/sprint1_futures.py:119-165`.

---

## 2. The 39 input features

### Canonical feature registry

The exact list is `FUTURES_FEATURES` in `src/engine/features.py:162-186`:

```python
FUTURES_FEATURES = [
    # Returns
    "ret_1m", "ret_5m", "ret_15m", "ret_30m", "ret_60m",
    "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m",
    # Volatility
    "atr_5m", "atr_15m", "atr_30m",
    "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    # VWAP
    "vwap_dev", "vwap_slope_5m",
    # Opening range
    "or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn",
    # OI
    "oi_chg_1m", "oi_chg_5m", "oi_chg_30m",
    "oi_long_buildup", "oi_short_buildup", "oi_short_cover", "oi_long_unwind",
    # Basis
    "basis", "basis_chg_30m",
    # Session
    "minute_of_day", "hour_of_day", "session_progress", "day_of_week",
    # Gap
    "gap_pct",
    # Trend
    "consec_bars", "ema_slope",
    # Volume
    "vol_oi_ratio", "vol_zscore",
]
```

Count = 39. The model file itself reports the identical ordered list when loaded.

### Where each feature is computed

All are computed in `src/engine/features.py:56-158` inside `compute_features(df, trade_date)`:

| Feature(s) | Computation location |
|---|---|
| `ret_*m`, `log_ret_*m` | `src/engine/features.py:64-66` |
| `atr_*m`, `realized_vol_*m` | `src/engine/features.py:70-83` |
| `vwap_dev`, `vwap_slope_5m` | `src/engine/features.py:86-90` |
| `or_dist_high/low`, `or_breakout_up/dn` | `src/engine/features.py:92-102` |
| `oi_chg_*m`, `oi_long/short_buildup/cover/unwind` | `src/engine/features.py:104-112` |
| `basis`, `basis_chg_30m` | `src/engine/features.py:114-116` |
| `minute_of_day`, `hour_of_day`, `session_progress`, `day_of_week` | `src/engine/features.py:118-126` |
| `gap_pct` | `src/engine/features.py:128-133` |
| `consec_bars`, `ema_slope` | `src/engine/features.py:135-149` |
| `vol_oi_ratio`, `vol_zscore` | `src/engine/features.py:151-155` |

`add_regime()` (used for filtering, not a model input) is at `src/engine/features.py:195-212`.

---

## 3. Trade geometry 40 / 30 / 60

### Definitions
- `TARGET_PCT = 0.0040` → +0.40% profit target
- `STOP_PCT = 0.0030` → −0.30% stop-loss
- `HORIZON = 60` → maximum 60-minute holding period

### Where defined
- `scripts/research/forward_walk.py:47-49`
- `scripts/research/backtest_futures.py:48-50`
- `scripts/trading/paper_trade.py:59-61`
- `src/engine/strategies/futures.py:44-46`
- `archive/scripts/sprint1_futures.py:59-61`

### Semantics
The geometry is a **path-dependent barrier**:
- LONG entry at price `P`.
- Target hit if price reaches `P * 1.0040`.
- Stop hit if price reaches `P * 0.9970`.
- If neither is touched within 60 minutes, the trade is exited at the 60th-minute close ("TIME" exit).

Simulation code implementing this:
- `scripts/research/forward_walk.py:400-437`
- `scripts/research/backtest_futures.py:175-201`
- `scripts/trading/paper_trade.py:147-185`

---

## 4. Selection policy: top-15%-of-day score percentile gating

### Confirmed

The forward walk gates entries using each day's **full-day 85th percentile** of `long_score`. Logic is at `scripts/research/forward_walk.py:468-477`:

```python
# Score
df["long_score"] = model.predict(df[FUTURES_FEATURES])

# Per-day percentile thresholds
p85 = df.groupby("trade_date")["long_score"].transform(
    lambda s: s.quantile(SIGNAL_PCT))
p95 = df.groupby("trade_date")["long_score"].transform(
    lambda s: s.quantile(HIGH_CONF_PCT))
df["take_long"] = df["long_score"] >= p85
df["size_mult"] = np.where(df["long_score"] >= p95, 1.5, 1.0)
```

### Supporting constants
- `SIGNAL_PCT = 0.85` → 85th percentile threshold (`scripts/research/forward_walk.py:59`)
- `HIGH_CONF_PCT = 0.95` → 95th percentile for 1.5× sizing (`scripts/research/forward_walk.py:60`)

### Hard filters applied before scoring
Same file, `scripts/research/forward_walk.py:457-466`:

```python
df = df[
    (mod >= ENTRY_MIN) &          # 09:45 or later
    (t < HARD_CUTOFF) &           # before 14:55
    ~((t >= SKIP_START) & (t < SKIP_END)) &  # skip 11:00-11:59
    ~df["regime"].isin(SKIP_REGIMES)         # skip "compression"
].copy()
```

Constants:
- `ENTRY_MIN = 30` (`scripts/research/forward_walk.py:58`) → 30 minutes after 09:15 = 09:45
- `HARD_CUTOFF = dtime(14, 55)` (`scripts/research/forward_walk.py:55`)
- `SKIP_START = dtime(11, 0)`, `SKIP_END = dtime(12, 0)` (`scripts/research/forward_walk.py:56-57`)
- `SKIP_REGIMES = {"compression"}` (`scripts/research/forward_walk.py:61`)
- LONG-only: the entire engine is long-only (`scripts/research/backtest_futures.py:67` sets `LONG_ONLY = True`).

The same gating logic is duplicated in production code at `src/engine/strategies/futures.py:124-150`.

---

## 5. Trade cap, position sizing, and halt logic

### Daily trade cap
- `MAX_TRADES = 3` (`scripts/research/forward_walk.py:54`, `scripts/research/backtest_futures.py:59`, `scripts/trading/paper_trade.py:71`).
- Enforced in the backtest loop: `scripts/research/forward_walk.py:514-517`; `scripts/research/backtest_futures.py:246-247`; `scripts/trading/paper_trade.py:258-259`.

### Position sizing
- Default size multiplier = 1.0.
- 1.5× when `long_score` ≥ 95th percentile of the day's eligible scores.
- Defined at `scripts/research/forward_walk.py:477`; `scripts/research/backtest_futures.py:232`; `scripts/trading/paper_trade.py:231`; `src/engine/strategies/futures.py:158`.

### Halt logic

**Per-session halts (backtest / paper runner):**
- `DAILY_HALT = -15000.0` → stop taking new entries if daily net PnL ≤ −₹15,000 (`scripts/research/forward_walk.py:53`, `scripts/trading/paper_trade.py:65`).
- `INTRADAY_CUM_HALT = -9000.0` → tighter cumulative session halt (`scripts/trading/paper_trade.py:67`; `scripts/research/backtest_futures.py:56`).
- `STOP_FLOOR = -3000.0` → per-trade net-PnL floor (`scripts/research/forward_walk.py:52`, `scripts/trading/paper_trade.py:64`).

**Operational hard/soft halts (`scripts/trading/paper_status.py`):**
- Hard halt: cumulative drawdown ≤ −₹150,000 writes `results/router_v0/PAPER_TRADING_HALTED` and blocks further runs (`scripts/trading/paper_status.py:31`, `114-117`, `435-443`).
- Soft halts (alert only):
  - Trailing 30-day Sharpe < 0.5 (with ≥30 trades) (`scripts/trading/paper_status.py:32`, `118-121`).
  - Trailing 5-day net PnL ≤ −₹50,000 (`scripts/trading/paper_status.py:33`, `122-125`).
  - ≥7 consecutive losing days (`scripts/trading/paper_status.py:34`, `126-128`).

The kill-switch is also checked at run start in `scripts/trading/paper_trade.py:374-377` and is defined in `src/engine/orders.py:31`.

---

## 6. Reference ledger: 1,128 trades / ~₹429k / ~875 time exits

### Storage
- **Primary file:** `results/router_v0/phase3_fwd_no_guard.parquet`
- Verified contents (read directly):
  - Rows: **1,128**
  - Total net PnL: **₹429,227.22**
  - Exit reasons: **TIME = 875**, STOP = 155, TARGET = 98
  - Time-exit net PnL: **₹313,442.50**
  - Date range: 2024-11-04 → 2026-05-15
  - Unique trading days: 376

### How to regenerate
Run the Phase-3 forward-walk driver:

```bash
cd /Users/onkarj012/Projects/market/intranet_optinet
PYTHONPATH=src .venv/bin/python scripts/research/forward_walk.py
```

This writes:
- `results/router_v0/phase3_fwd_no_guard.parquet` (Variant A, 1,128 trades)
- `results/router_v0/phase3_fwd_with_guard.parquet` (Variant B, 879 trades)
- `results/router_v0/forward_walk_summary.json`

Source: `scripts/research/forward_walk.py:616-656`.

### Related ledger files present
- `results/router_v0/paper_trading_ledger.csv` — live paper-trading ledger (Variant A + C + optional bootstrap rows), appended by `scripts/trading/paper_trade.py`.
- `results/router_v0/futures_long_2024_trades.parquet` — 622-trade 2024 blind window (+₹287,536), generated by `scripts/research/backtest_futures.py`.

---

## 7. Cost model: flat ₹105 per trade

### Definition
- `COSTS_INR = 105.0` declared as "round-trip: brokerage + STT + GST + slippage" (`scripts/research/backtest_futures.py:52`; `scripts/research/forward_walk.py:51`; `scripts/trading/paper_trade.py:63`; `src/engine/strategies/futures.py:48`).

### Application
Costs are multiplied by `size_mult` and subtracted from gross PnL:

```python
gross = (exit_px - entry_px) * LOT * size_mult
net   = gross - COSTS_INR * size_mult
```

Locations:
- `scripts/research/forward_walk.py:425-426`
- `scripts/research/backtest_futures.py:192-193`
- `scripts/trading/paper_trade.py:173-175`

### Origin
The original sprint used `COSTS_PER_LOT_INR = 80.0` + `SLIPPAGE_PER_LOT_INR = 25.0` = ₹105 (`archive/scripts/sprint1_futures.py:67-68`).

---

## 8. Fill / exit semantics in the original backtest

### Entry fill
- Entry price = **close of the signal bar** (`fut_close` at the minute the score crosses the threshold).
- Implemented in:
  - `scripts/research/forward_walk.py:527`
  - `scripts/research/backtest_futures.py:272`
  - `scripts/trading/paper_trade.py:270`

### Exit detection
- Exits are evaluated on **subsequent 1-minute close prices only** (`fut_close`), not high/low.
- The code iterates forward bar-by-bar and checks:
  - If `fut_close >= target_px` → exit at target.
  - Else if `fut_close <= stop_px` → exit at stop.
- Source: `scripts/research/forward_walk.py:409-416`; `scripts/research/backtest_futures.py:184-187`; `scripts/trading/paper_trade.py:156-163`.

**Stop-vs-target ambiguity:** target is checked **before** stop in the loop. If both conditions would be satisfied on the same bar, target wins. This is the explicit ordering in the code.

### Time exit
- If neither target nor stop is hit within `HORIZON` (60) bars, exit at the close of the 60th bar.
- Exit reason is `"TIME"`.
- Source: `scripts/research/forward_walk.py:417-423`; `scripts/research/backtest_futures.py:188-190`; `scripts/trading/paper_trade.py:164-172`.

**Note:** an older version commented that high/low was unavailable (`archive/scripts/sprint1_futures.py:242-243`). The current validated engine continues to use close-only detection.

---

## 9. Data inputs consumed by the forward walk

### Phase-3 forward walk (Nov 2024 → May 2026)
The forward walk does **not** use front-month futures minute data. It uses the **NIFTY spot/index minute bars as a futures proxy**:

- `data/nifty_intraday/NIFTY 50_minute.csv`
  - Columns: `date, open, high, low, close, volume`
  - Frequency: 1 minute
  - Full range in file: 2015-01-09 → 2026-06-16
  - Forward-walk window used: 2024-11-01 → 2026-05-31 (`scripts/research/forward_walk.py:64-65`)
  - Actual trades produced: 2024-11-04 → 2026-05-15

In the proxy builder (`scripts/research/forward_walk.py:72-152`):
- Index OHLC columns are renamed to `f_open`, `f_high`, `f_low`, `f_close`.
- `f_vol` is set to a constant `1.0` because the source CSV has `volume = 0` for every minute.
- `f_oi` is set to `0.0` because OI is unavailable.
- `s_close` is set equal to `f_close` (spot ≈ futures basis assumption).

### Auxiliary daily data
- `data/nifty_intraday/INDIA VIX_day.csv` — prior-day VIX for regime-guard experiments (`scripts/research/forward_walk.py:159-165`).
- `data/indices/nifty_daily.csv` — prior 5-day NIFTY return for regime guards (`scripts/research/forward_walk.py:168-177`).

### Original model-training data (pre-2024)
The actual `final_long.lgb` was trained on:
- `cache/router_v0/futures_features.parquet` (feature matrix)
- `models/router_v0/futures/futures_barrier_labels.parquet` (barrier labels)

These cached files are present. The underlying raw inputs were:
- `data/option_data/nifty_data/nifty_fut/*.csv` — actual NIFTY futures minute bars
- `data/option_data/nifty_data/nifty_spot/*.csv` — spot minute bars

**MISSING:** the `data/option_data/` directory tree is **absent** from the working tree, so the original futures-minute raw inputs cannot be inspected. The builder function that would regenerate `futures_features.parquet` from raw futures data is `src/engine/features.py:215-257` (`discover_days` / `build_all`), but no explicit script that calls `build_all()` to produce `cache/router_v0/futures_features.parquet` was found in the repo.

### Barrier labels
- Generated by `archive/scripts/sprint1_futures.py:119-165`.
- Consumes actual futures minute bars (`fut_close`) and produces `long_label` / `short_label` using the same +0.40% / −0.30% / 60-min barrier rule.

---

## Summary table for the porting agent

| Item | Value / location |
|---|---|
| Model file | `models/router_v0/futures/final_long.lgb` |
| Model loader pattern | `lgb.Booster(model_file=...)` |
| Feature list | `src/engine/features.py:162-186` |
| Feature computation | `src/engine/features.py:56-158` |
| Geometry | TARGET_PCT=0.0040, STOP_PCT=0.0030, HORIZON=60 |
| Selection threshold | 85th percentile of day's `long_score`; 95th pct → 1.5× size |
| Selection code | `scripts/research/forward_walk.py:468-477` |
| Daily trade cap | 3 |
| Costs | ₹105 × `size_mult`, subtracted from gross |
| Reference ledger | `results/router_v0/phase3_fwd_no_guard.parquet` |
| Regeneration command | `PYTHONPATH=src .venv/bin/python scripts/research/forward_walk.py` |
| Forward-walk data proxy | `data/nifty_intraday/NIFTY 50_minute.csv` (index bars, futures proxy) |
| Forward window | 2024-11-04 → 2026-05-15 (376 days, 1,128 trades) |

---

## Orchestrator corrections (Fable, 2026-07-10)

1. **§9 MISSING claim is WRONG**: `data/option_data/nifty_data/{nifty_fut,nifty_options,nifty_spot}` exists in the working tree — 1,246 futures minute CSVs under `nifty_fut/`. The inventory agent's glob was likely gitignore-filtered. Raw futures inputs ARE inspectable and meridian's `ingest/optinet_data.py` already reads them.
2. **num_trees = 1**: `final_long.lgb` is a single-tree model (500 iterations configured, 1 tree saved). Matches joint plan step 6 ("single tree → multi-tree"). Port must reproduce single-tree scoring exactly — load the .lgb artifact, do not retrain.

Provenance: inventory by opencode-go (kimi-k2.7-code), read-only pass over intranet_optinet; ledger numbers verified by direct parquet read (1,128 trades, ₹429,227.22, TIME=875/₹313,442.50, window 2024-11-04→2026-05-15, 376 days).
