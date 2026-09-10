"""Loader tests for the RAI config YAML: the shipped example, and key checking."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from croissant_baker.rai.loader import load_rai_config
from croissant_baker.rai.schema import AIFairnessConfig

REPO_ROOT = Path(__file__).parent.parent
RAI_EXAMPLE = REPO_ROOT / "rai-example.yaml"


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rai.yaml"
    path.write_text(body, encoding="utf-8")
    return path


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


def test_unknown_fairness_key_is_refused(tmp_path: Path) -> None:
    """The typo from the template: close enough to look right, silently dropped."""
    path = write_config(
        tmp_path,
        "ai_fairness:\n  social_impact: It enables research.\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "ai_fairness.social_impact" in message
    assert "data_social_impact" in message


def test_unknown_top_level_key_is_refused(tmp_path: Path) -> None:
    path = write_config(tmp_path, "provenance:\n  models: []\n")

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    message = str(excinfo.value)
    assert "provenance" in message
    assert "ai_fairness" in message
    assert "lineage" in message
    assert "activities" in message


def test_unknown_key_inside_an_activity_agent_is_refused(tmp_path: Path) -> None:
    """The reported path has to say which entry, not just which level."""
    path = write_config(
        tmp_path,
        "activities:\n"
        "  - id: ACT-001\n"
        "    type: data_collection\n"
        "    agents:\n"
        "      - name: A team\n"
        "      - nme: A typo\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    message = str(excinfo.value)
    assert "activities[0].agents[1].nme" in message
    assert "is_synthetic" in message


def test_a_config_of_valid_keys_still_loads(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "ai_fairness:\n"
        "  data_social_impact: It enables research.\n"
        "  has_synthetic_data: true\n"
        "lineage:\n"
        "  source_datasets:\n"
        "    - url: https://example.org/source\n"
        "      name: Source\n"
        "      organisation: Example\n"
        "      license: CC-BY-4.0\n"
        "  models:\n"
        "    - url: https://example.org/model\n"
        "activities:\n"
        "  - id: ACT-001\n"
        "    type: data_collection\n"
        "    collection_types:\n"
        "      - observations\n"
        "    agents:\n"
        "      - name: A team\n"
        "        is_synthetic: false\n"
        "    platforms:\n"
        "      - name: A tool\n",
    )

    config = load_rai_config(path)

    assert config.ai_fairness.data_social_impact == "It enables research."
    assert config.ai_fairness.has_synthetic_data is True
    assert [s.name for s in config.lineage.source_datasets] == ["Source"]
    assert [m.url for m in config.lineage.models] == ["https://example.org/model"]
    assert [a.name for a in config.activities[0].agents] == ["A team"]
    assert [p.name for p in config.activities[0].platforms] == ["A tool"]


def test_an_empty_file_loads_to_an_empty_config(tmp_path: Path) -> None:
    config = load_rai_config(write_config(tmp_path, ""))

    assert config.ai_fairness == AIFairnessConfig()
    assert config.lineage.source_datasets == []
    assert config.lineage.models == []
    assert config.activities == []


def test_a_top_level_list_is_refused(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- ai_fairness: {}\n")

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    assert str(path) in str(excinfo.value)


def test_a_yaml_syntax_error_is_refused(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ai_fairness:\n  data_biases: [unclosed\n")

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    assert str(path) in str(excinfo.value)
