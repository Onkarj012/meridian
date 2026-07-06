from pathlib import Path


BANNED_PATTERNS = ["center=True", "shift(-"]


def test_feature_source_has_no_mechanical_lookahead_patterns():
    feature_root = Path(__file__).resolve().parents[1] / "features"
    offenders = []

    for path in feature_root.rglob("*.py"):
        source = path.read_text()
        for pattern in BANNED_PATTERNS:
            if pattern in source:
                offenders.append(f"{path.relative_to(feature_root)} contains {pattern!r}")

    assert offenders == []

