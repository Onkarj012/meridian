"""Liquidity feature helpers."""

def build_liquidity_features(rows=None, *args, adv_window=20, **kwargs):
    history = {}
    out = []
    for row in list(rows or []):
        symbol = str(row.get("symbol", ""))
        close = float(row.get("close", 0) or 0)
        volume = float(row.get("volume", 0) or 0)
        traded_value = close * volume
        values = history.setdefault(symbol, [])
        adv = sum(values[-adv_window:]) / min(len(values), adv_window) if values else traded_value
        item = dict(row)
        item["traded_value"] = traded_value
        item["adv"] = adv
        item["liquidity_bucket"] = "high" if adv >= 100_000_000 else "medium" if adv >= 10_000_000 else "low"
        values.append(traded_value)
        out.append(item)
    return out
