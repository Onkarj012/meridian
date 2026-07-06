"""Volume context feature helpers."""

def build_volume_features(rows=None, *args, window=20, **kwargs):
    history = {}
    out = []
    for row in list(rows or []):
        symbol = str(row.get("symbol", ""))
        volume = float(row.get("volume", 0) or 0)
        values = history.setdefault(symbol, [])
        avg = sum(values[-window:]) / min(len(values), window) if values else 0.0
        item = dict(row)
        item["volume_ratio"] = volume / avg if avg else 1.0
        item["volume_shock"] = item["volume_ratio"] - 1.0
        values.append(volume)
        out.append(item)
    return out
