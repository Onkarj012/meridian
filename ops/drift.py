"""Simple feature drift monitor."""
from __future__ import annotations

import pandas as pd


def monitor_drift(live: pd.DataFrame, reference: pd.DataFrame, *, z_threshold: float = 3.0) -> dict:
    alerts: list[str] = []
    for col in live.select_dtypes("number").columns.intersection(reference.select_dtypes("number").columns):
        ref_std = float(reference[col].std(ddof=0))
        if ref_std == 0.0:
            continue
        z = abs(float(live[col].mean()) - float(reference[col].mean())) / ref_std
        if z >= z_threshold:
            alerts.append(f"{col}: mean drift z={z:.2f}")
    return {"ok": not alerts, "alerts": alerts}
