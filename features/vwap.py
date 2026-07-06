"""Intraday VWAP feature helpers."""

def build_vwap_features(rows=None, *args, **kwargs):
    totals = {}
    out = []
    for row in list(rows or []):
        symbol = str(row.get("symbol", ""))
        price = float(row.get("close", 0) or 0)
        volume = float(row.get("volume", 0) or 0)
        notional, qty = totals.get(symbol, (0.0, 0.0))
        notional += price * volume
        qty += volume
        vwap = notional / qty if qty else price
        totals[symbol] = (notional, qty)
        item = dict(row)
        item["vwap"] = vwap
        item["vwap_distance"] = (price / vwap - 1.0) if vwap else 0.0
        out.append(item)
    return out
