"""Cross-sectional rank feature helpers."""

def build_cross_sectional_features(rows=None, *args, score_key="return_1", **kwargs):
    groups = {}
    for row in list(rows or []):
        groups.setdefault(str(row.get("timestamp", "")), []).append(row)
    out = []
    for _, group in sorted(groups.items()):
        ranked = sorted(group, key=lambda row: float(row.get(score_key, 0) or 0))
        denom = max(len(ranked) - 1, 1)
        ranks = {id(row): index / denom for index, row in enumerate(ranked)}
        for row in group:
            item = dict(row)
            item[f"{score_key}_cross_sectional_rank"] = ranks[id(row)]
            out.append(item)
    return out
