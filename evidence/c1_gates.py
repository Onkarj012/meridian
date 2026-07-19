"""Frozen C1 viability, selection, and outcome gates."""
from __future__ import annotations

import json
from typing import Any, Mapping

import pandas as pd

from evidence.stats import sharpe


# Every numeric gate threshold lives in this one frozen block.  The comments
# quote the registered text so changes are reviewable against the authority.
CONFIG: dict[str, Any] = {
    "six_a": {
        # registration §6 / protocol §6a: "net mean daily PnL > 0 at era costs"
        "net_mean_daily_pnl_min_exclusive": 0.0,
        # registration §6 / protocol §6a: "WF Sharpe ≥ 0.75"
        "wf_sharpe_min": 0.75,
        # registration §6 / protocol §6a: "trades: ≥300 WF"
        "wf_trade_min": 300,
        # registration §6 / protocol §6a: "holdout Sharpe ≥ 0.50"
        "holdout_sharpe_min": 0.50,
        # registration §6 / protocol §6a: "trades: ≥150 holdout"
        "holdout_trade_min": 150,
        # registration §6 / protocol §6a: "≥40 per holdout half-year"
        "holdout_half_trade_min": 40,
        # registration §6 / protocol §6a: "≥30 post-2026-04-01 (STT era)"
        "post_2026_04_trade_min": 30,
        # registration §6 / protocol §6a: "positive aggregate PnL and Sharpe at 5 bps stress"
        "stress_slippage_bps": 5.0,
        # registration §6 / protocol §6a: "beats its activity-matched baselines (paired)"
        "paired_baseline_required": True,
        # registration §6 / protocol §6a: "no single quarter/era/time-bucket dominating profits"
        # No registered numeric cap exists: this leg is REPORT-ONLY.
        "domination_gate": "REPORT-ONLY",
        # registration §6 / protocol §6a: "operationally executable"
        "operational_required": True,
    },
    "six_b": {
        # registration §6 / protocol §6b: "ΔSharpe ≥ 0.25 in WF and holdout"
        "delta_sharpe_min": 0.25,
        # registration §6 / protocol §6b: "paired moving-block-bootstrap 95% CI-low > 0"
        "paired_ci_low_min_exclusive": 0.0,
    },
    "dsr_k": 95,  # registration §6: "Cumulative k = 88 prior + 7 = 95"
}


def evaluate_6a_wf(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate only the walk-forward legs of 6a.

    Holdout-specific thresholds are intentionally absent here; call
    :func:`evaluate_6a_holdout` separately after the one-shot holdout opens.
    """
    section = _section(artifact, "wf")
    legs: dict[str, Any] = {}
    legs["net_mean_daily_pnl"] = _leg(
        _number(section, artifact, "mean_daily_pnl", "net_mean_daily_pnl", "mean_daily_pnl_bps"),
        ">",
        CONFIG["six_a"]["net_mean_daily_pnl_min_exclusive"],
    )
    legs["wf_sharpe"] = _leg(
        _number(section, artifact, "sharpe", "annualized_sharpe", "wf_sharpe"),
        ">=",
        CONFIG["six_a"]["wf_sharpe_min"],
    )
    legs["wf_trade_minimum"] = _leg(
        _trade_count(section, artifact),
        ">=",
        CONFIG["six_a"]["wf_trade_min"],
        hard_fail=True,
    )
    stress = _stress_values(section, artifact, CONFIG["six_a"]["stress_slippage_bps"])
    legs["stress_5_bps_positive_pnl"] = _leg(stress["mean"], ">", 0.0)
    legs["stress_5_bps_positive_sharpe"] = _leg(stress["sharpe"], ">", 0.0)
    legs["matched_baselines_paired"] = _bool_leg(
        _first(section, artifact, "beats_matched_baselines", "paired_baseline_pass", "baseline_passed"),
        enforced=True,
    )
    # Registration did not specify what share constitutes domination.  It is
    # emitted and labeled but cannot be promoted to a threshold post hoc.
    legs["quarter_era_bucket_domination"] = {
        "passed": None,
        "enforced": False,
        "status": "REPORT-ONLY",
        "reason": "registration §6 says no domination but supplies no measurable share cap",
    }
    legs["operationally_executable"] = _bool_leg(
        _first(section, artifact, "operationally_executable", "operational_pass"),
        enforced=True,
    )
    hard_legs = [value for value in legs.values() if value.get("enforced")]
    return _verdict("6a_wf", legs, passed=all(value["passed"] is True for value in hard_legs))


def evaluate_6a_holdout(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate holdout-only 6a legs; never called by ``evaluate_6a_wf``."""
    section = _section(artifact, "holdout")
    half_counts = _first(section, artifact, "trades_per_holdout_half", "holdout_half_trades") or []
    if isinstance(half_counts, Mapping):
        half_counts = list(half_counts.values())
    post_count = _first(section, artifact, "post_2026_04_trades", "trades_post_2026_04")
    legs = {
        "holdout_sharpe": _leg(_number(section, artifact, "sharpe", "annualized_sharpe", "holdout_sharpe"), ">=", CONFIG["six_a"]["holdout_sharpe_min"]),
        "holdout_trade_minimum": _leg(_trade_count(section, artifact), ">=", CONFIG["six_a"]["holdout_trade_min"], hard_fail=True),
        "holdout_half_trade_minimum": _leg(min([int(value) for value in half_counts], default=0), ">=", CONFIG["six_a"]["holdout_half_trade_min"], hard_fail=True),
        "post_2026_04_trade_minimum": _leg(int(post_count or 0), ">=", CONFIG["six_a"]["post_2026_04_trade_min"], hard_fail=True),
    }
    return _verdict("6a_holdout", legs, passed=all(value["passed"] is True for value in legs.values()))


def evaluate_6b(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Require both WF and holdout Sharpe deltas plus paired CI-low."""
    wf = _comparison_section(artifact, "wf")
    holdout = _comparison_section(artifact, "holdout")
    threshold = CONFIG["six_b"]["delta_sharpe_min"]
    ci_threshold = CONFIG["six_b"]["paired_ci_low_min_exclusive"]
    legs = {
        "wf_delta_sharpe": _leg(_delta_sharpe(wf, artifact, "wf"), ">=", threshold),
        "holdout_delta_sharpe": _leg(_delta_sharpe(holdout, artifact, "holdout"), ">=", threshold),
        "wf_paired_mbb_ci_low": _leg(_ci_low(wf, artifact, "wf"), ">", ci_threshold),
        "holdout_paired_mbb_ci_low": _leg(_ci_low(holdout, artifact, "holdout"), ">", ci_threshold),
    }
    return _verdict("6b", legs, passed=all(value["passed"] is True for value in legs.values()))


def classify_6e(six_a: Mapping[str, Any] | bool, six_b: Mapping[str, Any] | bool = False) -> dict[str, Any]:
    """Classify outcomes using the registered winner/thin-positive/dead table."""
    a_passed = _passed(six_a)
    b_passed = _passed(six_b)
    if a_passed and b_passed:
        classification = "winner"
        action = "paper-trade exact frozen artifact"
    elif a_passed:
        classification = "thin-positive"
        action = "continue frozen measurement; no promotion"
    else:
        classification = "dead"
        action = "Sleeve F v0 dead"
    return {"gate": "6e", "classification": classification, "action": action, "six_a_passed": a_passed, "six_b_passed": b_passed}


evaluate_6e = classify_6e


def verdict_json(verdict: Mapping[str, Any]) -> str:
    """Serialize a verdict without timestamps or nondeterministic fields."""
    return json.dumps(verdict, sort_keys=True, separators=(",", ":"))


def _verdict(name: str, legs: Mapping[str, Any], *, passed: bool) -> dict[str, Any]:
    return {"gate": name, "passed": bool(passed), "legs": dict(legs)}


def _leg(value: Any, operator: str, threshold: float | int, *, hard_fail: bool = False) -> dict[str, Any]:
    numeric = _finite_number(value)
    if operator == ">":
        passed = numeric is not None and numeric > float(threshold)
    else:
        passed = numeric is not None and numeric >= float(threshold)
    return {
        "passed": bool(passed),
        "enforced": True,
        "hard_fail": bool(hard_fail),
        "value": numeric,
        "operator": operator,
        "threshold": threshold,
    }


def _bool_leg(value: Any, *, enforced: bool) -> dict[str, Any]:
    return {"passed": bool(value) if value is not None else False, "enforced": enforced, "value": value}


def _section(artifact: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = artifact.get(name)
    return value if isinstance(value, Mapping) else artifact


def _comparison_section(artifact: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = artifact.get(name)
    return value if isinstance(value, Mapping) else artifact


def _first(section: Mapping[str, Any], artifact: Mapping[str, Any], *names: str) -> Any:
    for source in (section, artifact):
        for name in names:
            if name in source:
                return source[name]
    return None


def _number(section: Mapping[str, Any], artifact: Mapping[str, Any], *names: str) -> float | None:
    value = _first(section, artifact, *names)
    return _finite_number(value)


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _trade_count(section: Mapping[str, Any], artifact: Mapping[str, Any]) -> int:
    direct = _first(section, artifact, "trades", "trade_count", "executed_trade_count")
    if direct is not None and not isinstance(direct, (pd.DataFrame, list, tuple, dict)):
        return int(direct)
    for source in (section, artifact):
        trades = source.get("trades")
        if isinstance(trades, pd.DataFrame):
            return len(trades)
        if isinstance(trades, (list, tuple)):
            return len(trades)
    daily = _first(section, artifact, "daily")
    if isinstance(daily, pd.DataFrame) and "trade_count" in daily:
        return int(pd.to_numeric(daily["trade_count"], errors="coerce").sum())
    return 0


def _stress_values(section: Mapping[str, Any], artifact: Mapping[str, Any], slippage: float) -> dict[str, float | None]:
    key = f"stress_{str(slippage).replace('.', '_')}_return_bps"
    source = _first(section, artifact, "stress_5_bps", "stress_5bps", "stress")
    if isinstance(source, Mapping):
        mean = _finite_number(source.get("mean_daily_pnl", source.get("mean", source.get("pnl"))))
        stress_sharpe = _finite_number(source.get("sharpe", source.get("annualized_sharpe")))
        return {"mean": mean, "sharpe": stress_sharpe}
    if isinstance(source, pd.DataFrame):
        column = key if key in source else next((name for name in source.columns if "5" in str(name) and "return" in str(name)), None)
        values = pd.to_numeric(source[column], errors="coerce").dropna().tolist() if column else []
        return {"mean": sum(values) / len(values) if values else None, "sharpe": sharpe(values)}
    daily = _first(section, artifact, "daily")
    if isinstance(daily, pd.DataFrame) and key in daily:
        values = pd.to_numeric(daily[key], errors="coerce").dropna().tolist()
        return {"mean": sum(values) / len(values) if values else None, "sharpe": sharpe(values)}
    return {"mean": _finite_number(_first(section, artifact, "stress_mean_daily_pnl", "stress_5_mean_daily_pnl")), "sharpe": _finite_number(_first(section, artifact, "stress_sharpe", "stress_5_sharpe"))}


def _delta_sharpe(section: Mapping[str, Any], artifact: Mapping[str, Any], name: str) -> float | None:
    direct = _first(section, artifact, f"{name}_delta_sharpe", "delta_sharpe")
    if direct is not None:
        return _finite_number(direct)
    candidate = _number(section, artifact, "candidate_sharpe", "sharpe")
    baseline = _number(section, artifact, "baseline_sharpe", "matched_baseline_sharpe")
    return candidate - baseline if candidate is not None and baseline is not None else None


def _ci_low(section: Mapping[str, Any], artifact: Mapping[str, Any], name: str) -> float | None:
    direct = _first(section, artifact, f"{name}_paired_mbb_ci_low", "paired_mbb_ci_low", "ci_low")
    if direct is not None:
        return _finite_number(direct)
    mbb = _first(section, artifact, "paired_mbb", "paired_difference_mbb")
    if isinstance(mbb, Mapping):
        return _finite_number(mbb.get("ci_low", mbb.get("lower", mbb.get("low"))))
    return None


def _passed(value: Mapping[str, Any] | bool) -> bool:
    return bool(value.get("passed", False)) if isinstance(value, Mapping) else bool(value)


# Public names used by report/orchestration callers.
gate_6a_wf = evaluate_6a_wf
gate_6a_holdout = evaluate_6a_holdout
gate_6b = evaluate_6b
gate_6e = classify_6e
