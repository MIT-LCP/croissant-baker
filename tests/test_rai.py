"""Integration test for the RAI metadata extension."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from croissant_baker.__main__ import app
from croissant_baker.rai import inject_rai
from croissant_baker.rai.schema import Activity, RAIConfig
from tests.helpers import cli

runner = CliRunner()

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
        "observations",
        "existing_datasets",
        "surveys",
    ]


def test_a_single_collection_type_is_written_as_a_string() -> None:
    document = inject_rai({"@context": {}}, _config(_activity("ACT-001", "surveys")))

    assert document["rai:dataCollectionType"] == "surveys"


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

    assert expected["rai:dataCollectionType"] == ["observations", "existing_datasets"]


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
