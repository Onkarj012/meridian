"""Quarantined sentiment feature helpers."""

def build_sentiment_features(rows=None, *args, quarantine_status="quarantined", **kwargs):
    out = []
    for row in list(rows or []):
        item = dict(row)
        item.setdefault("sentiment_score", 0.0)
        item["sentiment_quarantine_status"] = quarantine_status
        out.append(item)
    return out
