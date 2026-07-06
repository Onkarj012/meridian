"""Trailing volatility feature helpers."""

from statistics import pstdev

def build_volatility_features(rows=None, *args, window=20, **kwargs):
    rows = list(rows or [])
    by_symbol = {}
    out = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        history = by_symbol.setdefault(symbol, [])
        close = float(row.get("close", 0) or 0)
        high = float(row.get("high", close) or close)
        low = float(row.get("low", close) or close)
        item = dict(row)
        item["intraday_range"] = high - low
        item["intraday_range_bps"] = ((high - low) / close * 10000.0) if close else 0.0
        returns = history[-window:]
        item["rolling_volatility"] = pstdev(returns) if len(returns) > 1 else 0.0
        if history and history[-1]:
            history.append(close / history[-1] - 1.0)
        elif close:
            history.append(0.0)
        out.append(item)
    return out
