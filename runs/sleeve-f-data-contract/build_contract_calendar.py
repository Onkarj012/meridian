#!/usr/bin/env python3
"""Build the NIFTY futures data contract from NSE bhavcopy archives.

The script intentionally reads source ZIP members in memory and writes only the
requested report artifacts to this directory.  It is re-runnable and does not
modify the source data.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path("/Users/onkarj012/Projects/market/meridian")
SOURCE_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/raw")
UDIFF_ROOT = SOURCE_ROOT / "udiff"
LEGACY_ROOT = SOURCE_ROOT / "legacy"
MINUTE_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_fut")
OUT_DIR = PROJECT_ROOT / "runs/sleeve-f-data-contract"

START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-07-10")
GAP_START = pd.Timestamp("2024-11-01")
GAP_END = pd.Timestamp("2026-03-31")

UDIFF_REQUIRED = [
    "TradDt",
    "FinInstrmTp",
    "FinInstrmId",
    "TckrSymb",
    "XpryDt",
    "StrkPric",
    "OptnTp",
    "ClsPric",
    "SttlmPric",
    "OpnIntrst",
    "TtlTradgVol",
]
LEGACY_REQUIRED = [
    "INSTRUMENT",
    "SYMBOL",
    "EXPIRY_DT",
    "STRIKE_PR",
    "OPTION_TYP",
    "CLOSE",
    "SETTLE_PR",
    "OPEN_INT",
    "CONTRACTS",
    "TIMESTAMP",
]


def read_zip_csv(path: Path, usecols: list[str] | None = None) -> pd.DataFrame:
    """Read the first CSV member from a ZIP without extracting it."""
    with zipfile.ZipFile(path) as zf:
        members = [name for name in zf.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"Expected one file in {path}, found {members}")
        with zf.open(members[0]) as fh:
            return pd.read_csv(fh, usecols=usecols, low_memory=False)


def iso(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def fmt_num(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def collect_sources() -> tuple[pd.DataFrame, dict[str, object]]:
    """Load UDiFF and legacy futures rows, with UDiFF date precedence."""
    udiff_rows: list[pd.DataFrame] = []
    legacy_rows: list[pd.DataFrame] = []
    udiff_types: set[str] = set()
    udiff_type_counts: Counter[str] = Counter()
    source_file_dates: list[dict[str, object]] = []
    format_counts: Counter[str] = Counter()

    for year in (2024, 2025, 2026):
        for path in sorted((UDIFF_ROOT / str(year)).glob("*.zip")):
            df = read_zip_csv(path)
            missing = set(UDIFF_REQUIRED) - set(df.columns)
            if missing:
                raise ValueError(f"{path} missing UDiFF columns: {sorted(missing)}")
            file_date = pd.to_datetime(df["TradDt"], errors="coerce").dropna().dt.normalize()
            if file_date.empty:
                continue
            date = file_date.iloc[0]
            source_file_dates.append({"source": "udiff", "path": str(path), "trade_date": date})
            format_counts["udiff"] += 1
            n = df[df["TckrSymb"].eq("NIFTY")].copy()
            udiff_types.update(n["FinInstrmTp"].dropna().astype(str).unique())
            udiff_type_counts.update(n["FinInstrmTp"].dropna().astype(str).tolist())
            n = n[n["FinInstrmTp"].eq("IDF")].copy()
            if n.empty:
                continue
            n["trade_date"] = pd.to_datetime(n["TradDt"], errors="coerce").dt.normalize()
            n["expiry"] = pd.to_datetime(n["XpryDt"], errors="coerce").dt.normalize()
            n["instrument_type"] = n["FinInstrmTp"].astype("string")
            n["instrument_id"] = n["FinInstrmId"].map(fmt_num)
            n["close"] = pd.to_numeric(n["ClsPric"], errors="coerce")
            n["settle"] = pd.to_numeric(n["SttlmPric"], errors="coerce")
            n["volume"] = pd.to_numeric(n["TtlTradgVol"], errors="coerce")
            n["oi"] = pd.to_numeric(n["OpnIntrst"], errors="coerce")
            n["source"] = "udiff"
            udiff_rows.append(
                n[
                    [
                        "trade_date",
                        "expiry",
                        "instrument_type",
                        "instrument_id",
                        "close",
                        "settle",
                        "volume",
                        "oi",
                        "source",
                    ]
                ]
            )

    for year in (2024,):
        for path in sorted((LEGACY_ROOT / str(year)).glob("*.zip")):
            df = read_zip_csv(path)
            missing = set(LEGACY_REQUIRED) - set(df.columns)
            if missing:
                raise ValueError(f"{path} missing legacy columns: {sorted(missing)}")
            file_date = pd.to_datetime(df["TIMESTAMP"], format="%d-%b-%Y", errors="coerce").dropna().dt.normalize()
            if file_date.empty:
                continue
            date = file_date.iloc[0]
            source_file_dates.append({"source": "legacy", "path": str(path), "trade_date": date})
            format_counts["legacy"] += 1
            n = df[
                df["SYMBOL"].eq("NIFTY")
                & df["INSTRUMENT"].eq("FUTIDX")
                & pd.to_numeric(df["STRIKE_PR"], errors="coerce").fillna(0).eq(0)
                & df["OPTION_TYP"].fillna("XX").eq("XX")
            ].copy()
            if n.empty:
                continue
            n["trade_date"] = pd.to_datetime(n["TIMESTAMP"], format="%d-%b-%Y", errors="coerce").dt.normalize()
            n["expiry"] = pd.to_datetime(n["EXPIRY_DT"], format="%d-%b-%Y", errors="coerce").dt.normalize()
            n["instrument_type"] = "FUTIDX"
            # The legacy schema has no FinInstrmId. Keep a deterministic,
            # auditable identifier rather than silently dropping contract IDs.
            n["instrument_id"] = n["expiry"].map(lambda x: f"legacy:NIFTY:FUTIDX:{iso(x)}")
            n["close"] = pd.to_numeric(n["CLOSE"], errors="coerce")
            n["settle"] = pd.to_numeric(n["SETTLE_PR"], errors="coerce")
            n["volume"] = pd.to_numeric(n["CONTRACTS"], errors="coerce")
            n["oi"] = pd.to_numeric(n["OPEN_INT"], errors="coerce")
            n["source"] = "legacy"
            legacy_rows.append(
                n[
                    [
                        "trade_date",
                        "expiry",
                        "instrument_type",
                        "instrument_id",
                        "close",
                        "settle",
                        "volume",
                        "oi",
                        "source",
                    ]
                ]
            )

    if not udiff_rows and not legacy_rows:
        raise FileNotFoundError("No source rows were loaded")

    udiff = pd.concat(udiff_rows, ignore_index=True) if udiff_rows else pd.DataFrame()
    legacy = pd.concat(legacy_rows, ignore_index=True) if legacy_rows else pd.DataFrame()
    udiff_dates = set(udiff["trade_date"].dropna().unique()) if not udiff.empty else set()
    if not legacy.empty:
        legacy = legacy[~legacy["trade_date"].isin(udiff_dates)].copy()
    futures = pd.concat([udiff, legacy], ignore_index=True)
    futures = futures[
        futures["trade_date"].between(START, END)
        & futures["expiry"].notna()
        & futures["expiry"].between(START, END + pd.Timedelta(days=370))
    ].copy()

    # Exact UDiFF justification: NIFTY has IDF and IDO; IDF is the index-future
    # type, while IDO is the index-option type. The no-strike/no-option check is
    # retained as a sanity assertion, not as a substitute for type filtering.
    udiff_futures = futures[futures["source"].eq("udiff")]
    if not udiff_futures["instrument_type"].eq("IDF").all():
        raise AssertionError("Unexpected non-IDF row survived UDiFF futures filtering")

    metadata = {
        "udiff_types": sorted(udiff_types),
        "udiff_type_counts": dict(sorted(udiff_type_counts.items())),
        "source_file_dates": pd.DataFrame(source_file_dates),
        "format_counts": dict(format_counts),
        "udiff_dates": sorted(pd.to_datetime(list(udiff_dates))),
        "legacy_dates_used": sorted(pd.to_datetime(legacy["trade_date"].unique())) if not legacy.empty else [],
    }
    return futures, metadata


def find_duplicate_rows(futures: pd.DataFrame) -> pd.DataFrame:
    keys = ["trade_date", "instrument_id"]
    dup = futures[futures.duplicated(keys, keep=False)].sort_values(keys)
    if dup.empty:
        return pd.DataFrame(columns=keys + ["count"])
    return dup.groupby(keys, as_index=False).size().rename(columns={"size": "count"})


def build_calendar(futures: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for trade_date, day in futures.groupby("trade_date", sort=True):
        candidates = day[day["expiry"] >= trade_date].sort_values(["expiry", "instrument_id"])
        candidates = candidates.drop_duplicates(["expiry", "instrument_id"], keep="last")
        if len(candidates) < 2:
            raise ValueError(f"Fewer than two unexpired NIFTY futures on {iso(trade_date)}")
        front, next_contract = candidates.iloc[0], candidates.iloc[1]
        row = {
            "trade_date": iso(trade_date),
            "front_instrument_id": front["instrument_id"],
            "front_expiry": iso(front["expiry"]),
            "front_close": front["close"],
            "front_settle": front["settle"],
            "front_volume": front["volume"],
            "front_oi": front["oi"],
            "next_instrument_id": next_contract["instrument_id"],
            "next_expiry": iso(next_contract["expiry"]),
            "next_close": next_contract["close"],
            "next_oi": next_contract["oi"],
        }
        rows.append(row)
    return pd.DataFrame(rows)


def build_expiries(futures: pd.DataFrame) -> pd.DataFrame:
    f = futures[futures["expiry"].between(START, END)].copy()
    rows = []
    for expiry, group in f.groupby("expiry", sort=True):
        contracts = group[["instrument_id", "source"]].drop_duplicates().sort_values(["source", "instrument_id"])
        native_ids = contracts.loc[contracts["source"].eq("udiff"), "instrument_id"].astype(str).tolist()
        contract_id = ";".join(native_ids) if native_ids else ";".join(contracts["instrument_id"].astype(str))
        rows.append(
            {
                "expiry": iso(expiry),
                "expiry_weekday": expiry.day_name(),
                "first_trade_date": iso(group["trade_date"].min()),
                "last_trade_date": iso(group["trade_date"].max()),
                "fin_instrm_id": contract_id,
                "source": ";".join(contracts["source"].astype(str)),
            }
        )
    return pd.DataFrame(rows)


MINUTE_RE = re.compile(r"nifty_fut_(\d{2})_(\d{2})_(\d{4})\.csv$")


def minute_files_on_disk() -> dict[pd.Timestamp, Path]:
    files: dict[pd.Timestamp, Path] = {}
    duplicates: list[tuple[pd.Timestamp, Path, Path]] = []
    for path in MINUTE_ROOT.glob("*/*/nifty_fut_*.csv"):
        match = MINUTE_RE.match(path.name)
        if not match:
            continue
        day, month, year = match.groups()
        date = pd.Timestamp(year=int(year), month=int(month), day=int(day))
        if date in files:
            duplicates.append((date, files[date], path))
        else:
            files[date] = path
    minute_files_on_disk.duplicates = duplicates  # type: ignore[attr-defined]
    return files


def read_last_minute_bar(path: Path) -> dict[str, object]:
    df = pd.read_csv(path)
    required = {"date", "time", "symbol", "close", "oi"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing minute columns: {sorted(missing)}")
    df = df[df["symbol"].eq("NIFTY-I")].copy()
    if df.empty:
        return {"status": "no_NIFTY-I_rows", "time": "", "close": pd.NA, "oi": pd.NA}
    df["parsed_time"] = pd.to_datetime(df["time"], format="%H:%M:%S", errors="coerce")
    df = df.sort_values("parsed_time")
    preferred = df[df["time"].eq("15:30:00")]
    if preferred.empty:
        preferred = df[df["time"].eq("15:29:00")]
    if preferred.empty:
        preferred = df.tail(1)
    row = preferred.iloc[-1]
    return {
        "status": "ok",
        "time": str(row["time"]),
        "close": pd.to_numeric(row["close"], errors="coerce"),
        "oi": pd.to_numeric(row["oi"], errors="coerce"),
    }


def make_roll_evidence(
    calendar: pd.DataFrame,
    expiries: pd.DataFrame,
    minute_files: dict[pd.Timestamp, Path],
    futures: pd.DataFrame,
) -> pd.DataFrame:
    cal = calendar.copy()
    cal["trade_date_ts"] = pd.to_datetime(cal["trade_date"])
    by_date = cal.set_index("trade_date_ts")
    expiry_dates = pd.to_datetime(expiries["expiry"])
    wanted = expiry_dates[
        ((expiry_dates >= pd.Timestamp("2024-01-01")) & (expiry_dates <= pd.Timestamp("2024-10-31")))
        | ((expiry_dates >= pd.Timestamp("2026-04-01")) & (expiry_dates <= pd.Timestamp("2026-06-30")))
    ]
    rows: list[dict[str, object]] = []
    for expiry in wanted:
        if expiry not in by_date.index:
            continue
        positions = by_date.index[by_date.index > expiry]
        if len(positions) == 0:
            continue
        next_session = positions[0]
        for session_role, session_date in [("expiry_day", expiry), ("day_after", next_session)]:
            if session_date not in minute_files:
                rows.append(
                    {
                        "expiry_date": iso(expiry),
                        "expiry_weekday": expiry.day_name(),
                        "session_role": session_role,
                        "session_date": iso(session_date),
                        "minute_file": "",
                        "minute_status": "missing_minute_file",
                    }
                )
                continue
            minute = read_last_minute_bar(minute_files[session_date])
            c = by_date.loc[session_date]
            minute_close = minute["close"]
            minute_oi = minute["oi"]
            ladder = futures[
                futures["trade_date"].eq(session_date) & (futures["expiry"] >= session_date)
            ].sort_values(["expiry", "instrument_id"]).drop_duplicates(["expiry", "instrument_id"])
            third = ladder.iloc[2] if len(ladder) >= 3 else None
            rows.append(
                {
                    "expiry_date": iso(expiry),
                    "expiry_weekday": expiry.day_name(),
                    "session_role": session_role,
                    "session_date": iso(session_date),
                    "minute_file": str(minute_files[session_date]),
                    "minute_status": minute["status"],
                    "minute_last_time": minute["time"],
                    "minute_close": minute_close,
                    "minute_oi": minute_oi,
                    "calendar_front_id": c["front_instrument_id"],
                    "calendar_front_expiry": c["front_expiry"],
                    "calendar_front_close": c["front_close"],
                    "calendar_front_oi": c["front_oi"],
                    "calendar_next_id": c["next_instrument_id"],
                    "calendar_next_expiry": c["next_expiry"],
                    "calendar_next_close": c["next_close"],
                    "calendar_next_oi": c["next_oi"],
                    "calendar_third_id": third["instrument_id"] if third is not None else "",
                    "calendar_third_expiry": iso(third["expiry"]) if third is not None else "",
                    "calendar_third_close": third["close"] if third is not None else pd.NA,
                    "calendar_third_oi": third["oi"] if third is not None else pd.NA,
                    "close_abs_diff_front": abs(float(minute_close) - float(c["front_close"])) if pd.notna(minute_close) else pd.NA,
                    "close_abs_diff_next": abs(float(minute_close) - float(c["next_close"])) if pd.notna(minute_close) else pd.NA,
                    "oi_abs_diff_front": abs(float(minute_oi) - float(c["front_oi"])) if pd.notna(minute_oi) else pd.NA,
                    "oi_abs_diff_next": abs(float(minute_oi) - float(c["next_oi"])) if pd.notna(minute_oi) else pd.NA,
                    "close_abs_diff_third": abs(float(minute_close) - float(third["close"])) if third is not None and pd.notna(minute_close) else pd.NA,
                    "oi_abs_diff_third": abs(float(minute_oi) - float(third["oi"])) if third is not None and pd.notna(minute_oi) else pd.NA,
                }
            )
    return pd.DataFrame(rows)


def write_csv(df: pd.DataFrame, name: str, columns: list[str] | None = None) -> Path:
    if columns:
        df = df.reindex(columns=columns)
    path = OUT_DIR / name
    df.to_csv(path, index=False, lineterminator="\n")
    return path


def weekday_gap_text(calendar: pd.DataFrame) -> str:
    if calendar.empty:
        return "none"
    dates = pd.to_datetime(calendar["trade_date"])
    expected = pd.date_range(dates.min(), dates.max(), freq="B")
    actual = set(dates)
    missing = [d.strftime("%Y-%m-%d") for d in expected if d not in actual]
    if not missing:
        return "none"
    by_year: dict[int, list[str]] = defaultdict(list)
    for day in missing:
        by_year[int(day[:4])].append(day)
    lines = []
    for year in sorted(by_year):
        lines.append(f"- {year}: {len(by_year[year])} weekday dates absent (likely exchange holidays/source gaps): {', '.join(by_year[year])}")
    return "\n".join(lines)


def build_coverage_report(
    calendar: pd.DataFrame,
    futures: pd.DataFrame,
    metadata: dict[str, object],
    expiries: pd.DataFrame,
    duplicate_rows: pd.DataFrame,
    minute_files: dict[pd.Timestamp, Path],
    gap_days: pd.DataFrame,
    evidence: pd.DataFrame,
) -> str:
    cal_dates = pd.to_datetime(calendar["trade_date"])
    source_dates = set(cal_dates)
    minute_dates = set(minute_files)
    years = sorted(cal_dates.dt.year.unique())
    counts = []
    for year in years:
        bhav = sum(d.year == year for d in source_dates)
        mins = sum(d.year == year and START <= d <= END for d in minute_dates)
        counts.append(f"| {year} | {bhav} | {mins} | {bhav - mins} |")

    front_zero = calendar[pd.to_numeric(calendar["front_oi"], errors="coerce").fillna(0).eq(0)]
    expiry_day = calendar[pd.to_datetime(calendar["trade_date"]).eq(pd.to_datetime(calendar["front_expiry"]))]
    expiry_quirks = []
    if expiry_day.empty:
        expiry_quirks.append("No expiry-day front rows were observed.")
    else:
        expiry_quirks.append(f"{len(expiry_day)} calendar rows have front_expiry equal to trade_date; the expiring contract is therefore retained on expiry day by construction.")
        bad = expiry_day[pd.to_numeric(expiry_day["front_oi"], errors="coerce").fillna(0).eq(0)]
        if not bad.empty:
            expiry_quirks.append(f"{len(bad)} expiry-day front rows have zero OI: {', '.join(bad.trade_date.tolist())}.")
        else:
            expiry_quirks.append("No expiry-day front rows have zero OI.")

    exp = expiries.copy()
    exp["expiry_ts"] = pd.to_datetime(exp["expiry"])
    weekday_changes = []
    prior = None
    for _, row in exp.sort_values("expiry_ts").iterrows():
        weekday = row["expiry_weekday"]
        if prior is not None and weekday != prior:
            weekday_changes.append(f"{row['expiry']} ({prior} -> {weekday})")
        prior = weekday

    source_dates_df = metadata["source_file_dates"]
    source_mismatch = source_dates_df.groupby(["source", "trade_date"]).size()
    duplicate_source_dates = source_mismatch[source_mismatch.gt(1)]
    udiff_dates = set(metadata["udiff_dates"])
    legacy_dates_used = metadata["legacy_dates_used"]
    source_end = max(source_dates) if source_dates else None
    source_start = min(source_dates) if source_dates else None
    minute_dup = getattr(minute_files_on_disk, "duplicates", [])
    id_expiry_counts = futures.groupby("instrument_id")["expiry"].nunique()
    id_expiry_changes = id_expiry_counts[id_expiry_counts.gt(1)].index.tolist()

    evidence_status = ""
    if evidence.empty:
        evidence_status = "No eligible expiry/day-after evidence rows were available."
    else:
        ok = evidence[evidence["minute_status"].eq("ok")]
        evidence_status = f"{len(ok)} evidence rows had a minute file; {len(evidence) - len(ok)} were missing."

    lines = [
        "# Coverage report",
        "",
        f"Source calendar span: {iso(source_start)} to {iso(source_end)}; requested span was {iso(START)} to {iso(END)}.",
        "",
        "## Bhavcopy vs minute-data days",
        "",
        "| Year | Bhavcopy trading days | Minute-data days on disk | Difference |",
        "|---:|---:|---:|---:|",
        *counts,
        "",
        f"Minute files are identified by `nifty_fut_DD_MM_YYYY.csv` under the supplied minute root; `{len(minute_files)}` unique dates were found in total. Duplicate minute-date paths: `{len(minute_dup)}`.",
        "",
        "## Gap days",
        "",
        f"`gap_days.csv` contains `{len(gap_days)}` bhavcopy trading days in {iso(GAP_START)} through {iso(GAP_END)} with no minute CSV.",
        "",
        "## Source and filter audit",
        "",
        f"UDiFF NIFTY `FinInstrmTp` values observed: `{', '.join(metadata['udiff_types'])}`. Counts across all NIFTY rows: `{metadata['udiff_type_counts']}`.",
        "The calendar filter is UDiFF `TckrSymb == NIFTY` and `FinInstrmTp == IDF`; `IDF` is the index-futures type. `IDO` is excluded as the index-options type. Futures rows also have empty strike and option-type fields in the observed UDiFF data. Legacy rows use `SYMBOL == NIFTY`, `INSTRUMENT == FUTIDX`, zero strike, and `OPTION_TYP == XX`.",
        "",
        f"UDiFF files loaded: `{metadata['format_counts'].get('udiff', 0)}`; legacy files loaded: `{metadata['format_counts'].get('legacy', 0)}`. UDiFF dates take precedence. Legacy 2024 was used for `{len(legacy_dates_used)}` dates not covered by UDiFF.",
        "Legacy files do not contain `FinInstrmId`; their `fin_instrm_id` values in the CSVs are deterministic synthetic IDs of the form `legacy:NIFTY:FUTIDX:YYYY-MM-DD`, and are not native NSE UDiFF IDs.",
        "",
        "## Archive-date anomalies",
        "",
        f"Weekday-calendar comparison (Mon-Fri dates absent from the observed bhavcopy date set):\n{weekday_gap_text(calendar)}",
        "",
        f"Source file/date duplicate groups: `{len(duplicate_source_dates)}`; duplicate filtered futures `(trade_date, instrument_id)` groups: `{len(duplicate_rows)}`.",
        f"Instrument IDs observed with more than one expiry date: `{', '.join(map(str, id_expiry_changes)) if id_expiry_changes else 'none'}`. This is retained as observed source data and is relevant around the 2025 expiry-weekday migration.",
        f"Front-month rows with zero OI: `{len(front_zero)}`. Dates: {', '.join(front_zero.trade_date.tolist()) if not front_zero.empty else 'none'}.",
        *expiry_quirks,
        "",
        "## Expiry weekday observation",
        "",
        f"Observed weekday transitions in `expiries.csv`: {', '.join(weekday_changes) if weekday_changes else 'none'}.",
        "The weekday is derived from each observed expiry date; no weekday convention is hardcoded into contract selection.",
        "",
        "## Roll-evidence availability",
        "",
        evidence_status,
        f"The supplied UDiFF archive extends through {iso(source_end)}; minute files are compared only against bhavcopy rows present in these inputs.",
        "",
        "## Expiry-day selection rule",
        "",
        "For each bhavcopy trading day, eligible futures are those with expiry greater than or equal to the trade date. Sorting by expiry makes the contract expiring on the trade date the front month on expiry day; the next trading session naturally selects the following contract.",
        "",
    ]
    return "\n".join(lines)


def build_roll_report(evidence: pd.DataFrame, expiries: pd.DataFrame) -> str:
    lines = [
        "# Roll convention",
        "",
        "## Method",
        "",
        "For each eligible expiry in 2024-01 through 2024-10 and 2026-04 through 2026-06, the last available `NIFTY-I` minute bar was compared with the calendar front and next contract close and OI on the same session. The minute files supplied here end at 15:29:00 for the selected sessions, so 15:29:00 was used when 15:30:00 was absent. OI is the `oi` field on that last bar.",
        "",
        "A lower absolute close difference and a lower absolute OI difference identify the matching contract. The per-session evidence below keeps the raw comparison values visible.",
        "",
        "## Evidence",
        "",
    ]
    if evidence.empty:
        lines.append("No evidence rows were available.")
        return "\n".join(lines) + "\n"

    display_cols = [
        "expiry_date",
        "session_role",
        "session_date",
        "minute_last_time",
        "minute_close",
        "minute_oi",
        "calendar_front_expiry",
        "calendar_front_close",
        "calendar_front_oi",
        "calendar_next_expiry",
        "calendar_next_close",
        "calendar_next_oi",
        "calendar_third_expiry",
        "calendar_third_close",
        "calendar_third_oi",
        "close_abs_diff_front",
        "close_abs_diff_next",
        "close_abs_diff_third",
        "oi_abs_diff_front",
        "oi_abs_diff_next",
        "oi_abs_diff_third",
        "minute_status",
    ]
    lines.append("| " + " | ".join(display_cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(display_cols)) + " |")
    for _, row in evidence.iterrows():
        values = []
        for col in display_cols:
            value = row.get(col, "")
            if pd.isna(value):
                value = ""
            elif isinstance(value, float):
                value = f"{value:.4f}"
            values.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")

    ok = evidence[evidence["minute_status"].eq("ok")].copy()
    if not ok.empty:
        ok["close_match"] = ok["close_abs_diff_front"] <= ok["close_abs_diff_next"]
        ok["oi_match"] = ok["oi_abs_diff_front"] <= ok["oi_abs_diff_next"]
        close_all = ["close_abs_diff_front", "close_abs_diff_next", "close_abs_diff_third"]
        oi_all = ["oi_abs_diff_front", "oi_abs_diff_next", "oi_abs_diff_third"]
        ok[close_all + oi_all] = ok[close_all + oi_all].apply(pd.to_numeric, errors="coerce")
        ok["close_closest"] = ok[close_all].idxmin(axis=1).str.replace("close_abs_diff_", "", regex=False)
        ok["oi_closest"] = ok[oi_all].idxmin(axis=1).str.replace("oi_abs_diff_", "", regex=False)

        def summary(frame: pd.DataFrame, column: str, label: str) -> str:
            if frame.empty:
                return f"{label}: no observed rows"
            counts = frame[column].value_counts()
            return ", ".join(f"{name} {int(counts.get(name, 0))}/{len(frame)}" for name in ("front", "next", "third"))

        expiry_2024 = ok[(pd.to_datetime(ok["session_date"]).dt.year == 2024) & ok["session_role"].eq("expiry_day")]
        after_2024 = ok[(pd.to_datetime(ok["session_date"]).dt.year == 2024) & ok["session_role"].eq("day_after")]
        expiry_2026 = ok[(pd.to_datetime(ok["session_date"]).dt.year == 2026) & ok["session_role"].eq("expiry_day")]
        after_2026 = ok[(pd.to_datetime(ok["session_date"]).dt.year == 2026) & ok["session_role"].eq("day_after")]
        lines.extend(
            [
                "",
                "## Conclusion",
                "",
                "2024 overlap (10 expiry weeks; the day after 2024-10-31 has no minute CSV):",
                f"- Expiry day, closest by close: {summary(expiry_2024, 'close_closest', 'close')}; closest by OI: {summary(expiry_2024, 'oi_closest', 'OI')}.",
                f"- Day after, closest by close: {summary(after_2024, 'close_closest', 'close')}; closest by OI: {summary(after_2024, 'oi_closest', 'OI')}.",
                "- This segment supports: expiring contract through expiry day, then the next calendar contract from the following trading session.",
                "",
                "2026 overlap (2026-04-28, 2026-05-26, and 2026-06-30 expiry sessions):",
                f"- Expiry day, closest by close across the available three-contract ladder: {summary(expiry_2026, 'close_closest', 'close')}; closest by OI: {summary(expiry_2026, 'oi_closest', 'OI')}.",
                f"- Day after, closest by close across the available three-contract ladder: {summary(after_2026, 'close_closest', 'close')}; closest by OI: {summary(after_2026, 'oi_closest', 'OI')}.",
                "- The refetched 2026-04-01 through 2026-06-30 files are evaluated against the regenerated calendar front month; the prior June-30 far-month vintage is no longer the reference for this segment.",
                "",
                "Overall, there is no single roll convention shared by both minute-data vintages. Use the 2024 rule only for the 2020-01 through 2024-10-style series; the 2026-04 onward files now follow the regenerated calendar front-month definition.",
                "",
                "The calendar itself remains the reference front-month definition: nearest expiry greater than or equal to the trade date.",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    futures, metadata = collect_sources()
    duplicate_rows = find_duplicate_rows(futures)
    calendar = build_calendar(futures)
    expiries = build_expiries(futures)
    minute_files = minute_files_on_disk()

    calendar_dates = pd.to_datetime(calendar["trade_date"])
    minute_dates = set(minute_files)
    gap = calendar[
        calendar_dates.between(GAP_START, GAP_END)
        & ~calendar_dates.isin(minute_dates)
    ][["trade_date", "front_instrument_id", "front_expiry"]].copy()
    evidence = make_roll_evidence(calendar, expiries, minute_files, futures)

    calendar_columns = [
        "trade_date",
        "front_instrument_id",
        "front_expiry",
        "front_close",
        "front_settle",
        "front_volume",
        "front_oi",
        "next_instrument_id",
        "next_expiry",
        "next_close",
        "next_oi",
    ]
    expiry_columns = [
        "expiry",
        "expiry_weekday",
        "first_trade_date",
        "last_trade_date",
        "fin_instrm_id",
        "source",
    ]
    write_csv(calendar, "contract_calendar.csv", calendar_columns)
    write_csv(expiries, "expiries.csv", expiry_columns)
    write_csv(gap, "gap_days.csv", ["trade_date", "front_instrument_id", "front_expiry"])

    coverage = build_coverage_report(calendar, futures, metadata, expiries, duplicate_rows, minute_files, gap, evidence)
    (OUT_DIR / "coverage_report.md").write_text(coverage, encoding="utf-8")
    (OUT_DIR / "roll_convention.md").write_text(build_roll_report(evidence, expiries), encoding="utf-8")

    checksum_paths = [OUT_DIR / "contract_calendar.csv", OUT_DIR / "expiries.csv", OUT_DIR / "gap_days.csv"]
    checksum_lines = []
    for path in checksum_paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {path.name}")
    (OUT_DIR / "checksums.txt").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    print(f"calendar_rows={len(calendar)}")
    print(f"expiry_rows={len(expiries)}")
    print(f"gap_rows={len(gap)}")
    print(f"evidence_rows={len(evidence)}")
    print(f"udiff_types={metadata['udiff_types']}")
    print(f"source_span={calendar.trade_date.min()}..{calendar.trade_date.max()}")
    print(f"legacy_dates_used={len(metadata['legacy_dates_used'])}")
    print(f"front_zero_oi={(pd.to_numeric(calendar['front_oi'], errors='coerce').fillna(0).eq(0)).sum()}")


if __name__ == "__main__":
    main()
