"""Trailing price and return feature helpers."""

def build_price_features(rows=None, *args, windows=(1, 3, 6, 12), price_key="close", **kwargs):
    rows = list(rows or [])
    by_symbol = {}
    out = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        history = by_symbol.setdefault(symbol, [])
        item = dict(row)
        price = float(row.get(price_key, 0) or 0)
        for window in windows:
            if len(history) >= window and history[-window] != 0 and price != 0:
                item[f"return_{window}"] = price / history[-window] - 1.0
            else:
                item[f"return_{window}"] = 0.0
        history.append(price)
        out.append(item)
    return out
