"""Evaluated regime gate overlay."""

def apply_regime_gate(candidates=None, *, allowed_regimes=None, current_regime=None, **kwargs):
    if allowed_regimes is None or current_regime in allowed_regimes:
        return list(candidates or [])
    return []
