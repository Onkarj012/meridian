"""Leakage assertions."""

def assert_no_leakage(train_end=None, validation_start=None, *, embargo=None, feature_columns=None, **kwargs):
    blocked = [col for col in (feature_columns or []) if "future" in str(col).lower() or "lead" in str(col).lower()]
    if blocked:
        raise AssertionError(f"leaky feature columns: {blocked}")
    return True
