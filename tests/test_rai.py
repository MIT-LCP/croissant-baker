"""Integration test for the RAI metadata extension."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from croissant_baker.__main__ import app
from croissant_baker.metadata_generator import RAI_CONFORMS_TO
from croissant_baker.rai import inject_rai
from croissant_baker.rai.injector import _COLLECTION_TYPE_TERMS
from croissant_baker.rai.schema import Activity, AIFairnessConfig, RAIConfig
from tests.helpers import cli

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


def test_rai_generation_matches_reference(
    mimiciv_demo_path: Path, tmp_path: Path
) -> None:
    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "-i",
            str(mimiciv_demo_path),
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

    assert result.exit_code == 0, result.output

    generated = json.loads(output.read_text())
    expected = json.loads(EXPECTED.read_text())

    for key in _RAI_PROV_KEYS:
        assert generated.get(key) == expected.get(key), f"Mismatch for {key}"


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


# The rai-apply command: enrich a Croissant file that already exists.

_APPLY_YAML = """
ai_fairness:
  data_limitations: Single site
  has_synthetic_data: false
lineage:
  source_datasets:
    - url: https://example.org/source
      name: Example source
      organisation: Example Lab
      license: CC0-1.0
  models:
    - url: https://example.org/model
      name: Example model
activities:
  - id: collection
    type: data_collection
    description: Bedside charting
    start_at: "2008-01-01"
    end_at: "2019-12-31"
    agents:
      - name: Nurses
        url: https://example.org/nurses
        description: Ward staff
      - name: Scraper
        is_synthetic: true
    platforms:
      - name: EHR
        url: https://example.org/ehr
        description: Hospital record system
  - id: cleanup
    type: custom_step
    platforms:
      - name: Python
"""


def _baked(tmp_path: Path) -> Path:
    """A plain Croissant file, baked from one CSV without any RAI metadata."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.csv").write_text("id,name\n1,Ada\n2,Grace\n")
    baked = tmp_path / "baked.jsonld"
    result = cli(dataset, baked)
    assert result.exit_code == 0, result.output
    return baked


def _rai_yaml(tmp_path: Path, text: str = _APPLY_YAML) -> Path:
    path = tmp_path / "rai.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_rai_apply_writes_rai_and_provenance_to_the_output(tmp_path: Path) -> None:
    baked = _baked(tmp_path)
    before = baked.read_text()
    output = tmp_path / "enriched.jsonld"

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(baked),
            "--rai-config",
            str(_rai_yaml(tmp_path)),
            "-o",
            str(output),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"saved to: {output}" in result.output
    assert "croissant-baker validate" in result.output
    assert baked.read_text() == before
    doc = json.loads(output.read_text())
    assert doc["rai:dataLimitations"] == "Single site"
    assert doc["rai:hasSyntheticData"] is False
    assert RAI_CONFORMS_TO in doc["conformsTo"]
    assert doc["@context"]["prov"] == "http://www.w3.org/ns/prov#"
    assert doc["prov:wasDerivedFrom"] == [
        {
            "url": "https://example.org/source",
            "name": "Example source",
            "license": "CC0-1.0",
            "prov:wasAssociatedWith": {
                "@type": "prov:Organization",
                "name": "Example Lab",
            },
        }
    ]
    assert doc["rai:usedBy"] == [
        {"url": "https://example.org/model", "name": "Example model"}
    ]


def test_rai_apply_describes_each_activity(tmp_path: Path) -> None:
    baked = _baked(tmp_path)

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(baked),
            "--rai-config",
            str(_rai_yaml(tmp_path)),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    collection, cleanup = json.loads(baked.read_text())["prov:wasGeneratedBy"]
    assert collection["prov:label"] == "Data Collection"
    assert collection["prov:startedAtTime"] == "2008-01-01"
    assert collection["prov:endedAtTime"] == "2019-12-31"
    assert collection["prov:wasAssociatedWith"] == [
        {
            "@type": "prov:Agent",
            "name": "Nurses",
            "url": "https://example.org/nurses",
            "prov:description": "Ward staff",
        },
        {"@type": "prov:SoftwareAgent", "name": "Scraper"},
    ]
    assert collection["rai:usedPlatform"] == {
        "name": "EHR",
        "url": "https://example.org/ehr",
        "prov:description": "Hospital record system",
    }
    # An unknown activity type keeps its own name as the label, and a single
    # platform is written as one node rather than a list of one.
    assert cleanup["prov:label"] == "custom_step"
    assert cleanup["rai:usedPlatform"] == {"name": "Python"}


def test_rai_apply_validates_by_default(tmp_path: Path) -> None:
    baked = _baked(tmp_path)

    result = runner.invoke(
        app, ["rai-apply", str(baked), "--rai-config", str(_rai_yaml(tmp_path))]
    )

    assert result.exit_code == 0, result.output
    assert "croissant-baker validate" not in result.output
    assert "rai:dataLimitations" in json.loads(baked.read_text())


def test_rai_apply_refuses_a_file_it_cannot_validate(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jsonld"
    broken.write_text("{}", encoding="utf-8")
    output = tmp_path / "out.jsonld"

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(broken),
            "--rai-config",
            str(_rai_yaml(tmp_path)),
            "-o",
            str(output),
        ],
    )

    assert result.exit_code == 1
    assert "Validation failed" in result.stderr
    assert not output.exists()


def test_rai_apply_rejects_a_missing_input(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(tmp_path / "missing.jsonld"),
            "--rai-config",
            str(_rai_yaml(tmp_path)),
        ],
    )

    assert result.exit_code == 1
    assert "is not a file" in result.stderr


def test_rai_apply_reports_input_that_is_not_json(tmp_path: Path) -> None:
    not_json = tmp_path / "notes.jsonld"
    not_json.write_text("not json", encoding="utf-8")

    result = runner.invoke(
        app,
        ["rai-apply", str(not_json), "--rai-config", str(_rai_yaml(tmp_path))],
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("Error:")
    assert "Expecting value" in result.stderr


def test_rai_apply_reports_a_malformed_config(tmp_path: Path) -> None:
    baked = _baked(tmp_path)
    before = baked.read_text()
    config = _rai_yaml(tmp_path, "lineage:\n  source_datasets:\n    - just a string\n")

    result = runner.invoke(
        app, ["rai-apply", str(baked), "--rai-config", str(config), "--no-validate"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert baked.read_text() == before


def test_rai_apply_twice_declares_rai_conformance_once(tmp_path: Path) -> None:
    baked = _baked(tmp_path)
    apply = ["rai-apply", str(baked), "--rai-config", str(_rai_yaml(tmp_path))]

    first = runner.invoke(app, [*apply, "--no-validate"])
    second = runner.invoke(app, [*apply, "--no-validate"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    conforms_to = json.loads(baked.read_text())["conformsTo"]
    assert conforms_to.count(RAI_CONFORMS_TO) == 1


def test_rai_apply_reports_an_output_it_cannot_write(tmp_path: Path) -> None:
    """An output path under a regular file fails cleanly, input untouched."""
    baked = _baked(tmp_path)
    before = baked.read_text()
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "rai-apply",
            str(baked),
            "--rai-config",
            str(_rai_yaml(tmp_path)),
            "--no-validate",
            "-o",
            str(blocker / "out.jsonld"),
        ],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert str(blocker) in result.stderr
    assert baked.read_text() == before


def test_inject_rai_leaves_the_context_alone_without_provenance() -> None:
    config = RAIConfig(ai_fairness=AIFairnessConfig(data_biases="Adults only"))
    metadata = {"@context": {"cr": "http://mlcommons.org/croissant/"}}

    inject_rai(metadata, config)

    assert metadata["@context"] == {"cr": "http://mlcommons.org/croissant/"}
    assert metadata["rai:dataBiases"] == "Adults only"
