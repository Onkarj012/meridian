# MERIDIAN

MERIDIAN is a research-first, promotion-gated stock recommendation and trading system for Indian markets. It treats each recommendation as the final output of a contracted and audited pipeline: raw sources -> source contracts -> bronze/silver/gold lake -> causal features and label contracts -> candidate signals -> evidence engine -> promotion -> paper trading -> daily recommendations.

## Three layers

1. **Data bedrock**: contracted, point-in-time-correct, versioned market data for NSE, including bronze/silver/gold storage, source contracts, and quarantine.
2. **Evidence engine**: a standing harness for causal feature contracts, full-cost backtests, pre-registered walk-forward runs, promotion gates, and sealed tests.
3. **Execution and recommendation layer**: daily cron, paper ledger, risk governor, halts, dashboard, and calibrated recommendations from promoted signals only.

## Strategy sleeves

- **Sleeve F (Futures Router)**: NIFTY futures intraday router, ported as the first promoted sleeve template after replacing proxy data with real futures feed.
- **Sleeve M (Intraday Equity Momentum)**: Asterion's alive-but-starved momentum slice, scaled to a larger universe and longer history.
- **Sleeve X (Cross-sectional Equity Swing)**: daily-horizon long/short equity ranking over Nifty 100-200, using economically viable targets and gated evidence.

## Design principles

- **P1 - Label economics before modeling.** Every target definition must pass a *label viability audit*: natural hit rate vs. post-cost breakeven win rate at realistic slippage, before any model trains on it. (Directly from optinet's `v8_diagnosis.json` method.)
- **P2 - Full Indian costs from day one, no light-cost mode.** StockXpert's +24.41% headline collapsed to +11.51% under full costs (~41 bps round-trip delivery) and to negative under walk-forward. The backtest engine has exactly one cost mode: full.
- **P3 - Evidence ladder with hard promotion gates.** Baselines (random, momentum, buy-and-hold) -> linear/GBM -> deep, each gated on: CI-low of expected value > 0 after costs, minimum trade count, minimum trading days, maximum symbol concentration, stability across folds (worst-fold metric, not mean). A signal that fails stays quarantined.
- **P4 - Pre-register, then seal.** Strategy-selection protocols are committed to git *before* out-of-sample execution (StockXpert's protocol). Final holdout periods are sealed and spendable once (Asterion's sealed test).
- **P5 - Point-in-time everything.** PIT index constituents (kills StockXpert's survivorship flaw), PIT FII/DII availability timestamps (kills Recommender V2's leakage risk), no proxy/actual instrument skew (kills the router's index-proxy weakness), trailing-only features enforced by contract, not convention.
- **P6 - Calibrated probabilities, selective execution.** StockXpert's isotonic calibration + confidence thresholding is the right interface between model and money: trade only high-conviction, sized by conviction, and report coverage honestly.
- **P7 - Prefer starving a good signal to feeding a bad one.** More data/universe for signals that are alive-but-starved (Sleeve M); no rescue attempts for signals that are structurally dead (V8).

## Promotion gates

A signal is promoted only if, on pre-registered walk-forward, all of:

1. CI-low (block-bootstrap, 95%) of per-trade EV > 0 after full costs.
2. ≥ 300 trades across ≥ 100 trading days (Sleeve M was rejected at 97 trades — right call; scale data, not standards).
3. Max single-symbol trade share ≤ 20%; ≥ 20 distinct symbols (equity sleeves).
4. Positive in ≥ 70% of folds; worst fold above a floor.
5. Beats all rung-0 baselines on net Sharpe.
6. Deflated Sharpe Ratio > 0 given the *full* number of configurations ever evaluated (StockXpert computed DSR(k=144) — keep an honest global k counter).
7. Sealed test (untouched final period) spent once, passes 1-5.
