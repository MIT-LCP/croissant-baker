"""Tests for Croissant Baker CLI."""

import json
import logging
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from croissant_baker.__main__ import app

runner = CliRunner()


@pytest.fixture
def csv_dataset(tmp_path: Path) -> Path:
    """Create a CSV dataset for testing."""
    dataset_dir = tmp_path / "test_dataset"
    dataset_dir.mkdir()
    csv_content = "id,name,age\n1,Alice,25\n2,Bob,30"
    (dataset_dir / "data.csv").write_text(csv_content)
    return dataset_dir


def test_basic_generation(csv_dataset: Path, tmp_path: Path) -> None:
    """Test basic metadata generation with defaults."""
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Alice Smith",
        ],
    )

    assert result.exit_code == 0

    with open(output) as f:
        metadata = json.load(f)

    assert metadata["name"] == "test_dataset"
    assert "Dataset containing" in metadata["description"]


def test_comprehensive_overrides(csv_dataset: Path, tmp_path: Path) -> None:
    """Test comprehensive metadata overrides with multiple creators."""
    output = tmp_path / "example-with-overrides.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--name",
            "Machine Learning Dataset",
            "--description",
            "Example dataset with comprehensive metadata",
            "--url",
            "https://example.com/dataset",
            "--license",
            "MIT",
            "--dataset-version",
            "2.1.0",
            "--creator",
            "John Doe,john@example.com,https://johndoe.com",
            "--creator",
            "Jane Smith,jane@example.com",  # No URL
            "--creator",
            "Bob Wilson,,https://bob.com",  # No email
            "--creator",
            "Alice Johnson",  # Name only
            "--citation",
            "Doe et al. (2024). Machine Learning Dataset v2.1.",
        ],
    )

    assert result.exit_code == 0

    with open(output) as f:
        metadata = json.load(f)

    # Check overridden fields
    assert metadata["name"] == "Machine Learning Dataset"
    assert metadata["description"] == "Example dataset with comprehensive metadata"
    assert metadata["url"] == "https://example.com/dataset"
    assert metadata["license"] == "https://opensource.org/licenses/MIT"
    assert metadata["version"] == "2.1.0"
    assert metadata["citeAs"] == "Doe et al. (2024). Machine Learning Dataset v2.1."

    # Check creators with different info levels
    creators = metadata["creator"]
    assert len(creators) == 4
    assert creators[0]["name"] == "John Doe"
    assert creators[0]["email"] == "john@example.com"
    assert creators[0]["url"] == "https://johndoe.com"
    assert creators[1]["name"] == "Jane Smith"
    assert creators[1]["email"] == "jane@example.com"
    assert "url" not in creators[1]
    assert creators[2]["name"] == "Bob Wilson"
    assert "email" not in creators[2]
    assert creators[2]["url"] == "https://bob.com"
    assert creators[3]["name"] == "Alice Johnson"
    assert "email" not in creators[3]
    assert "url" not in creators[3]


def test_error_handling() -> None:
    """Test error handling for invalid inputs."""
    # Invalid directory
    result = runner.invoke(
        app,
        ["--input", "/nonexistent", "--creator", "Placeholder"],
    )
    assert result.exit_code == 1
    assert "Error:" in result.stderr


def test_missing_creator_required(csv_dataset: Path, tmp_path: Path) -> None:
    """Test that missing --creator flag produces appropriate error."""
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        ["--input", str(csv_dataset), "--output", str(output)],
    )

    assert result.exit_code == 1
    assert "At least one '--creator' option is required" in result.stderr
    assert "Example:" in result.stderr


def test_creator_parsing_variants(csv_dataset: Path, tmp_path: Path) -> None:
    """Test creator parsing for comma, quoted, and semicolon formats."""

    test_cases = [
        # (input, expected_strings)
        ('"Google, LLC"', ["Google, LLC"]),
        ('"Google, LLC",info@google.com', ["Google, LLC", "info@google.com"]),
        (
            '"Google, LLC",info@google.com,https://google.com',
            ["Google, LLC", "info@google.com", "https://google.com"],
        ),
        (
            '"Doe, Jr., John",john@example.com',
            ["Doe, Jr., John", "john@example.com"],
        ),
        # Backward compatibility
        ("Alice Smith", ["Alice Smith"]),
        ("Alice Smith,alice@example.com", ["Alice Smith", "alice@example.com"]),
        (
            "Alice Smith,alice@example.com,https://example.com",
            ["Alice Smith", "alice@example.com", "https://example.com"],
        ),
        # Semicolon format
        (
            "Google, LLC;info@google.com;https://google.com",
            ["Google, LLC", "info@google.com", "https://google.com"],
        ),
    ]

    for creator_input, expected_values in test_cases:
        output = tmp_path / f"output_{hash(creator_input)}.jsonld"

        result = runner.invoke(
            app,
            [
                "--input",
                str(csv_dataset),
                "--output",
                str(output),
                "--creator",
                creator_input,
            ],
        )

        assert result.exit_code == 0, f"Failed for input: {creator_input}"

        content = output.read_text()

        for expected in expected_values:
            assert expected in content, (
                f"Missing '{expected}' for input: {creator_input}"
            )


def test_invalid_date_format(csv_dataset: Path, tmp_path: Path) -> None:
    """Test that invalid date format gives clear error message."""
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test User",
            "--date-published",
            "invalid-date-format",
        ],
    )

    assert result.exit_code == 1
    assert "Invalid date format for --date-published" in result.stderr
    assert "Expected ISO format like '2023-12-15'" in result.stderr


def test_spec_warnings_when_fields_missing(csv_dataset: Path, tmp_path: Path) -> None:
    """Test that missing spec-required fields produce a warning on stderr."""
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Alice Smith",
        ],
    )

    assert result.exit_code == 0
    assert "Warning:" in result.stderr
    assert "--description" in result.stderr
    assert "--url" in result.stderr
    assert "--license" in result.stderr
    assert "--date-published" in result.stderr
    assert "--creator" not in result.stderr  # was provided, should not appear


def test_no_spec_warnings_when_all_fields_provided(
    csv_dataset: Path, tmp_path: Path
) -> None:
    """Test that no warning appears when all spec-required fields are explicitly provided."""
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Alice Smith",
            "--description",
            "A test dataset",
            "--url",
            "https://example.com",
            "--license",
            "MIT",
            "--date-published",
            "2024-01-01",
        ],
    )

    assert result.exit_code == 0
    assert "Warning:" not in result.stderr


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", text)


def test_help_and_version() -> None:
    """Test help and version commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "--creator" in stdout
    assert "--rai-data-biases" in stdout
    assert "--rai-config" in stdout
    assert "--include" in stdout
    assert "--exclude" in stdout
    assert "--dry-run" in stdout

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "croissant-baker" in result.stdout

    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout


@pytest.fixture
def mixed_dataset(tmp_path: Path) -> Path:
    """Create a dataset with multiple file types for filter testing."""
    dataset_dir = tmp_path / "mixed_dataset"
    dataset_dir.mkdir()
    (dataset_dir / "sub").mkdir()
    (dataset_dir / "data.csv").write_text("id,name\n1,Alice")
    (dataset_dir / "notes.txt").write_text("notes")
    (dataset_dir / "sub/more.csv").write_text("id,val\n1,x")
    (dataset_dir / "sub/temp.csv").write_text("id,val\n1,y")
    return dataset_dir


def test_dry_run_no_creator_required(csv_dataset: Path) -> None:
    """Dry run should not require --creator."""
    result = runner.invoke(app, ["--input", str(csv_dataset), "--dry-run"])
    assert result.exit_code == 0


def test_dry_run_no_output_required(csv_dataset: Path) -> None:
    """Dry run should not require --output and must not create any output file."""
    result = runner.invoke(app, ["--input", str(csv_dataset), "--dry-run"])
    assert result.exit_code == 0
    assert not any(csv_dataset.glob("*.jsonld"))


def test_dry_run_lists_processable_files(csv_dataset: Path) -> None:
    """Dry run lists files that have a registered handler."""
    result = runner.invoke(app, ["--input", str(csv_dataset), "--dry-run"])
    assert result.exit_code == 0
    assert "data.csv" in result.stdout
    assert "Dry run" in result.stdout


def test_dry_run_with_include_filter(mixed_dataset: Path) -> None:
    """Dry run with --include only reports matching files."""
    result = runner.invoke(
        app, ["--input", str(mixed_dataset), "--dry-run", "--include", "*.csv"]
    )
    assert result.exit_code == 0
    assert "data.csv" in result.stdout
    assert "notes.txt" not in result.stdout


def test_dry_run_with_exclude_filter(mixed_dataset: Path) -> None:
    """Dry run with --exclude omits matching files."""
    result = runner.invoke(
        app,
        [
            "--input",
            str(mixed_dataset),
            "--dry-run",
            "--exclude",
            "temp.csv",
            "--exclude",
            "sub/temp.csv",
        ],
    )
    assert result.exit_code == 0
    assert "temp.csv" not in result.stdout
    assert "data.csv" in result.stdout


def test_dry_run_invalid_input() -> None:
    """Dry run on a non-existent directory exits with error."""
    result = runner.invoke(app, ["--input", "/no/such/dir", "--dry-run"])
    assert result.exit_code == 1


def test_summary_files_count_excludes_filesets(tmp_path: Path) -> None:
    """Banner Files line counts cr:FileObject entries only."""
    from PIL import Image

    dataset = tmp_path / "imgs"
    dataset.mkdir()
    for i in range(3):
        Image.new("RGB", (4, 4)).save(dataset / f"img_{i}.png")
    output = tmp_path / "out.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(dataset),
            "-o",
            str(output),
            "--creator",
            "Tester",
            "--no-validate",
        ],
    )
    assert result.exit_code == 0, result.output

    assert "Files: 3" in result.stdout
    assert "Files: 4" not in result.stdout
    assert "File sets: 1" in result.stdout

    metadata = json.loads(output.read_text())
    types = [d["@type"] for d in metadata["distribution"]]
    assert types.count("cr:FileObject") == 3
    assert types.count("cr:FileSet") == 1


def test_validate_command_files_count_excludes_filesets(tmp_path: Path) -> None:
    """Validate subcommand banner counts FileObject entries only."""
    from PIL import Image

    dataset = tmp_path / "imgs"
    dataset.mkdir()
    for i in range(2):
        Image.new("RGB", (4, 4)).save(dataset / f"img_{i}.png")
    jsonld = tmp_path / "out.jsonld"

    gen = runner.invoke(app, ["-i", str(dataset), "-o", str(jsonld), "--creator", "T"])
    assert gen.exit_code == 0, gen.output

    result = runner.invoke(app, ["validate", str(jsonld)])
    assert result.exit_code == 0, result.output
    assert "Files: 2" in result.stdout
    assert "Files: 3" not in result.stdout
    assert "File sets: 1" in result.stdout


def test_include_filter_limits_generated_files(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    """--include restricts which files appear in the generated metadata."""
    output = tmp_path / "out.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(mixed_dataset),
            "--output",
            str(output),
            "--creator",
            "Test User",
            "--include",
            "data.csv",
        ],
    )
    assert result.exit_code == 0
    metadata = json.loads(output.read_text())
    names = [d["name"] for d in metadata.get("distribution", [])]
    assert any("data.csv" in n for n in names)
    assert not any("temp" in n for n in names)


def test_exclude_filter_omits_matching_files(
    mixed_dataset: Path, tmp_path: Path
) -> None:
    """--exclude removes matching files from the generated metadata."""
    output = tmp_path / "out.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(mixed_dataset),
            "--output",
            str(output),
            "--creator",
            "Test User",
            "--exclude",
            "temp.csv",
            "--exclude",
            "sub/temp.csv",
        ],
    )
    assert result.exit_code == 0
    metadata = json.loads(output.read_text())
    names = [d["name"] for d in metadata.get("distribution", [])]
    assert not any("temp" in n for n in names)


def test_native_rai_flags_generate_metadata(csv_dataset: Path, tmp_path: Path) -> None:
    """Native --rai-* flags should flow into mlcroissant metadata output."""
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Alice Smith",
            "--rai-data-biases",
            "Single-site cohort",
            "--rai-data-biases",
            "Adults only",
            "--rai-data-limitations",
            "Not representative of pediatric patients",
            "--rai-data-social-impact",
            "May improve triage research",
            "--rai-personal-sensitive-information",
            "Contains de-identified health records",
            "--rai-data-use-cases",
            "Benchmarking",
            "--rai-data-collection-timeframe",
            "2023-01-01",
            "--rai-data-collection-timeframe",
            "2023-06-01T12:30:00",
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output

    metadata = json.loads(output.read_text())

    assert metadata["rai:dataBiases"] == ["Single-site cohort", "Adults only"]
    assert metadata["rai:dataLimitations"] == (
        "Not representative of pediatric patients"
    )
    assert metadata["rai:dataSocialImpact"] == "May improve triage research"
    assert metadata["rai:personalSensitiveInformation"] == (
        "Contains de-identified health records"
    )
    assert metadata["rai:dataUseCases"] == "Benchmarking"
    assert metadata["conformsTo"] == [
        "http://mlcommons.org/croissant/1.1",
        "http://mlcommons.org/croissant/RAI/1.0",
    ]
    assert metadata["rai:dataCollectionTimeFrame"] == [
        "2023-01-01",
        "2023-06-01T12:30:00",
    ]


def test_native_rai_timeframe_invalid_format(csv_dataset: Path, tmp_path: Path) -> None:
    """Invalid native RAI timeframe values should fail with a clear error."""
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Alice Smith",
            "--rai-data-collection-timeframe",
            "not-a-date",
            "--no-validate",
        ],
    )

    assert result.exit_code == 1
    assert "Invalid date format for --rai-data-collection-timeframe" in result.stderr


def test_native_rai_flags_conflict_with_yaml(csv_dataset: Path, tmp_path: Path) -> None:
    """Users must choose either native --rai-* flags or --rai-config."""
    output = tmp_path / "output.jsonld"
    rai_yaml = tmp_path / "rai.yaml"
    rai_yaml.write_text("ai_fairness:\n  data_bias: Example bias\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Alice Smith",
            "--rai-config",
            str(rai_yaml),
            "--rai-data-biases",
            "Single-site cohort",
            "--no-validate",
        ],
    )

    assert result.exit_code == 1
    assert "cannot be combined with --rai-config" in result.stderr


def test_yaml_rai_workflow_declares_rai_conformance(
    csv_dataset: Path, tmp_path: Path
) -> None:
    """The YAML-based RAI workflow should also declare RAI conformance."""
    output = tmp_path / "output.jsonld"
    rai_yaml = tmp_path / "rai.yaml"
    rai_yaml.write_text(
        """
lineage:
  source_datasets:
    - url: https://example.org/source
      name: Example source
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Alice Smith",
            "--rai-config",
            str(rai_yaml),
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output

    metadata = json.loads(output.read_text())
    assert metadata["conformsTo"] == [
        "http://mlcommons.org/croissant/1.1",
        "http://mlcommons.org/croissant/RAI/1.0",
    ]


def test_optional_1_1_flags_round_trip(csv_dataset: Path, tmp_path: Path) -> None:
    """Optional 1.1 flags emit their schema.org fields unchanged.

    Exercises both repeat-flag and comma-delimited input shapes for the
    list-valued flags, plus single-value plumbing for the rest. Locks in
    the JSON-LD field names mlcroissant emits for each input.
    """
    output = tmp_path / "output.jsonld"

    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test",
            "--publisher",
            "PhysioNet",
            "--keywords",
            "ehr,icu",
            "--keywords",
            "demo",
            "--in-language",
            "en",
            "--same-as",
            "https://doi.org/10.1234/example,https://example.org/mirror",
            "--sd-license",
            "https://creativecommons.org/publicdomain/zero/1.0/",
            "--sd-version",
            "1.0.0",
            "--alternate-name",
            "test-ds",
            "--is-live-dataset",
            "--date-created",
            "2023-01-01",
            "--date-modified",
            "2023-06-01T12:00:00",
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = json.loads(output.read_text())

    assert metadata["publisher"] == {
        "@type": "sc:Organization",
        "name": "PhysioNet",
    }
    assert metadata["keywords"] == ["ehr", "icu", "demo"]
    assert metadata["sameAs"] == [
        "https://doi.org/10.1234/example",
        "https://example.org/mirror",
    ]
    # mlcroissant flattens single-item lists for these properties (spec-valid):
    assert metadata["inLanguage"] == "en"
    assert metadata["sdLicense"] == (
        "https://creativecommons.org/publicdomain/zero/1.0/"
    )
    assert metadata["sdVersion"] == "1.0.0"
    assert metadata["alternateName"] == "test-ds"
    assert metadata["isLiveDataset"] is True
    assert metadata["dateCreated"] == "2023-01-01"
    assert metadata["dateModified"] == "2023-06-01T12:00:00"


def test_temporal_coverage_and_usage_info(csv_dataset: Path, tmp_path: Path) -> None:
    """--temporal-coverage and --usage-info flow through as schema.org fields."""
    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test",
            "--temporal-coverage",
            "2008/2019",
            "--usage-info",
            "http://purl.obolibrary.org/obo/DUO_0000042",
            "--no-validate",
        ],
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads(output.read_text())
    assert metadata["temporalCoverage"] == "2008/2019"
    assert metadata["usageInfo"] == "http://purl.obolibrary.org/obo/DUO_0000042"


def test_field_mappings_yaml_appends_to_dataType(
    csv_dataset: Path, tmp_path: Path
) -> None:
    """--field-mappings adds equivalentProperty + appends external dataType."""
    mappings = tmp_path / "mappings.yaml"
    mappings.write_text(
        "fields:\n"
        "  age:\n"
        "    equivalent_property: 'wdt:P3629'\n"
        "    data_types: ['wd:Q11464']\n",
        encoding="utf-8",
    )
    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test",
            "--field-mappings",
            str(mappings),
            "--no-validate",
        ],
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads(output.read_text())
    age_field = next(
        f
        for rs in metadata["recordSet"]
        for f in (rs["field"] if isinstance(rs["field"], list) else [rs["field"]])
        if f["name"] == "age"
    )
    # Inferred Croissant type preserved, external vocab appended.
    assert age_field["equivalentProperty"] == "wdt:P3629"
    assert "wd:Q11464" in age_field["dataType"]
    assert any(t.startswith(("cr:", "sc:")) for t in age_field["dataType"])


def test_field_mapping_flag_overrides_yaml(csv_dataset: Path, tmp_path: Path) -> None:
    """The repeatable --field-mapping flag merges with --field-mappings YAML and wins."""
    mappings = tmp_path / "mappings.yaml"
    mappings.write_text(
        "fields:\n  age:\n    equivalent_property: 'wdt:OLD'\n",
        encoding="utf-8",
    )
    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test",
            "--field-mappings",
            str(mappings),
            "--field-mapping",
            "age=wdt:NEW",
            "--field-mapping",
            "id=wdt:P527",
            "--no-validate",
        ],
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads(output.read_text())
    fields = {
        f["name"]: f
        for rs in metadata["recordSet"]
        for f in (rs["field"] if isinstance(rs["field"], list) else [rs["field"]])
    }
    assert fields["age"]["equivalentProperty"] == "wdt:NEW"  # flag won
    assert fields["id"]["equivalentProperty"] == "wdt:P527"  # flag-only column


def test_usage_info_rejects_free_text(csv_dataset: Path, tmp_path: Path) -> None:
    """Reject strings without a URI scheme; accept any RFC 3986 scheme."""
    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test",
            "--usage-info",
            "see license file",
            "--no-validate",
        ],
    )
    assert result.exit_code != 0
    assert "URI" in result.output or "scheme" in result.output.lower()


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.org/policy",
        "http://purl.obolibrary.org/obo/DUO_0000042",
        "urn:lex:eu:council:directive:2022-09-14;2022-2065",
        "did:web:example.org:dataset",
        "mailto:dac@example.org",
    ],
)
def test_usage_info_accepts_any_uri_scheme(
    csv_dataset: Path, tmp_path: Path, uri: str
) -> None:
    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test",
            "--usage-info",
            uri,
            "--no-validate",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["usageInfo"] == uri


def test_field_mapping_warns_on_multiple_matches(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A mapping that hits more than one field warns the user (via logging)."""
    dataset_dir = tmp_path / "ds"
    dataset_dir.mkdir()
    # Two CSVs both with an 'id' column — same name, different semantics.
    (dataset_dir / "patients.csv").write_text("id,age\n1,40\n2,50\n")
    (dataset_dir / "diagnoses.csv").write_text("id,code\n1,X\n2,Y\n")

    output = tmp_path / "out.jsonld"
    with caplog.at_level(logging.WARNING, logger="croissant_baker.metadata_generator"):
        result = runner.invoke(
            app,
            [
                "--input",
                str(dataset_dir),
                "--output",
                str(output),
                "--creator",
                "Test",
                "--field-mapping",
                "id=http://www.wikidata.org/entity/Q577",
                "--no-validate",
            ],
        )
    assert result.exit_code == 0, result.output
    assert any(
        "field mapping 'id' applied to 2 fields" in r.getMessage()
        for r in caplog.records
    ), caplog.records


def test_field_mappings_yaml_rejects_unknown_keys(
    csv_dataset: Path, tmp_path: Path
) -> None:
    mappings = tmp_path / "mappings.yaml"
    mappings.write_text(
        "fields:\n  age:\n    equivalentProperty: 'wdt:P3629'\n",  # camelCase typo
        encoding="utf-8",
    )
    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--creator",
            "Test",
            "--field-mappings",
            str(mappings),
            "--no-validate",
        ],
    )
    assert result.exit_code != 0
    assert "unknown" in result.output.lower()


def test_baked_output_round_trips_through_mlcroissant(
    csv_dataset: Path, tmp_path: Path
) -> None:
    """mlc.Dataset() can load our output and the values read back match the inputs.

    Validator-pass alone proves shape; this proves consumers can iterate values.
    """
    import mlcroissant as mlc

    output = tmp_path / "output.jsonld"
    result = runner.invoke(
        app,
        [
            "--input",
            str(csv_dataset),
            "--output",
            str(output),
            "--name",
            "Round-trip dataset",
            "--description",
            "Reads back through mlc.Dataset().",
            "--url",
            "https://example.com/rt",
            "--license",
            "CC-BY-4.0",
            "--creator",
            "Test",
            "--keywords",
            "rt,demo",
        ],
    )
    assert result.exit_code == 0, result.output

    ds = mlc.Dataset(str(output))
    assert ds.metadata.name == "Round-trip dataset"
    assert ds.metadata.description == "Reads back through mlc.Dataset()."
    assert ds.metadata.url == "https://example.com/rt"
    # Field-level access works too.
    record_sets = list(ds.metadata.record_sets)
    fields = list(record_sets[0].fields)
    assert {f.name for f in fields} == {"id", "name", "age"}
