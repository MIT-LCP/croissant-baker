"""Integration test for the RAI metadata extension."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from croissant_baker.__main__ import app
from croissant_baker.metadata_generator import RAI_CONFORMS_TO
from croissant_baker.rai import RAIConfig, inject_rai
from croissant_baker.rai.schema import AIFairnessConfig
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
            str(tmp_path),
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


def test_rai_apply_reports_a_malformed_config(tmp_path: Path) -> None:
    baked = _baked(tmp_path)
    before = baked.read_text()
    config = _rai_yaml(tmp_path, "lineage:\n  source_datasets:\n    - just a string\n")

    result = runner.invoke(
        app, ["rai-apply", str(baked), "--rai-config", str(config), "--no-validate"]
    )

    assert result.exit_code == 1
    assert "Unexpected error" in result.stderr
    assert baked.read_text() == before


def test_inject_rai_leaves_the_context_alone_without_provenance() -> None:
    config = RAIConfig(ai_fairness=AIFairnessConfig(data_biases="Adults only"))
    metadata = {"@context": {"cr": "http://mlcommons.org/croissant/"}}

    inject_rai(metadata, config)

    assert metadata["@context"] == {"cr": "http://mlcommons.org/croissant/"}
    assert metadata["rai:dataBiases"] == "Adults only"
