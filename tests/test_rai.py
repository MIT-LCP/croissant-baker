"""Integration test for the RAI metadata extension."""

import json
import logging
import re
from pathlib import Path

import mlcroissant as mlc
import pytest
from click.testing import Result
from typer.testing import CliRunner

from croissant_baker.__main__ import app
from croissant_baker.rai import inject_rai
from croissant_baker.rai.injector import _COLLECTION_TYPE_TERMS
from croissant_baker.__main__ import _ensure_rai_conforms_to
from croissant_baker.metadata_generator import (
    CROISSANT_CONFORMS_TO,
    RAI_1_0_TERMS,
    RAI_CONFORMS_TO,
)
from croissant_baker.rai.schema import (
    Activity,
    AIFairnessConfig,
    LineageConfig,
    ModelRef,
    Platform,
    RAIConfig,
    SourceDataset,
)
from tests.helpers import cli
from tests.test_end_to_end import _discovery_independent

runner = CliRunner()

_REPO_ROOT = Path(__file__).parent.parent
RAI_EXAMPLE = _REPO_ROOT / "rai-example.yaml"
RAI_DOC = _REPO_ROOT / "docs" / "user-guide" / "rai.md"

_DATA = Path(__file__).parent / "data"
RAI_YAML = (
    _DATA / "input" / "mimiciv_demo" / "physionet.org" / "mimiciv_demo-rai-example.yaml"
)
EXPECTED = _DATA / "output" / "mimiciv_demo_croissant_rai.jsonld"
MIMICIV_PATH = (
    _DATA
    / "input"
    / "mimiciv_demo"
    / "physionet.org"
    / "files"
    / "mimic-iv-demo"
    / "2.2"
)

_RAI_PROV_KEYS = [
    "prov:wasGeneratedBy",
    "rai:dataLimitations",
    "rai:dataBiases",
    "rai:personalSensitiveInformation",
    "rai:dataUseCases",
    "rai:dataSocialImpact",
    "rai:dataCollectionType",
    "prov:wasDerivedFrom",
]


@pytest.fixture
def mimiciv_demo_path() -> Path:
    if not MIMICIV_PATH.exists():
        pytest.skip(f"MIMIC-IV demo dataset not found at {MIMICIV_PATH}")
    return MIMICIV_PATH


def _bake_with_rai(dataset: Path, output: Path) -> Result:
    """Bake the MIMIC-IV demo with the fixture RAI config, as the golden was."""
    return runner.invoke(
        app,
        [
            "-i",
            str(dataset),
            "-o",
            str(output),
            "--name",
            "MIMIC-IV Demo Dataset",
            "--description",
            "Demo subset of MIMIC-IV, a freely accessible electronic health record dataset from Beth Israel Deaconess Medical Center (2008-2019)",
            "--url",
            "https://physionet.org/content/mimic-iv-demo/",
            "--license",
            "PhysioNet Restricted Health Data License 1.5.0",
            "--dataset-version",
            "2.2",
            "--date-published",
            "2023-01-06",
            "--creator",
            "Alistair Johnson,aewj@mit.edu,https://physionet.org/",
            "--creator",
            "Lucas Bulgarelli,,https://mit.edu/",
            "--creator",
            "Tom Pollard,tpollard@mit.edu,https://physionet.org/",
            "--creator",
            "Steven Horng,,https://www.bidmc.org/",
            "--creator",
            "Leo Anthony Celi,lceli@mit.edu,https://lcp.mit.edu/",
            "--creator",
            "Roger Mark,,https://lcp.mit.edu/",
            "--citation",
            "Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023). MIMIC-IV (version 2.2). PhysioNet. https://doi.org/10.13026/6mm1-ek67",
            "--no-validate",
            "--rai-config",
            str(RAI_YAML),
        ],
    )


def test_rai_generation_matches_reference(
    mimiciv_demo_path: Path, tmp_path: Path
) -> None:
    output = tmp_path / "output.jsonld"
    result = _bake_with_rai(mimiciv_demo_path, output)

    assert result.exit_code == 0, result.output

    generated = json.loads(output.read_text())
    expected = json.loads(EXPECTED.read_text())

    for key in _RAI_PROV_KEYS:
        assert generated.get(key) == expected.get(key), f"Mismatch for {key}"


@pytest.mark.parametrize("reverse_discovery", [False, True])
def test_rai_generation_matches_the_whole_reference(
    mimiciv_demo_path: Path, tmp_path: Path, monkeypatch, reverse_discovery: bool
) -> None:
    """The whole document is compared, so the golden cannot drift from the bake.

    Compared through :func:`_discovery_independent` and run in both discovery
    orders, since ``rglob`` order depends on the filesystem. To regenerate, bake with the arguments of
    :func:`_bake_with_rai` and write to ``EXPECTED``.
    """
    if reverse_discovery:
        from croissant_baker import scan

        discover = scan.discover_files
        monkeypatch.setattr(
            scan,
            "discover_files",
            lambda *args, **kwargs: list(reversed(discover(*args, **kwargs))),
        )
    output = tmp_path / "output.jsonld"
    result = _bake_with_rai(mimiciv_demo_path, output)

    assert result.exit_code == 0, result.output
    assert _discovery_independent(
        json.loads(output.read_text())
    ) == _discovery_independent(json.loads(EXPECTED.read_text()))


BAD_RAI_YAML = "ai_fairness:\n  social_impact: It enables research.\n"


def _bad_config(tmp_path: Path) -> Path:
    path = tmp_path / "rai.yaml"
    path.write_text(BAD_RAI_YAML, encoding="utf-8")
    return path


def test_generate_reports_a_bad_rai_config_without_a_traceback(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.csv").write_text("id,name\n1,Ada\n", encoding="utf-8")

    result = cli(
        dataset,
        tmp_path / "out.jsonld",
        "--rai-config",
        str(_bad_config(tmp_path)),
    )

    assert result.exit_code == 1
    assert "ai_fairness.social_impact" in result.stderr
    assert "data_social_impact" in result.stderr
    assert "Traceback" not in result.stderr + result.output
    assert not isinstance(result.exception, ValueError)


def test_rai_apply_reports_a_bad_rai_config_without_a_traceback(tmp_path: Path) -> None:
    document = tmp_path / "croissant.jsonld"
    document.write_text(json.dumps({"@context": {}, "name": "test"}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(document),
            "--rai-config",
            str(_bad_config(tmp_path)),
            "--no-validate",
        ],
    )

    assert result.exit_code == 1
    assert "ai_fairness.social_impact" in result.stderr
    assert "data_social_impact" in result.stderr
    assert "Traceback" not in result.stderr + result.output
    assert not isinstance(result.exception, ValueError)


def test_a_bad_rai_config_is_reported_before_the_dataset_is_scanned(
    tmp_path: Path,
) -> None:
    """The config is an input, not a result: checking it after a bake is wasted work."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.csv").write_text("id,name\n1,Ada\n", encoding="utf-8")

    result = cli(
        dataset,
        tmp_path / "out.jsonld",
        "--rai-config",
        str(_bad_config(tmp_path)),
    )

    assert result.exit_code == 1
    assert "Scanned" not in result.output


def test_an_intended_exit_is_not_reported_as_an_unexpected_error(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.csv").write_text("id,name\n1,Ada\n", encoding="utf-8")

    result = cli(
        dataset,
        tmp_path / "out.jsonld",
        "--rai-config",
        str(_bad_config(tmp_path)),
        "--rai-data-biases",
        "Single site",
    )

    assert result.exit_code == 1
    assert "cannot be combined with --rai-config" in result.stderr
    assert "Unexpected error" not in result.stderr


def _config(*activities: Activity) -> RAIConfig:
    return RAIConfig(activities=list(activities))


def _activity(identifier: str, *collection_types: str) -> Activity:
    return Activity(
        id=identifier,
        type="data_collection",
        collection_types=list(collection_types),
    )


def test_collection_types_reach_the_dataset_node() -> None:
    """rai:dataCollectionType is a dataset-level property in RAI 1.0."""
    config = _config(
        _activity("ACT-001", "observations", "existing_datasets"),
        _activity("ACT-002", "existing_datasets", "surveys"),
    )

    document = inject_rai({"@context": {}}, config)

    assert document["rai:dataCollectionType"] == [
        "Passive Data Collection",
        "Secondary Data analysis",
        "Surveys",
    ]


def test_a_single_collection_type_is_written_as_a_string() -> None:
    document = inject_rai({"@context": {}}, _config(_activity("ACT-001", "surveys")))

    assert document["rai:dataCollectionType"] == "Surveys"


@pytest.mark.parametrize(
    ("value", "term"),
    [
        ("surveys", "Surveys"),
        ("interviews", "Interviews"),
        ("observations", "Passive Data Collection"),
        ("experiments", "Experiments"),
        ("web_scraping", "Web Scraping"),
        ("crowdsourcing", "Crowdsourcing"),
        ("existing_datasets", "Secondary Data analysis"),
        ("simulations", "Simulations"),
        ("other", "Others"),
    ],
)
def test_each_config_value_is_written_as_its_published_term(
    value: str, term: str
) -> None:
    """The words the config accepts are ours, so the output has to read as RAI."""
    document = inject_rai({"@context": {}}, _config(_activity("ACT-001", value)))

    assert document["rai:dataCollectionType"] == term


@pytest.mark.parametrize(
    ("written", "term"),
    [
        ("SURVEYS", "Surveys"),
        ("Web_Scraping", "Web Scraping"),
        (" observations ", "Passive Data Collection"),
    ],
)
def test_a_value_is_recognised_whatever_its_case_and_padding(
    written: str, term: str
) -> None:
    """A user who shouts or capitalises still means the same collection type."""
    document = inject_rai({"@context": {}}, _config(_activity("ACT-001", written)))

    assert document["rai:dataCollectionType"] == term


def test_an_unknown_collection_type_is_written_as_given() -> None:
    """The range is open text, so a term of the user's own has to survive."""
    document = inject_rai(
        {"@context": {}}, _config(_activity("ACT-001", "Chart Review"))
    )

    assert document["rai:dataCollectionType"] == "Chart Review"


def test_a_published_term_written_in_the_config_is_left_alone() -> None:
    document = inject_rai(
        {"@context": {}}, _config(_activity("ACT-001", "Web Scraping"))
    )

    assert document["rai:dataCollectionType"] == "Web Scraping"


def test_a_value_and_its_term_collapse_to_one_value() -> None:
    """Two spellings of one collection type are still one collection type."""
    config = _config(
        _activity("ACT-001", "web_scraping"),
        _activity("ACT-002", "Web Scraping"),
    )

    document = inject_rai({"@context": {}}, config)

    assert document["rai:dataCollectionType"] == "Web Scraping"


def test_no_collection_types_writes_no_key() -> None:
    document = inject_rai({"@context": {}}, _config(_activity("ACT-001")))

    assert "rai:dataCollectionType" not in document


def test_no_activity_node_carries_collection_types() -> None:
    """RAI 1.0 declares the property on sc:Dataset, so a prov:Activity is
    outside its domain and must not carry a copy."""
    config = _config(
        _activity("ACT-001", "observations", "existing_datasets"),
        _activity("ACT-002"),
    )

    activities = inject_rai({"@context": {}}, config)["prov:wasGeneratedBy"]

    assert all("rai:dataCollectionType" not in act for act in activities)


def test_the_reference_output_records_the_fixture_collection_types() -> None:
    expected = json.loads(EXPECTED.read_text())

    assert expected["rai:dataCollectionType"] == [
        "Passive Data Collection",
        "Secondary Data analysis",
    ]


#: A row of the template's comment block, such as ``#   surveys → Surveys``.
_TEMPLATE_ROW = re.compile(r"^#\s+(\S+)\s+→\s+(\S.*?)\s*$")

#: A row of the user guide's table, such as ``| `surveys` | `Surveys` |``.
_DOC_ROW = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \|$")

_DOC_TABLE_HEADER = "| You write | The output holds |"


def _template_pairs() -> list[tuple[str, str]]:
    """The pairs in the collection_types block of the shipped template.

    Read off the comment text rather than the YAML, because a comment is all
    the template can say about a value it does not itself write.
    """
    lines = RAI_EXAMPLE.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("# collection_types")
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "#")
    matches = (_TEMPLATE_ROW.match(line) for line in lines[start:end])
    return [(m.group(1), m.group(2)) for m in matches if m]


def _doc_pairs() -> list[tuple[str, str]]:
    """The rows of the collection type table in the user guide."""
    lines = RAI_DOC.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[lines.index(_DOC_TABLE_HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return [(m.group(1), m.group(2)) for m in map(_DOC_ROW.match, rows) if m]


def test_the_terms_are_written_down_the_same_in_every_place() -> None:
    """The table is copied into the template and the user guide.

    A user reads whichever of the three they open first, so a copy that drifts
    tells them to write a value the output will not recognise.
    """
    template = _template_pairs()
    doc = _doc_pairs()

    assert dict(template) == _COLLECTION_TYPE_TERMS
    assert dict(doc) == _COLLECTION_TYPE_TERMS
    assert len(template) == len(doc) == len(_COLLECTION_TYPE_TERMS)


def test_a_dry_run_checks_the_rai_config_too(tmp_path: Path) -> None:
    """A dry run is how a user checks a command before committing to it."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.csv").write_text("id,name\n1,Ada\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--input",
            str(dataset),
            "--dry-run",
            "--rai-config",
            str(_bad_config(tmp_path)),
        ],
    )

    assert result.exit_code == 1
    assert "ai_fairness.social_impact" in result.stderr
    assert "would be processed" not in result.output


CB_NAMESPACE = "https://github.com/MIT-LCP/croissant-baker#"


def _keys(node) -> set[str]:
    """Every key in a JSON document, at any depth."""
    if isinstance(node, dict):
        return set(node).union(*(_keys(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_keys(value) for value in node))
    return set()


def _rai_keys(node) -> set[str]:
    return {key for key in _keys(node) if key.startswith("rai:")}


def _full_config() -> RAIConfig:
    """A config that fills in every field the injector writes."""
    return RAIConfig(
        ai_fairness=AIFairnessConfig(
            data_limitations="Single site.",
            data_biases="Adults only.",
            personal_sensitive_information="De-identified.",
            data_use_cases="Benchmarking.",
            data_social_impact="May improve triage research.",
            has_synthetic_data=True,
        ),
        lineage=LineageConfig(
            source_datasets=[SourceDataset(url="https://example.org/source")],
            models=[ModelRef(url="https://example.org/model", name="A model")],
        ),
        activities=[
            Activity(
                id="ACT-001",
                type="data_collection",
                collection_types=["surveys"],
                platforms=[Platform(name="A platform", url="https://example.org")],
            )
        ],
    )


def _extensions_only_config() -> RAIConfig:
    """A config whose output carries no RAI 1.0 term at all."""
    return RAIConfig(
        ai_fairness=AIFairnessConfig(has_synthetic_data=False),
        lineage=LineageConfig(models=[ModelRef(url="https://example.org/model")]),
    )


def test_every_rai_key_the_injector_writes_is_a_rai_1_0_term() -> None:
    """The rai: prefix is a claim that the term is in the RAI 1.0 vocabulary."""
    document = inject_rai({"@context": {}}, _full_config())

    assert _rai_keys(document) <= RAI_1_0_TERMS


def test_has_synthetic_data_is_written_as_a_croissant_baker_term() -> None:
    document = inject_rai({"@context": {}}, _full_config())

    assert document["cb:hasSyntheticData"] is True


def test_models_are_written_as_a_croissant_baker_term() -> None:
    document = inject_rai({"@context": {}}, _full_config())

    assert document["cb:usedBy"] == [
        {"url": "https://example.org/model", "name": "A model"}
    ]


def test_platforms_are_written_as_a_croissant_baker_term() -> None:
    document = inject_rai({"@context": {}}, _full_config())

    assert document["prov:wasGeneratedBy"]["cb:usedPlatform"] == {
        "name": "A platform",
        "url": "https://example.org",
    }


def test_the_croissant_baker_prefix_is_declared_when_a_term_uses_it() -> None:
    document = inject_rai({"@context": {}}, _extensions_only_config())

    assert document["@context"]["cb"] == CB_NAMESPACE


def test_the_croissant_baker_prefix_is_declared_for_a_platform_alone() -> None:
    """cb:usedPlatform sits on an activity node, not on the dataset node."""
    config = RAIConfig(
        activities=[
            Activity(id="ACT-001", type="data_collection", platforms=[Platform("P")])
        ]
    )

    document = inject_rai({"@context": {}}, config)

    assert document["@context"]["cb"] == CB_NAMESPACE


def test_the_croissant_baker_prefix_follows_the_output_not_the_config() -> None:
    """A cb: term already in the document needs the prefix as much as a new one."""
    document = inject_rai({"@context": {}, "cb:hasSyntheticData": True}, RAIConfig())

    assert document["@context"]["cb"] == CB_NAMESPACE


def test_a_cb_prefix_bound_elsewhere_is_left_alone_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rebinding a prefix the author chose would change what their terms mean."""
    other = "https://example.org/other#"

    with caplog.at_level(logging.WARNING, logger="croissant_baker.rai.injector"):
        document = inject_rai({"@context": {"cb": other}}, _extensions_only_config())

    assert document["@context"]["cb"] == other
    assert any(
        other in record.getMessage() and CB_NAMESPACE in record.getMessage()
        for record in caplog.records
    )


def test_the_croissant_baker_prefix_is_not_declared_when_no_term_uses_it() -> None:
    config = RAIConfig(ai_fairness=AIFairnessConfig(data_biases="Adults only."))

    document = inject_rai({"@context": {}}, config)

    assert "cb" not in document["@context"]


def test_the_reference_output_carries_only_rai_1_0_terms_under_rai() -> None:
    expected = json.loads(EXPECTED.read_text())

    assert _rai_keys(expected) <= RAI_1_0_TERMS


def test_the_reference_output_writes_synthetic_data_under_cb() -> None:
    expected = json.loads(EXPECTED.read_text())

    assert expected["cb:hasSyntheticData"] is False
    assert expected["@context"]["cb"] == CB_NAMESPACE


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rai.yaml"
    path.write_text(text, encoding="utf-8")
    return path


_FULL_YAML = """
ai_fairness:
  data_biases: Adults only.
  has_synthetic_data: true
lineage:
  models:
    - url: https://example.org/model
      name: A model
activities:
  - id: ACT-001
    type: data_collection
    platforms:
      - name: A platform
        url: https://example.org
""".strip()


def _bake_with_extensions(tmp_path: Path) -> Path:
    """Bake a one-file dataset with every cb: term, validated by mlcroissant."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.csv").write_text("id,name\n1,Ada\n", encoding="utf-8")
    output = tmp_path / "out.jsonld"

    result = cli(
        dataset,
        output,
        "--rai-config",
        str(_write_config(tmp_path, _FULL_YAML)),
        validate=True,
    )

    assert result.exit_code == 0, result.output
    return output


def test_a_bake_with_the_extension_terms_passes_mlcroissant(tmp_path: Path) -> None:
    """mlcroissant reads the whole document back, the cb: terms included."""
    document = json.loads(_bake_with_extensions(tmp_path).read_text())

    assert {"cb:hasSyntheticData", "cb:usedBy"} <= set(document)
    assert "cb:usedPlatform" in document["prov:wasGeneratedBy"]
    assert document["@context"]["cb"] == CB_NAMESPACE


def test_the_extension_terms_survive_a_round_trip_through_mlcroissant(
    tmp_path: Path,
) -> None:
    """A tool that loads the file with mlcroissant and writes it out keeps them."""
    document = mlc.Dataset(_bake_with_extensions(tmp_path)).metadata.to_json()

    assert document["@context"]["cb"] == CB_NAMESPACE
    assert document["cb:hasSyntheticData"] is True
    # JSON-LD writes a one-item list as the item, so either shape is the same.
    assert document["cb:usedBy"] in (
        {"url": "https://example.org/model", "name": "A model"},
        [{"url": "https://example.org/model", "name": "A model"}],
    )
    assert document["prov:wasGeneratedBy"]["cb:usedPlatform"] == {
        "name": "A platform",
        "url": "https://example.org",
    }


_EXTENSIONS_ONLY_YAML = """
ai_fairness:
  has_synthetic_data: false
lineage:
  models:
    - url: https://example.org/model
""".strip()


def test_extension_terms_alone_do_not_claim_rai_conformance(tmp_path: Path) -> None:
    """RAI 1.0 is claimed for the rai: terms, and none of these is one."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.csv").write_text("id,name\n1,Ada\n", encoding="utf-8")
    output = tmp_path / "out.jsonld"

    result = cli(
        dataset,
        output,
        "--rai-config",
        str(_write_config(tmp_path, _EXTENSIONS_ONLY_YAML)),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["conformsTo"] == CROISSANT_CONFORMS_TO


def test_rai_apply_with_extension_terms_alone_does_not_claim_rai_conformance(
    tmp_path: Path,
) -> None:
    document = tmp_path / "croissant.jsonld"
    document.write_text(
        json.dumps(
            {"@context": {}, "name": "test", "conformsTo": ["http://example.org/x"]}
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(document),
            "--rai-config",
            str(_write_config(tmp_path, _EXTENSIONS_ONLY_YAML)),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(document.read_text())["conformsTo"] == ["http://example.org/x"]


def test_rai_apply_with_a_rai_term_claims_rai_conformance(tmp_path: Path) -> None:
    document = tmp_path / "croissant.jsonld"
    document.write_text(json.dumps({"@context": {}, "name": "test"}), encoding="utf-8")
    config = "ai_fairness:\n  data_biases: Adults only.\n"

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(document),
            "--rai-config",
            str(_write_config(tmp_path, config)),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(document.read_text())["conformsTo"] == [RAI_CONFORMS_TO]


def test_the_rai_term_list_holds_the_twenty_terms_of_the_ttl() -> None:
    """Checked by hand against croissant_rai.ttl, which lists twenty terms."""
    assert len(RAI_1_0_TERMS) == 20
    assert all(term.startswith("rai:") for term in RAI_1_0_TERMS)
    assert "rai:hasSyntheticData" not in RAI_1_0_TERMS


@pytest.mark.parametrize(
    "key", ["rai:hasSyntheticData", "rai:usedBy", "rai:somethingElse"]
)
def test_a_rai_key_outside_rai_1_0_does_not_claim_rai_conformance(key: str) -> None:
    """A document written before the cb: move still carries the old keys."""
    document = {"conformsTo": CROISSANT_CONFORMS_TO, key: False}

    _ensure_rai_conforms_to(document)

    assert document["conformsTo"] == CROISSANT_CONFORMS_TO


@pytest.mark.parametrize(
    "key", ["rai:dataCollectionTimeFrame", "rai:dataDataManipulationProtocol"]
)
def test_the_mlcroissant_spelling_of_a_rai_term_claims_rai_conformance(
    key: str,
) -> None:
    """mlcroissant writes these two RAI 1.0 terms with its own spelling.

    The ttl has dataCollectionTimeframe and dataManipulationProtocol, but a
    native --rai-* flag reaches the output through mlcroissant, so the claim
    has to follow what mlcroissant writes.
    """
    document = {"conformsTo": CROISSANT_CONFORMS_TO, key: "x"}

    _ensure_rai_conforms_to(document)

    assert document["conformsTo"] == [CROISSANT_CONFORMS_TO, RAI_CONFORMS_TO]


def _old_document() -> dict:
    """A document baked before the three terms moved from rai: to cb:."""
    return {
        "@context": {"prov": "http://www.w3.org/ns/prov#"},
        "name": "test",
        "conformsTo": CROISSANT_CONFORMS_TO,
        "rai:hasSyntheticData": False,
        "rai:usedBy": [{"url": "https://example.org/old-model"}],
        "prov:wasGeneratedBy": {
            "@type": "prov:Activity",
            "@id": "ACT-001",
            "rai:usedPlatform": {"name": "Old platform"},
        },
    }


def test_an_old_document_keeps_no_rai_extension_key() -> None:
    document = inject_rai(_old_document(), RAIConfig())

    assert _rai_keys(document) == set()


def test_an_old_value_is_carried_to_cb_when_the_config_leaves_it_empty() -> None:
    document = inject_rai(_old_document(), RAIConfig())

    assert document["cb:hasSyntheticData"] is False
    assert document["cb:usedBy"] == [{"url": "https://example.org/old-model"}]
    assert document["prov:wasGeneratedBy"]["cb:usedPlatform"] == {
        "name": "Old platform"
    }
    assert document["@context"]["cb"] == CB_NAMESPACE


def test_the_config_value_wins_over_an_old_value() -> None:
    config = RAIConfig(
        ai_fairness=AIFairnessConfig(has_synthetic_data=True),
        lineage=LineageConfig(models=[ModelRef(url="https://example.org/model")]),
    )

    document = inject_rai(_old_document(), config)

    assert document["cb:hasSyntheticData"] is True
    assert document["cb:usedBy"] == [{"url": "https://example.org/model"}]


def test_an_existing_cb_value_wins_over_an_old_rai_value() -> None:
    """A document that holds both was already updated once; cb: is the newer."""
    old = _old_document()
    old["cb:hasSyntheticData"] = True

    document = inject_rai(old, RAIConfig())

    assert document["cb:hasSyntheticData"] is True
    assert "rai:hasSyntheticData" not in document


def test_a_platform_in_an_activity_list_is_carried_to_cb() -> None:
    old = _old_document()
    old["prov:wasGeneratedBy"] = [old["prov:wasGeneratedBy"]]

    document = inject_rai(old, RAIConfig())

    assert document["prov:wasGeneratedBy"][0]["cb:usedPlatform"] == {
        "name": "Old platform"
    }


def _rai_apply(document: dict, config: str, tmp_path: Path) -> dict:
    path = tmp_path / "croissant.jsonld"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(path),
            "--rai-config",
            str(_write_config(tmp_path, config)),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    return json.loads(path.read_text())


def test_rai_apply_moves_the_old_keys_when_the_config_sets_them(
    tmp_path: Path,
) -> None:
    config = "ai_fairness:\n  has_synthetic_data: true\n"

    document = _rai_apply(_old_document(), config, tmp_path)

    assert document["cb:hasSyntheticData"] is True
    assert document["cb:usedBy"] == [{"url": "https://example.org/old-model"}]
    assert _rai_keys(document) == set()


def test_rai_apply_carries_the_old_keys_when_the_config_is_empty(
    tmp_path: Path,
) -> None:
    config = "ai_fairness:\n  data_limitations:\n"

    document = _rai_apply(_old_document(), config, tmp_path)

    assert document["cb:hasSyntheticData"] is False
    assert _rai_keys(document) == set()


def test_rai_apply_makes_no_rai_claim_from_the_old_keys_alone(
    tmp_path: Path,
) -> None:
    config = "ai_fairness:\n  has_synthetic_data: true\n"

    document = _rai_apply(_old_document(), config, tmp_path)

    assert document["conformsTo"] == CROISSANT_CONFORMS_TO


_OTHER_CB = "https://example.org/other#"


def _old_document_with_another_cb() -> dict:
    old = _old_document()
    old["@context"]["cb"] = _OTHER_CB
    return old


def test_old_keys_stay_put_when_cb_is_bound_elsewhere() -> None:
    """Moving them would file croissant-baker terms under someone else's IRI."""
    document = inject_rai(_old_document_with_another_cb(), RAIConfig())

    assert document["rai:hasSyntheticData"] is False
    assert document["rai:usedBy"] == [{"url": "https://example.org/old-model"}]
    assert "rai:usedPlatform" in document["prov:wasGeneratedBy"]
    assert not any(key.startswith("cb:") for key in _keys(document))
    assert document["@context"]["cb"] == _OTHER_CB


def test_rai_apply_prints_a_warning_when_cb_is_bound_elsewhere(
    tmp_path: Path,
) -> None:
    """The package logs to a NullHandler, so the CLI has to say it itself."""
    path = tmp_path / "croissant.jsonld"
    path.write_text(json.dumps(_old_document_with_another_cb()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(path),
            "--rai-config",
            str(_write_config(tmp_path, "ai_fairness:\n  data_biases: x\n")),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Warning" in result.stderr
    assert _OTHER_CB in result.stderr
    assert CB_NAMESPACE in result.stderr
    assert "rai:hasSyntheticData" in result.stderr


def test_rai_apply_prints_no_cb_warning_for_our_own_binding(tmp_path: Path) -> None:
    path = tmp_path / "croissant.jsonld"
    path.write_text(json.dumps(_old_document()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(path),
            "--rai-config",
            str(_write_config(tmp_path, "ai_fairness:\n  data_biases: x\n")),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Warning" not in result.stderr
