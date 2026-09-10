"""Loader tests for the RAI config YAML: the shipped example, and key checking."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from croissant_baker.rai import schema
from croissant_baker.rai.loader import load_rai_config
from croissant_baker.rai.schema import AIFairnessConfig

REPO_ROOT = Path(__file__).parent.parent
RAI_EXAMPLE = REPO_ROOT / "rai-example.yaml"


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rai.yaml"
    path.write_text(body, encoding="utf-8")
    return path


#: A schema dataclass the template is not required to fill in, and why.
EXEMPT_FROM_TEMPLATE = {
    "ModelRef": (
        "a models entry asserts that a third party used the dataset, which no "
        "template can know, so the template shows the fields in a comment"
    )
}


def _template_mappings(raw: dict) -> dict[str, list[dict]]:
    """Every mapping in the template, grouped by the dataclass that reads it.

    Read off the raw YAML rather than the loaded config, so a field counts as
    shown only when the template literally names the key. A loaded value cannot
    tell the two apart: ``is_synthetic`` defaults to False whether the author
    wrote ``false`` or wrote nothing.
    """
    lineage = raw.get("lineage") or {}
    activities = raw.get("activities") or []
    return {
        "RAIConfig": [raw],
        "AIFairnessConfig": [raw.get("ai_fairness") or {}],
        "LineageConfig": [lineage],
        "SourceDataset": lineage.get("source_datasets") or [],
        "ModelRef": lineage.get("models") or [],
        "Activity": activities,
        "Agent": [a for act in activities for a in act.get("agents") or []],
        "Platform": [p for act in activities for p in act.get("platforms") or []],
    }


def test_shipped_example_names_every_schema_field() -> None:
    """The template has to show every field the config can carry.

    ``--rai-config --help`` points users at ``rai-example.yaml``, so a field
    missing from it is a field they will never know they could have filled in,
    and a key it spells wrong is one silently dropped from their output.
    """
    raw = yaml.safe_load(RAI_EXAMPLE.read_text(encoding="utf-8"))
    mappings = _template_mappings(raw)

    declared = {
        obj.__name__: obj
        for obj in vars(schema).values()
        if dataclasses.is_dataclass(obj) and obj.__module__ == schema.__name__
    }
    assert sorted(mappings) == sorted(declared), (
        "a schema dataclass has nowhere to be read from in the template"
    )

    unnamed = sorted(
        f"{name}.{f.name}"
        for name, entries in mappings.items()
        if name not in EXEMPT_FROM_TEMPLATE
        for f in dataclasses.fields(declared[name])
        if not any(f.name in entry for entry in entries)
    )
    assert unnamed == []


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


def test_the_template_claims_no_model_used_the_dataset() -> None:
    """A models entry names a third party and asserts it used this dataset.

    Nothing in a template can know that, so the file shows the fields in a
    comment; a live entry would put a fabricated claim in every bake made
    from it.
    """
    config = load_rai_config(RAI_EXAMPLE)

    assert config.lineage.models == []
