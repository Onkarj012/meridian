"""Overnight and open-gap feature helpers."""

def build_gap_features(rows=None, *args, **kwargs):
    rows = list(rows or [])
    previous_close = {}
    out = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        open_price = float(row.get("open", 0) or 0)
        close = float(row.get("close", 0) or 0)
        prior = previous_close.get(symbol)
        item = dict(row)
        item["opening_gap"] = (open_price / prior - 1.0) if prior else 0.0
        if close:
            previous_close[symbol] = close
        out.append(item)
    return out
