# Meridian Local Full-System Run

- Run ID: full-system-local-20260706
- Scope: local deterministic smoke/evidence path; no network credentials; no live trading
- Limitation: no packaged real-market full training pipeline or local market dataset was present; the training helper returned no_train_fn.
- Backtest: 2 trades, hit rate 100.00%, net score after costs 1.994600, cost model indian_equity_full_cost_v1.
- Sleeve M Phase 3: quarantined (beats_rung0_baselines_net_sharpe); trades 500, trading days 100.
- Sleeve X Phase 4: quarantined (ci_low_positive_full_cost); trades 300, trading days 150.
- Daily DAG: ok; manifest runs/full-system-local-20260706/manifests/daily_dag.jsonl.

Promotion remained quarantined because baseline-beating, positive DSR, and sealed-test gates are not satisfied by this local deterministic run.
