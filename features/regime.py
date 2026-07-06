"""Regime feature helpers."""

def build_regime_features(rows=None, *args, **kwargs):
    out = []
    for row in list(rows or []):
        item = dict(row)
        breadth = float(row.get("breadth", row.get("advance_decline", 0)) or 0)
        trend = float(row.get("relative_strength", row.get("return_20", 0)) or 0)
        item["trend_regime"] = "up" if trend > 0 else "down" if trend < 0 else "flat"
        item["breadth_regime"] = "broad" if breadth > 0 else "narrow" if breadth < 0 else "neutral"
        out.append(item)
    return out
