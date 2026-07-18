"""Causal V1 candidate feature matrices for intraday prediction."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from features.intraday_external import (
    BANK_FEATURE_COLUMNS,
    BASIS_FEATURE_COLUMNS,
    SPOT_FEATURE_COLUMNS,
    VIX_FEATURE_COLUMNS,
    load_intraday_external_features,
)
from features.intraday_prediction import (
    FEATURE_MANIFEST as V0_FEATURE_MANIFEST,
    FORBIDDEN_COLUMNS as V0_FORBIDDEN_COLUMNS,
    build_prediction_features as build_v0_prediction_features,
)


_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "registrations/intraday-pred-v1/feature_manifest.json"

V1_A_FEATURE_MANIFEST = list(V0_FEATURE_MANIFEST)
V1_B_FEATURE_MANIFEST = [
    column for column in V1_A_FEATURE_MANIFEST if column not in {"basis", "basis_chg_30m"}
] + list(SPOT_FEATURE_COLUMNS) + list(BASIS_FEATURE_COLUMNS)
V1_C_FEATURE_MANIFEST = V1_B_FEATURE_MANIFEST + list(BANK_FEATURE_COLUMNS) + list(VIX_FEATURE_COLUMNS)

FEATURE_MANIFESTS = {
    "V1-A": V1_A_FEATURE_MANIFEST,
    "V1-B": V1_B_FEATURE_MANIFEST,
    "V1-C": V1_C_FEATURE_MANIFEST,
}
CANDIDATE_FEATURES = FEATURE_MANIFESTS
V1_FEATURE_MANIFESTS = FEATURE_MANIFESTS
FEATURE_MANIFEST = V1_A_FEATURE_MANIFEST

FORBIDDEN_COLUMNS = set(V0_FORBIDDEN_COLUMNS) | {
    "expected_log_return_bps",
    "median_abs_move_bps",
    "target_h15_dir",
    "target_h60_dir",
    "target_h15_normalized_magnitude",
    "target_h60_normalized_magnitude",
    "target_h15_scale",
    "target_h60_scale",
}
_RAW_INPUT_COLUMNS = {
    "datetime", "trade_date", "f_open", "f_high", "f_low", "f_close", "f_vol", "f_oi",
}
_EXECUTION_OR_TARGET_COLUMNS = FORBIDDEN_COLUMNS - _RAW_INPUT_COLUMNS


def _assert_manifest_conformance() -> None:
    with _MANIFEST_PATH.open(encoding="utf-8") as handle:
        registered = json.load(handle)["candidates"]
    for candidate, produced in FEATURE_MANIFESTS.items():
        expected = list(registered[candidate])
        assert produced == expected, f"{candidate} feature manifest differs from registration"
    assert [len(FEATURE_MANIFESTS[name]) for name in ("V1-A", "V1-B", "V1-C")] == [42, 54, 71]


_assert_manifest_conformance()


def assert_no_forbidden_columns(frame: pd.DataFrame) -> None:
    """Raise if a frame intended as a feature matrix contains target/execution data."""
    forbidden = sorted(set(frame.columns) & FORBIDDEN_COLUMNS)
    forbidden.extend(sorted(column for column in frame.columns if str(column).startswith("target_h")))
    if forbidden:
        raise AssertionError(f"forbidden columns entered intraday feature matrix: {sorted(set(forbidden))}")


def build_prediction_features_v1(
    df: pd.DataFrame,
    candidate: str = "V1-A",
    *,
    cutoff: pd.Timestamp | str | None = None,
    external_features: pd.DataFrame | None = None,
    external_loader_kwargs: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Build one registered V1 candidate in its exact feature order.

    ``external_features`` must be the aligned result of
    :func:`features.intraday_external.load_intraday_external_features`.  When
    omitted for V1-B/V1-C, this function invokes that adapter itself.
    """
    candidate_name = _normalise_candidate(candidate)
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("df must be a non-empty DataFrame")
    unsafe = sorted(set(df.columns) & _EXECUTION_OR_TARGET_COLUMNS)
    unsafe.extend(sorted(column for column in df.columns if str(column).startswith("target_h")))
    if unsafe:
        raise AssertionError(f"forbidden input columns: {sorted(set(unsafe))}")

    prepared = df.copy()
    prepared["datetime"] = pd.to_datetime(prepared["datetime"], errors="raise")
    prepared = prepared.sort_values("datetime", kind="stable").reset_index(drop=True)
    base, _ = build_v0_prediction_features(prepared)
    if candidate_name == "V1-A":
        result = base.reindex(columns=FEATURE_MANIFESTS[candidate_name]).copy()
        assert_no_forbidden_columns(result)
        return result, list(FEATURE_MANIFESTS[candidate_name])

    mode = candidate_name
    if external_features is None:
        if cutoff is None:
            cutoff = prepared["datetime"].max() + pd.Timedelta(minutes=1)
        loader_kwargs = dict(external_loader_kwargs or {})
        external_features, _ = load_intraday_external_features(prepared, cutoff, mode, **loader_kwargs)
    external = external_features.reset_index(drop=True).copy()
    if len(external) != len(prepared):
        raise ValueError("external_features must align row-for-row with decision rows")
    required_external = _external_manifest(mode)
    missing = sorted(set(required_external).difference(external.columns))
    if missing:
        raise ValueError(f"external_features are missing {', '.join(missing)}")
    legacy_removed = base.drop(columns=["basis", "basis_chg_30m"])
    result = pd.concat([legacy_removed, external.loc[:, required_external]], axis=1)
    result = result.reindex(columns=FEATURE_MANIFESTS[candidate_name]).copy()
    assert list(result.columns) == FEATURE_MANIFESTS[candidate_name]
    assert_no_forbidden_columns(result)
    return result, list(FEATURE_MANIFESTS[candidate_name])


def build_prediction_features(
    df: pd.DataFrame,
    candidate: str = "V1-A",
    **kwargs: Any,
) -> tuple[pd.DataFrame, list[str]]:
    """Short alias matching the frozen V0 feature-builder interface."""
    return build_prediction_features_v1(df, candidate, **kwargs)


def build_candidate_features(
    df: pd.DataFrame,
    candidate: str,
    **kwargs: Any,
) -> tuple[pd.DataFrame, list[str]]:
    """Explicitly named alias for callers building the candidate ladder."""
    return build_prediction_features_v1(df, candidate, **kwargs)


def _external_manifest(candidate: str) -> list[str]:
    if candidate == "V1-B":
        return list(SPOT_FEATURE_COLUMNS) + list(BASIS_FEATURE_COLUMNS)
    return list(SPOT_FEATURE_COLUMNS) + list(BASIS_FEATURE_COLUMNS) + list(BANK_FEATURE_COLUMNS) + list(VIX_FEATURE_COLUMNS)


def _normalise_candidate(candidate: str) -> str:
    name = str(candidate).strip().upper().replace("_", "-")
    aliases = {"A": "V1-A", "B": "V1-B", "C": "V1-C"}
    name = aliases.get(name, name)
    if name not in FEATURE_MANIFESTS:
        raise ValueError("candidate must be V1-A, V1-B, or V1-C")
    return name
