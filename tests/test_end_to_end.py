"""End-to-end tests for Croissant Baker using real datasets."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from croissant_baker.__main__ import app

runner = CliRunner()


# We intentionally use an explicit, versioned subdirectory for MIMIC-IV to
# demonstrate running against a precise dataset root. This complements the eICU
# test, which points to a higher-level directory to exercise recursive
# file discovery.
@pytest.fixture
def mimiciv_demo_path() -> Path:
    """Path to the MIMIC-IV demo dataset for testing."""
    dataset_path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mimiciv_demo"
        / "physionet.org"
        / "files"
        / "mimic-iv-demo"
        / "2.2"
    )

    if not dataset_path.exists():
        pytest.skip(f"MIMIC-IV demo dataset not found at {dataset_path}")

    return dataset_path


@pytest.fixture
def output_dir() -> Path:
    """Create and return the tests/output directory."""
    output_path = Path(__file__).parent / "data" / "output"
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def test_mimiciv_demo_generation(mimiciv_demo_path: Path, output_dir: Path) -> None:
    """Test end-to-end metadata generation with MIMIC-IV demo dataset."""
    output_file = output_dir / "mimiciv_demo_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(mimiciv_demo_path),
            "-o",
            str(output_file),
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
        ],
    )

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert output_file.exists(), "Output file was not created"

    # Validate the generated metadata
    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "MIMIC-IV Demo Dataset"
    assert metadata["version"] == "2.2"
    assert metadata["url"] == "https://physionet.org/content/mimic-iv-demo/"
    assert len(metadata["creator"]) == 6  # Six creators
    assert len(metadata["distribution"]) > 20  # Many CSV files
    assert len(metadata["recordSet"]) > 10  # Many record sets


# We intentionally point the eICU test at the top-level directory to verify that
# recursive discovery works and handlers filter supported files (e.g., CSV, CSV.GZ).
# Non-CSV artifacts (HTML, checksums, sqlite) are ignored by design.
@pytest.fixture
def eicu_demo_path() -> Path:
    """Path to the eICU CRD demo dataset for testing."""
    dataset_path = Path(__file__).parent / "data" / "input" / "eicu_demo"

    if not dataset_path.exists():
        pytest.skip(f"eICU CRD demo dataset not found at {dataset_path}")

    return dataset_path


def test_eicu_demo_generation(eicu_demo_path: Path, output_dir: Path) -> None:
    """Test end-to-end metadata generation with eICU CRD demo dataset."""
    output_file = output_dir / "eicu_demo_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(eicu_demo_path),
            "-o",
            str(output_file),
            "--name",
            "eICU Collaborative Research Database Demo",
            "--description",
            "Demo version of the eICU Collaborative Research Database",
            "--url",
            "https://physionet.org/content/eicu-crd-demo/2.0.1/",
            "--dataset-version",
            "2.0.1",
            "--date-published",
            "2021-05-06",
            "--creator",
            "Alistair Johnson",
            "--creator",
            "Tom Pollard",
            "--creator",
            "Omar Badawi",
            "--creator",
            "Jesse Raffa",
            "--citation",
            "Johnson, A., Pollard, T., Badawi, O., & Raffa, J. (2021). eICU Collaborative Research Database Demo (version 2.0.1). PhysioNet. https://doi.org/10.13026/4mxk-na84",
        ],
    )

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert output_file.exists(), "Output file was not created"

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "eICU Collaborative Research Database Demo"
    assert metadata["version"] == "2.0.1"
    assert metadata["url"] == "https://physionet.org/content/eicu-crd-demo/2.0.1/"
    assert len(metadata["creator"]) >= 4
    assert len(metadata["distribution"]) > 10
    assert len(metadata["recordSet"]) > 5


@pytest.fixture
def mitdb_wfdb_path() -> Path:
    """Path to MIT-BIH Arrhythmia Database for testing."""
    dataset_path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mitdb_wfdb"
        / "physionet.org"
        / "files"
        / "mitdb"
        / "1.0.0"
    )
    if not dataset_path.exists() or not (dataset_path / "100.hea").exists():
        pytest.skip(f"MIT-BIH WFDB dataset not found at {dataset_path}")
    return dataset_path


def test_mitdb_wfdb_generation(mitdb_wfdb_path: Path, output_dir: Path) -> None:
    """Test end-to-end metadata generation with MIT-BIH Arrhythmia Database."""
    output_file = output_dir / "mitdb_wfdb_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(mitdb_wfdb_path),
            "-o",
            str(output_file),
            "--name",
            "MIT-BIH Arrhythmia Database",
            "--description",
            "MIT-BIH Arrhythmia Database containing 48 ECG recordings with annotations",
            "--url",
            "https://physionet.org/content/mitdb/1.0.0/",
            "--license",
            "https://physionet.org/content/mitdb/1.0.0/LICENSE.txt",
            "--dataset-version",
            "1.0.0",
            "--date-published",
            "1992-07-30",
            "--creator",
            "MIT-BIH",
            "--citation",
            "Moody GB, Mark RG. The impact of the MIT-BIH Arrhythmia Database. IEEE Eng in Med and Biol 20(3):45-50 (May-June 2001).",
        ],
    )

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert output_file.exists(), "Output file was not created"

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "MIT-BIH Arrhythmia Database"
    assert metadata["version"] == "1.0.0"
    assert metadata["url"] == "https://physionet.org/content/mitdb/1.0.0/"

    # Should have 71 records * 3 files each = 213 files (71 .hea + 71 .dat + 71 .atr)
    # This includes the main 48 records (100-234) plus additional x_ prefixed records
    assert len(metadata["distribution"]) == 213
    assert len(metadata["recordSet"]) == 71

    # Check a few specific records
    record_names = [rs["name"] for rs in metadata["recordSet"]]
    assert "100" in record_names
    assert "200" in record_names
    assert "234" in record_names

    # Check that we have the expected signals
    record_100 = next(rs for rs in metadata["recordSet"] if rs["name"] == "100")
    assert len(record_100["field"]) == 2
    assert "MLII" in [f["name"] for f in record_100["field"]]
    assert "V5" in [f["name"] for f in record_100["field"]]


@pytest.fixture
def mimiciv_demo_meds_path() -> Path:
    """Path to the MEDS demo dataset (Parquet) for testing."""
    dataset_path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mimiciv_demo_meds"
        / "physionet.org"
        / "files"
        / "mimic-iv-demo-meds"
        / "0.0.1"
        / "data"
    )
    # Do not skip: tests assume data was downloaded by setup step
    assert dataset_path.exists(), f"MEDS demo dataset not found at {dataset_path}"
    return dataset_path


def test_mimiciv_demo_meds_generation(
    mimiciv_demo_meds_path: Path, output_dir: Path
) -> None:
    """Test end-to-end metadata generation with MEDS Parquet demo dataset."""
    output_file = output_dir / "mimiciv_demo_meds_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(mimiciv_demo_meds_path),
            "-o",
            str(output_file),
            "--name",
            "MIMIC-IV demo data in the Medical Event Data Standard (MEDS)",
            "--description",
            "MEDS demo of MIMIC-IV represented as Parquet event streams",
            "--url",
            "https://physionet.org/content/mimic-iv-demo-meds/0.0.1/",
            "--dataset-version",
            "0.0.1",
            "--date-published",
            "2025-09-29",
            "--creator",
            "Robin van de Water",
            "--creator",
            "Matthew McDermott",
        ],
    )

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert output_file.exists(), "Output file was not created"

    with open(output_file) as f:
        metadata = json.load(f)

    assert (
        metadata["name"]
        == "MIMIC-IV demo data in the Medical Event Data Standard (MEDS)"
    )
    assert metadata["version"] == "0.0.1"
    assert metadata["url"] == "https://physionet.org/content/mimic-iv-demo-meds/0.0.1/"
    assert len(metadata["distribution"]) > 0
    assert len(metadata["recordSet"]) > 0

    # At least one field should be a Date (timestamps common in MEDS)
    has_date = any(
        any("sc:Date" in field.get("dataType", []) for field in rs.get("field", []))
        for rs in metadata.get("recordSet", [])
    )
    assert has_date, "Expected at least one Date field in MEDS record sets"


@pytest.fixture
def mimiciv_demo_omop_path() -> Path:
    """Path to the MIMIC-IV OMOP demo dataset for testing."""
    dataset_path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mimiciv_demo_omop"
        / "physionet.org"
        / "files"
        / "mimic-iv-demo-omop"
        / "0.9"
        / "1_omop_data_csv"
    )

    if not dataset_path.exists():
        pytest.skip(f"MIMIC-IV OMOP demo dataset not found at {dataset_path}")

    return dataset_path


def test_mimiciv_demo_omop_generation(
    mimiciv_demo_omop_path: Path, output_dir: Path
) -> None:
    """Test end-to-end metadata generation with MIMIC-IV OMOP demo dataset."""
    output_file = output_dir / "mimiciv_demo_omop_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(mimiciv_demo_omop_path),
            "-o",
            str(output_file),
            "--name",
            "MIMIC-IV demo data in the OMOP Common Data Model",
            "--description",
            "A 100-patient demo of MIMIC-IV in the OMOP Common Data Model",
            "--url",
            "https://physionet.org/content/mimic-iv-demo-omop/0.9/",
            "--dataset-version",
            "0.9",
            "--date-published",
            "2021-06-21",
            "--creator",
            "Michael Kallfelz",
            "--creator",
            "Anna Tsvetkova",
            "--creator",
            "Tom Pollard",
            "--citation",
            "Kallfelz, M., et al. (2021). MIMIC-IV demo data in the OMOP Common Data Model (version 0.9). PhysioNet. https://doi.org/10.13026/p1f5-7x35",
        ],
    )

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert output_file.exists(), "Output file was not created"

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "MIMIC-IV demo data in the OMOP Common Data Model"
    assert metadata["version"] == "0.9"
    assert metadata["url"] == "https://physionet.org/content/mimic-iv-demo-omop/0.9/"
    assert len(metadata["creator"]) == 3
    assert len(metadata["distribution"]) > 0
    assert len(metadata["recordSet"]) > 0


# ---------------------------------------------------------------------------
# Glaucoma fundus dataset (JPG images + CSV labels)
# ---------------------------------------------------------------------------


@pytest.fixture
def glaucoma_fundus_path() -> Path:
    """Path to the glaucoma fundus dataset for testing."""
    dataset_path = Path(__file__).parent / "data" / "input" / "glaucoma_fundus"
    if not dataset_path.exists():
        pytest.skip(f"Glaucoma fundus dataset not found at {dataset_path}")
    return dataset_path


def test_glaucoma_fundus_generation(
    glaucoma_fundus_path: Path, output_dir: Path
) -> None:
    """Test end-to-end metadata generation with glaucoma fundus images."""
    output_file = output_dir / "glaucoma_fundus_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(glaucoma_fundus_path),
            "-o",
            str(output_file),
            "--name",
            "Hillel Yaffe Glaucoma Dataset (subset)",
            "--description",
            "Subset of fundus images for glaucoma screening with GON labels",
            "--url",
            "https://physionet.org/content/hyg-dataset/1.0.0/",
            "--license",
            "https://physionet.org/content/hyg-dataset/1.0.0/LICENSE.txt",
            "--dataset-version",
            "1.0.0",
            "--date-published",
            "2024-11-14",
            "--creator",
            "Hillel Yaffe Medical Center",
        ],
    )

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert output_file.exists(), "Output file was not created"

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "Hillel Yaffe Glaucoma Dataset (subset)"
    assert metadata["version"] == "1.0.0"

    # Should have FileObjects for Labels.csv + 12 JPG images + 1 FileSet = 14
    assert len(metadata["distribution"]) == 14

    # Should have RecordSets: 1 for Labels.csv + 1 for images = 2
    assert len(metadata["recordSet"]) == 2

    # Verify image FileSet in distribution
    filesets = [d for d in metadata["distribution"] if d["@type"] == "cr:FileSet"]
    assert len(filesets) == 1
    assert "*.jpg" in str(filesets[0].get("includes", ""))

    # Verify the image RecordSet exists with sc:ImageObject sourced from FileSet
    image_rs = [rs for rs in metadata["recordSet"] if rs["name"] == "images"]
    assert len(image_rs) == 1
    image_field = image_rs[0]["field"][0]
    assert "sc:ImageObject" in image_field["dataType"]
    assert image_field["source"]["fileSet"]["@id"] == "image-files"

    # Verify the tabular RecordSet for Labels.csv
    label_rs = [rs for rs in metadata["recordSet"] if rs["name"] == "Labels"]
    assert len(label_rs) == 1


# ---------------------------------------------------------------------------
# Satellite public health dataset (multi-band TIFF + CSV metadata)
# ---------------------------------------------------------------------------


@pytest.fixture
def satellite_path() -> Path:
    """Path to the satellite public health dataset for testing."""
    dataset_path = Path(__file__).parent / "data" / "input" / "satellite_public_health"
    if not dataset_path.exists():
        pytest.skip(f"Satellite dataset not found at {dataset_path}")
    return dataset_path


def test_satellite_generation(satellite_path: Path, output_dir: Path) -> None:
    """Test end-to-end metadata generation with multi-band satellite TIFFs."""
    output_file = output_dir / "satellite_public_health_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(satellite_path),
            "-o",
            str(output_file),
            "--name",
            "Multi-Modal Satellite Imagery for Public Health (subset)",
            "--description",
            "Subset of Sentinel-2 satellite imagery linked to public health indicators in Colombia",
            "--url",
            "https://physionet.org/content/multimodal-satellite-data/1.0.0/",
            "--license",
            "https://physionet.org/content/multimodal-satellite-data/1.0.0/LICENSE.txt",
            "--dataset-version",
            "1.0.0",
            "--date-published",
            "2024-01-01",
            "--creator",
            "Suri Allison Garzón Mora",
        ],
    )

    assert result.exit_code == 0, f"Command failed: {result.stdout}"
    assert output_file.exists(), "Output file was not created"

    with open(output_file) as f:
        metadata = json.load(f)

    assert (
        metadata["name"] == "Multi-Modal Satellite Imagery for Public Health (subset)"
    )
    assert metadata["version"] == "1.0.0"

    # Should have FileObjects for metadata.csv + 10 TIFF images + 1 FileSet = 12
    assert len(metadata["distribution"]) == 12

    # Should have RecordSets: 1 for metadata.csv + 1 for images = 2
    assert len(metadata["recordSet"]) == 2

    # Verify the image RecordSet
    image_rs = [rs for rs in metadata["recordSet"] if rs["name"] == "images"]
    assert len(image_rs) == 1
    # Description should mention 12 bands (Sentinel-2)
    assert (
        "band" in image_rs[0]["description"].lower()
        or "10 images" in image_rs[0]["description"]
    )


# ---------------------------------------------------------------------------
# Synthetic Open Targets-like dataset (partitioned Parquet tables)
# ---------------------------------------------------------------------------


@pytest.fixture
def open_targets_like_path(tmp_path: Path) -> Path:
    """Create a synthetic Open Targets-like dataset with diverse Parquet schemas.

    Structure mirrors the OT Platform download layout:
      diseases/        — 2 partitions: string, int32, bool
      targets/         — 2 partitions: string, float64, bool
      association_by_datatype_direct/  — 2 partitions: string×3, float64, int32
      drug_molecule/   — 1 partition only (standalone, not grouped)
                         with float32, int16, bool to exercise less-common types
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = tmp_path / "open_targets_toy"

    def _write_parts(
        table_dir: Path, schema: pa.Schema, rows_per_part: int, n_parts: int
    ) -> None:
        table_dir.mkdir(parents=True)
        for i in range(n_parts):
            offset = i * rows_per_part
            arrays = []
            for field in schema:
                if pa.types.is_string(field.type):
                    arrays.append(
                        pa.array(
                            [
                                f"{field.name}_{offset + j}"
                                for j in range(rows_per_part)
                            ],
                            type=field.type,
                        )
                    )
                elif pa.types.is_boolean(field.type):
                    arrays.append(
                        pa.array(
                            [j % 2 == 0 for j in range(rows_per_part)], type=field.type
                        )
                    )
                elif pa.types.is_floating(field.type):
                    arrays.append(
                        pa.array(
                            [float(offset + j) * 0.1 for j in range(rows_per_part)],
                            type=field.type,
                        )
                    )
                else:  # integers
                    arrays.append(
                        pa.array(
                            [offset + j for j in range(rows_per_part)], type=field.type
                        )
                    )
            pq.write_table(
                pa.table(arrays, schema=schema), table_dir / f"part-{i:05d}.parquet"
            )

    _write_parts(
        root / "diseases",
        pa.schema(
            [
                ("id", pa.string()),
                ("name", pa.string()),
                ("numChildren", pa.int32()),
                ("isTherapeuticArea", pa.bool_()),
            ]
        ),
        rows_per_part=5,
        n_parts=2,
    )
    _write_parts(
        root / "targets",
        pa.schema(
            [
                ("id", pa.string()),
                ("approvedSymbol", pa.string()),
                ("tractabilityScore", pa.float64()),
                ("isDriver", pa.bool_()),
            ]
        ),
        rows_per_part=5,
        n_parts=2,
    )
    _write_parts(
        root / "association_by_datatype_direct",
        pa.schema(
            [
                ("targetId", pa.string()),
                ("diseaseId", pa.string()),
                ("datatypeId", pa.string()),
                ("score", pa.float64()),
                ("evidenceCount", pa.int32()),
            ]
        ),
        rows_per_part=5,
        n_parts=2,
    )
    # Single-partition table — must NOT be grouped into a FileSet
    _write_parts(
        root / "drug_molecule",
        pa.schema(
            [
                ("id", pa.string()),
                ("name", pa.string()),
                ("maxClinicalTrialPhase", pa.float32()),
                ("yearOfFirstApproval", pa.int16()),
                ("hasBeenWithdrawn", pa.bool_()),
            ]
        ),
        rows_per_part=5,
        n_parts=1,
    )

    # Partitioned table with complex types: list<string>, struct, list<struct>
    credible_set_schema = pa.schema(
        [
            ("studyId", pa.string()),
            ("chromosome", pa.string()),
            ("position", pa.int32()),
            ("qualityControls", pa.list_(pa.string())),  # list<string>
            (
                "locus",
                pa.list_(
                    pa.struct(
                        [  # list<struct>
                            pa.field("variantId", pa.string()),
                            pa.field("posteriorProbability", pa.float64()),
                        ]
                    )
                ),
            ),
            (
                "stats",
                pa.struct(
                    [  # struct (not in list)
                        pa.field("beta", pa.float64()),
                        pa.field("pValueExponent", pa.int32()),
                    ]
                ),
            ),
        ]
    )

    credible_set_dir = root / "credible_set"
    credible_set_dir.mkdir(parents=True)
    n_rows = 3
    for i in range(2):
        offset = i * n_rows
        table = pa.table(
            {
                "studyId": pa.array([f"STUDY_{offset + j}" for j in range(n_rows)]),
                "chromosome": pa.array([f"chr{j + 1}" for j in range(n_rows)]),
                "position": pa.array(
                    [offset + j for j in range(n_rows)], type=pa.int32()
                ),
                "qualityControls": pa.array(
                    [[f"QC_{j}"] for j in range(n_rows)], type=pa.list_(pa.string())
                ),
                "locus": pa.array(
                    [
                        [
                            {
                                "variantId": f"var_{j}",
                                "posteriorProbability": 0.5 + j * 0.1,
                            }
                        ]
                        for j in range(n_rows)
                    ],
                    type=pa.list_(
                        pa.struct(
                            [
                                pa.field("variantId", pa.string()),
                                pa.field("posteriorProbability", pa.float64()),
                            ]
                        )
                    ),
                ),
                "stats": pa.array(
                    [
                        {"beta": float(j) * 0.01, "pValueExponent": -(j + 1)}
                        for j in range(n_rows)
                    ],
                    type=pa.struct(
                        [
                            pa.field("beta", pa.float64()),
                            pa.field("pValueExponent", pa.int32()),
                        ]
                    ),
                ),
            },
            schema=credible_set_schema,
        )
        pq.write_table(table, credible_set_dir / f"part-{i:05d}.parquet")

    return root


def test_open_targets_like_generation(
    open_targets_like_path: Path, output_dir: Path
) -> None:
    """Test end-to-end generation with a synthetic Open Targets-like partitioned Parquet dataset.

    Verifies that:
    - Partitioned tables (>=2 files in a directory) produce one FileSet + one RecordSet
    - Standalone tables (1 file in a directory) still produce per-file FileObject + RecordSet
    - All expected Croissant types are present across the diverse schemas
    """
    output_file = output_dir / "open_targets_like_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(open_targets_like_path),
            "-o",
            str(output_file),
            "--name",
            "Open Targets Platform (toy)",
            "--description",
            "Synthetic subset of Open Targets Platform data for testing partitioned Parquet support",
            "--url",
            "https://platform.opentargets.org",
            "--license",
            "CC0-1.0",
            "--dataset-version",
            "25.03",
            "--date-published",
            "2025-03-01",
            "--creator",
            "Open Targets,data@opentargets.org,https://www.opentargets.org",
            # Skip mlcroissant round-trip validation: local FileSets without
            # containedIn are valid per spec but may not resolve in all validators.
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, f"Command failed:\n{result.stdout}"
    assert output_file.exists()

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "Open Targets Platform (toy)"
    assert metadata["version"] == "25.03"
    assert metadata["license"] == "https://creativecommons.org/publicdomain/zero/1.0/"

    dist = metadata["distribution"]
    file_objects = [d for d in dist if d["@type"] == "cr:FileObject"]
    file_sets = [d for d in dist if d["@type"] == "cr:FileSet"]

    # 4 partitioned tables × 2 part files = 8 FileObjects
    # + 1 standalone table × 1 part file = 1 FileObject  → 9 total
    # + 4 FileSets (one per partitioned table directory)  → 13 distribution entries
    assert len(file_objects) == 9, f"Expected 9 FileObjects, got {len(file_objects)}"
    assert len(file_sets) == 4, f"Expected 4 FileSets, got {len(file_sets)}"
    assert len(dist) == 13

    record_sets = metadata["recordSet"]
    # 4 partitioned RecordSets + 1 standalone RecordSet
    assert len(record_sets) == 5, f"Expected 5 RecordSets, got {len(record_sets)}"

    rs_names = {rs["name"] for rs in record_sets}
    assert rs_names == {
        "diseases",
        "targets",
        "association_by_datatype_direct",
        "drug_molecule",
        "credible_set",
    }

    # FileSets carry directory-scoped glob patterns, widened by the generator to
    # cover every registered compression, so includes is a list.
    includes_patterns = {
        pattern
        for fs in file_sets
        for pattern in (
            fs["includes"] if isinstance(fs["includes"], list) else [fs["includes"]]
        )
    }
    assert "diseases/*.parquet" in includes_patterns
    assert "targets/*.parquet" in includes_patterns
    assert "association_by_datatype_direct/*.parquet" in includes_patterns
    assert "credible_set/*.parquet" in includes_patterns

    # Collect all Croissant types emitted across all fields.
    # dataType serializes as a plain string in the JSON output.
    all_types: set[str] = set()
    for rs in record_sets:
        for field in rs.get("field", []):
            dtype_val = field.get("dataType")
            if isinstance(dtype_val, list):
                all_types.update(dtype_val)
            elif isinstance(dtype_val, str):
                all_types.add(dtype_val)

    assert "sc:Text" in all_types
    assert "sc:Boolean" in all_types
    assert "cr:Float64" in all_types
    assert "cr:Float32" in all_types
    assert "cr:Int32" in all_types
    assert "cr:Int16" in all_types

    # Partitioned table fields must reference their FileSet
    partitioned_rs = next(rs for rs in record_sets if rs["name"] == "diseases")
    assert all("fileSet" in f["source"] for f in partitioned_rs["field"])

    # Standalone table fields must reference their FileObject
    standalone_rs = next(rs for rs in record_sets if rs["name"] == "drug_molecule")
    assert all("fileObject" in f["source"] for f in standalone_rs["field"])

    # --- Nested type assertions (credible_set table) ---
    cs_rs = next(rs for rs in record_sets if rs["name"] == "credible_set")
    cs_fields = {f["name"]: f for f in cs_rs["field"]}

    # list<string> → cr:isArray + sc:Text
    qc = cs_fields["qualityControls"]
    assert qc.get("cr:isArray") is True, "qualityControls should be marked as array"
    assert qc.get("dataType") == "sc:Text", (
        f"Expected sc:Text inner type, got {qc.get('dataType')}"
    )

    # struct<beta, pValueExponent> → subField list, no dataType at top level
    stats = cs_fields["stats"]
    assert "subField" in stats, "stats should have subField"
    sub_names = {
        sf["name"]
        for sf in (
            stats["subField"]
            if isinstance(stats["subField"], list)
            else [stats["subField"]]
        )
    }
    assert sub_names == {"beta", "pValueExponent"}, (
        f"Unexpected sub_fields: {sub_names}"
    )

    # list<struct<variantId, posteriorProbability>> → cr:isArray + subField
    locus = cs_fields["locus"]
    assert locus.get("cr:isArray") is True, "locus should be marked as array"
    assert "subField" in locus, "locus should have subField (inner struct)"
    locus_sub_names = {
        sf["name"]
        for sf in (
            locus["subField"]
            if isinstance(locus["subField"], list)
            else [locus["subField"]]
        )
    }
    assert locus_sub_names == {"variantId", "posteriorProbability"}, (
        f"Unexpected locus sub_fields: {locus_sub_names}"
    )


# ---------------------------------------------------------------------------
# Real Open Targets subset (committed Parquet, no download required)
# ---------------------------------------------------------------------------


@pytest.fixture
def open_targets_subset_path() -> Path:
    """Path to the committed Open Targets subset (drug_warning, so, disease_hpo)."""
    dataset_path = Path(__file__).parent / "data" / "input" / "open_targets_subset"
    if not dataset_path.exists():
        pytest.skip(f"Open Targets subset not found at {dataset_path}")
    return dataset_path


def test_open_targets_subset(open_targets_subset_path: Path, output_dir: Path) -> None:
    """Test generation and mlcroissant validation on real Open Targets Parquet data.

    Uses a small committed subset (drug_warning, so, disease_hpo) to verify that
    Croissant Baker handles real-world schemas including nested structs with
    reserved-name subfields and produces output that passes mlcroissant validation.
    """
    output_file = output_dir / "open_targets_subset_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(open_targets_subset_path),
            "-o",
            str(output_file),
            "--name",
            "Open Targets Platform (subset)",
            "--creator",
            "Open Targets",
            "--description",
            "Subset of Open Targets Platform for testing",
            "--url",
            "https://platform.opentargets.org",
            "--license",
            "https://platform-docs.opentargets.org/licence",
            "--date-published",
            "2025-01-01",
            "--citation",
            "Open Targets. (2025). Open Targets Platform (subset).",
        ],
    )

    assert result.exit_code == 0, f"Command failed:\n{result.stdout}"
    assert output_file.exists(), "Output file was not created"

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "Open Targets Platform (subset)"

    record_sets = metadata["recordSet"]
    rs_names = {rs["name"] for rs in record_sets}
    assert rs_names == {"drug_warning", "so", "disease_hpo"}

    rs_by_name = {rs["name"]: rs for rs in record_sets}

    # drug_warning: 11 columns including nested struct with 'source' subfield
    dw_fields = rs_by_name["drug_warning"]["field"]
    dw_field_names = {f["name"] for f in dw_fields}
    assert "references" in dw_field_names
    assert len(dw_fields) == 11

    # so: 2 flat columns
    so_fields = rs_by_name["so"]["field"]
    assert len(so_fields) == 2

    # disease_hpo: 6 columns with list types
    dhpo_fields = rs_by_name["disease_hpo"]["field"]
    assert len(dhpo_fields) == 6


# ---------------------------------------------------------------------------
# MIMIC-IV FHIR Demo (NDJSON bulk export, gzip-compressed)
# ---------------------------------------------------------------------------


@pytest.fixture
def mimiciv_fhir_demo_path() -> Path:
    """Path to MIMIC-IV FHIR Demo dataset (fhir/ subdirectory).

    Download with:
        wget -r -N -c -np https://physionet.org/files/mimic-iv-fhir-demo/2.1.0/ \\
             -P tests/data/input/mimiciv_fhir/
    """
    dataset_path = (
        Path(__file__).parent
        / "data"
        / "input"
        / "mimiciv_fhir"
        / "physionet.org"
        / "files"
        / "mimic-iv-fhir-demo"
        / "2.1.0"
        / "fhir"
    )
    if not dataset_path.exists():
        pytest.skip(f"MIMIC-IV FHIR demo not found at {dataset_path}")
    return dataset_path


def test_mimiciv_fhir_demo_generation(
    mimiciv_fhir_demo_path: Path, output_dir: Path
) -> None:
    """End-to-end metadata generation for MIMIC-IV FHIR Demo (NDJSON bulk export)."""
    output_file = output_dir / "mimiciv_fhir_demo_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(mimiciv_fhir_demo_path),
            "-o",
            str(output_file),
            "--name",
            "MIMIC-IV Clinical Database Demo on FHIR",
            "--description",
            "100-patient demo of MIMIC-IV represented as FHIR R4 NDJSON resources",
            "--url",
            "https://physionet.org/content/mimic-iv-fhir-demo/2.1.0/",
            "--license",
            "https://physionet.org/content/mimic-iv-fhir-demo/2.1.0/LICENSE.txt",
            "--dataset-version",
            "2.1.0",
            "--date-published",
            "2023-10-23",
            "--creator",
            "Alistair Johnson,aewj@mit.edu,https://physionet.org/",
            "--creator",
            "Lucas Bulgarelli,,https://mit.edu/",
            "--citation",
            "Johnson, A., Bulgarelli, L., et al. (2023). MIMIC-IV Clinical Database Demo on FHIR (version 2.1.0). PhysioNet. https://doi.org/10.13026/t74w-kd56",
        ],
    )

    assert result.exit_code == 0, f"Command failed:\n{result.stdout}"
    assert output_file.exists()

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "MIMIC-IV Clinical Database Demo on FHIR"
    assert metadata["version"] == "2.1.0"

    # 30 NDJSON files produce 30 FileObjects plus FileSets for multi-file resource
    # types (e.g. several files share resourceType "Observation").
    # RecordSets are one per unique FHIR resourceType, not one per file.
    assert len(metadata["distribution"]) >= 30
    assert len(metadata["recordSet"]) >= 10

    # RecordSet names should be FHIR resource types, not raw filenames
    rs_names = {rs["name"] for rs in metadata["recordSet"]}
    assert "Patient" in rs_names
    assert "Observation" in rs_names
    assert "Condition" in rs_names
    assert "Encounter" in rs_names

    # Patient RecordSet should have meaningful fields with correct types
    patient_rs = next(rs for rs in metadata["recordSet"] if rs["name"] == "Patient")
    field_names = {f["name"] for f in patient_rs.get("field", [])}
    assert "id" in field_names
    assert "gender" in field_names

    patient_fields = {f["name"]: f for f in patient_rs.get("field", [])}
    assert "sc:Date" in patient_fields.get("birthDate", {}).get("dataType", [])


# ---------------------------------------------------------------------------
# GH Archive demo (committed JSONL.GZ fixture — no download required)
# ---------------------------------------------------------------------------


@pytest.fixture
def gharchive_demo_path() -> Path:
    """Path to the committed GH Archive demo fixture (200 events, 60 KB gzipped).

    The fixture is a 200-line slice of the 2023-01-01-0.json.gz hourly archive
    from https://www.gharchive.org/.  It covers 11 distinct GitHub event types
    (PushEvent, PullRequestEvent, IssuesEvent, …) with heterogeneous nested
    payloads — a good stress-test for JSONHandler schema inference.

    To regenerate:
        wget https://data.gharchive.org/2023-01-01-0.json.gz -O /tmp/gh.json.gz
        gzcat /tmp/gh.json.gz | head -200 | gzip > \\
            tests/data/input/gharchive_demo/2023-01-01-0.jsonl.gz
    """
    dataset_path = Path(__file__).parent / "data" / "input" / "gharchive_demo"
    if not dataset_path.exists() or not list(dataset_path.glob("*.jsonl.gz")):
        pytest.skip(f"GH Archive demo fixture not found at {dataset_path}")
    return dataset_path


def test_gharchive_demo_generation(gharchive_demo_path: Path, output_dir: Path) -> None:
    """End-to-end metadata generation for a GH Archive JSONL.GZ slice.

    Verifies that JSONHandler correctly handles:
    - Compressed JSONL streaming (.jsonl.gz)
    - Heterogeneous nested payloads (different schemas per event type)
    - Non-FHIR JSON routed to JSONHandler, not FHIRHandler
    """
    output_file = output_dir / "gharchive_demo_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(gharchive_demo_path),
            "-o",
            str(output_file),
            "--name",
            "GH Archive (demo slice)",
            "--description",
            "200-event slice of GitHub Archive hourly JSONL export (2023-01-01 00:00 UTC)",
            "--url",
            "https://www.gharchive.org/",
            "--license",
            "https://creativecommons.org/licenses/by/4.0/",
            "--dataset-version",
            "2023-01-01",
            "--date-published",
            "2023-01-01",
            "--creator",
            "GitHub Archive,,,https://www.gharchive.org/",
        ],
    )

    assert result.exit_code == 0, f"Command failed:\n{result.stdout}"
    assert output_file.exists()

    with open(output_file) as f:
        metadata = json.load(f)

    assert metadata["name"] == "GH Archive (demo slice)"

    # One JSONL.GZ file → one FileObject, one RecordSet
    assert len(metadata["distribution"]) == 1
    assert len(metadata["recordSet"]) == 1

    rs = metadata["recordSet"][0]
    field_names = {f["name"] for f in rs.get("field", [])}

    # All GH Archive events share these top-level keys
    assert "id" in field_names
    assert "type" in field_names
    assert "actor" in field_names
    assert "repo" in field_names
    assert "created_at" in field_names

    # created_at should be inferred as sc:DateTime (ISO 8601)
    created_at = next(f for f in rs["field"] if f["name"] == "created_at")
    assert "sc:DateTime" in created_at.get("dataType", [])

    # actor and repo are nested objects → should produce subField entries
    actor_field = next((f for f in rs["field"] if f["name"] == "actor"), None)
    assert actor_field is not None
    assert "subField" in actor_field, "actor should expand to subFields (nested struct)"


# ---------------------------------------------------------------------------
# UniProt TSV — real-world TSV end-to-end
# ---------------------------------------------------------------------------
#
# Dataset: UniProt reviewed human proteins (Swiss-Prot)
# Columns: 14 — integers (Length, Mass), free text, GO terms, empty cells
# To refresh:
#   curl "https://rest.uniprot.org/uniprotkb/search?query=reviewed:true+AND+organism_id:9606&format=tsv&size=200&fields=accession,id,protein_name,gene_names,organism_name,length,mass,sequence_version,ft_signal,go_p,go_f,go_c,cc_subcellular_location,feature_count" \
#     -o tests/data/input/uniprot_demo/uniprot_human_reviewed_200.tsv


@pytest.fixture
def uniprot_demo_path() -> Path:
    p = Path(__file__).parent / "data" / "input" / "uniprot_demo"
    if not p.exists():
        pytest.skip("UniProt demo dataset not found")
    return p


def test_uniprot_tsv_generation(uniprot_demo_path: Path, output_dir: Path) -> None:
    """End-to-end generation from a real-world TSV dataset (UniProt Swiss-Prot).

    Tests: TSVHandler type inference, correct MIME type, Croissant output structure,
    and that integer columns (Length, Mass) are not inferred as strings.
    """
    output_file = output_dir / "uniprot_demo_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(uniprot_demo_path),
            "-o",
            str(output_file),
            "--name",
            "UniProt Human Reviewed (demo)",
            "--description",
            "200 reviewed human proteins from UniProt Swiss-Prot",
            "--url",
            "https://www.uniprot.org/uniprotkb?query=reviewed:true&organism_id=9606",
            "--date-published",
            "2025-01-01",
            "--creator",
            "UniProt Consortium",
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    assert output_file.exists()

    with open(output_file) as f:
        metadata = json.load(f)

    record_sets = metadata.get("recordSet", [])
    assert len(record_sets) == 1

    fields = {
        f["name"]: (
            [f["dataType"]]
            if isinstance(f.get("dataType"), str)
            else f.get("dataType", [])
        )
        for f in record_sets[0].get("field", [])
    }
    assert len(fields) == 14

    # Length and Mass are integers in UniProt — must not fall back to sc:Text
    assert any("Int" in t for t in fields.get("Length", [])), fields.get("Length")
    assert any("Int" in t for t in fields.get("Mass", [])), fields.get("Mass")


@pytest.fixture
def spect_demo_path() -> Path:
    p = Path(__file__).parent / "data" / "input" / "spect_demo"
    if not p.exists():
        pytest.skip("SPECT demo dataset not found")
    return p


def test_spect_demo_generation(spect_demo_path: Path, output_dir: Path) -> None:
    """End-to-end bake on a mixed-format real-world dataset (DICOM + NIfTI).

    The fixture mirrors the source dataset's own DICOM/ and NIfTI/
    subdirectories. One bake exercises both handlers concurrently and
    must produce two FileSets and two RecordSets with the correct
    per-format fields and encoding formats.
    """
    output_file = output_dir / "spect_demo_croissant.jsonld"

    result = runner.invoke(
        app,
        [
            "-i",
            str(spect_demo_path),
            "-o",
            str(output_file),
            "--name",
            "Myocardial Perfusion SPECT (demo subset)",
            "--description",
            "Mixed DICOM + NIfTI subset of the PhysioNet open-access SPECT database",
            "--url",
            "https://physionet.org/content/myocardial-perfusion-spect/1.0.0/",
            "--license",
            "https://creativecommons.org/licenses/by/4.0/",
            "--date-published",
            "2025-04-26",
            "--creator",
            "Calixto et al.",
            "--publisher",
            "PhysioNet",
            "--keywords",
            "spect,cardiac,nuclear-medicine,segmentation-mask",
            "--no-validate",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    assert output_file.exists()

    with open(output_file) as f:
        metadata = json.load(f)

    distribution = metadata.get("distribution", [])
    if not isinstance(distribution, list):
        distribution = [distribution]
    file_objects = [d for d in distribution if d.get("@type") == "cr:FileObject"]
    file_sets_by_id = {
        d["@id"]: d for d in distribution if d.get("@type") == "cr:FileSet"
    }

    # 3 DICOM + 3 NIfTI = 6 FileObjects, one FileSet each.
    assert len(file_objects) == 6
    assert set(file_sets_by_id) == {"dicom-files", "nifti-files"}
    # The handler reports its own format; the generator adds the wrapper, so the
    # gzipped NIfTI set carries both rather than the fused x-nifti+gzip.
    assert file_sets_by_id["dicom-files"]["encodingFormat"] == "application/dicom"
    assert file_sets_by_id["nifti-files"]["encodingFormat"] == [
        "application/x-nifti",
        "application/gzip",
    ]
    assert "NM (3)" in file_sets_by_id["dicom-files"]["description"]
    assert "70x70x50" in file_sets_by_id["nifti-files"]["description"]

    record_sets = metadata.get("recordSet", [])
    if not isinstance(record_sets, list):
        record_sets = [record_sets]
    rs_by_name = {r["name"]: r for r in record_sets}
    assert set(rs_by_name) == {"dicom", "nifti"}

    dicom_fields = {f["name"] for f in rs_by_name["dicom"]["field"]}
    assert {
        "modality",
        "rows",
        "columns",
        "patient_id",
        "study_instance_uid",
        "series_instance_uid",
    } <= dicom_fields

    nifti_fields = {f["name"] for f in rs_by_name["nifti"]["field"]}
    assert {
        "dim_x",
        "dim_y",
        "dim_z",
        "voxel_spacing",
        "data_dtype",
        "nifti_version",
    } <= nifti_fields
    assert "tr_seconds" not in nifti_fields  # no 4D file in this fixture


# ---------------------------------------------------------------------------
# GEO SOFT
# ---------------------------------------------------------------------------


@pytest.fixture
def geo_soft_path() -> Path:
    """Three real NCBI GEO family exports; see the directory's README.

    A hard failure rather than a skip: the fixtures are committed, so they can
    only go missing by accident, and skipping would hide the loss.
    """
    dataset_path = Path(__file__).parent / "data" / "input" / "geo_soft"
    assert dataset_path.is_dir(), f"tracked GEO SOFT fixtures missing at {dataset_path}"
    return dataset_path


def test_geo_soft_generation(geo_soft_path: Path, tmp_path: Path) -> None:
    """The whole CLI over three real deposits, compared against the committed
    document rather than overwriting it.

    Both shapes in one bake: two modern tableless exports read through their
    gzip wrappers, and one classic series whose tables carry three column
    signatures. The fixture README says how to regenerate the golden.
    """
    output_file = tmp_path / "geo_soft_croissant.jsonld"
    golden = Path(__file__).parent / "data" / "output" / "geo_soft_croissant.jsonld"
    assert golden.is_file(), f"tracked GEO SOFT golden missing at {golden}"

    result = runner.invoke(
        app,
        [
            "-i",
            str(geo_soft_path),
            "-o",
            str(output_file),
            "--name",
            "GEO SOFT family exports (demo)",
            "--description",
            "Three NCBI GEO family exports: two modern tableless deposits and one classic series with data tables",
            "--url",
            "https://www.ncbi.nlm.nih.gov/geo/",
            "--date-published",
            "2026-08-31",
            "--creator",
            "NCBI Gene Expression Omnibus",
        ],
    )

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    assert "Scanned 4 file(s): 3 described, 1 not described" in result.output
    assert output_file.read_text() == golden.read_text()


def test_a_stem_shared_with_another_format_suffixes_both_sides(
    tmp_path: Path,
) -> None:
    """`GSE1_family.soft` derives the record set `GSE1_family_samples`, which
    is also what the CSV sitting beside it is called.

    The two are in different handler batches, so the collision is invisible to
    either handler's allocator and is settled by the generator, which suffixes
    *every* member of a colliding group — the same rule that already turns
    `sample.csv` and `sample.tsv` into `sample_csv` and `sample_tsv` rather
    than letting either keep the bare stem. Asserted here because the design
    note this handler was written from claimed the CSV would keep
    `GSE1_family_samples`, and it does not.
    """
    (tmp_path / "GSE1_family.soft").write_text(
        "^SAMPLE = GSM1\n!Sample_title = a sample\n"
    )
    (tmp_path / "GSE1_family_samples.csv").write_text("id,name\n1,Ada\n")

    output = tmp_path / "out.jsonld"
    result = runner.invoke(
        app, ["-i", str(tmp_path), "-o", str(output), "--creator", "Tester"]
    )

    assert result.exit_code == 0, result.output
    with open(output) as f:
        ids = {rs["@id"] for rs in json.load(f)["recordSet"]}
    assert ids == {"GSE1_family_samples_csv", "GSE1_family_samples_soft"}
