"""Inject RAI and PROV-O attributes into a Croissant JSON-LD metadata dict."""

from __future__ import annotations

from croissant_baker.rai.schema import Activity, RAIConfig

_PROV_NS = "http://www.w3.org/ns/prov#"

_ACTIVITY_LABELS = {
    "data_collection": "Data Collection",
    "data_annotation": "Data Annotation",
    "data_preprocessing": "Data Preprocessing",
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
    The prov: namespace is added to @context automatically when needed.

    Structure:
    - AI Safety and Fairness fields are direct rai: properties on the dataset.
    - Source datasets → prov:wasDerivedFrom.
    - Models that used this dataset → rai:usedBy.
    - Activities → prov:wasGeneratedBy (list of prov:Activity), each with
      optional prov:wasAssociatedWith (agents) and rai:usedPlatform (platforms).
    - Collection types → rai:dataCollectionType, on each activity that declares
      one and, unioned, on the dataset node the RAI spec puts the property on.
    """
    _ensure_prov_context(metadata, config)

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
        metadata["rai:hasSyntheticData"] = af.has_synthetic_data

    # rai:dataCollectionType is a dataset-level property, so the activities'
    # types are unioned onto the dataset as well as kept on their own nodes.
    collection_types = _unique(
        t for act in config.activities for t in act.collection_types
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
        metadata["rai:usedBy"] = [
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
    if act.collection_types:
        node["rai:dataCollectionType"] = _one_or_many(_unique(act.collection_types))

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
        node["rai:usedPlatform"] = _one_or_many(platform_nodes)

    return node


def _ensure_prov_context(metadata: dict, config: RAIConfig) -> None:
    """Add prov: namespace to @context if any PROV-O output will be injected."""
    needs_prov = bool(config.activities or config.lineage.source_datasets)
    if not needs_prov:
        return
    ctx = metadata.get("@context")
    if isinstance(ctx, dict) and "prov" not in ctx:
        ctx["prov"] = _PROV_NS
