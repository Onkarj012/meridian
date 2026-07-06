import importlib

import pytest


PACKAGES = [
    "ingest",
    "contracts",
    "lake",
    "features",
    "models",
    "evidence",
    "policy",
    "ops",
    "serve",
    "registry",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_top_level_package_is_importable(package):
    importlib.import_module(package)
