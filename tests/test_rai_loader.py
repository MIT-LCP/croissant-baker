"""Loader tests for the RAI config YAML: the shipped example, and key checking."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from croissant_baker.rai import schema
from croissant_baker.rai.loader import load_rai_config
from croissant_baker.rai.schema import AIFairnessConfig

REPO_ROOT = Path(__file__).parent.parent
RAI_EXAMPLE = REPO_ROOT / "rai-example.yaml"


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rai.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _collect(value, found: dict) -> dict:
    """Group every dataclass instance reachable from ``value`` by its class."""
    if dataclasses.is_dataclass(value):
        found.setdefault(type(value), []).append(value)
        for f in dataclasses.fields(value):
            _collect(getattr(value, f.name), found)
    elif isinstance(value, list):
        for item in value:
            _collect(item, found)
    return found


def _is_set(value) -> bool:
    """A field the template actually says something about.

    ``False`` counts: ``has_synthetic_data: false`` is a deliberate answer,
    not an omission. An empty string or list is not.
    """
    if value is None:
        return False
    if isinstance(value, (str, list)):
        return bool(value)
    return True


def test_shipped_example_exercises_every_schema_field() -> None:
    """The template has to show every field the config can carry.

    ``--rai-config --help`` points users at ``rai-example.yaml``, so a field
    missing from it is a field they will never know they could have filled in,
    and a key it spells wrong is one silently dropped from their output.
    """
    found = _collect(load_rai_config(RAI_EXAMPLE), {})

    declared = {
        obj
        for obj in vars(schema).values()
        if dataclasses.is_dataclass(obj) and obj.__module__ == schema.__name__
    }
    assert sorted(c.__name__ for c in declared - set(found)) == [], (
        "a schema dataclass has no entry in the template"
    )

    unset = sorted(
        f"{cls.__name__}.{f.name}"
        for cls in declared
        for f in dataclasses.fields(cls)
        if not any(_is_set(getattr(instance, f.name)) for instance in found[cls])
    )
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


def test_a_string_of_collection_types_is_refused(tmp_path: Path) -> None:
    """Iterating a string yields characters, which is never what was meant."""
    path = write_config(
        tmp_path,
        "activities:\n"
        "  - id: ACT-001\n"
        "    type: data_collection\n"
        "    collection_types: observations\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "activities[0].collection_types" in message


def test_a_mapping_of_collection_types_is_refused(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "activities:\n"
        "  - id: ACT-001\n"
        "    type: data_collection\n"
        "    collection_types:\n"
        "      observations: true\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    assert "activities[0].collection_types" in str(excinfo.value)


def test_a_nested_collection_type_is_refused(tmp_path: Path) -> None:
    """A list entry has to be a value, not another structure."""
    path = write_config(
        tmp_path,
        "activities:\n"
        "  - id: ACT-001\n"
        "    type: data_collection\n"
        "    collection_types:\n"
        "      - name: observations\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_rai_config(path)

    assert "activities[0].collection_types[0]" in str(excinfo.value)
