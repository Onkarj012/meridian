"""Confidence threshold selection inside validation folds."""

def select_threshold(results=None, *, metric="net_expectancy_bps", **kwargs):
    rows = list(results or [])
    if not rows:
        return None
    return max(rows, key=lambda row: float(row.get(metric, 0) or 0)).get("threshold")
