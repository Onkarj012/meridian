"""Source-trust inventory and point-in-time data-bedrock contracts."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from .features import load_sector_map
from lake.io import parse_simple_yaml
from lake.market import normalize_row, parse_timestamp


INVENTORY_FORMAT = "meridian.data-inventory.v1"
DATA_SNAPSHOT_FORMAT = "meridian.data-source-snapshot.v1"
SYMBOL_MASTER_FORMAT = "meridian.symbol-master.v1"
RECONCILIATION_FORMAT = "meridian.source-reconciliation.v1"
DAILY_CONTEXT_FORMAT = "meridian.daily-context-contracts.v1"
PROMOTION_FORMAT = "meridian.feature-promotion-manifest.v1"
EVENT_QUARANTINE_FORMAT = "meridian.event-quarantine-manifest.v1"
ACCEPTANCE_FORMAT = "meridian.data-bedrock-v1-acceptance.v1"
TIMESTAMP_QUALITIES = {
    "point_in_time_safe",
    "intraday_no_timezone",
    "fixed_time_suspect",
    "date_only",
    "missing",
    "unparseable",
}
ROLE_POLICY: dict[str, dict[str, Any]] = {
    "equity_minute": {"decision": "include", "promotion_eligible": True, "reason": "canonical equity minute OHLCV source"},
    "index_minute": {"decision": "include", "promotion_eligible": True, "reason": "canonical index minute context source"},
    "index_daily": {"decision": "include", "promotion_eligible": True, "reason": "daily index context source"},
    "vix_daily": {"decision": "include", "promotion_eligible": True, "reason": "daily volatility/regime context source"},
    "index_spot_minute": {"decision": "include", "promotion_eligible": True, "reason": "index derivatives spot regime source"},
    "index_futures_minute": {"decision": "include", "promotion_eligible": True, "reason": "index futures regime source"},
    "index_options_minute": {"decision": "include", "promotion_eligible": True, "reason": "index options regime source, not equity feature v1"},
    "cash_bhavcopy_daily": {"decision": "include", "promotion_eligible": True, "reason": "daily cash-market breadth/liquidity/delivery source"},
    "fo_bhavcopy_daily": {"decision": "include", "promotion_eligible": True, "reason": "daily futures/options open-interest context source"},
    "symbol_metadata": {"decision": "include", "promotion_eligible": True, "reason": "symbol/company/industry metadata seed"},
    "news_raw": {"decision": "quarantine", "promotion_eligible": False, "reason": "news requires first_seen_at/available_at proof before features"},
    "sentiment_raw": {"decision": "quarantine", "promotion_eligible": False, "reason": "existing sentiment timestamps are not proven point-in-time safe"},
    "gdelt_daily": {"decision": "quarantine", "promotion_eligible": False, "reason": "GDELT/event rows need available_at join checks before features"},
    "generated_artifact": {"decision": "exclude", "promotion_eligible": False, "reason": "generated artifact, not a raw source contract"},
    "uncontracted": {"decision": "exclude", "promotion_eligible": False, "reason": "uncontracted source path"},
}
NEWS_REQUIRED_FIELDS = {
    "event_id", "symbol", "company_name", "headline", "url", "source", "published_at",
    "first_seen_at", "available_at", "fetched_at", "raw_text_or_snippet",
    "sentiment_score", "event_type", "timestamp_quality", "promotion_status",
}
LAKE_DOMAINS = {
    "market_lake": {"equity_minute", "index_minute"},
    "derivatives_lake": {"index_spot_minute", "index_futures_minute", "index_options_minute"},
    "daily_context_lake": {"index_daily", "vix_daily", "cash_bhavcopy_daily", "fo_bhavcopy_daily", "gdelt_daily"},
    "event_lake_quarantine": {"news_raw", "sentiment_raw"},
    "metadata_lake": {"symbol_metadata"},
}
_FIXED_TIMES = {"07:00:00", "08:00:00"}
SOURCE_EXPECTATIONS = {
    "equity_minute": {"expected": 536, "minimum": 500},
    "market_lake": {"minimum": 48},
    "symbol_metadata": {"minimum": 1},
    "event_lake_quarantine": {"minimum": 0},
}
SECTOR_INDEX_BY_SECTOR = {
    "Automobile": "NIFTY AUTO",
    "Banks": "NIFTY BANK",
    "CapitalGoods": "NIFTY INDIA MANUFACTURING",
    "Cement": "NIFTY INFRASTRUCTURE",
    "Chemicals": "NIFTY CHEMICALS",
    "Conglomerate": "NIFTY 50",
    "ConsumerDiscretionary": "NIFTY INDIA CONSUMPTION",
    "ConsumerDurables": "NIFTY CONSUMER DURABLES",
    "ConsumerStaples": "NIFTY FMCG",
    "FinancialServices": "NIFTY FINANCIAL SERVICES",
    "Healthcare": "NIFTY HEALTHCARE",
    "HotelsTravel": "NIFTY INDIA CONSUMPTION",
    "IT": "NIFTY IT",
    "Media": "NIFTY MEDIA",
    "Metals": "NIFTY METAL",
    "OilGas": "NIFTY OIL & GAS",
    "Pharma": "NIFTY PHARMA",
    "Power": "NIFTY ENERGY",
    "Realty": "NIFTY REALTY",
    "Telecom": "NIFTY TELECOMMUNICATIONS",
    "Textiles": "NIFTY INDIA CONSUMPTION",
    "TransportLogistics": "NIFTY TRANSPORTATION & LOGISTICS",
}
INFERRED_SECTOR_BY_SYMBOL = {
    "360ONE": "FinancialServices", "3MINDIA": "ConsumerDurables", "AADHARHFC": "FinancialServices",
    "AARTIIND": "Chemicals", "AAVAS": "FinancialServices", "ABB": "CapitalGoods",
    "ABBOTINDIA": "Pharma", "ABCAPITAL": "FinancialServices", "ABDL": "ConsumerStaples",
    "ABFRL": "ConsumerDiscretionary", "ABLBL": "ConsumerDiscretionary", "ABREL": "Realty",
    "ABSLAMC": "FinancialServices", "ACC": "Cement", "ACE": "CapitalGoods",
    "ACMESOLAR": "Power", "ACUTAAS": "Chemicals", "ADANIENSOL": "Power",
    "ADANIENT": "Conglomerate", "ADANIGREEN": "Power", "ADANIPORTS": "TransportLogistics",
    "APARINDS": "CapitalGoods", "APLAPOLLO": "Metals", "APOLLOHOSP": "Healthcare",
    "APOLLOTYRE": "Automobile", "ASHOKLEY": "Automobile", "ASIANPAINT": "ConsumerDurables",
    "ASTRAL": "CapitalGoods", "ATGL": "OilGas", "AUBANK": "Banks", "AUROPHARMA": "Pharma",
    "AXISBANK": "Banks", "BAJAJ-AUTO": "Automobile", "BAJAJFINSV": "FinancialServices",
    "BAJFINANCE": "FinancialServices", "BANDHANBNK": "Banks", "BANKBARODA": "Banks",
    "BANKINDIA": "Banks", "BDL": "CapitalGoods", "BEL": "CapitalGoods",
    "BHARATFORG": "Automobile", "BHARTIARTL": "Telecom", "BHEL": "CapitalGoods",
    "BIOCON": "Pharma", "BOSCHLTD": "Automobile", "BPCL": "OilGas", "BRITANNIA": "ConsumerStaples",
    "BSE": "FinancialServices", "CANBK": "Banks", "CGPOWER": "CapitalGoods",
    "CHOLAFIN": "FinancialServices", "CIPLA": "Pharma", "COALINDIA": "Metals",
    "COFORGE": "IT", "COLPAL": "ConsumerStaples", "CONCOR": "TransportLogistics",
    "CUMMINSIND": "CapitalGoods", "DABUR": "ConsumerStaples", "DALBHARAT": "Cement",
    "DELHIVERY": "TransportLogistics", "DIVISLAB": "Pharma", "DLF": "Realty",
    "DMART": "ConsumerStaples", "DRREDDY": "Pharma", "EICHERMOT": "Automobile",
    "ETERNAL": "ConsumerDiscretionary", "EXIDEIND": "Automobile", "FEDERALBNK": "Banks",
    "FORTIS": "Healthcare", "GAIL": "OilGas", "GLAND": "Pharma", "GODREJCP": "ConsumerStaples",
    "GODREJPROP": "Realty", "GRASIM": "Cement", "HAL": "CapitalGoods", "HAVELLS": "ConsumerDurables",
    "HCLTECH": "IT", "HDFCAMC": "FinancialServices", "HDFCBANK": "Banks",
    "HDFCLIFE": "FinancialServices", "HEROMOTOCO": "Automobile", "HINDALCO": "Metals",
    "HINDPETRO": "OilGas", "HINDUNILVR": "ConsumerStaples", "HINDZINC": "Metals",
    "HUDCO": "FinancialServices", "ICICIBANK": "Banks", "ICICIGI": "FinancialServices",
    "ICICIPRULI": "FinancialServices", "IDEA": "Telecom", "IDFCFIRSTB": "Banks",
    "IEX": "FinancialServices", "IGL": "OilGas", "INDHOTEL": "HotelsTravel",
    "INDIGO": "TransportLogistics", "INDUSINDBK": "Banks", "INDUSTOWER": "Telecom",
    "INFY": "IT", "IOC": "OilGas", "IRCTC": "TransportLogistics", "IREDA": "FinancialServices",
    "IRFC": "FinancialServices", "ITC": "ConsumerStaples", "JINDALSTEL": "Metals",
    "JIOFIN": "FinancialServices", "JSWENERGY": "Power", "JSWSTEEL": "Metals",
    "JUBLFOOD": "ConsumerDiscretionary", "KALYANKJIL": "ConsumerDiscretionary",
    "KOTAKBANK": "Banks", "KPITTECH": "IT", "LICHSGFIN": "FinancialServices",
    "LICI": "FinancialServices", "LODHA": "Realty", "LT": "CapitalGoods", "LTIM": "IT",
    "LUPIN": "Pharma", "M&M": "Automobile", "M&MFIN": "FinancialServices",
    "MANAPPURAM": "FinancialServices", "MARICO": "ConsumerStaples", "MARUTI": "Automobile",
    "MAXHEALTH": "Healthcare", "MAZDOCK": "CapitalGoods", "MCX": "FinancialServices",
    "MFSL": "FinancialServices", "MOTHERSON": "Automobile", "MPHASIS": "IT",
    "MUTHOOTFIN": "FinancialServices", "NATIONALUM": "Metals", "NAUKRI": "IT",
    "NESTLEIND": "ConsumerStaples", "NHPC": "Power", "NMDC": "Metals", "NTPC": "Power",
    "OBEROIRLTY": "Realty", "OFSS": "IT", "OIL": "OilGas", "ONGC": "OilGas",
    "PAGEIND": "Textiles", "PATANJALI": "ConsumerStaples", "PAYTM": "FinancialServices",
    "PEL": "FinancialServices", "PERSISTENT": "IT", "PETRONET": "OilGas",
    "PFC": "FinancialServices", "PHOENIXLTD": "Realty", "PIDILITIND": "Chemicals",
    "PIIND": "Chemicals", "PNB": "Banks", "POLICYBZR": "FinancialServices",
    "POLYCAB": "CapitalGoods", "POWERGRID": "Power", "PRESTIGE": "Realty",
    "RBLBANK": "Banks", "RECLTD": "FinancialServices", "RELIANCE": "OilGas",
    "RVNL": "CapitalGoods", "SAIL": "Metals", "SBICARD": "FinancialServices",
    "SBILIFE": "FinancialServices", "SBIN": "Banks", "SHREECEM": "Cement",
    "SHRIRAMFIN": "FinancialServices", "SIEMENS": "CapitalGoods", "SOLARINDS": "Chemicals",
    "SONACOMS": "Automobile", "SRF": "Chemicals", "SUNPHARMA": "Pharma",
    "SUPREMEIND": "ConsumerDurables", "SUZLON": "Power", "TATACHEM": "Chemicals",
    "TATACONSUM": "ConsumerStaples", "TATAELXSI": "IT", "TATAMOTORS": "Automobile",
    "TATAPOWER": "Power", "TATASTEEL": "Metals", "TATATECH": "IT", "TCS": "IT",
    "TECHM": "IT", "TITAN": "ConsumerDurables", "TORNTPHARM": "Pharma",
    "TORNTPOWER": "Power", "TRENT": "ConsumerDiscretionary", "TVSMOTOR": "Automobile",
    "ULTRACEMCO": "Cement", "UNIONBANK": "Banks", "UNITDSPR": "ConsumerStaples",
    "UPL": "Chemicals", "VBL": "ConsumerStaples", "VEDL": "Metals", "VOLTAS": "ConsumerDurables",
    "WIPRO": "IT", "YESBANK": "Banks", "ZEEL": "Media", "ZYDUSLIFE": "Pharma",
}


def build_data_inventory(source_root: Path, output: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source root must be a directory: {source_root}")
    records = [_inspect_record(source_root, path) for path in sorted(source_root.rglob("*")) if path.is_file()]
    inventory = {
        "format": INVENTORY_FORMAT,
        "source_root": ".",
        "roles": sorted(ROLE_POLICY),
        "lake_domains": {name: sorted(roles) for name, roles in LAKE_DOMAINS.items()},
        "sources": records,
        "counts": _counts(records),
    }
    inventory_id = _sha256_json(_identity_inventory(inventory))
    inventory["inventory_id"] = inventory_id
    _write_json(output, inventory)
    return {"inventory_id": inventory_id, "output": str(output), **inventory["counts"]}


def snapshot_data_sources(inventory_path: Path, output_dir: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("format") != INVENTORY_FORMAT:
        raise ValueError(f"not a data inventory: {inventory_path}")
    identity = {
        "format": DATA_SNAPSHOT_FORMAT,
        "inventory_id": inventory["inventory_id"],
        "sources": [_identity_source(source) for source in inventory.get("sources", [])],
    }
    snapshot_id = _sha256_json(identity)
    snapshot = {
        **identity,
        "snapshot_id": snapshot_id,
        "lake_domains": inventory.get("lake_domains", {}),
        "counts": inventory.get("counts", {}),
    }
    path = output_dir / "data-source-snapshots" / f"{snapshot_id}.json"
    _write_immutable_json(path, snapshot, "data source snapshot")
    return {"snapshot_id": snapshot_id, "snapshot": str(path), "inventory_id": inventory["inventory_id"]}


def build_data_trust_report(snapshot_path: Path, report_path: Path) -> dict[str, Any]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot.get("format") != DATA_SNAPSHOT_FORMAT:
        raise ValueError(f"not a data source snapshot: {snapshot_path}")
    sources = snapshot.get("sources", [])
    by_role = Counter(str(source.get("logical_role")) for source in sources)
    by_decision = Counter(str(source.get("selection_decision")) for source in sources)
    lines = [
        "# Data Bedrock Trust Report",
        "",
        f"- Snapshot: `{snapshot['snapshot_id']}`",
        f"- Inventory: `{snapshot['inventory_id']}`",
        f"- Sources: {len(sources)}",
        "",
        "## Decisions",
    ]
    for decision, count in sorted(by_decision.items()):
        lines.append(f"- {decision}: {count}")
    lines.extend(["", "## Roles"])
    for role in sorted(ROLE_POLICY):
        lines.append(f"- {role}: {by_role.get(role, 0)}")
    lines.extend(["", "## Lake Domains"])
    for domain, roles in sorted(snapshot.get("lake_domains", {}).items()):
        count = sum(by_role.get(role, 0) for role in roles)
        lines.append(f"- {domain}: {count} source(s); roles {', '.join(roles)}")
    lines.extend([
        "",
        "## Event/News Gate",
        "No news, sentiment, corporate, or event row is promotable into v1 model features unless it has non-empty entity fields and a defensible `available_at <= decision_time` timestamp.",
    ])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(report_path), "snapshot_id": snapshot["snapshot_id"], "sources": len(sources), "decisions": dict(by_decision)}


def build_source_reconciliation(snapshot_path: Path, report_path: Path) -> dict[str, Any]:
    snapshot = _load_snapshot(snapshot_path)
    sources = snapshot.get("sources", [])
    by_role = Counter(str(source.get("logical_role")) for source in sources)
    market_count = sum(by_role.get(role, 0) for role in LAKE_DOMAINS["market_lake"])
    checks = {
        "equity_minute_minimum_met": by_role.get("equity_minute", 0) >= SOURCE_EXPECTATIONS["equity_minute"]["minimum"],
        "equity_minute_expected_met": by_role.get("equity_minute", 0) >= SOURCE_EXPECTATIONS["equity_minute"]["expected"],
        "market_lake_minimum_met": market_count >= SOURCE_EXPECTATIONS["market_lake"]["minimum"],
        "symbol_metadata_present": by_role.get("symbol_metadata", 0) >= SOURCE_EXPECTATIONS["symbol_metadata"]["minimum"],
        "event_sources_quarantined": all(
            str(source.get("selection_decision", "")).startswith("quarantine")
            for source in sources
            if source.get("logical_role") in LAKE_DOMAINS["event_lake_quarantine"] | {"gdelt_daily"}
        ),
    }
    report = {
        "format": RECONCILIATION_FORMAT,
        "snapshot_id": snapshot["snapshot_id"],
        "inventory_id": snapshot["inventory_id"],
        "role_counts": dict(sorted(by_role.items())),
        "expected_counts": SOURCE_EXPECTATIONS,
        "checks": checks,
        "ready_for_v1": bool(
            checks["equity_minute_minimum_met"]
            and checks["market_lake_minimum_met"]
            and checks["symbol_metadata_present"]
            and checks["event_sources_quarantined"]
        ),
        "notes": [
            "Exact 536-file equity expectation is tracked as evidence, not a hard V1 blocker; below-minimum market breadth is the blocker.",
            "News, sentiment, and GDELT sources remain quarantined by policy.",
        ],
    }
    _write_json(report_path, report)
    return {"report": str(report_path), "ready_for_v1": report["ready_for_v1"], "checks": checks}


def build_symbol_master(source_root: Path, universe_config: Path, output: Path) -> dict[str, Any]:
    metadata_file = source_root / "sentiment" / "ind_nifty500list.csv"
    rows = _read_symbol_metadata(metadata_file) if metadata_file.exists() else {}
    sector_map = load_sector_map(universe_config)
    configured = parse_simple_yaml(universe_config).get("symbols", []) if universe_config.exists() else []
    symbols = sorted(set(rows) | {str(symbol).upper() for symbol in configured} | set(sector_map))
    master_rows: list[dict[str, Any]] = []
    quarantined: list[dict[str, str]] = []
    for symbol in symbols:
        source = rows.get(symbol, {})
        sector, sector_source, confidence = _sector_for_symbol(symbol, source, sector_map)
        industry = str(source.get("industry") or "").strip()
        row = {
            "symbol": symbol,
            "company_name": source.get("company_name", ""),
            "industry": industry,
            "sector": sector,
            "sector_index": SECTOR_INDEX_BY_SECTOR.get(sector, "NIFTY 50"),
            "isin": source.get("isin", ""),
            "aliases": sorted({symbol, str(source.get("company_name", "")).strip()} - {""}),
            "source": sector_source,
            "metadata_confidence": confidence,
        }
        master_rows.append(row)
        if not sector:
            quarantined.append({"symbol": symbol, "reason": "missing sector metadata"})
    identity = {
        "format": SYMBOL_MASTER_FORMAT,
        "source_root": ".",
        "metadata_file": "sentiment/ind_nifty500list.csv" if metadata_file.exists() else None,
        "universe_config": str(universe_config),
        "symbols": master_rows,
        "quarantined_symbols": quarantined,
    }
    master_id = _sha256_json(identity)
    master = {**identity, "symbol_master_id": master_id}
    _write_json(output, master)
    return {"symbol_master_id": master_id, "output": str(output), "symbols": len(master_rows), "quarantined_symbols": len(quarantined)}


def build_daily_context_contracts(snapshot_path: Path, output: Path) -> dict[str, Any]:
    snapshot = _load_snapshot(snapshot_path)
    daily_roles = {"index_daily", "vix_daily", "cash_bhavcopy_daily", "fo_bhavcopy_daily"}
    sources = [
        source for source in snapshot.get("sources", [])
        if source.get("logical_role") in daily_roles
    ]
    contracts = []
    for source in sources:
        role = str(source["logical_role"])
        quality = str(source.get("timestamp_quality"))
        promoted = role in daily_roles and str(source.get("selection_decision")) == "include"
        contracts.append(
            {
                "role": role,
                "path": source["path"],
                "sha256": source["sha256"],
                "schema": source["schema"],
                "row_count": source["row_count"],
                "timestamp_quality": quality,
                "promotion_status": "promoted_daily_context" if promoted else "quarantined_daily_context",
                "available_at_policy": "session_close_after_source_date",
                "feature_join_policy": "daily_context_available_after_market_close_only",
            }
        )
    report = {
        "format": DAILY_CONTEXT_FORMAT,
        "snapshot_id": snapshot["snapshot_id"],
        "contracts": contracts,
        "promoted_roles": sorted(daily_roles),
        "ready_for_v1": bool(contracts) and all(item["promotion_status"] == "promoted_daily_context" for item in contracts),
    }
    _write_json(output, report)
    return {"output": str(output), "contracts": len(contracts), "ready_for_v1": report["ready_for_v1"]}


def build_promotion_manifest(snapshot_path: Path, symbol_master_path: Path, daily_contracts_path: Path, output: Path) -> dict[str, Any]:
    snapshot = _load_snapshot(snapshot_path)
    symbol_master = json.loads(symbol_master_path.read_text(encoding="utf-8"))
    daily = json.loads(daily_contracts_path.read_text(encoding="utf-8"))
    sources = snapshot.get("sources", [])
    counts = Counter(str(source.get("logical_role")) for source in sources if source.get("selection_decision") == "include")
    complete_symbols = [row for row in symbol_master.get("symbols", []) if row.get("sector")]
    manifest = {
        "format": PROMOTION_FORMAT,
        "snapshot_id": snapshot["snapshot_id"],
        "symbol_master_id": symbol_master.get("symbol_master_id"),
        "domains": {
            "market_lake": {
                "promotion_status": "promoted",
                "roles": sorted(LAKE_DOMAINS["market_lake"]),
                "source_count": sum(counts.get(role, 0) for role in LAKE_DOMAINS["market_lake"]),
                "feature_use": "allowed",
            },
            "daily_context_lake": {
                "promotion_status": "promoted" if daily.get("ready_for_v1") else "partial",
                "roles": sorted({"index_daily", "vix_daily", "cash_bhavcopy_daily", "fo_bhavcopy_daily"}),
                "source_count": len(daily.get("contracts", [])),
                "feature_use": "allowed_after_session_close_join",
            },
            "derivatives_lake": {
                "promotion_status": "detected_not_promoted",
                "roles": sorted(LAKE_DOMAINS["derivatives_lake"]),
                "source_count": sum(counts.get(role, 0) for role in LAKE_DOMAINS["derivatives_lake"]),
                "feature_use": "blocked_in_v1_until_schema_normalization",
            },
            "event_lake_quarantine": {
                "promotion_status": "quarantined",
                "roles": sorted(LAKE_DOMAINS["event_lake_quarantine"] | {"gdelt_daily"}),
                "feature_use": "blocked_in_v1",
            },
            "metadata_lake": {
                "promotion_status": "promoted_complete_symbols",
                "complete_symbols": len(complete_symbols),
                "quarantined_symbols": len(symbol_master.get("quarantined_symbols", [])),
                "feature_use": "allowed_for_complete_symbols",
            },
        },
    }
    manifest["ready_for_v1"] = (
        manifest["domains"]["market_lake"]["source_count"] >= SOURCE_EXPECTATIONS["market_lake"]["minimum"]
        and manifest["domains"]["daily_context_lake"]["promotion_status"] == "promoted"
        and manifest["domains"]["metadata_lake"]["complete_symbols"] > 0
        and manifest["domains"]["event_lake_quarantine"]["promotion_status"] == "quarantined"
    )
    _write_json(output, manifest)
    return {"output": str(output), "ready_for_v1": manifest["ready_for_v1"], "domains": manifest["domains"]}


def build_event_quarantine(inventory_path: Path, output: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("format") != INVENTORY_FORMAT:
        raise ValueError(f"not a data inventory: {inventory_path}")
    event_roles = LAKE_DOMAINS["event_lake_quarantine"] | {"gdelt_daily"}
    sources = [
        {
            "path": source["path"],
            "logical_role": source["logical_role"],
            "sha256": source["sha256"],
            "row_count": source["row_count"],
            "schema": source["schema"],
            "timestamp_quality": source["timestamp_quality"],
            "promotion_status": "blocked_in_v1",
            "required_to_promote_later": sorted(NEWS_REQUIRED_FIELDS),
        }
        for source in inventory.get("sources", [])
        if source.get("logical_role") in event_roles
    ]
    manifest = {
        "format": EVENT_QUARANTINE_FORMAT,
        "inventory_id": inventory["inventory_id"],
        "sources": sources,
        "source_count": len(sources),
        "row_count": sum(int(source.get("row_count", 0)) for source in sources),
        "feature_use": "blocked_in_v1",
        "promotion_rule": "available_at must be defensible and <= decision_time before any feature join",
    }
    _write_json(output, manifest)
    return {"output": str(output), "sources": len(sources), "rows": manifest["row_count"], "feature_use": manifest["feature_use"]}


def build_v1_acceptance_report(
    reconciliation_path: Path,
    symbol_master_path: Path,
    daily_contracts_path: Path,
    promotion_manifest_path: Path,
    event_quarantine_path: Path,
    output: Path,
) -> dict[str, Any]:
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    symbol_master = json.loads(symbol_master_path.read_text(encoding="utf-8"))
    daily = json.loads(daily_contracts_path.read_text(encoding="utf-8"))
    promotion = json.loads(promotion_manifest_path.read_text(encoding="utf-8"))
    event = json.loads(event_quarantine_path.read_text(encoding="utf-8"))
    complete_symbols = len([row for row in symbol_master.get("symbols", []) if row.get("sector")])
    checks = {
        "source_reconciliation_ready": bool(reconciliation.get("ready_for_v1")),
        "symbol_master_ready": complete_symbols >= SOURCE_EXPECTATIONS["market_lake"]["minimum"] and not symbol_master.get("quarantined_symbols"),
        "daily_context_ready": bool(daily.get("ready_for_v1")),
        "promotion_manifest_ready": bool(promotion.get("ready_for_v1")),
        "events_fail_closed": event.get("feature_use") == "blocked_in_v1",
    }
    ready = all(checks.values())
    lines = [
        "# Data Bedrock V1 Acceptance",
        "",
        f"- Ready for V1: {'yes' if ready else 'no'}",
        f"- Complete symbol metadata: {complete_symbols}",
        f"- Event quarantine sources: {event.get('source_count', 0)}",
        "",
        "## Checks",
    ]
    for name, passed in checks.items():
        lines.append(f"- {name}: {'pass' if passed else 'fail'}")
    lines.extend([
        "",
        "## Promotion Policy",
        "- Market and daily-context contracts are the only feature-promoted data in V1.",
        "- News, sentiment, GDELT, corporate, and event data stay quarantined and cannot affect validation.",
        "- Derivatives trees are detected but not promoted until their schemas are normalized.",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(output), "ready_for_v1": ready, "checks": checks}


def _inspect_record(source_root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(source_root).as_posix()
    schema, row_count, timestamp_bounds, timestamp_quality = _inspect_tabular(path)
    role = _detect_role(relative, schema)
    decision, promotion_eligible, reason = _policy(role, schema, timestamp_quality)
    return {
        "logical_role": role,
        "path": relative,
        "sha256": _sha256_file(path),
        "byte_size": path.stat().st_size,
        "schema": schema,
        "row_count": row_count,
        "timestamp_bounds": timestamp_bounds,
        "timestamp_quality": timestamp_quality,
        "selection_decision": decision,
        "promotion_eligible": promotion_eligible,
        "reason": reason,
    }


def _detect_role(relative: str, schema: dict[str, Any]) -> str:
    parts = Path(relative).parts
    lower = relative.lower()
    name = Path(relative).name.lower()
    if any(part in {"bronze", "silver", "gold", "reports", "cache"} for part in parts):
        return "generated_artifact"
    if len(parts) >= 2 and parts[0] == "nifty500" and name.endswith("_minute.csv"):
        return "equity_minute"
    if parts and parts[0] == "indices_minute" and name.endswith(".csv"):
        return "index_minute"
    if parts and parts[0] == "nifty_intraday":
        if "vix" in name and "day" in name:
            return "vix_daily"
        return "index_minute" if "minute" in name or "1m" in name else "index_daily"
    if parts and parts[0] == "banknifty_intraday":
        return "index_minute" if "minute" in name or "1m" in name else "index_daily"
    if lower.startswith("bhavcopy/cm/"):
        return "cash_bhavcopy_daily"
    if lower.startswith("bhavcopy/fo/"):
        return "fo_bhavcopy_daily"
    if lower.startswith("option_data/"):
        if "options" in lower:
            return "index_options_minute"
        if "fut" in lower:
            return "index_futures_minute"
        if "spot" in lower:
            return "index_spot_minute"
    if parts and parts[0] == "sentiment":
        if name == "ind_nifty500list.csv":
            return "symbol_metadata"
        if "gdelt" in name:
            return "gdelt_daily"
        return "sentiment_raw"
    if "news" in lower or NEWS_REQUIRED_FIELDS & set(schema.get("canonical_columns", [])):
        return "news_raw"
    return "uncontracted"


def _policy(role: str, schema: dict[str, Any], timestamp_quality: str) -> tuple[str, bool, str]:
    base = ROLE_POLICY[role]
    decision = str(base["decision"])
    eligible = bool(base["promotion_eligible"])
    reason = str(base["reason"])
    if role in {"news_raw", "sentiment_raw", "gdelt_daily"}:
        canonical = set(schema.get("canonical_columns", []))
        if role == "news_raw" and NEWS_REQUIRED_FIELDS <= canonical and timestamp_quality == "point_in_time_safe":
            return "quarantine_promotable_after_join_test", False, "schema complete, still requires reproducible available_at join test"
        return decision, False, reason
    if role in {"equity_minute", "index_minute", "index_spot_minute", "index_futures_minute", "index_options_minute"} and schema.get("name") == "unrecognized_csv":
        return "quarantine_invalid_schema", False, "minute source path detected but schema is not recognized"
    return decision, eligible, reason


def _inspect_tabular(path: Path) -> tuple[dict[str, Any], int, dict[str, str | None], str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _inspect_csv(path)
    if suffix == ".parquet":
        try:
            lazy = pl.scan_parquet(path)
            columns = list(lazy.collect_schema().names())
            row_count = int(lazy.select(pl.len()).collect().item())
        except Exception as exc:  # pragma: no cover - parquet engine message is environment-specific.
            return {"name": "unreadable_parquet", "columns": [], "canonical_columns": [], "valid": False, "error": str(exc)}, 0, {"start": None, "end": None}, "unparseable"
        return _schema("parquet", columns, True), row_count, {"start": None, "end": None}, "missing"
    return {"name": "not_tabular", "columns": [], "canonical_columns": [], "valid": False}, 0, {"start": None, "end": None}, "missing"


def _inspect_csv(path: Path) -> tuple[dict[str, Any], int, dict[str, str | None], str]:
    rows = max(0, _line_count(path) - 1)
    parsed: list[datetime] = []
    samples: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            canonical_columns = sorted(normalize_row({column: "" for column in columns}))
            timestamp_column = _timestamp_column(columns)
            for index, row in enumerate(reader):
                if index >= 200:
                    break
                if timestamp_column and len(samples) < 200:
                    value = str(row.get(timestamp_column, "")).strip()
                    if value:
                        samples.append(value)
                        try:
                            parsed.append(parse_timestamp(value))
                        except ValueError:
                            pass
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return {"name": "unreadable_csv", "columns": [], "canonical_columns": [], "valid": False, "error": str(exc)}, 0, {"start": None, "end": None}, "unparseable"
    quality = _timestamp_quality(samples, parsed)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    valid_market = required <= set(canonical_columns)
    return _schema("minute_ohlcv_v1" if valid_market else "unrecognized_csv", columns, valid_market), rows, {
        "start": min(parsed).isoformat() if parsed else None,
        "end": max(parsed).isoformat() if parsed else None,
    }, quality


def _line_count(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            count += chunk.count(b"\n")
    return count


def _schema(name: str, columns: list[str], valid: bool) -> dict[str, Any]:
    return {"name": name, "columns": columns, "canonical_columns": sorted(normalize_row({column: "" for column in columns})), "valid": valid}


def _timestamp_column(columns: list[str]) -> str | None:
    canonical = {next(iter(normalize_row({column: ""}))): column for column in columns}
    for name in ("available_at", "first_seen_at", "published_at", "timestamp", "datetime", "date"):
        if name in canonical:
            return canonical[name]
    return None


def _timestamp_quality(samples: list[str], parsed: list[datetime]) -> str:
    if not samples:
        return "missing"
    if not parsed:
        return "unparseable"
    if any(_is_date_only(sample) for sample in samples):
        return "date_only"
    if any(_time_part(sample) in _FIXED_TIMES for sample in samples):
        return "fixed_time_suspect"
    if all(value.tzinfo is not None for value in parsed):
        return "point_in_time_safe"
    return "intraday_no_timezone"


def _is_date_only(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}[-/]\d{2}[-/]\d{4}", value.strip()))


def _time_part(value: str) -> str:
    match = re.search(r"(\d{2}:\d{2}:\d{2})", value)
    return match.group(1) if match else ""


def _read_symbol_metadata(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        out: dict[str, dict[str, str]] = {}
        for raw in reader:
            row = normalize_row(raw)
            symbol = _first(row, "symbol", "nse_symbol", "nse symbol", "ticker").upper()
            if not symbol:
                continue
            out[symbol] = {
                "company_name": _first(row, "company_name", "company name", "company", "name"),
                "industry": _first(row, "industry"),
                "sector": _first(row, "sector"),
                "isin": _first(row, "isin", "isin code"),
            }
    return out


def _sector_for_symbol(symbol: str, source: dict[str, str], sector_map: dict[str, str]) -> tuple[str, str, str]:
    explicit = str(source.get("sector") or "").strip()
    if explicit:
        return explicit, "sentiment/ind_nifty500list.csv", "complete"
    configured = str(sector_map.get(symbol) or "").strip()
    if configured:
        return configured, "configs/universe.yaml", "complete"
    industry_sector = _infer_sector_from_industry(str(source.get("industry") or ""))
    if industry_sector:
        return industry_sector, "sentiment/ind_nifty500list.csv:industry", "inferred"
    inferred = INFERRED_SECTOR_BY_SYMBOL.get(symbol) or _infer_sector_from_symbol(symbol)
    if inferred:
        return inferred, "inferred_sector_v1", "inferred"
    return "", "unresolved", "incomplete"


def _infer_sector_from_industry(industry: str) -> str:
    text = industry.lower()
    if not text:
        return ""
    rules = [
        ("Banks", ("bank", "banking")),
        ("FinancialServices", ("finance", "financial", "insurance", "asset management", "housing finance", "capital markets")),
        ("Pharma", ("pharma", "drug", "healthcare", "hospital", "diagnostic", "biotech")),
        ("IT", ("software", "information technology", "it services", "computer")),
        ("Automobile", ("auto", "automobile", "ancillary", "tyre")),
        ("Metals", ("metal", "steel", "aluminium", "mining", "zinc", "copper")),
        ("Power", ("power", "electric", "renewable", "energy")),
        ("OilGas", ("oil", "gas", "petroleum", "refinery")),
        ("Cement", ("cement",)),
        ("Chemicals", ("chemical", "fertilizer", "agrochemical", "specialty chemicals")),
        ("Realty", ("realty", "real estate", "construction")),
        ("Telecom", ("telecom",)),
        ("ConsumerStaples", ("fmcg", "food", "beverage", "tobacco", "personal care")),
        ("ConsumerDiscretionary", ("retail", "restaurant", "apparel", "jewellery", "consumer services")),
        ("ConsumerDurables", ("consumer durable", "paint", "electrical consumer")),
        ("TransportLogistics", ("logistics", "transport", "port", "rail", "airline")),
        ("CapitalGoods", ("capital goods", "industrial", "engineering", "defence", "equipment")),
        ("Media", ("media", "entertainment")),
        ("Textiles", ("textile",)),
    ]
    for sector, tokens in rules:
        if any(token in text for token in tokens):
            return sector
    return ""


def _infer_sector_from_symbol(symbol: str) -> str:
    upper = symbol.upper()
    if any(token in upper for token in ("BANK", "BNK")):
        return "Banks"
    if any(token in upper for token in ("FIN", "AMC", "LIFE", "CARD", "HFC", "CAPITAL", "CREDIT")):
        return "FinancialServices"
    if any(token in upper for token in ("PHARMA", "PHARM", "LIFE", "BIO", "HEALTH", "HOSP", "LAB")):
        return "Pharma"
    if any(token in upper for token in ("TECH", "INFO", "SOFT", "TCS", "HCL", "WIPRO", "LTIM", "MPHASIS")):
        return "IT"
    if any(token in upper for token in ("AUTO", "MOTOR", "TYRE", "FORG", "SONA")):
        return "Automobile"
    if any(token in upper for token in ("STEEL", "METAL", "ZINC", "ALUM", "MIN", "COAL")):
        return "Metals"
    if any(token in upper for token in ("POWER", "ENERGY", "GRID", "SOLAR", "GREEN", "NHPC", "NTPC")):
        return "Power"
    if any(token in upper for token in ("OIL", "GAS", "PETRO", "LNG", "ONGC", "GAIL")):
        return "OilGas"
    if any(token in upper for token in ("CEM", "ACC", "DALBHARAT", "GRASIM")):
        return "Cement"
    if any(token in upper for token in ("CHEM", "SRF", "UPL", "PIIND", "AARTI")):
        return "Chemicals"
    if any(token in upper for token in ("REAL", "PROP", "DLF", "LODHA", "OBEROI", "PRESTIGE")):
        return "Realty"
    if any(token in upper for token in ("TEL", "BHARTI", "IDEA", "TOWER")):
        return "Telecom"
    if any(token in upper for token in ("HOTEL", "FOOD", "RETAIL", "TRENT", "DMART", "JUBL")):
        return "ConsumerDiscretionary"
    if any(token in upper for token in ("HINDUNILVR", "ITC", "DABUR", "MARICO", "BRIT", "NESTLE", "VBL", "COLPAL")):
        return "ConsumerStaples"
    if any(token in upper for token in ("PORT", "CONCOR", "DELHIVERY", "INDIGO", "IRCTC", "RAIL", "RVNL")):
        return "TransportLogistics"
    if any(token in upper for token in ("ABB", "BEL", "BHEL", "HAL", "SIEMENS", "CUMMINS", "POLYCAB")):
        return "CapitalGoods"
    if any(token in upper for token in ("PAINT", "TITAN", "VOLTAS", "HAVELLS")):
        return "ConsumerDurables"
    return "Conglomerate"


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return ""


def _load_snapshot(snapshot_path: Path) -> dict[str, Any]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot.get("format") != DATA_SNAPSHOT_FORMAT:
        raise ValueError(f"not a data source snapshot: {snapshot_path}")
    return snapshot


def _counts(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_role = Counter(record["logical_role"] for record in records)
    by_decision = Counter(record["selection_decision"] for record in records)
    by_quality = Counter(record["timestamp_quality"] for record in records)
    return {
        "sources": len(records),
        "roles": dict(sorted(by_role.items())),
        "decisions": dict(sorted(by_decision.items())),
        "timestamp_quality": dict(sorted(by_quality.items())),
    }


def _identity_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": inventory["format"],
        "roles": inventory["roles"],
        "lake_domains": inventory["lake_domains"],
        "sources": [_identity_source(source) for source in inventory["sources"]],
    }


def _identity_source(source: dict[str, Any]) -> dict[str, Any]:
    return {key: source[key] for key in ("logical_role", "path", "sha256", "byte_size", "schema", "row_count", "timestamp_bounds", "timestamp_quality", "selection_decision", "promotion_eligible", "reason")}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_immutable_json(path: Path, value: object, label: str) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise RuntimeError(f"immutable {label} collision: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
