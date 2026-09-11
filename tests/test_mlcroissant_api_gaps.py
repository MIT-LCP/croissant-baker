"""Tripwire for mlcroissant API gaps that we patch via post-hoc dict edits.

When mlcroissant gains a native parameter (or fixes a context alias) for any
field listed below, the corresponding assertion fails. That is the signal to
delete the post-hoc inject in ``metadata_generator.py`` and switch to the
native API.

Bump the upper pin in ``pyproject.toml`` deliberately when 1.2.x ships and
reconcile this list against the new release.
"""

import inspect

import mlcroissant as mlc


# Fields MetadataGenerator post-hoc-injects because mlcroissant 1.1.0 exposes
# no Python parameter for them. Two kinds sit in one set: fields the Croissant
# 1.1 spec itself defines (``alternate_name``, ``is_live_dataset``,
# ``temporal_coverage``, ``usage_info``) and plain schema.org properties the
# document reaches through the @vocab in its @context (``identifier``,
# ``conditions_of_access``, ``is_accessible_for_free``,
# ``included_in_data_catalog``). One set rather than two because the action
# when the assertion fires is the same for both: switch that field to the
# native parameter and delete its inject. The kinds differ only in how likely
# mlcroissant is to close the gap.
_METADATA_GAPS_AS_OF_MLC_1_1_0 = {
    "alternate_name",
    "is_live_dataset",
    "temporal_coverage",
    "usage_info",
    "identifier",
    "conditions_of_access",
    "is_accessible_for_free",
    "included_in_data_catalog",
}


# Field-level gaps. ``equivalent_property`` lives on cr:Field per the spec but
# mlcroissant 1.1.0 has no constructor parameter for it.
_FIELD_GAPS_AS_OF_MLC_1_1_0 = {
    "equivalent_property",
}


def test_metadata_post_hoc_fields_still_lack_native_params() -> None:
    sig = inspect.signature(mlc.Metadata.__init__).parameters
    closed_gaps = {name for name in _METADATA_GAPS_AS_OF_MLC_1_1_0 if name in sig}
    assert not closed_gaps, (
        f"mlcroissant Metadata gained native support for {closed_gaps}. "
        "Remove the matching post-hoc inject in MetadataGenerator and "
        "drop the entry from _METADATA_GAPS_AS_OF_MLC_1_1_0."
    )


def test_field_post_hoc_props_still_lack_native_params() -> None:
    sig = inspect.signature(mlc.Field.__init__).parameters
    closed_gaps = {name for name in _FIELD_GAPS_AS_OF_MLC_1_1_0 if name in sig}
    assert not closed_gaps, (
        f"mlcroissant Field gained native support for {closed_gaps}. "
        "Switch _apply_field_mappings to the native API."
    )


def test_sd_version_native_param_still_emits_prefixed_key() -> None:
    """sd_version is a native Metadata param, but mlcroissant emits it as
    ``cr:sdVersion`` because the @context lacks an alias for ``sdVersion``.
    Canonical 1.1 examples use the unprefixed key, so we post-hoc inject.
    When mlcroissant adds the alias, this test fails — switch to native.
    """
    md = mlc.Metadata(
        name="t",
        description="d",
        url="https://example.com",
        license="mit",
        conforms_to="http://mlcommons.org/croissant/1.1",
        sd_version="1.0.0",
    )
    out = md.to_json()
    assert "sdVersion" not in out, (
        "mlcroissant now emits the unprefixed sdVersion key. "
        "Drop the post-hoc inject in MetadataGenerator.generate_metadata."
    )
    assert "cr:sdVersion" in out


def test_metadata_id_param_still_emits_no_top_level_id() -> None:
    """``id`` is a native Metadata param, but ``to_json()`` writes no ``@id``
    for the Dataset itself, so the document has no subject to name it by.
    Profiles that require one — Bioschemas Dataset lists @id among its minimum
    fields — need the key, so we post-hoc inject the dataset URL. When
    mlcroissant starts emitting it, this test fails: drop the inject in
    MetadataGenerator.generate_metadata.
    """
    md = mlc.Metadata(
        id="https://example.com/dataset",
        name="t",
        description="d",
        url="https://example.com/dataset",
        license="mit",
        conforms_to="http://mlcommons.org/croissant/1.1",
    )
    out = md.to_json()
    assert "@id" not in out, (
        "mlcroissant now emits a top-level @id. "
        "Drop the post-hoc inject in MetadataGenerator.generate_metadata."
    )


def test_rai_conforms_to_not_auto_appended_when_rai_fields_set() -> None:
    """The Croissant 1.1 spec defines ``http://mlcommons.org/croissant/RAI/1.0``
    as the conformsTo URI for the RAI extension, but mlcroissant 1.1.0 does
    NOT append it when RAI fields like ``data_biases`` are populated.
    croissant-baker patches this via ``_ensure_rai_conforms_to``. When mlc
    starts auto-appending it, this test fails — drop the helper.
    """
    md = mlc.Metadata(
        name="t",
        description="d",
        url="https://example.com",
        license="mit",
        conforms_to="http://mlcommons.org/croissant/1.1",
        data_biases=["single-site cohort"],
        data_use_cases=["benchmarking"],
    )
    out = md.to_json()
    rai_uri = "http://mlcommons.org/croissant/RAI/1.0"
    ct = out.get("conformsTo")
    flat = ct if isinstance(ct, list) else [ct]
    assert rai_uri not in flat, (
        "mlcroissant now auto-appends the RAI conformsTo URI. "
        "Drop _ensure_rai_conforms_to in croissant_baker/__main__.py."
    )
