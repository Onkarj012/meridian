"""Engine-agnostic offline recomputation and comparison of live snapshots."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import inspect
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from contracts.decision_envelope import PolicyDecision
from contracts.live_snapshot import LiveSnapshot


PASS_TOLERANCE = 1e-10
DEGRADED_TOLERANCE = 1e-6
ParityVerdict = Literal["PASS", "DEGRADED", "FAIL"]


@dataclass(frozen=True)
class RecomputeResult:
    feature_vector: Mapping[str, float]
    score: float
    threshold_active: float
    eligibility_pass: bool
    eligibility_fail_reasons: tuple[str, ...]
    policy_decision: PolicyDecision
    code_digest: str
    model_digest: str
    config_digest: str


@dataclass(frozen=True)
class FeatureDiff:
    name: str
    live: float | None
    offline: float | None
    absdiff: float


@dataclass(frozen=True)
class ParityReport:
    snapshot_id: str
    feature_diffs: tuple[FeatureDiff, ...]
    score_live: float
    score_offline: float
    score_absdiff: float
    threshold_live: float
    threshold_offline: float
    threshold_equal: bool
    eligibility_pass_live: bool
    eligibility_pass_offline: bool
    eligibility_equal: bool
    eligibility_pass_equal: bool
    eligibility_fail_reasons_equal: bool
    decision_live: PolicyDecision
    decision_offline: PolicyDecision
    decision_equal: bool
    digest_equal: bool
    digest_mismatches: tuple[str, ...]
    verdict: ParityVerdict

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json_line(self) -> str:
        return json.dumps(_json_safe(self.to_dict()), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"

    def to_markdown(self) -> str:
        lines = [
            f"# Live parity: {self.snapshot_id}",
            "",
            f"**Verdict: {self.verdict}**",
            "",
            "| Check | Live | Offline | Result |",
            "| --- | ---: | ---: | --- |",
            f"| Score | {self.score_live:.17g} | {self.score_offline:.17g} | absdiff={self.score_absdiff:.17g} |",
            f"| Threshold | {self.threshold_live:.17g} | {self.threshold_offline:.17g} | {'equal' if self.threshold_equal else 'different'} |",
            f"| Eligibility | {self.eligibility_pass_live} | {self.eligibility_pass_offline} | {'equal' if self.eligibility_equal else 'different'} |",
            f"| Policy decision | {self.decision_live} | {self.decision_offline} | {'equal' if self.decision_equal else 'different'} |",
            f"| Digests | - | - | {'equal' if self.digest_equal else 'mismatch: ' + ', '.join(self.digest_mismatches)} |",
            "",
            "## Feature diffs",
            "",
            "| Feature | Live | Offline | Absolute difference |",
            "| --- | ---: | ---: | ---: |",
        ]
        lines.extend(
            f"| {diff.name} | {_format_number(diff.live)} | {_format_number(diff.offline)} | {_format_number(diff.absdiff)} |"
            for diff in self.feature_diffs
        )
        return "\n".join(lines) + "\n"


def _format_number(value: float | None) -> str:
    return "-" if value is None else f"{value:.17g}"


def _json_safe(value: Any) -> Any:
    """Convert comparison sentinels such as ``inf`` to valid JSON ``null``."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {name: _json_safe(item) for name, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def _call_pipeline(fn: Callable[..., Any], primary: object, auxiliary: Mapping[str, object]) -> Any:
    """Call the documented one- or two-argument engine adapter safely.

    ``feature_fn(bars, auxiliary)`` and ``score_fn(features, auxiliary)`` are
    the normal forms.  One-argument adapters are accepted for small engines.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(primary, auxiliary)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    accepts_varargs = any(parameter.kind is parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    return fn(primary, auxiliary) if accepts_varargs or len(positional) >= 2 else fn(primary)


def recompute_from_snapshot(
    snapshot: LiveSnapshot,
    *,
    feature_fn: Callable[..., Mapping[str, float]],
    score_fn: Callable[..., Any],
) -> RecomputeResult:
    """Run injected offline feature/scoring adapters against captured inputs.

    ``score_fn`` normally returns a mapping with ``score``,
    ``eligibility_pass``, ``eligibility_fail_reasons``, and ``policy_decision``.
    Omitted non-score output fields intentionally retain their captured values,
    which supports score-only engines while preserving the comparison surface.
    ``threshold_active`` and the three digests are also optional mapping keys.
    """
    raw_features = _call_pipeline(feature_fn, snapshot.bar_window, snapshot.auxiliary_inputs)
    if not isinstance(raw_features, Mapping):
        raise TypeError("feature_fn must return a name-to-value mapping")
    feature_vector = {str(name): float(value) for name, value in raw_features.items()}
    raw_score = _call_pipeline(score_fn, feature_vector, snapshot.auxiliary_inputs)
    values = _normalise_score_output(raw_score, snapshot)
    return RecomputeResult(
        feature_vector=feature_vector,
        score=float(values["score"]),
        threshold_active=float(values["threshold_active"]),
        eligibility_pass=bool(values["eligibility_pass"]),
        eligibility_fail_reasons=tuple(values["eligibility_fail_reasons"]),
        policy_decision=values["policy_decision"],
        code_digest=str(values["code_digest"]),
        model_digest=str(values["model_digest"]),
        config_digest=str(values["config_digest"]),
    )


def _normalise_score_output(raw_score: Any, snapshot: LiveSnapshot) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "threshold_active": snapshot.threshold_active,
        "eligibility_pass": snapshot.eligibility_pass,
        "eligibility_fail_reasons": snapshot.eligibility_fail_reasons,
        "policy_decision": snapshot.policy_decision,
        "code_digest": snapshot.code_digest,
        "model_digest": snapshot.model_digest,
        "config_digest": snapshot.config_digest,
    }
    if isinstance(raw_score, Mapping):
        values = {**defaults, **raw_score}
        if "score" not in values:
            raise ValueError("score_fn result mapping must contain 'score'")
        return values
    if isinstance(raw_score, (int, float)):
        return {**defaults, "score": raw_score}
    if isinstance(raw_score, tuple) and len(raw_score) == 4:
        score, eligibility_pass, fail_reasons, decision = raw_score
        return {
            **defaults,
            "score": score,
            "eligibility_pass": eligibility_pass,
            "eligibility_fail_reasons": fail_reasons,
            "policy_decision": decision,
        }
    raise TypeError("score_fn must return a score, a four-tuple, or an output mapping")


def _numeric_diff(live: object, offline: object) -> float:
    if not isinstance(live, (int, float)) or not isinstance(offline, (int, float)):
        return math.inf
    if not math.isfinite(live) or not math.isfinite(offline):
        return math.inf
    return abs(float(live) - float(offline))


def compare(snapshot: LiveSnapshot, recompute: RecomputeResult) -> ParityReport:
    """Compare live and offline outputs using the frozen serving parity gate."""
    feature_diffs = tuple(
        FeatureDiff(
            name=name,
            live=float(snapshot.feature_vector[name]) if name in snapshot.feature_vector else None,
            offline=float(recompute.feature_vector[name]) if name in recompute.feature_vector else None,
            absdiff=_numeric_diff(snapshot.feature_vector.get(name), recompute.feature_vector.get(name)),
        )
        for name in sorted(set(snapshot.feature_vector) | set(recompute.feature_vector))
    )
    score_absdiff = _numeric_diff(snapshot.score, recompute.score)
    threshold_equal = snapshot.threshold_active == recompute.threshold_active
    eligibility_pass_equal = snapshot.eligibility_pass == recompute.eligibility_pass
    eligibility_fail_reasons_equal = set(snapshot.eligibility_fail_reasons) == set(recompute.eligibility_fail_reasons)
    eligibility_equal = eligibility_pass_equal and eligibility_fail_reasons_equal
    decision_equal = snapshot.policy_decision == recompute.policy_decision
    digest_mismatches = tuple(
        name
        for name in ("code_digest", "model_digest", "config_digest")
        if getattr(snapshot, name) != getattr(recompute, name)
    )
    numeric_diffs = [score_absdiff, *(diff.absdiff for diff in feature_diffs)]
    all_exact_enough = all(diff <= PASS_TOLERANCE for diff in numeric_diffs)
    all_within_degraded = all(diff <= DEGRADED_TOLERANCE for diff in numeric_diffs)
    some_degraded = any(PASS_TOLERANCE < diff <= DEGRADED_TOLERANCE for diff in numeric_diffs)
    if not digest_mismatches and all_exact_enough and threshold_equal and eligibility_equal and decision_equal:
        verdict: ParityVerdict = "PASS"
    elif (
        not digest_mismatches
        and decision_equal
        and threshold_equal
        and eligibility_equal
        and all_within_degraded
        and some_degraded
    ):
        verdict = "DEGRADED"
    else:
        verdict = "FAIL"
    return ParityReport(
        snapshot_id=snapshot.snapshot_id,
        feature_diffs=feature_diffs,
        score_live=snapshot.score,
        score_offline=recompute.score,
        score_absdiff=score_absdiff,
        threshold_live=snapshot.threshold_active,
        threshold_offline=recompute.threshold_active,
        threshold_equal=threshold_equal,
        eligibility_pass_live=snapshot.eligibility_pass,
        eligibility_pass_offline=recompute.eligibility_pass,
        eligibility_equal=eligibility_equal,
        eligibility_pass_equal=eligibility_pass_equal,
        eligibility_fail_reasons_equal=eligibility_fail_reasons_equal,
        decision_live=snapshot.policy_decision,
        decision_offline=recompute.policy_decision,
        decision_equal=decision_equal,
        digest_equal=not digest_mismatches,
        digest_mismatches=digest_mismatches,
        verdict=verdict,
    )


class ParityReportWriter:
    """Durable JSONL appender for investigation and trend reporting."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, report: ParityReport) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(report.to_json_line())
            handle.flush()
            os.fsync(handle.fileno())


def append_parity_report(path: str | Path, report: ParityReport) -> None:
    ParityReportWriter(path).append(report)
