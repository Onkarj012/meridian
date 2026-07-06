"""Rung-0 baseline evaluation."""

def evaluate_rung0_baselines(returns=None, **kwargs):
    values = [float(v) for v in (returns or [])]
    mean = sum(values) / len(values) if values else 0.0
    return {"random": {"net_sharpe": 0.0}, "buy_and_hold": {"expected_value_bps": mean}, "momentum": {"expected_value_bps": mean}}
