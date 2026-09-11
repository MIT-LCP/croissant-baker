"""Load and validate a RAI config YAML file into a RAIConfig dataclass."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Optional

import yaml

from croissant_baker.rai.schema import (
    Activity,
    Agent,
    AIFairnessConfig,
    LineageConfig,
    ModelRef,
    Platform,
    RAIConfig,
    SourceDataset,
)


def _accepted(cls) -> frozenset[str]:
    """The YAML keys a level accepts: the field names of its dataclass."""
    return frozenset(f.name for f in dataclass_fields(cls))


_TOP_LEVEL_KEYS = _accepted(RAIConfig)
_FAIRNESS_KEYS = _accepted(AIFairnessConfig)
_LINEAGE_KEYS = _accepted(LineageConfig)
_SOURCE_DATASET_KEYS = _accepted(SourceDataset)
_MODEL_KEYS = _accepted(ModelRef)
_ACTIVITY_KEYS = _accepted(Activity)
_AGENT_KEYS = _accepted(Agent)
_PLATFORM_KEYS = _accepted(Platform)


def _str(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _mapping(value, path: str, file: Path) -> dict:
    """Return ``value`` as a mapping, or fail naming where one was expected."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            f"{file}: expected a mapping at {path or 'the top level'}, "
            f"found {type(value).__name__}."
        )
    return value


def _check_keys(mapping: dict, allowed: frozenset[str], path: str, file: Path) -> None:
    """Refuse a key this level does not read, rather than dropping it silently."""
    unknown = sorted(key for key in mapping if key not in allowed)
    if not unknown:
        return
    prefix = f"{path}." if path else ""
    listed = ", ".join(f"{prefix}{key}" for key in unknown)
    accepted = ", ".join(sorted(allowed))
    label = "key" if len(unknown) == 1 else "keys"
    raise ValueError(
        f"{file}: unknown {label} in the RAI config: {listed}. "
        f"Accepted keys at {path or 'the top level'}: {accepted}."
    )


def _entries(value, path: str, file: Path) -> list[dict]:
    """Return ``value`` as a list of mappings, one per numbered entry."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            f"{file}: expected a list at {path}, found {type(value).__name__}."
        )
    return [_mapping(entry, f"{path}[{i}]", file) for i, entry in enumerate(value)]


def _scalars(value, path: str, file: Path) -> list[str]:
    """Return ``value`` as a list of plain values, dropping the blank ones.

    A bare string is refused rather than iterated: its characters are never
    what the author meant, and a mapping's keys are not either.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            f"{file}: expected a list of values at {path}, "
            f"found {type(value).__name__}."
        )
    scalars = []
    for i, item in enumerate(value):
        if isinstance(item, (dict, list)):
            raise ValueError(
                f"{file}: expected a value at {path}[{i}], found {type(item).__name__}."
            )
        text = _str(item)
        if text:
            scalars.append(text)
    return scalars


def _load_yaml(path: Path):
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc


def load_rai_config(path: Path) -> RAIConfig:
    """Load a RAI YAML config file and return a RAIConfig instance."""
    raw = _mapping(_load_yaml(path), "", path)
    _check_keys(raw, _TOP_LEVEL_KEYS, "", path)

    # AI Safety and Fairness
    af_raw = _mapping(raw.get("ai_fairness"), "ai_fairness", path)
    _check_keys(af_raw, _FAIRNESS_KEYS, "ai_fairness", path)
    ai_fairness = AIFairnessConfig(
        data_limitations=_str(af_raw.get("data_limitations")),
        data_biases=_str(af_raw.get("data_biases")),
        personal_sensitive_information=_str(
            af_raw.get("personal_sensitive_information")
        ),
        data_use_cases=_str(af_raw.get("data_use_cases")),
        data_social_impact=_str(af_raw.get("data_social_impact")),
        has_synthetic_data=bool(af_raw["has_synthetic_data"])
        if "has_synthetic_data" in af_raw
        else None,
    )

    # Lineage
    ln_raw = _mapping(raw.get("lineage"), "lineage", path)
    _check_keys(ln_raw, _LINEAGE_KEYS, "lineage", path)

    source_datasets = []
    for i, s in enumerate(
        _entries(ln_raw.get("source_datasets"), "lineage.source_datasets", path)
    ):
        _check_keys(s, _SOURCE_DATASET_KEYS, f"lineage.source_datasets[{i}]", path)
        if not s.get("url"):
            continue
        source_datasets.append(
            SourceDataset(
                url=str(s.get("url", "")),
                id=_str(s.get("id")),
                name=_str(s.get("name")),
                organisation=_str(s.get("organisation")),
                license=_str(s.get("license")),
            )
        )

    models = []
    for i, m in enumerate(_entries(ln_raw.get("models"), "lineage.models", path)):
        _check_keys(m, _MODEL_KEYS, f"lineage.models[{i}]", path)
        if not m.get("url"):
            continue
        models.append(
            ModelRef(
                url=str(m.get("url", "")),
                id=_str(m.get("id")),
                name=_str(m.get("name")),
            )
        )

    lineage = LineageConfig(source_datasets=source_datasets, models=models)

    # Activities
    activities = []
    for act_index, act_raw in enumerate(
        _entries(raw.get("activities"), "activities", path)
    ):
        act_path = f"activities[{act_index}]"
        _check_keys(act_raw, _ACTIVITY_KEYS, act_path, path)

        agents = []
        for i, a in enumerate(
            _entries(act_raw.get("agents"), f"{act_path}.agents", path)
        ):
            _check_keys(a, _AGENT_KEYS, f"{act_path}.agents[{i}]", path)
            if not a.get("name"):
                continue
            agents.append(
                Agent(
                    name=str(a.get("name", "")),
                    url=_str(a.get("url")),
                    description=_str(a.get("description")),
                    is_synthetic=bool(a.get("is_synthetic", False)),
                )
            )

        platforms = []
        for i, p in enumerate(
            _entries(act_raw.get("platforms"), f"{act_path}.platforms", path)
        ):
            _check_keys(p, _PLATFORM_KEYS, f"{act_path}.platforms[{i}]", path)
            if not p.get("name"):
                continue
            platforms.append(
                Platform(
                    name=str(p.get("name", "")),
                    url=_str(p.get("url")),
                    description=_str(p.get("description")),
                )
            )

        collection_types = _scalars(
            act_raw.get("collection_types"), f"{act_path}.collection_types", path
        )

        activities.append(
            Activity(
                id=str(act_raw.get("id", "")),
                type=str(act_raw.get("type", "")),
                description=_str(act_raw.get("description")),
                start_at=_str(act_raw.get("start_at")),
                end_at=_str(act_raw.get("end_at")),
                collection_types=collection_types,
                agents=agents,
                platforms=platforms,
            )
        )

    return RAIConfig(
        ai_fairness=ai_fairness,
        lineage=lineage,
        activities=activities,
    )
