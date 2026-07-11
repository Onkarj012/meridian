"""Sleeve F futures-router feature composition and phase-2 hardening."""
from __future__ import annotations

import math
import importlib.util
import re
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from features.calendar import build_calendar_features
from features.regime import build_regime_features
from features.volatility import build_volatility_features

_SLEEVES_SPEC = importlib.util.spec_from_file_location("_meridian_evidence_sleeves", Path(__file__).resolve().parents[1] / "evidence" / "sleeves.py")
_SLEEVES = importlib.util.module_from_spec(_SLEEVES_SPEC)
assert _SLEEVES_SPEC and _SLEEVES_SPEC.loader
_SLEEVES_SPEC.loader.exec_module(_SLEEVES)
canonical_report = _SLEEVES.canonical_report
gate_failure_reason = _SLEEVES.gate_failure_reason
promotion_gates_from_metrics = _SLEEVES.promotion_gates_from_metrics

def build_sleeve_f_features(rows=None, *args, **kwargs):
    return build_regime_features(build_calendar_features(build_volatility_features(rows or [])))


INDEX_PROXY_SYMBOLS = {"NIFTY", "NIFTY 50", "NSE:NIFTY 50", "NIFTY50", "BANKNIFTY INDEX"}
_INDEX_PROXY_COMPACT_SYMBOLS = {
    "NIFTY",
    "NIFTY50",
    "NIFTY50INDEX",
    "NSE:NIFTY",
    "NSE:NIFTY50",
    "NSE:NIFTY50INDEX",
    "BANKNIFTY",
    "BANKNIFTYINDEX",
    "NSE:BANKNIFTY",
    "NSE:BANKNIFTYINDEX",
}
_FUTURES_INSTRUMENT_TYPES = {"FUT", "FUTIDX", "FUTSTK", "FUTURE", "NFO-FUT", "NFO:FUT", "NFO_FUT"}
_CONTINUOUS_FUTURES_RE = re.compile(r"^[A-Z0-9]+-(I|II|III)$")


def validate_real_futures_feed(
    rows: Iterable[Mapping[str, Any]],
    *,
    require_volume: bool = True,
    max_oi_staleness_rows: int = 5,
    hard_fail_stale_oi: bool = False,
) -> dict[str, Any]:
    """Validate that Sleeve F rows are actual futures bars, not index proxies."""
    row_list = [dict(row) for row in rows]
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    last_oi_by_symbol: dict[str, float] = {}
    stale_oi_count_by_symbol: dict[str, int] = defaultdict(int)
    zero_oi_count = 0
    zero_volume_count = 0
    for index, row in enumerate(row_list):
        symbol = _primary_symbol(row)
        instrument_type = _instrument_type(row)
        continuous_futures = _has_continuous_futures_alias(row)
        expiry = row.get("expiry")
        token = row.get("instrument_token")
        volume = _number(row.get("volume"))
        oi = _number(row.get("oi", row.get("open_interest")))

        reasons = []
        row_warnings = []
        if _is_index_proxy_symbol(symbol) or not _is_futures_like(row):
            reasons.append("index_proxy_or_missing_futures_symbol")
        if not _has_bar_timestamp(row):
            reasons.append("missing_timestamp")
        if not continuous_futures and not expiry:
            reasons.append("missing_expiry")
        if not continuous_futures and not row.get("tradingsymbol"):
            reasons.append("missing_tradingsymbol")
        if not continuous_futures and token in (None, ""):
            reasons.append("missing_instrument_token")
        if oi is None or oi < 0:
            reasons.append("missing_or_negative_oi")
        elif oi == 0:
            zero_oi_count += 1
            row_warnings.append("zero_oi")
        elif symbol in last_oi_by_symbol and oi == last_oi_by_symbol[symbol]:
            stale_oi_count_by_symbol[symbol] += 1
            if max_oi_staleness_rows > 0 and stale_oi_count_by_symbol[symbol] >= max_oi_staleness_rows:
                if hard_fail_stale_oi:
                    reasons.append("stale_oi")
                else:
                    row_warnings.append("stale_oi")
        else:
            stale_oi_count_by_symbol[symbol] = 0
        if oi is not None and oi > 0:
            last_oi_by_symbol[symbol] = oi
        if require_volume and (volume is None or volume < 0):
            reasons.append("missing_or_negative_volume")
        elif volume == 0:
            zero_volume_count += 1
            row_warnings.append("zero_volume")
        if reasons:
            failures.append({"row_index": index, "tradingsymbol": row.get("tradingsymbol"), "symbol": row.get("symbol"), "reasons": reasons})
        if row_warnings:
            warnings.append({"row_index": index, "tradingsymbol": row.get("tradingsymbol"), "symbol": row.get("symbol"), "reasons": row_warnings})

    return {
        "validated": not failures and bool(row_list),
        "rows": len(row_list),
        "require_volume": bool(require_volume),
        "zero_oi_count": zero_oi_count,
        "zero_oi_share": zero_oi_count / len(row_list) if row_list else 0.0,
        "zero_volume_count": zero_volume_count,
        "zero_volume_share": zero_volume_count / len(row_list) if row_list else 0.0,
        "failures": failures,
        "warnings": warnings,
    }


def build_sleeve_f_router_features(rows: Iterable[Mapping[str, Any]], *, vol_window: int = 30) -> list[dict[str, Any]]:
    """Build trailing-only futures-router features used by the phase-2 runner."""
    by_symbol: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max(2, int(vol_window))))
    last_close: dict[str, float] = {}
    last_volume: dict[str, float] = {}
    last_oi: dict[str, float] = {}
    output: list[dict[str, Any]] = []
    for row in sorted((dict(item) for item in rows), key=lambda item: (str(item.get("tradingsymbol", item.get("symbol", ""))), _timestamp_value(item))):
        symbol = str(row.get("tradingsymbol", row.get("symbol", "")))
        close = _number(row.get("close", row.get("price")))
        volume = _number(row.get("volume")) or 0.0
        oi = _number(row.get("oi", row.get("open_interest"))) or 0.0
        previous_close = last_close.get(symbol)
        return_bps = ((close / previous_close - 1.0) * 10000.0) if close is not None and previous_close else 0.0
        returns = by_symbol[symbol]
        returns.append(return_bps)
        realized_vol = _std(list(returns))
        previous_volume = last_volume.get(symbol, volume)
        previous_oi = last_oi.get(symbol, oi)
        enriched = {
            **row,
            "minute_of_day": _minute_of_day(_timestamp_value(row)),
            f"realized_vol_{vol_window}m": realized_vol,
            "realized_vol": realized_vol,
            "return_1m_bps": return_bps,
            "volume": volume,
            "volume_change": volume - previous_volume,
            "volume_ratio": volume / previous_volume if previous_volume else 0.0,
            "oi": oi,
            "oi_change": oi - previous_oi,
            "oi_ratio": oi / previous_oi if previous_oi else 0.0,
        }
        for optional in ("basis_bps", "basis", "regime", "volatility_regime", "trend_regime"):
            if optional in row:
                enriched[optional] = row[optional]
        output.append(enriched)
        if close is not None:
            last_close[symbol] = close
        last_volume[symbol] = volume
        last_oi[symbol] = oi
    return output


def run_sleeve_f_walkforward(
    *,
    rows: Iterable[Mapping[str, Any]],
    folds: Iterable[Mapping[str, Any]] | None = None,
    grid: Iterable[Mapping[str, Any]] | None = None,
    grid_committed: bool = False,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    """Run a deterministic Phase-2 futures-router prerequisite report."""
    validation = validate_real_futures_feed(rows)
    grid_list = [dict(item) for item in (grid or [])]
    prerequisites = {
        "real_feed_validated": validation["validated"],
        "committed_grid": bool(grid_committed),
        "single_tree_multi_tree_compared": _has_model_style(grid_list, "single_tree") and _has_model_style(grid_list, "multi_tree"),
        "fixture_mode": bool(fixture_mode),
    }
    if not validation["validated"]:
        return canonical_report(sleeve="F", phase=2, prerequisites=prerequisites, gates={}, candidate=None, reason="real_futures_feed_validation_failed", extra={"feed_validation": validation})
    if not grid_committed:
        return canonical_report(sleeve="F", phase=2, prerequisites=prerequisites, gates={}, candidate=None, reason="walkforward_grid_not_committed", extra={"feed_validation": validation})

    fold_list = [dict(item) for item in (folds or [])]
    candidates = [_summarize_f_config(config, fold_list) for config in grid_list] or [_summarize_f_config({"id": "single_tree_incumbent", "model_style": "single_tree"}, fold_list)]
    candidate = max(candidates, key=lambda item: (item["worst_fold_bps"], item["expected_value_bps"], item["trades"]))
    gates = promotion_gates_from_metrics(
        trades=candidate["trades"],
        trading_days=candidate["trading_days"],
        distinct_symbols=1,
        max_symbol_trade_share=0.0,
        positive_fold_share=candidate["positive_fold_share"],
        worst_fold_bps=candidate["worst_fold_bps"],
        ci_low_bps=candidate["ci_low_bps"],
        beats_baselines=bool(candidate.get("beats_baselines", False)),
        dsr=float(candidate.get("dsr", 0.0)),
        sealed_test_passed=bool(candidate.get("sealed_test_passed", False)),
        fixture_mode=fixture_mode,
    )
    promoted = all(gates.values()) and all(prerequisites.values())
    return canonical_report(
        sleeve="F",
        phase=2,
        prerequisites=prerequisites,
        gates=gates,
        candidate=candidate,
        promoted=promoted,
        reason=None if promoted else gate_failure_reason(gates),
        extra={"feed_validation": validation, "config_summaries": candidates},
    )


def _summarize_f_config(config: Mapping[str, Any], folds: list[Mapping[str, Any]]) -> dict[str, Any]:
    config_id = str(config.get("id", config.get("name", "config")))
    configured = config.get("fold_results", config.get("folds"))
    fold_rows = [dict(item) for item in configured] if configured else [dict(item) for item in folds]
    returns = [float(item.get("net_return_bps", item.get("net_return", item.get("expected_value_bps", 0.0))) or 0.0) for item in fold_rows]
    trades = sum(int(item.get("trades", 0) or 0) for item in fold_rows)
    return {
        "config_id": config_id,
        "model_style": str(config.get("model_style", config.get("style", config_id))),
        "trades": int(config.get("trades", trades)),
        "trading_days": int(config.get("trading_days", len(fold_rows))),
        "expected_value_bps": sum(returns) / len(returns) if returns else float(config.get("expected_value_bps", 0.0) or 0.0),
        "ci_low_bps": float(config.get("ci_low_bps", min(returns) if returns else 0.0) or 0.0),
        "positive_fold_share": sum(value > 0 for value in returns) / len(returns) if returns else float(config.get("positive_fold_share", 0.0) or 0.0),
        "worst_fold_bps": min(returns) if returns else float(config.get("worst_fold_bps", 0.0) or 0.0),
        "beats_baselines": bool(config.get("beats_baselines", False)),
        "dsr": float(config.get("dsr", config.get("deflated_sharpe_ratio", 0.0)) or 0.0),
        "sealed_test_passed": bool(config.get("sealed_test_passed", False)),
    }


def _has_model_style(grid: list[Mapping[str, Any]], style: str) -> bool:
    return any(style in str(item.get("model_style", item.get("style", item.get("id", "")))).lower() for item in grid)


def _primary_symbol(row: Mapping[str, Any]) -> str:
    for value in (row.get("tradingsymbol"), row.get("symbol")):
        text = _clean_symbol(value)
        if text:
            return text
    return ""


def _symbol_candidates(row: Mapping[str, Any]) -> list[str]:
    candidates = []
    for value in (row.get("tradingsymbol"), row.get("symbol")):
        text = _clean_symbol(value)
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def _clean_symbol(value: Any) -> str:
    return " ".join(str(value or "").upper().strip().split())


def _compact_symbol(value: str) -> str:
    return value.replace(" ", "")


def _instrument_type(row: Mapping[str, Any]) -> str:
    return _clean_symbol(row.get("instrument_type", row.get("segment", "")))


def _is_index_proxy_symbol(symbol: str) -> bool:
    text = _clean_symbol(symbol)
    compact = _compact_symbol(text)
    exchange_stripped = compact.split(":", 1)[-1]
    return text in INDEX_PROXY_SYMBOLS or compact in _INDEX_PROXY_COMPACT_SYMBOLS or exchange_stripped in _INDEX_PROXY_COMPACT_SYMBOLS


def _is_futures_like(row: Mapping[str, Any]) -> bool:
    instrument_type = _instrument_type(row)
    if instrument_type in _FUTURES_INSTRUMENT_TYPES:
        return True
    for symbol in _symbol_candidates(row):
        compact = _compact_symbol(symbol)
        if "FUT" in compact or _CONTINUOUS_FUTURES_RE.match(compact):
            return True
    return False


def _has_continuous_futures_alias(row: Mapping[str, Any]) -> bool:
    return any(_CONTINUOUS_FUTURES_RE.match(_compact_symbol(symbol)) for symbol in _symbol_candidates(row))


def _has_bar_timestamp(row: Mapping[str, Any]) -> bool:
    if row.get("timestamp") not in (None, "") or row.get("datetime") not in (None, ""):
        return True
    date_value = row.get("date")
    time_value = row.get("time")
    if date_value not in (None, "") and time_value not in (None, ""):
        return True
    date_text = str(date_value or "")
    return bool(date_text and ("T" in date_text or (len(date_text) >= 16 and date_text[10:11] == " ")))


def _timestamp_value(row: Mapping[str, Any]) -> str:
    if row.get("timestamp") not in (None, ""):
        return str(row.get("timestamp"))
    if row.get("datetime") not in (None, ""):
        return str(row.get("datetime"))
    if row.get("date") not in (None, "") and row.get("time") not in (None, ""):
        return f"{row.get('date')}T{row.get('time')}"
    return str(row.get("date", ""))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _minute_of_day(value: Any) -> int:
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.hour * 60 + parsed.minute
    except ValueError:
        pass
    if len(text) >= 16 and text[11:13].isdigit() and text[14:16].isdigit():
        return int(text[11:13]) * 60 + int(text[14:16])
    return 0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
