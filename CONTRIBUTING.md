# Contributing

- Backtests are full-cost-only. There is no light-cost mode.
- No result exists outside the canonical report schema from section 7.2: net return, Sharpe with block-bootstrap 95% CI, DSR with global config count, PF, win rate, per-trade EV bps with CI, max DD, trade count, trading-day count, symbol concentration, per-fold table, baseline comparison table, cost breakdown, and label-viability audit.
- Pre-register strategy-selection protocols before out-of-sample execution.
- Port audited components only when the unified design explicitly adopts them; keep source imports renamed to MERIDIAN package names.
