"""Fixtures shared by the whole suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from croissant_baker import compression
from croissant_baker.handlers import base_handler
from croissant_baker.handlers import registry as registry_module


@pytest.fixture(autouse=True)
def clean_globals():
    """Snapshot and restore every process-wide registry around each test."""
    compressions = list(compression._registry)
    warned = set(base_handler._warned)
    warned_calls = set(base_handler._warned_calls)
    handlers = list(registry_module._default._handlers)

    yield

    compression._registry[:] = compressions
    base_handler._warned.clear()
    base_handler._warned.update(warned)
    base_handler._warned_calls.clear()
    base_handler._warned_calls.update(warned_calls)
    registry_module._default._handlers[:] = handlers


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """An empty dataset directory to write fixture files into."""
    target = tmp_path / "dataset"
    target.mkdir()
    return target
