"""Sector and index relative-strength feature helpers."""

def build_relative_strength_features(rows=None, *args, benchmark_returns=None, **kwargs):
    benchmark_returns = benchmark_returns or {}
    out = []
    for row in list(rows or []):
        item = dict(row)
        ts = str(row.get("timestamp", ""))
        stock_return = float(row.get("return_1", row.get("return", 0)) or 0)
        benchmark = float(benchmark_returns.get(ts, 0.0) or 0.0)
        item["relative_strength"] = stock_return - benchmark
        out.append(item)
    return out
