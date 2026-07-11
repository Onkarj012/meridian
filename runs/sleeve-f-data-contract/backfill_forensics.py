#!/usr/bin/env python3
"""Reproduce the local Groww backfill validation forensics.

This is intentionally offline: it reads the minute archive, contract calendar,
the saved validation report, frozen router cache, and (for the reused 53001
identifier) four local UDiFF ZIPs.  It writes the human-readable conclusions
to ``backfill_forensics.md``.
"""
from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_fut")
DEFAULT_CALENDAR = ROOT / "runs/sleeve-f-data-contract/contract_calendar.csv"
DEFAULT_GAPS = ROOT / "runs/sleeve-f-data-contract/gap_days.csv"
DEFAULT_REPORT = ROOT / "runs/sleeve-f-data-contract/groww_backfill_report.json"
DEFAULT_CACHE = Path("/Users/onkarj012/Projects/market/intranet_optinet/cache/router_v0/futures_features_proxy.parquet")
DEFAULT_OUTPUT = ROOT / "runs/sleeve-f-data-contract/backfill_forensics.md"
SOURCE_UDIFF = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/raw/udiff/2025")

BAR_MIN, BAR_MAX = 350, 380
CLOSE_TOLERANCE, VOLUME_TOLERANCE, OI_TOLERANCE = 0.001, 0.05, 0.05
BASELINE_LOTS = (("2024-06", 50), ("2024-12", 25), ("2025-12", 75), ("9999-12", 65))


def lot(expiry: str) -> int:
    month = str(expiry)[:7]
    for last_month, value in BASELINE_LOTS:
        if month <= last_month:
            return value
    raise ValueError(expiry)


def path_for(day: str, archive: Path) -> Path:
    stamp = pd.Timestamp(day)
    return archive / str(stamp.year) / str(stamp.month) / f"nifty_fut_{stamp:%d_%m_%Y}.csv"


def baseline_failures(path: Path, row: pd.Series) -> list[str]:
    """Reconstruct the pre-refinement report (last row, old lot table)."""
    if not path.exists():
        return ["missing_file"]
    frame = pd.read_csv(path)
    failures: list[str] = []
    if not BAR_MIN <= len(frame) <= BAR_MAX:
        failures.append("bar_count")
    if frame.empty:
        return failures + ["close", "volume", "oi"]
    close = float(frame.iloc[-1]["close"])
    volume = float(pd.to_numeric(frame["volume"], errors="coerce").sum())
    oi = float(frame.iloc[-1]["oi"])
    expected_close = float(row.front_close)
    expected_volume = float(row.front_volume) * lot(row.front_expiry)
    expected_oi = float(row.front_oi)
    if abs(close - expected_close) / abs(expected_close) > CLOSE_TOLERANCE:
        failures.append("close")
    if abs(volume - expected_volume) / abs(expected_volume) > VOLUME_TOLERANCE:
        failures.append("volume")
    if abs(oi - expected_oi) / abs(expected_oi) > OI_TOLERANCE:
        failures.append("oi")
    return failures


def load_records(archive: Path, calendar_path: Path, gaps_path: Path) -> pd.DataFrame:
    calendar = pd.read_csv(calendar_path, dtype=str).set_index("trade_date")
    gaps = pd.read_csv(gaps_path, dtype=str)
    rows: list[dict[str, object]] = []
    for day in gaps.trade_date:
        row = calendar.loc[day]
        path = path_for(day, archive)
        item: dict[str, object] = {"trade_date": day, "path": str(path), "exists": path.exists()}
        item["front_expiry"] = row.front_expiry
        item["front_instrument"] = row.front_instrument_id
        item["baseline_failures"] = baseline_failures(path, row)
        if not path.exists():
            rows.append(item)
            continue
        frame = pd.read_csv(path)
        close = pd.to_numeric(frame.close, errors="coerce")
        volume = pd.to_numeric(frame.volume, errors="coerce")
        oi = pd.to_numeric(frame.oi, errors="coerce")
        close_index = frame.index[volume > 0][-1] if (volume > 0).any() else frame.index[-1]
        oi_index = frame.index[oi != 0][-1] if (oi != 0).any() else frame.index[-1]
        expected_close = float(row.front_close)
        expected_oi = float(row.front_oi)
        expected_volume = float(row.front_volume) * lot(row.front_expiry)
        zero_tail = []
        for index in reversed(frame.index):
            if oi.loc[index] == 0:
                zero_tail.append(str(frame.loc[index, "time"]))
            else:
                break
        item.update(
            bars=len(frame), first_time=str(frame.time.iloc[0]), last_time=str(frame.time.iloc[-1]),
            last_close=float(close.iloc[-1]), last_oi=float(oi.iloc[-1]), last_volume=float(volume.iloc[-1]),
            close_time=str(frame.loc[close_index, "time"]), close_observed=float(close.loc[close_index]),
            oi_time=str(frame.loc[oi_index, "time"]), oi_observed=float(oi.loc[oi_index]),
            expected_close=expected_close, expected_oi=expected_oi, expected_volume=expected_volume,
            volume_sum=float(volume.sum()), zero_tail_times=list(reversed(zero_tail)),
            close_rel=(float(close.loc[close_index]) - expected_close) / expected_close,
            last_close_rel=(float(close.iloc[-1]) - expected_close) / expected_close,
            oi_rel=(float(oi.loc[oi_index]) - expected_oi) / expected_oi,
            volume_ratio=float(volume.sum()) / expected_volume,
            raw_volume_ratio=float(volume.sum()) / float(row.front_volume),
        )
        rows.append(item)
    return pd.DataFrame(rows)


def vintage_stats(archive: Path) -> dict[str, dict[str, object]]:
    groups = {
        "2023": sorted((archive / "2023").glob("**/*.csv")),
        "2024 Jan-Oct": [p for p in sorted((archive / "2024").glob("**/*.csv")) if int(p.parent.name) <= 10],
    }
    result = {}
    for name, files in groups.items():
        stats = []
        for path in files:
            frame = pd.read_csv(path)
            if not frame.empty:
                stats.append((len(frame), str(frame.time.iloc[-1]), float(frame.oi.iloc[-1]), int((frame.time == "15:30:00").sum())))
        data = pd.DataFrame(stats, columns=["bars", "last_time", "last_oi", "bars_1530"])
        result[name] = {
            "files": len(data), "bar_counts": dict(data.bars.value_counts().sort_index()),
            "last_times": dict(data.last_time.value_counts()), "zero_last_oi": int((data.last_oi == 0).sum()),
            "with_1530": int((data.bars_1530 > 0).sum()),
        }
    return result


def source_53001_rows() -> pd.DataFrame:
    dates = {"2025-07-31", "2025-08-01", "2025-09-29", "2025-09-30"}
    rows = []
    paths = [path for day in dates for path in SOURCE_UDIFF.glob(f"*{day.replace('-', '')}*.zip")]
    for path in paths:
        with zipfile.ZipFile(path) as zipped:
            raw = pd.read_csv(zipped.open(zipped.namelist()[0]), dtype=str, low_memory=False,
                              usecols=["TradDt", "FinInstrmTp", "FinInstrmId", "TckrSymb", "XpryDt", "ClsPric", "TtlTradgVol", "OpnIntrst"])
        raw["id"] = raw.FinInstrmId.str.replace(".0", "", regex=False)
        selected = raw[(raw.TradDt.isin(dates)) & raw.TckrSymb.eq("NIFTY") & raw.FinInstrmTp.eq("IDF") & raw.id.eq("53001")]
        if not selected.empty:
            rows.append(selected[["TradDt", "XpryDt", "ClsPric", "TtlTradgVol", "OpnIntrst"]])
    return pd.concat(rows, ignore_index=True).sort_values("TradDt") if rows else pd.DataFrame()


def md_table(frame: pd.DataFrame, columns: list[str], headers: list[str] | None = None) -> str:
    if frame.empty:
        return "_none_"
    view = frame[columns].copy()
    if headers:
        view.columns = headers
    view = view.where(view.notna(), "")
    lines = [
        "| " + " | ".join(str(column) for column in view.columns) + " |",
        "| " + " | ".join("---" for _ in view.columns) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in view.itertuples(index=False, name=None))
    return "\n".join(lines)


def build_report(records: pd.DataFrame, report: dict, cache_path: Path, archive: Path) -> str:
    existing = records[records.exists].copy()
    existing["failure_text"] = existing.baseline_failures.map(lambda x: ",".join(x))
    oi = existing[existing.baseline_failures.map(lambda x: "oi" in x)].copy()
    close = existing[existing.baseline_failures.map(lambda x: "close" in x)].copy()
    volume = existing[existing.baseline_failures.map(lambda x: "volume" in x)].copy()
    supplied_categories = Counter(tuple(item.get("failures", [])) for item in report.get("validation_failures", []))
    lines = [
        "# Groww backfill forensics",
        "",
        "Generated offline by `backfill_forensics.py` from the archive, calendar, saved report, frozen cache, and local UDiFF ZIPs.",
        "",
        "## Baseline failure inventory",
        "",
        f"The supplied report contains {len(report.get('validation_failures', []))} failure entries. Its category counts at inspection time were:",
        "",
        md_table(pd.DataFrame([{"category": "+".join(k) or "pass", "days": v} for k, v in supplied_categories.items()]).sort_values("days", ascending=False), ["category", "days"]),
        "",
        f"Reconstruction over the {len(records)} gap dates finds {int(records.exists.sum())} files and {int((~records.exists).sum())} missing paths. The two close failures that also fail volume are counted separately below; therefore there are {len(close)} total close failures but {len(close[~close.baseline_failures.map(lambda x: 'volume' in x)])} close failures outside the volume-overlap category.",
        "",
        "## 1a. OI trailing-bar hypothesis",
        "",
        f"There are {len(oi)} baseline OI-failing files. A deterministic sample of up to 10 per year ({min(10, len(oi[oi.trade_date.str[:4] == '2024'])) + min(10, len(oi[oi.trade_date.str[:4] == '2025'])) + min(10, len(oi[oi.trade_date.str[:4] == '2026']))} rows) is shown; `trailing_zero_oi_times` lists the contiguous zero-OI suffix from oldest to newest.",
        "",
    ]
    sample_parts = []
    for year in ("2024", "2025", "2026"):
        subset = oi[oi.trade_date.str[:4].eq(year)].sort_values("trade_date")
        if not subset.empty:
            positions = np.linspace(0, len(subset) - 1, min(10, len(subset)), dtype=int)
            sample_parts.append(subset.iloc[positions].drop_duplicates("trade_date"))
    sample = pd.concat(sample_parts, ignore_index=True) if sample_parts else pd.DataFrame()
    sample = sample.assign(
        trailing_zero_oi_times=sample.zero_tail_times.map(lambda x: ", ".join(x) or "none"),
        nonzero_oi_pass=sample.oi_rel.abs() <= OI_TOLERANCE,
    )
    lines += [md_table(sample, ["trade_date", "bars", "last_time", "trailing_zero_oi_times", "oi_time", "oi_observed", "expected_oi", "oi_rel", "nonzero_oi_pass"], ["date", "bars", "last", "trailing_zero_oi_times", "last_nonzero_oi_time", "last_nonzero_oi", "bhavcopy_oi", "relative_error", "passes_5pct"]), ""]
    oi_pass = int((oi.oi_rel.abs() <= OI_TOLERANCE).sum())
    lines += [
        f"Across all OI failures, the last nonzero-OI observation passes the ±5% check on {oi_pass}/{len(oi)} days. The 15:30-zero pattern is real for the 2025/2026 sample, but it is not the whole explanation: 2024 has nonzero last observations that still miss, and Groww's 2025–2026 nonzero OI is approximately 1/100 of the bhavcopy OI (for example 133,488 vs 13,270,450 on 2025-01-01). The fallback is therefore diagnostic and explicit; it does not make a bad OI value pass.",
        "",
        "## 1b. Vintage and frozen-cache convention",
        "",
    ]
    vintage = vintage_stats(archive)
    vintage_frame = pd.DataFrame([{"vintage": name, **value} for name, value in vintage.items()])
    lines += [md_table(vintage_frame, ["vintage", "files", "bar_counts", "last_times", "with_1530", "zero_last_oi"], ["vintage", "files", "bar counts", "last-time counts", "files with 15:30", "last OI zero"]), ""]
    if cache_path.exists():
        cache = pd.read_parquet(cache_path)
        cache["date_parsed"] = pd.to_datetime(cache["trade_date"])
        day_counts = cache.groupby(cache.date_parsed.dt.date).size()
        cache_line = f"The frozen cache has {len(cache):,} rows over {len(day_counts)} days: {day_counts.min()}–{day_counts.max()} rows/day (median {day_counts.median():.0f}), with {int((day_counts == 315).sum())} days at 315 rows. Its first cached time is {pd.to_datetime(cache.datetime).dt.strftime('%H:%M:%S').groupby(cache.date_parsed.dt.date).min().min()} and its last cached time is {pd.to_datetime(cache.datetime).dt.strftime('%H:%M:%S').groupby(cache.date_parsed.dt.date).max().value_counts().index[0]}; no cached row is 15:30. The 315 rows are the post-warmup feature rows from a 375-minute 09:15–15:29 input session."
    else:
        cache_line = "Frozen cache unavailable."
    lines += [cache_line, "", "Conclusion: archive vintage files do include 15:30 on many sessions, and the incumbent cache convention ends at 15:29 after feature warmup. Because the archive itself is mixed (375 and 376 bars), the port keeps 15:30 and refines validation observations rather than rewriting new files.", "", "## 1c. Close failures", ""]
    close["last_15_29_close"] = np.nan
    close["last_15_29_rel"] = np.nan
    for index, item in close.iterrows():
        frame = pd.read_csv(item.path)
        matching = frame[frame.time.eq("15:29:00")]
        if not matching.empty:
            value = float(matching.iloc[-1].close)
            close.loc[index, "last_15_29_close"] = value
            close.loc[index, "last_15_29_rel"] = (value - item.expected_close) / item.expected_close
    close["last_pass"] = close.close_rel.abs() <= CLOSE_TOLERANCE
    close["1529_pass"] = close.last_15_29_rel.abs() <= CLOSE_TOLERANCE
    worst = close.reindex(close.close_rel.abs().sort_values(ascending=False).index).head(5)
    close_non_volume = len(close[~close.baseline_failures.map(lambda x: "volume" in x)])
    close_non_muhurat = len(close[~close.trade_date.isin({"2025-10-21"}) & ~close.baseline_failures.map(lambda x: "volume" in x)])
    close_1529_pass = int(close["1529_pass"].sum())
    lines += [
        f"{len(close)} files fail close under the baseline last-bar rule; {close_non_muhurat} remain after excluding the two close+volume overlaps and the known Muhurat day 2025-10-21. Only {close_1529_pass} have a 15:29 close inside tolerance; the other {len(close) - close_1529_pass} are genuine close mismatches rather than a 15:30-only artifact. Worst five by absolute selected-close error:",
        "",
        md_table(worst.assign(selected_close=worst.close_observed, selected_rel_pct=worst.close_rel * 100), ["trade_date", "selected_close", "expected_close", "selected_rel_pct", "last_15_29_close", "last_15_29_rel"], ["date", "selected close", "bhavcopy close", "selected error %", "15:29 close", "15:29 error"]),
        "",
        "## 1d. Volume residuals and dual expiry",
        "",
    ]
    volume["scaled_ratio"] = volume.volume_ratio
    volume_table = volume.sort_values("volume_ratio")[["trade_date", "bars", "last_time", "front_expiry", "raw_volume_ratio", "volume_sum", "expected_volume", "scaled_ratio"]]
    lines += [md_table(volume_table, ["trade_date", "bars", "last_time", "front_expiry", "raw_volume_ratio", "volume_sum", "expected_volume", "scaled_ratio"], ["date", "bars", "last", "expiry", "units/contracts", "file units", "expected units", "file/expected"]), ""]
    lines += [
        "The 25 January-2025 residuals have file volume exactly 25×bhavcopy contracts, not 75×. They prove the 2025-01-30 expiry is still lot 25; the lot table is corrected accordingly. The remaining 2025-09-26 residual is a truncated 346-bar file ending 15:00, with raw units/contracts 61.43 and scaled volume ratio 0.819; it is bad/incomplete fetched data, not an expiry-reference ambiguity.",
        "",
        "The same instrument ID 53001 is reused for two expiry mappings: 2025-09-25 in the early July rows and 2025-09-30 from August onward. Local UDiFF rows for the relevant transition and target dates are:",
        "",
    ]
    source = source_53001_rows()
    if not source.empty:
        source = source.rename(columns={"TradDt": "date", "XpryDt": "expiry", "ClsPric": "close", "TtlTradgVol": "contracts", "OpnIntrst": "oi"})
        lines.append(md_table(source, ["date", "expiry", "close", "contracts", "oi"], ["date", "expiry", "close", "contracts", "OI"]))
    else:
        lines.append("_source ZIP rows unavailable_")
    lines += [
        "",
        "On 2025-09-29 and 2025-09-30 the only source row for 53001 is the 2025-09-30 expiry; the archive sums are exactly 95,052×75 = 7,128,900 and 64,496×75 = 4,837,200. The dual mapping is therefore not an ambiguity for these fetched days. Raw ratios around 64.1 for 2026 and 74.6 around the 2025-12 lot boundary are normal near-lot rounding/source-volume noise and remain within the existing ±5% tolerance.",
        "",
        "## Applied changes",
        "",
        "- Keep 15:30 rows: vintage files include them, while the frozen feature cache ends at 15:29 after warmup.",
        "- Validate close from the last positive-volume bar and OI from the last nonzero-OI bar, recording observation times and notes.",
        "- Skip 2024-11-01 and 2025-10-21 as known Muhurat dates with `muhurat_skip`, including the missing 2024-11-01 file.",
        "- Correct the lot-size boundary for expiry 2025-01-30 from 75 to 25; tolerances remain unchanged.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--gap-days", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = load_records(args.archive, args.calendar, args.gap_days)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    args.output.write_text(build_report(records, report, args.cache, args.archive), encoding="utf-8")
    print(f"forensics: {len(records)} gap dates, {int(records.exists.sum())} files, output={args.output}")


if __name__ == "__main__":
    main()
