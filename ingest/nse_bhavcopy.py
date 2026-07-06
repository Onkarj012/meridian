"""NSE bhavcopy parsing and point-in-time availability stamps."""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from ingest.kite import data_root


def availability_timestamp(trade_date: date, release_time: time = time(18, 0)) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(trade_date, release_time), tz="Asia/Kolkata")


def usable_from_date(trade_date: date) -> date:
    return trade_date + timedelta(days=1)


def parse_cm_bhavcopy(content: bytes, trade_date: date) -> pd.DataFrame:
    raw = pd.read_csv(io.BytesIO(content), dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    raw = raw[raw["SERIES"].str.strip() == "EQ"].copy()
    df = pd.DataFrame()
    df["symbol"] = raw["SYMBOL"].str.strip()
    df["date"] = pd.to_datetime(trade_date)
    df["close"] = pd.to_numeric(raw["CLOSE_PRICE"], errors="coerce")
    df["prev_close"] = pd.to_numeric(raw["PREV_CLOSE"], errors="coerce")
    df["avg_price"] = pd.to_numeric(raw["AVG_PRICE"], errors="coerce")
    df["volume"] = pd.to_numeric(raw["TTL_TRD_QNTY"], errors="coerce")
    df["turnover_lacs"] = pd.to_numeric(raw["TURNOVER_LACS"], errors="coerce")
    df["n_trades"] = pd.to_numeric(raw["NO_OF_TRADES"], errors="coerce")
    df["delivery_qty"] = pd.to_numeric(raw["DELIV_QTY"], errors="coerce")
    df["delivery_pct"] = pd.to_numeric(raw["DELIV_PER"], errors="coerce")
    df["source"] = "nse_bhavcopy_cm"
    df["available_at"] = availability_timestamp(trade_date).isoformat()
    df["usable_from"] = pd.to_datetime(usable_from_date(trade_date))
    return df.reset_index(drop=True)


def parse_fo_bhavcopy_zip(content: bytes, trade_date: date) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_name = next((n for n in zf.namelist() if n.endswith(".csv")), zf.namelist()[0])
        raw = pd.read_csv(zf.open(csv_name), dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    raw = raw[(raw["FinInstrmTp"].str.strip() == "STF") & (raw["Sgmt"].str.strip() == "FO")].copy()
    if raw.empty:
        return pd.DataFrame()
    raw["_expiry"] = pd.to_datetime(raw["XpryDt"], errors="coerce")
    raw["_vol"] = pd.to_numeric(raw["TtlTradgVol"], errors="coerce").fillna(0)
    raw = raw[raw["_vol"] > 0].copy()
    raw = raw.loc[raw.groupby("TckrSymb")["_expiry"].idxmin()]
    df = pd.DataFrame()
    df["symbol"] = raw["TckrSymb"].str.strip()
    df["date"] = pd.to_datetime(trade_date)
    df["fut_oi"] = pd.to_numeric(raw["OpnIntrst"], errors="coerce")
    df["fut_oi_chg"] = pd.to_numeric(raw["ChngInOpnIntrst"], errors="coerce")
    df["fut_volume"] = raw["_vol"].values
    df["fut_close"] = pd.to_numeric(raw["ClsPric"], errors="coerce")
    df["underlying_price"] = pd.to_numeric(raw["UndrlygPric"], errors="coerce")
    df["source"] = "nse_bhavcopy_fo"
    df["available_at"] = availability_timestamp(trade_date).isoformat()
    df["usable_from"] = pd.to_datetime(usable_from_date(trade_date))
    return df.reset_index(drop=True)


def bhavcopy_path(segment: str, trade_date: date) -> Path:
    return data_root() / "bhavcopy" / segment / f"{segment}_{trade_date.isoformat()}.parquet"


def save_bhavcopy(df: pd.DataFrame, segment: str, trade_date: date) -> Path:
    out = bhavcopy_path(segment, trade_date)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out
