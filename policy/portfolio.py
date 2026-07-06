"""Portfolio order filtering helpers."""

def build_portfolio_orders(candidates=None, *, max_positions=10, **kwargs):
    ranked = sorted(list(candidates or []), key=lambda item: float(item.get("score", item.get("probability", 0)) or 0), reverse=True)
    return ranked[:max_positions]
