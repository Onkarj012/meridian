"""Calendar feature helpers."""

from datetime import datetime

def build_calendar_features(rows=None, *args, **kwargs):
    out = []
    for row in list(rows or []):
        item = dict(row)
        ts = str(row.get("timestamp", ""))[:10]
        try:
            day = datetime.fromisoformat(ts)
            item["day_of_week"] = day.weekday()
            item["month"] = day.month
        except ValueError:
            item["day_of_week"] = None
            item["month"] = None
        out.append(item)
    return out
