#!/usr/bin/env python3
"""Regenerate archived Sleeve F labels and emit a reproducible parity report."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contracts.sleeve_f_labels import generate_legacy_parity_labels_for_sessions


DEFAULT_MODELS = os.environ.get("SLEEVE_F_MODELS_DIR")
DEFAULT_DATA = os.environ.get("SLEEVE_F_DATA_DIR")
OUTPUT_DIR = ROOT / "runs/sleeve-f-label-parity"


def main() -> int:
    args = parse_args()
    if args.models_dir is None:
        raise SystemExit("error: --models-dir or SLEEVE_F_MODELS_DIR is required")
    if args.data_dir is None:
        raise SystemExit("error: --data-dir or SLEEVE_F_DATA_DIR is required")
    parquet_paths = sorted(args.models_dir.glob("*.parquet")) + sorted(args.models_dir.glob("*/*.parquet"))
    label_paths = [path for path in parquet_paths if "barrier" in path.name.lower() and "label" in path.name.lower()]
    if not label_paths:
        report = {"verdict": "BLOCKER", "reason": "No barrier-label parquet found", "searched": str(args.models_dir)}
        write_report(report)
        print("BLOCKER: no barrier-label parquet found")
        return 2
    if len(label_paths) > 1:
        print(f"using {label_paths[0]} (found {len(label_paths)} candidates)")
    report = compare(label_paths[0], args.data_dir)
    write_report(report)
    print(f"{report['verdict']}: {report['summary']}")
    return 0 if report["verdict"] == "PASS" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=Path(DEFAULT_MODELS) if DEFAULT_MODELS else None)
    parser.add_argument("--data-dir", type=Path, default=Path(DEFAULT_DATA) if DEFAULT_DATA else None)
    return parser.parse_args()


def compare(parquet_path: Path, data_dir: Path) -> dict[str, object]:
    archived = pq.read_table(parquet_path).to_pandas()
    archived["datetime"] = pd.to_datetime(archived["datetime"])
    start, end = archived["datetime"].min(), archived["datetime"].max()
    raw = load_archive_csvs(data_dir, start.normalize(), end.normalize())
    skipped_empty_frames = int(raw.attrs.get("skipped_empty_frames", 0))
    regenerated = generate_legacy_parity_labels_for_sessions(raw)
    regenerated = regenerated[(regenerated["datetime"] >= start) & (regenerated["datetime"] <= end)].copy()

    key_columns = ["datetime", "trade_date"]
    value_columns = ["fut_close", "long_label", "short_label"]
    joined = archived.merge(regenerated, on=key_columns, how="outer", suffixes=("_archive", "_generated"), indicator=True)
    detail: list[dict[str, object]] = []
    mismatches = 0
    max_abs_diff: dict[str, float] = {}
    for column in value_columns:
        left, right = f"{column}_archive", f"{column}_generated"
        both = joined[left].notna() & joined[right].notna()
        if pd.api.types.is_float_dtype(joined[left]):
            diff = (joined.loc[both, left] - joined.loc[both, right]).abs()
            max_abs_diff[column] = float(diff.max()) if not diff.empty else 0.0
            unequal = ~both | ((joined[left] - joined[right]).abs() > 1e-9)
        else:
            unequal = ~both | (joined[left] != joined[right])
        mismatch_rows = joined.loc[unequal]
        mismatches += int(unequal.sum())
        for _, row in mismatch_rows.head(max(0, 50 - len(detail))).iterrows():
            detail.append({
                "column": column, "datetime": str(row["datetime"]), "merge": row["_merge"],
                "archive": _json_value(row.get(left)), "generated": _json_value(row.get(right)),
            })
    missing_rows = int((joined["_merge"] != "both").sum())
    verdict = "PASS" if mismatches == 0 and len(archived) == len(regenerated) else "FAIL"
    return {
        "verdict": verdict,
        "parquet_path": str(parquet_path),
        "schema": str(pq.ParquetFile(parquet_path).schema_arrow),
        "date_range": {"start": str(start), "end": str(end)},
        "archive_rows": len(archived),
        "generated_rows": len(regenerated),
        "joined_rows": len(joined),
        "missing_or_extra_rows": missing_rows,
        "value_mismatch_count": mismatches,
        "skipped_empty_frames": skipped_empty_frames,
        "max_abs_diff": max_abs_diff,
        "mismatches": detail,
        "spec_conflicts": [
            "None: the protocol and task intentionally separate the archived close-touch generator from the execution-consistent generator; the archived script governs the former.",
        ],
        "semantic_distinctions": [
            "Legacy labels use future closes only; execution labels use next-open entry plus OHLC first-touch.",
            "The archived target/stop ordering comparison is strict; execution OHLC double-touches stop first.",
        ],
        "summary": f"archive_rows={len(archived)}, generated_rows={len(regenerated)}, value_mismatches={mismatches}, missing_or_extra_rows={missing_rows}, skipped_empty_frames={skipped_empty_frames}",
    }


def load_archive_csvs(data_dir: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    skipped_empty = 0
    for path in sorted(data_dir.rglob("*.csv")):
        try:
            frame = pd.read_csv(path, usecols=lambda column: column in {"date", "time", "open", "high", "low", "close"})
        except pd.errors.EmptyDataError:
            skipped_empty += 1
            continue
        if not {"date", "time", "open", "high", "low", "close"}.issubset(frame.columns):
            continue
        if frame.empty:
            skipped_empty += 1
            continue
        day = pd.to_datetime(frame["date"].iloc[0]).normalize()
        if start <= day <= end:
            frames.append(frame)
    if not frames:
        raise FileNotFoundError(
            f"No non-empty archive minute CSVs found in {data_dir} for {start.date()}..{end.date()}"
            f" ({skipped_empty} empty/header-only CSVs skipped)"
        )
    result = pd.concat(frames, ignore_index=True)
    result.attrs["skipped_empty_frames"] = skipped_empty
    return result


def write_report(report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "parity_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    lines = ["# Sleeve F legacy-label parity", "", f"**Verdict: {report['verdict']}**", "", str(report.get("summary", report.get("reason", ""))), ""]
    for key in ("parquet_path", "date_range", "archive_rows", "generated_rows", "joined_rows", "missing_or_extra_rows", "value_mismatch_count", "max_abs_diff", "spec_conflicts", "semantic_distinctions"):
        if key in report:
            lines.append(f"- `{key}`: {json.dumps(report[key], default=str)}")
    mismatches = report.get("mismatches", [])
    if mismatches:
        lines += ["", "## First mismatches", "", "| column | datetime | merge | archive | generated |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {m['column']} | {m['datetime']} | {m['merge']} | {m['archive']} | {m['generated']} |" for m in mismatches]
    (OUTPUT_DIR / "parity_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _json_value(value: object) -> object:
    return None if pd.isna(value) else value.item() if hasattr(value, "item") else value


if __name__ == "__main__":
    raise SystemExit(main())
