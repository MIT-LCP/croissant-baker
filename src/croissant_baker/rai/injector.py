"""Inject RAI and PROV-O attributes into a Croissant JSON-LD metadata dict."""

from __future__ import annotations

import logging

from croissant_baker.rai.schema import Activity, RAIConfig

logger = logging.getLogger(__name__)

_PROV_NS = "http://www.w3.org/ns/prov#"

# Terms croissant-baker writes that RAI 1.0 does not define: hasSyntheticData,
# usedBy and usedPlatform. They get a prefix of their own so that rai: carries
# only the terms in https://github.com/mlcommons/croissant/blob/main/docs/croissant_rai.ttl
# and the RAI 1.0 conformsTo claim is not read as covering them.
_CB_PREFIX = "cb"
_CB_NS = "https://github.com/MIT-LCP/croissant-baker#"

# Where a document baked before the move holds each of the three terms.
_DATASET_OLD_TERMS = ("hasSyntheticData", "usedBy")
_ACTIVITY_OLD_TERMS = ("usedPlatform",)

_ACTIVITY_LABELS = {
    "data_collection": "Data Collection",
    "data_annotation": "Data Annotation",
    "data_preprocessing": "Data Preprocessing",
}

# The words the config accepts are ours, so each value is translated on the way
# out. Where the RAI 1.0 spec recommends a term for the same thing, that term is
# used. Where it recommends none, the value is written in title case: the range
# of the property is open text and the recommended list is advice, so an honest
# term beats a poor fit. The spec is the place to re-check the split:
# https://docs.mlcommons.org/croissant/docs/croissant-rai-spec.html
_COLLECTION_TYPE_TERMS = {
    "surveys": "Surveys",
    "experiments": "Experiments",
    "web_scraping": "Web Scraping",
    "existing_datasets": "Secondary Data analysis",
    "other": "Others",
    "observations": "Passive Data Collection",
    "interviews": "Interviews",
    "crowdsourcing": "Crowdsourcing",
    "simulations": "Simulations",
}


def _one_or_many(values: list):
    """Croissant writes a single-valued property as a scalar, not a list."""
    return values[0] if len(values) == 1 else values


def _unique(values) -> list:
    """The values in declaration order, first occurrence kept."""
    return list(dict.fromkeys(values))


def inject_rai(metadata: dict, config: RAIConfig) -> dict:
    """
    Inject RAI and PROV-O attributes into a Croissant metadata dict.

    Mutates and returns the dict. Fields that are None/empty are skipped.
    The prov: and cb: namespaces are added to @context when a term uses them.

    Structure:
    - AI Safety and Fairness fields are direct rai: properties on the dataset.
    - Source datasets → prov:wasDerivedFrom.
    - Whether the data holds synthetic content → cb:hasSyntheticData.
    - Models that used this dataset → cb:usedBy.
    - Activities → prov:wasGeneratedBy (list of prov:Activity), each with
      optional prov:wasAssociatedWith (agents) and cb:usedPlatform (platforms).
    - cb: terms are croissant-baker extensions, not part of RAI 1.0. A
      document written before they moved holds them under rai:; those keys
      are moved to cb:, where a value the config sets replaces them.
    - Collection types → rai:dataCollectionType on the dataset node, unioned
      across the activities and written with the terms RAI 1.0 recommends.
      RAI 1.0 declares the property on sc:Dataset, so it does not go on the
      prov:Activity that carries the types in the config.
    """
    _ensure_prov_context(metadata, config)
    _move_old_terms(metadata)

    # AI Safety and Fairness
    af = config.ai_fairness
    if af.data_limitations:
        metadata["rai:dataLimitations"] = af.data_limitations
    if af.data_biases:
        metadata["rai:dataBiases"] = af.data_biases
    if af.personal_sensitive_information:
        metadata["rai:personalSensitiveInformation"] = af.personal_sensitive_information
    if af.data_use_cases:
        metadata["rai:dataUseCases"] = af.data_use_cases
    if af.data_social_impact:
        metadata["rai:dataSocialImpact"] = af.data_social_impact
    if af.has_synthetic_data is not None:
        metadata["cb:hasSyntheticData"] = af.has_synthetic_data

    # rai:dataCollectionType is declared on sc:Dataset, so the types every
    # activity declares are unioned onto the dataset rather than left on it.
    # Case and padding are not part of what the author meant, so they are
    # ignored when a value is looked up. A value the table does not list is
    # written exactly as given, because the range of the property is open text.
    # Duplicates are dropped after the lookup, so a value and the term it stands
    # for count as one value.
    collection_types = _unique(
        _COLLECTION_TYPE_TERMS.get(t.strip().lower(), t)
        for act in config.activities
        for t in act.collection_types
    )
    if collection_types:
        metadata["rai:dataCollectionType"] = _one_or_many(collection_types)

    # Lineage — source datasets
    if config.lineage.source_datasets:
        metadata["prov:wasDerivedFrom"] = [
            _build_source_dataset(s) for s in config.lineage.source_datasets
        ]

    # Lineage — models that used this dataset
    if config.lineage.models:
        metadata["cb:usedBy"] = [
            {
                k: v
                for k, v in {
                    "url": m.url,
                    "id": m.id,
                    "name": m.name,
                }.items()
                if v
            }
            for m in config.lineage.models
        ]

    # Activities
    activities = [_build_activity(act) for act in config.activities]
    if activities:
        metadata["prov:wasGeneratedBy"] = _one_or_many(activities)

    _ensure_cb_context(metadata)
    return metadata


def _build_source_dataset(s) -> dict:
    node: dict = {
        k: v
        for k, v in {
            "url": s.url,
            "id": s.id,
            "name": s.name,
            "license": s.license,
        }.items()
        if v
    }
    if s.organisation:
        node["prov:wasAssociatedWith"] = {
            "@type": "prov:Organization",
            "name": s.organisation,
        }
    return node


def _build_activity(act: Activity) -> dict:
    label = _ACTIVITY_LABELS.get(act.type, act.type)
    node: dict = {
        "@type": "prov:Activity",
        "@id": act.id,
        "prov:label": label,
        "prov:type": label,
    }

    if act.description:
        node["prov:description"] = act.description
    if act.start_at:
        node["prov:startedAtTime"] = act.start_at
    if act.end_at:
        node["prov:endedAtTime"] = act.end_at

    if act.agents:
        agent_nodes = []
        for a in act.agents:
            agent_type = "prov:SoftwareAgent" if a.is_synthetic else "prov:Agent"
            agent: dict = {"@type": agent_type, "name": a.name}
            if a.url:
                agent["url"] = a.url
            if a.description:
                agent["prov:description"] = a.description
            agent_nodes.append(agent)
        node["prov:wasAssociatedWith"] = _one_or_many(agent_nodes)

    if act.platforms:
        platform_nodes = []
        for p in act.platforms:
            plat: dict = {"name": p.name}
            if p.url:
                plat["url"] = p.url
            if p.description:
                plat["prov:description"] = p.description
            platform_nodes.append(plat)
        node["cb:usedPlatform"] = _one_or_many(platform_nodes)

    return node


def _ensure_prov_context(metadata: dict, config: RAIConfig) -> None:
    """Add prov: namespace to @context if any PROV-O output will be injected."""
    needs_prov = bool(config.activities or config.lineage.source_datasets)
    if not needs_prov:
        return
    ctx = metadata.get("@context")
    if isinstance(ctx, dict) and "prov" not in ctx:
        ctx["prov"] = _PROV_NS


def _move_old_terms(metadata: dict) -> None:
    """Move rai:hasSyntheticData, rai:usedBy and rai:usedPlatform to cb:.

    Runs before the config is written, so a value the config sets replaces the
    old one and a value it leaves empty is kept under cb:. A cb: value already
    in the document is newer than the rai: one, so it is kept.
    """
    moves = [(metadata, _DATASET_OLD_TERMS)]
    moves += [(act, _ACTIVITY_OLD_TERMS) for act in _activity_nodes(metadata)]
    for node, terms in moves:
        for term in terms:
            if f"rai:{term}" in node:
                node.setdefault(f"cb:{term}", node.pop(f"rai:{term}"))


def _activity_nodes(metadata: dict) -> list[dict]:
    """The prov:Activity nodes on the dataset, whether one or a list."""
    activities = metadata.get("prov:wasGeneratedBy")
    if isinstance(activities, dict):
        return [activities]
    if isinstance(activities, list):
        return [act for act in activities if isinstance(act, dict)]
    return []


def _ensure_cb_context(metadata: dict) -> None:
    """Add the cb: namespace to @context if the output carries a cb: term.

    The output is read rather than the config, so the check cannot drift from
    what was written, and a cb: term that was already in the document counts.
    A cb prefix the document already binds elsewhere is left as it is.
    """
    nodes = [metadata, *_activity_nodes(metadata)]
    if not any(key.startswith("cb:") for node in nodes for key in node):
        return
    ctx = metadata.get("@context")
    if not isinstance(ctx, dict):
        return
    bound = ctx.setdefault(_CB_PREFIX, _CB_NS)
    if bound != _CB_NS:
        logger.warning(
            "@context already binds %r to %r, so the cb: terms are left under "
            "that IRI rather than %r",
            _CB_PREFIX,
            bound,
            _CB_NS,
        )
