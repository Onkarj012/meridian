#!/usr/bin/env python3
"""Build a BankNifty front-futures calendar and missing-day plan."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path("/Users/onkarj012/Projects/market/meridian")
SOURCE_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/raw")
UDIFF_ROOT = SOURCE_ROOT / "udiff"
LEGACY_ROOT = SOURCE_ROOT / "legacy"
ARCHIVE_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/banknifty_data/banknifty_fut")
SPOT_SOURCE = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/nifty_intraday/NIFTY 50_minute.csv")
OUT_DIR = PROJECT_ROOT / "runs/sleeve-f-data-contract"

START = pd.Timestamp("2024-01-01")
SOURCE_END = pd.Timestamp("2026-06-30")
GAP_START = pd.Timestamp("2024-11-01")
GAP_END = pd.Timestamp("2026-06-30")
EXTENSION_SOURCE_START = pd.Timestamp("2026-05-27")

UDIFF_REQUIRED = [
    "TradDt", "FinInstrmTp", "FinInstrmId", "TckrSymb", "XpryDt", "ClsPric", "SttlmPric", "OpnIntrst", "TtlTradgVol",
]
LEGACY_REQUIRED = [
    "INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP", "CLOSE", "SETTLE_PR", "OPEN_INT", "CONTRACTS", "TIMESTAMP",
]
FILE_RE = re.compile(r"banknifty_fut_(\d{2})_(\d{2})_(\d{4})\.csv$")


def read_zip_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        members = [name for name in zf.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"expected one file in {path}, found {members}")
        with zf.open(members[0]) as fh:
            return pd.read_csv(fh, usecols=usecols, low_memory=False)


def fmt_num(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def collect_sources() -> tuple[pd.DataFrame, dict[str, object]]:
    udiff_rows: list[pd.DataFrame] = []
    legacy_rows: list[pd.DataFrame] = []
    source_dates: list[dict[str, object]] = []
    type_counts: Counter[str] = Counter()
    types: set[str] = set()
    format_counts: Counter[str] = Counter()

    for year in (2024, 2025, 2026):
        for path in sorted((UDIFF_ROOT / str(year)).glob("*.zip")):
            df = read_zip_csv(path)
            missing = set(UDIFF_REQUIRED) - set(df.columns)
            if missing:
                raise ValueError(f"{path} missing UDiFF columns: {sorted(missing)}")
            file_dates = pd.to_datetime(df["TradDt"], errors="coerce").dropna().dt.normalize()
            if file_dates.empty:
                continue
            source_dates.append({"source": "udiff", "path": str(path), "trade_date": file_dates.iloc[0]})
            format_counts["udiff"] += 1
            bank = df[df["TckrSymb"].eq("BANKNIFTY")].copy()
            types.update(bank["FinInstrmTp"].dropna().astype(str).unique())
            type_counts.update(bank["FinInstrmTp"].dropna().astype(str).tolist())
            bank = bank[bank["FinInstrmTp"].eq("IDF")].copy()
            if bank.empty:
                continue
            bank["trade_date"] = pd.to_datetime(bank["TradDt"], errors="coerce").dt.normalize()
            bank["expiry"] = pd.to_datetime(bank["XpryDt"], errors="coerce").dt.normalize()
            bank["instrument_id"] = bank["FinInstrmId"].map(fmt_num)
            bank["close"] = pd.to_numeric(bank["ClsPric"], errors="coerce")
            bank["settle"] = pd.to_numeric(bank["SttlmPric"], errors="coerce")
            bank["volume"] = pd.to_numeric(bank["TtlTradgVol"], errors="coerce")
            bank["oi"] = pd.to_numeric(bank["OpnIntrst"], errors="coerce")
            bank["source"] = "udiff"
            udiff_rows.append(bank[["trade_date", "expiry", "instrument_id", "close", "settle", "volume", "oi", "source"]])

    for path in sorted((LEGACY_ROOT / "2024").glob("*.zip")):
        df = read_zip_csv(path)
        missing = set(LEGACY_REQUIRED) - set(df.columns)
        if missing:
            raise ValueError(f"{path} missing legacy columns: {sorted(missing)}")
        file_dates = pd.to_datetime(df["TIMESTAMP"], format="%d-%b-%Y", errors="coerce").dropna().dt.normalize()
        if file_dates.empty:
            continue
        source_dates.append({"source": "legacy", "path": str(path), "trade_date": file_dates.iloc[0]})
        format_counts["legacy"] += 1
        bank = df[
            df["SYMBOL"].eq("BANKNIFTY")
            & df["INSTRUMENT"].eq("FUTIDX")
            & pd.to_numeric(df["STRIKE_PR"], errors="coerce").fillna(0).eq(0)
            & df["OPTION_TYP"].fillna("XX").eq("XX")
        ].copy()
        if bank.empty:
            continue
        bank["trade_date"] = pd.to_datetime(bank["TIMESTAMP"], format="%d-%b-%Y", errors="coerce").dt.normalize()
        bank["expiry"] = pd.to_datetime(bank["EXPIRY_DT"], format="%d-%b-%Y", errors="coerce").dt.normalize()
        bank["instrument_id"] = bank["expiry"].map(lambda value: f"legacy:BANKNIFTY:FUTIDX:{value:%Y-%m-%d}")
        bank["close"] = pd.to_numeric(bank["CLOSE"], errors="coerce")
        bank["settle"] = pd.to_numeric(bank["SETTLE_PR"], errors="coerce")
        bank["volume"] = pd.to_numeric(bank["CONTRACTS"], errors="coerce")
        bank["oi"] = pd.to_numeric(bank["OPEN_INT"], errors="coerce")
        bank["source"] = "legacy"
        legacy_rows.append(bank[["trade_date", "expiry", "instrument_id", "close", "settle", "volume", "oi", "source"]])

    if not udiff_rows and not legacy_rows:
        raise FileNotFoundError("no BankNifty futures rows were loaded")
    udiff = pd.concat(udiff_rows, ignore_index=True) if udiff_rows else pd.DataFrame()
    legacy = pd.concat(legacy_rows, ignore_index=True) if legacy_rows else pd.DataFrame()
    udiff_dates = set(udiff["trade_date"].dropna().unique()) if not udiff.empty else set()
    if not legacy.empty:
        legacy = legacy[~legacy["trade_date"].isin(udiff_dates)].copy()
    futures = pd.concat([udiff, legacy], ignore_index=True)
    futures = futures[
        futures["trade_date"].between(START, SOURCE_END)
        & futures["expiry"].notna()
        & futures["expiry"].between(START, SOURCE_END + pd.Timedelta(days=370))
    ].copy()
    # NSE reused instrument IDs around the 2025 BankNifty expiry-weekday
    # migration. The later row is the stable mapping for the same contract
    # (e.g. 35012: early rows say 2025-01-29, later rows say 2025-01-30).
    # Canonicalize all rows by that observed final mapping before selecting the
    # nearest expiry; otherwise Groww correctly rejects the stale early date.
    final_mapping = futures.sort_values("trade_date").drop_duplicates("instrument_id", keep="last").set_index("instrument_id")["expiry"]
    futures["expiry"] = futures["instrument_id"].map(final_mapping).fillna(futures["expiry"])
    return futures, {
        "udiff_types": sorted(types),
        "udiff_type_counts": dict(sorted(type_counts.items())),
        "format_counts": dict(format_counts),
        "source_file_dates": pd.DataFrame(source_dates),
        "source_trade_start": futures["trade_date"].min(),
        "source_trade_end": futures["trade_date"].max(),
    }


def build_calendar(futures: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for trade_date, day in futures.groupby("trade_date", sort=True):
        candidates = day[day["expiry"] >= trade_date].sort_values(["expiry", "instrument_id"])
        candidates = candidates.drop_duplicates(["expiry", "instrument_id"], keep="last")
        if len(candidates) < 2:
            raise ValueError(f"fewer than two unexpired BankNifty futures on {trade_date:%Y-%m-%d}")
        front, next_contract = candidates.iloc[0], candidates.iloc[1]
        rows.append(
            {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "front_instrument_id": front["instrument_id"],
                "front_expiry": front["expiry"].strftime("%Y-%m-%d"),
                "front_close": front["close"],
                "front_settle": front["settle"],
                "front_volume": front["volume"],
                "front_oi": front["oi"],
                "next_instrument_id": next_contract["instrument_id"],
                "next_expiry": next_contract["expiry"].strftime("%Y-%m-%d"),
                "next_close": next_contract["close"],
                "next_oi": next_contract["oi"],
            }
        )
    return pd.DataFrame(rows)


def build_expiries(futures: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for expiry, group in futures.groupby("expiry", sort=True):
        ids = group[["instrument_id", "source"]].drop_duplicates().sort_values(["source", "instrument_id"])
        native = ids.loc[ids["source"].eq("udiff"), "instrument_id"].astype(str).tolist()
        rows.append(
            {
                "expiry": expiry.strftime("%Y-%m-%d"),
                "expiry_weekday": expiry.day_name(),
                "first_trade_date": group["trade_date"].min().strftime("%Y-%m-%d"),
                "last_trade_date": group["trade_date"].max().strftime("%Y-%m-%d"),
                "fin_instrm_id": ";".join(native or ids["instrument_id"].astype(str).tolist()),
                "source": ";".join(ids["source"].astype(str).tolist()),
            }
        )
    return pd.DataFrame(rows)


def archive_files(root: Path) -> dict[pd.Timestamp, Path]:
    result: dict[pd.Timestamp, Path] = {}
    for path in root.glob("*/*/banknifty_fut_*.csv"):
        match = FILE_RE.fullmatch(path.name)
        if match:
            day = pd.Timestamp(year=int(match.group(3)), month=int(match.group(2)), day=int(match.group(1)))
            result[day.normalize()] = path
    return result


def source_extension_dates() -> list[pd.Timestamp]:
    """Return exchange-session candidates after the supplied bhavcopy cutoff.

    The NIFTY minute source supplies observed sessions through 2026-06-16.  For
    the remaining June weekdays we retain candidates; Groww's response decides
    which were actual sessions and failures are reported by the fetcher.
    """
    frame = pd.read_csv(SPOT_SOURCE, usecols=["date"])
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna().dt.normalize().unique()
    observed = sorted(pd.Timestamp(value) for value in dates if EXTENSION_SOURCE_START <= value <= pd.Timestamp("2026-06-16"))
    tail = list(pd.bdate_range("2026-06-17", "2026-06-30"))
    return sorted(set(observed + tail))


def write_csv(frame: pd.DataFrame, name: str, columns: list[str]) -> Path:
    path = OUT_DIR / name
    frame[columns].to_csv(path, index=False)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", default=str(ARCHIVE_ROOT))
    args = parser.parse_args(argv)

    root = Path(args.archive_root)
    futures, metadata = collect_sources()
    calendar = build_calendar(futures)
    expiries = build_expiries(futures)
    files = archive_files(root)
    cal_dates = pd.to_datetime(calendar["trade_date"])
    gap = calendar[cal_dates.between(GAP_START, GAP_END) & ~cal_dates.isin(files)][
        ["trade_date", "front_instrument_id", "front_expiry", "front_close", "front_volume", "front_oi"]
    ].copy()

    # The supplied UDiFF archive stops at 2026-05-26, although it already
    # contains the 2026-06-30 expiry mapping. Extend the plan with exchange
    # session candidates until that expiry; expected bhavcopy fields remain NA.
    source_end = pd.Timestamp(metadata["source_trade_end"])
    latest_expiry = pd.to_datetime(expiries.loc[pd.to_datetime(expiries["expiry"]).le(GAP_END), "expiry"]).max()
    extension = []
    if source_end < latest_expiry:
        for day in source_extension_dates():
            if source_end < day <= latest_expiry and day not in files and day.strftime("%Y-%m-%d") not in set(gap["trade_date"]):
                extension.append(
                    {
                        "trade_date": day.strftime("%Y-%m-%d"),
                        "front_instrument_id": "source_extension:2026-06-30",
                        "front_expiry": latest_expiry.strftime("%Y-%m-%d"),
                        "front_close": pd.NA,
                        "front_volume": pd.NA,
                        "front_oi": pd.NA,
                        "bhavcopy_expected": False,
                    }
                )
    gap["bhavcopy_expected"] = True
    gap = pd.concat([gap, pd.DataFrame(extension)], ignore_index=True)
    gap = gap.sort_values("trade_date").reset_index(drop=True)

    calendar_columns = [
        "trade_date", "front_instrument_id", "front_expiry", "front_close", "front_volume", "front_oi",
        "next_instrument_id", "next_expiry", "next_close", "next_oi",
    ]
    write_csv(calendar, "banknifty_contract_calendar.csv", calendar_columns)
    write_csv(gap, "banknifty_gap_days.csv", ["trade_date", "front_instrument_id", "front_expiry", "front_close", "front_volume", "front_oi", "bhavcopy_expected"])
    write_csv(expiries, "banknifty_expiries.csv", list(expiries.columns))

    target_expiries = expiries[pd.to_datetime(expiries["expiry"]).le(GAP_END)]
    metadata_report = {
        "calendar_rows": len(calendar),
        "calendar_source_trade_start": str(metadata["source_trade_start"])[:10],
        "calendar_source_trade_end": str(metadata["source_trade_end"])[:10],
        "gap_rows": len(gap),
        "source_gap_rows": int(gap["bhavcopy_expected"].sum()),
        "source_extension_rows": int((~gap["bhavcopy_expected"]).sum()),
        "latest_expiry_through_2026_06_30": target_expiries.iloc[-1].to_dict(),
        "udiff_types": metadata["udiff_types"],
        "udiff_type_counts": metadata["udiff_type_counts"],
        "format_counts": metadata["format_counts"],
        "expiry_weekday_counts": expiries["expiry_weekday"].value_counts().to_dict(),
        "archive_files_before": len(files),
    }
    (OUT_DIR / "banknifty_calendar_report.json").write_text(json_dumps(metadata_report), encoding="utf-8")
    digest_paths = [OUT_DIR / "banknifty_contract_calendar.csv", OUT_DIR / "banknifty_gap_days.csv", OUT_DIR / "banknifty_expiries.csv"]
    (OUT_DIR / "banknifty_checksums.txt").write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in digest_paths), encoding="utf-8"
    )
    print(f"calendar_rows={len(calendar)}")
    print(f"expiry_rows={len(expiries)}")
    print(f"gap_rows={len(gap)} source_gap_rows={metadata_report['source_gap_rows']} extension_rows={metadata_report['source_extension_rows']}")
    print(f"source_span={metadata_report['calendar_source_trade_start']}..{metadata_report['calendar_source_trade_end']}")
    print(f"expiry_weekday_counts={metadata_report['expiry_weekday_counts']}")
    print(f"archive_files_before={len(files)}")
    return 0


def json_dumps(value: object) -> str:
    return json.dumps(value, indent=2, default=str) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
