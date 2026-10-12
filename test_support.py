"""Isolated mechanical inputs for offline tests, never experiment inputs."""

import tempfile
from functools import lru_cache
from pathlib import Path

import retailer_profiles
from generate_inputs import build_bank, build_registry, write

_TEMP = tempfile.TemporaryDirectory()


@lru_cache(maxsize=1)
def inputs():
    path = Path(_TEMP.name)
    write(path / "option_bank.json", build_bank(None, 1, demo=True))
    write(path / "retailer_profiles.json", build_registry(None, {}, demo=True))
    return path


def bind_test_registry():
    retailer_profiles.REGISTRY = inputs() / "retailer_profiles.json"
    retailer_profiles.registry.cache_clear()
