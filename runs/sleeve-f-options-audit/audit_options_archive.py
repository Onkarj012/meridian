#!/usr/bin/env python3
"""Read-only scan of Meridian's option archives.

The script only writes beneath --out-dir. It intentionally labels all audit
statistics as sampled: the source archive is too large for an unconditional
full CSV scan during review.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

import pandas as pd

TODAY = date(2026, 7, 12)
REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_DATE = re.compile(r"_(\d{2})_(\d{2})_(\d{4})\.csv$", re.I)
PARQUET_DATE = re.compile(r"options_(\d{8})\.parquet$", re.I)
SYM_RE = re.compile(r"^(?P<underlying>NIFTY|BANKNIFTY)(?P<day>\d{1,2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<otype>CE|PE)$", re.I)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--roots", nargs="+", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--sample-days", type=int, default=30)
    return p.parse_args()


def jsonable(v):
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return v.isoformat()
    if isinstance(v, (Path,)):
        return str(v)
    if hasattr(v, "item"):
        return v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def files(roots: list[Path], suffix: str | None = None) -> list[Path]:
    out = []
    for root in roots:
        if root.exists():
            out.extend(p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store" and (suffix is None or p.suffix.lower() == suffix))
    return sorted(set(out))


def file_date(p: Path) -> date | None:
    m = CSV_DATE.search(p.name)
    if m:
        try: return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError: return None
    m = PARQUET_DATE.search(p.name)
    if m:
        try: return datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError: return None
    return None


def is_option(p: Path) -> bool:
    return "option" in p.name.lower() or "option" in str(p.parent).lower()


def fmt_bytes(n: int) -> str:
    x = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if x < 1024 or unit == "TiB": return f"{x:.1f} {unit}"
        x /= 1024


def report_root(root: Path) -> str:
    """Describe an input root without persisting a developer's absolute path."""
    if not root.is_absolute():
        return str(root)
    try:
        return root.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"$SLEEVE_F_OPTIONS_ROOT/{root.name}"


def report_path(path: Path, roots: list[Path]) -> str:
    for root in roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        prefix = report_root(root)
        return f"{prefix}/{relative.as_posix()}" if relative.parts else prefix
    return report_root(path)


def inventory(all_files: list[Path], roots: list[Path]) -> str:
    lines = ["# Options archive inventory", "", "Read-only inventory. Coverage dates are filename-derived where possible; directory statistics include all files under the supplied roots.", "", "## Roots", ""]
    for r in roots: lines.append(f"- `{report_root(r)}` ({'exists' if r.exists() else 'missing'})")
    lines += ["", "## Directory tree (two levels)", "", "```text"]
    for root in roots:
        lines.append(report_root(root))
        dirs = sorted({p.parent for p in all_files if root in p.parents})
        for d in dirs:
            if len(d.relative_to(root).parts) <= 2:
                lines.append(f"  {report_path(d, roots)}")
    lines += ["```", "", "## Per-directory statistics", "", "| Directory | Files | Formats | Size | Date coverage (filename-derived) |", "|---|---:|---|---:|---|"]
    dirs = sorted({p.parent for p in all_files})
    for d in dirs:
        ps = [p for p in all_files if p.parent == d]
        exts = Counter((p.suffix.lower() or "[none]") for p in ps)
        dates = sorted(x for x in (file_date(p) for p in ps) if x)
        ext = ", ".join(f"{k} ({v})" for k, v in sorted(exts.items()))
        cov = f"{dates[0]} to {dates[-1]}" if dates else "not derivable"
        lines.append(f"| `{report_path(d, roots)}` | {len(ps)} | {ext} | {fmt_bytes(sum(p.stat().st_size for p in ps))} | {cov} |")
    return "\n".join(lines) + "\n"


def sample_dates(candidates: set[date], n: int) -> tuple[list[date], dict]:
    dates = sorted(candidates)
    target = max(30, n)
    if len(dates) <= target: return dates, {"basis": "all available candidate dates", "target": target}
    chosen: set[date] = set()
    # Broad early/middle/recent coverage.
    for lo, hi in ((0, 0.25), (0.35, 0.65), (0.75, 1.0)):
        a, b = int((len(dates)-1)*lo), int((len(dates)-1)*hi)
        pool = dates[a:b+1]
        take = max(1, target // 10)
        for i in range(take): chosen.add(pool[round(i*(len(pool)-1)/max(1,take-1))])
    # At least four distinct expiry weeks: nearest Thursday to selected dates.
    thursdays = [d for d in dates if d.weekday() == 3]
    for d in thursdays[::max(1, len(thursdays)//4)][:4]: chosen.add(d)
    if len(chosen) < target:
        for d in dates:
            if len(chosen) >= target: break
            chosen.add(d)
    return sorted(chosen), {"basis": "stratified early/middle/recent plus four Thursday expiry-week anchors", "target": target}


def read_csv_day(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p)
    if {"date", "time"} <= set(df):
        df["timestamp"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
    df["source_file"] = str(p)
    return df


def parse_symbols(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    parsed = out["symbol"].astype(str).str.upper().str.extract(SYM_RE)
    out["underlying_parsed"] = parsed["underlying"]
    out["expiry_parsed"] = pd.to_datetime(parsed["day"] + parsed["mon"] + parsed["yy"], format="%d%b%y", errors="coerce")
    out["strike_parsed"] = pd.to_numeric(parsed["strike"], errors="coerce")
    out["option_type_parsed"] = parsed["otype"]
    bad = out.loc[out["strike_parsed"].isna() | out["expiry_parsed"].isna() | out["option_type_parsed"].isna(), "symbol"].drop_duplicates().astype(str).tolist()
    return out, bad[:10]


def cadence(df: pd.DataFrame) -> dict:
    ts = sorted(pd.to_datetime(df["timestamp"], errors="coerce").dropna().drop_duplicates())
    gaps = [(b-a).total_seconds()/60 for a,b in zip(ts, ts[1:])]
    if not gaps: return {"timestamp_count": len(ts), "median_gap_minutes": None, "p95_gap_minutes": None, "max_gap_minutes": None, "unique_gap_minutes": [], "monotonic_after_sort": True}
    return {"timestamp_count": len(ts), "median_gap_minutes": median(gaps), "p95_gap_minutes": float(pd.Series(gaps).quantile(.95)), "max_gap_minutes": max(gaps), "unique_gap_minutes": sorted(set(gaps))[:20], "monotonic_after_sort": ts == sorted(ts)}


def surface(df: pd.DataFrame, n_strikes: int = 5) -> dict:
    # Proxy: at each timestamp, use all parsed contracts, set ATM to the median
    # active strike, and require >=N distinct nonzero-LTP strikes on each side
    # with both CE and PE observations. This is not a full market-definition ATM.
    if df.empty: return {"session_minutes": 0, "valid_minutes": 0, "fraction": None, "n_strikes_each_side": n_strikes, "proxy": "median active strike; nonzero close/LTP; both CE and PE"}
    work = df.dropna(subset=["timestamp", "strike_parsed", "option_type_parsed"]).copy()
    work["ltp_valid"] = pd.to_numeric(work.get("close"), errors="coerce") > 0
    valid = 0; minutes = set()
    for ts, g in work.groupby("timestamp"):
        minutes.add(ts.floor("min"))
        g = g[g["ltp_valid"]]
        if g.empty: continue
        atm = g["strike_parsed"].median()
        sides = {}
        for typ in ("CE", "PE"):
            h = g[g["option_type_parsed"] == typ]
            sides[typ] = {"below": set(h.loc[h.strike_parsed < atm, "strike_parsed"]), "above": set(h.loc[h.strike_parsed > atm, "strike_parsed"])}
        if all(len(sides[t][s]) >= n_strikes for t in sides for s in ("below", "above")): valid += 1
    return {"session_minutes": len(minutes), "valid_minutes": valid, "fraction": valid/len(minutes) if minutes else None, "n_strikes_each_side": n_strikes, "proxy": "median active strike; nonzero close/LTP; both CE and PE; >=N distinct strikes below and above ATM"}


def main() -> None:
    args = parse_args(); args.out_dir.mkdir(parents=True, exist_ok=True)
    all_files = files(args.roots)
    csvs = [p for p in all_files if p.suffix.lower()==".csv" and is_option(p) and file_date(p)]
    pars = [p for p in all_files if p.suffix.lower()==".parquet" and is_option(p) and file_date(p)]
    candidates = {file_date(p) for p in csvs+pars if file_date(p)}
    sampled, sample_basis = sample_dates(candidates, args.sample_days)
    csv_by_date = defaultdict(list); par_by_date = defaultdict(list)
    for p in csvs: csv_by_date[file_date(p)].append(p)
    for p in pars: par_by_date[file_date(p)].append(p)
    day_stats=[]; parse_bad=[]; csv_loaded={}; parquet_rows={}
    for d in sampled:
        rows=[]
        for p in csv_by_date[d]:
            x=read_csv_day(p); rows.append(x); csv_loaded[d]=p
        if rows:
            x=pd.concat(rows, ignore_index=True); x, bad=parse_symbols(x); parse_bad += bad
            ts_info=cadence(x); surf=surface(x)
            day_stats.append({"date":str(d), "csv_files":len(rows), "csv_rows":len(x), "timestamps":"intraday", "native_exchange_timestamp":"date+time fields; no fetch timestamp field observed", "timezone":"naive local/unspecified", "cadence":ts_info, "surface":surf})
        else:
            n=0
            for p in par_by_date[d]: n += len(pd.read_parquet(p, columns=["date"]))
            parquet_rows[d]=n
            day_stats.append({"date":str(d), "csv_files":0, "parquet_files":len(par_by_date[d]), "parquet_rows":n, "timestamps":"date-only/EOD grain", "native_exchange_timestamp":"not present as intraday timestamp", "timezone":"not applicable", "cadence":{"timestamp_count":1 if n else 0, "median_gap_minutes":None, "p95_gap_minutes":None, "max_gap_minutes":None, "unique_gap_minutes":[]}, "surface":None})
    # Seam: sampled overlap dates, comparing CSV last close per contract with Parquet close.
    overlap=sorted(set(csv_by_date)&set(par_by_date)&set(sampled)); seam=[]
    for d in overlap:
        c=pd.concat([read_csv_day(p) for p in csv_by_date[d]], ignore_index=True); c,_=parse_symbols(c)
        c["close_num"]=pd.to_numeric(c["close"],errors="coerce"); c=c.sort_values("timestamp").drop_duplicates(["symbol"],keep="last")
        p=pd.concat([pd.read_parquet(q) for q in par_by_date[d]], ignore_index=True)
        p["key"]=p["symbol"].astype(str).str.upper()+"|"+pd.to_datetime(p["expiry_date"]).dt.strftime("%Y-%m-%d")+"|"+p["strike_price"].astype(str)+"|"+p["option_type"].astype(str).str.upper()
        c["key"]=c["underlying_parsed"].astype(str).str.upper()+"|"+c["expiry_parsed"].dt.strftime("%Y-%m-%d")+"|"+c["strike_parsed"].astype(str)+"|"+c["option_type_parsed"].astype(str).str.upper()
        m=c.merge(p[["key","close"]].rename(columns={"close":"parquet_close"}),on="key")
        drift=(pd.to_numeric(m["close_num"],errors="coerce")-pd.to_numeric(m["parquet_close"],errors="coerce")).abs()
        seam.append({"date":str(d),"csv_rows":len(c),"parquet_rows":len(p),"joined_contracts":len(m),"exact_close_matches":int((drift.fillna(999)==0).sum()),"nonzero_close_drift":int((drift>1e-9).sum()),"max_abs_close_drift":float(drift.max()) if len(drift) else None})
    recent=max(candidates) if candidates else None
    result={"generated_at":datetime.now().isoformat(),"today_assumed":str(TODAY),"roots":[report_root(x) for x in args.roots],"inventory":{"all_files":len(all_files),"csv_files":len([p for p in all_files if p.suffix.lower()=='.csv']),"parquet_files":len([p for p in all_files if p.suffix.lower()=='.parquet']),"option_csv_files":len(csvs),"option_parquet_files":len(pars),"all_file_bytes":sum(p.stat().st_size for p in all_files)},"coverage":{"candidate_date_min":str(min(candidates)) if candidates else None,"candidate_date_max":str(max(candidates)) if candidates else None,"most_recent_date":str(recent) if recent else None,"gap_to_today_days":(TODAY-recent).days if recent else None,"sampled_days":len(sampled),"sample_dates":[str(d) for d in sampled],"sample_basis":sample_basis},"timestamps_cadence":day_stats,"strike_expiry":{"csv_encoding":"symbol regex UNDERLYING+DDMMMYY+STRIKE+CE/PE","parquet_columns":["strike_price","expiry_date","option_type"],"ambiguous_or_unparseable_count":len(set(parse_bad)),"examples":sorted(set(parse_bad))[:10]},"csv_parquet_seam":{"sampled_overlap_dates":seam,"seam_interpretation":"CSV is intraday and Parquet is date-only/EOD; exact row-count equality is not expected. Joined close drift compares CSV final observed close with Parquet close on contract keys."}}
    (args.out_dir/'audit_options_archive.py').write_text(Path(__file__).read_text())
    (args.out_dir/'inventory.md').write_text(inventory(all_files,args.roots))
    (args.out_dir/'audit_report.json').write_text(json.dumps(result,default=jsonable,indent=2,sort_keys=True)+"\n")
    lines=["# Options archive audit report","",f"**Status:** scan/report only; no verdict. All evidence is sampled unless explicitly labeled inventory.","",f"## Scope and sampling\n\n- Roots: {', '.join(f'`{report_root(r)}`' for r in args.roots)}\n- Candidate option dates: {min(candidates) if candidates else 'none'} to {max(candidates) if candidates else 'none'}; sampled {len(sampled)} days.\n- Basis: {sample_basis['basis']}. The sample includes early, middle, recent, and four Thursday expiry-week anchors where available.\n- Inventory: {len(csvs):,} option CSVs and {len(pars):,} option Parquet files; all supplied files: {len(all_files):,}.\n", "## Timestamps and cadence (sample basis)", "", "CSV rows carry `date` + `time` at intraday grain; no separate fetch timestamp was observed, and timestamps are timezone-naive/unspecified. Parquet option rows carry `date` only, so they are EOD/date-grain for this audit. Cadence below is computed after sorting unique CSV timestamps per sampled day.", "", "| Day | CSV rows | timestamps | median gap (min) | p95 gap | max gap | unique gap examples | valid-surface minutes / observed minutes |", "|---|---:|---|---:|---:|---:|---|---:|"]
    for s in day_stats:
        c=s['cadence']; sv=s['surface']; frac=f"{sv['valid_minutes']} / {sv['session_minutes']} ({sv['fraction']:.1%})" if sv and sv['fraction'] is not None else "n/a"
        lines.append(f"| {s['date']} | {s.get('csv_rows',0):,} | {s['timestamps']} | {c.get('median_gap_minutes') or 'n/a'} | {c.get('p95_gap_minutes') or 'n/a'} | {c.get('max_gap_minutes') or 'n/a'} | {c.get('unique_gap_minutes',[])[:6]} | {frac} |")
    lines += ["", "## Strike/expiry mapping", "", "CSV contracts encode underlying, expiry, strike, and CE/PE in the symbol; Parquet has explicit columns. Unparseable CSV identifier count in the sampled CSV rows is **%d** (examples: %s). This is not a full-archive identifier verdict." % (len(set(parse_bad)), ', '.join(sorted(set(parse_bad))[:10]) or 'none'), "", "## Valid-surface proxy", "", "Per timestamp, the proxy sets ATM to the median active strike, requires nonzero `close`/LTP, and requires at least N=5 distinct strikes below and above ATM for both CE and PE. This is an audit assumption, not a market-data validity standard; the denominator is observed CSV timestamps/minutes, not an exchange session calendar.", "", "## CSV ↔ Parquet seam (sample basis)", "", "| Date | CSV final-contract rows | Parquet rows | joined contracts | exact close matches | nonzero drift | max abs drift |", "|---|---:|---:|---:|---:|---:|---:|"]
    for s in seam: lines.append(f"| {s['date']} | {s['csv_rows']:,} | {s['parquet_rows']:,} | {s['joined_contracts']:,} | {s['exact_close_matches']:,} | {s['nonzero_close_drift']:,} | {s['max_abs_close_drift'] if s['max_abs_close_drift'] is not None else 'n/a'} |")
    lines += ["", "The seam is a grain change, not a clean same-row format conversion: CSV contains intraday observations while Parquet contains one date-level observation per contract. Overlap-day join results above are sampled and do not establish full seam reconciliation.", "", "## Live continuation", "", f"Most recent filename-derived option date: **{recent or 'none'}**; gap to assumed today ({TODAY}): **{(TODAY-recent).days if recent else 'n/a'} days**. Inventory includes 2026 Parquet partitions, but this read-only scan cannot establish whether anything currently appends; filesystem modification times and a writer/process audit would be needed.", "", "## Reviewer conclusion inputs", "", "This report intentionally issues no go/no-go verdict. The load-bearing evidence is that the legacy CSV archive is intraday but timezone-unspecified and the Parquet continuation is date-only/EOD, while cadence, valid-surface coverage, and seam drift are sample-dependent. A reviewer should treat the four cascade criteria as requiring deeper audit unless the sampled evidence is judged sufficient."]
    (args.out_dir/'audit_report.md').write_text("\n".join(lines)+"\n")


if __name__ == "__main__": main()
