"""FII/DII flow helpers with point-in-time availability."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

def release_timestamp(trade_date: date, release_time: time = time(18, 30)) -> datetime:
    return datetime.combine(trade_date, release_time, tzinfo=ZoneInfo("Asia/Kolkata"))

def parse_fii_dii_payload(payload: list[dict], fetched_at: datetime | None = None) -> list[dict]:
    row: dict[str, object] = {}
    for item in payload:
        category=str(item.get("category","")).lower(); raw_date=item.get("date")
        if raw_date and "date" not in row:
            row["date"]=datetime.strptime(str(raw_date), "%d-%b-%Y").date()
        buy=_num(item.get("buyValue",0)); sell=_num(item.get("sellValue",0)); net=_num(item.get("netValue",0))
        if "fii" in category or "fpi" in category: row.update({"fii_buy":buy,"fii_sell":sell,"fii_net":net})
        elif "dii" in category: row.update({"dii_buy":buy,"dii_sell":sell,"dii_net":net})
    if "date" not in row: return []
    trade_date=row["date"]
    row["total_inst_net"]=float(row.get("fii_net",0.0))+float(row.get("dii_net",0.0))
    row["released_at"]=release_timestamp(trade_date).isoformat()
    row["fetched_at"]=(fetched_at or datetime.now(timezone.utc)).isoformat()
    row["usable_from"]=trade_date+timedelta(days=1)
    return [row]

def load_as_features(rows: list[dict]) -> list[dict]:
    out=[]
    history={"fii_net":[],"dii_net":[],"total_inst_net":[]}
    for row in sorted(rows, key=lambda r:r["usable_from"]):
        item=dict(row); item["date"]=item["usable_from"]
        for col in history:
            history[col].append(float(item.get(col,0.0) or 0.0))
            item[f"{col}_5d"]=sum(history[col][-5:])/min(len(history[col]),5)
        out.append(item)
    return out

def _num(value: object) -> float:
    try: return float(str(value).replace(",", ""))
    except (TypeError, ValueError): return float("nan")
