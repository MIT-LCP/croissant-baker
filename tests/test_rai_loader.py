"""Loader tests for the RAI config YAML: the shipped example, and key checking."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from croissant_baker.rai.loader import load_rai_config
from croissant_baker.rai.schema import AIFairnessConfig

REPO_ROOT = Path(__file__).parent.parent
RAI_EXAMPLE = REPO_ROOT / "rai-example.yaml"


def test_shipped_example_populates_every_fairness_field() -> None:
    """Every key in the template must be one the loader actually reads.

    ``--rai-config --help`` points users at ``rai-example.yaml``, so a key the
    loader does not know is a field silently missing from their output.
    """
    config = load_rai_config(RAI_EXAMPLE)

    unset = [
        f.name
        for f in dataclasses.fields(AIFairnessConfig)
        if getattr(config.ai_fairness, f.name) is None
    ]
    assert unset == []
