"""Tests for FHIRHandler.

Unit tests use tmp_path for lightweight synthetic data.
Integration tests pointing to downloaded datasets use pytest.skip when
the data is absent.
"""

import gzip
import json
from pathlib import Path

import pytest

from croissant_baker.handlers.fhir_handler import (
    FHIRHandler,
    _is_bulk_chunk,
)
from croissant_baker.handlers.utils import infer_croissant_type as _infer_croissant_type
from croissant_baker.handlers.utils import infer_field_type as _infer_field_type
from croissant_baker.sources import make_source


def _ndjson(path: Path, records: list) -> None:
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _ndjson_gz(path: Path, records: list) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


_PATIENTS = [
    {
        "resourceType": "Patient",
        "id": f"p{i:03d}",
        "gender": "male",
        "birthDate": f"199{i}-05-12",
        "active": True,
    }
    for i in range(5)
]

_OBSERVATIONS = [
    {
        "resourceType": "Observation",
        "id": f"o{i:03d}",
        "status": "final",
        "effectiveDateTime": f"2024-01-{i + 1:02d}T10:00:00Z",
        "valueQuantity": {"value": float(60 + i), "unit": "bpm"},
        "subject": {"reference": "Patient/p001"},
    }
    for i in range(3)
]


def test_can_handle_fhir_json(tmp_path: Path) -> None:
    """A .json file with resourceType is claimed."""
    p = tmp_path / "bundle.json"
    p.write_text(
        json.dumps({"resourceType": "Bundle", "type": "collection", "entry": []})
    )
    assert FHIRHandler().claims(make_source(p)) is True


def test_can_handle_non_fhir_json(tmp_path: Path) -> None:
    """A plain JSON file without resourceType is rejected."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"name": "test", "version": "1"}))
    assert FHIRHandler().claims(make_source(p)) is False


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "sc:Boolean"),
        (42, "cr:Int64"),
        (3.14, "cr:Float64"),
        ("hello", "sc:Text"),
        ("1990-05-12", "sc:Date"),
        ("2024-01-15T10:00:00Z", "sc:DateTime"),
        ("http://loinc.org", "sc:URL"),
        ("urn:oid:1.2.3", "sc:URL"),
        ([1, 2], "sc:Text"),
        ({"a": 1}, "sc:Text"),
    ],
)
def test_infer_croissant_type(value, expected: str) -> None:
    assert _infer_croissant_type(value) == expected


def test_infer_field_type_primitive_majority() -> None:
    """A list of scalar values returns the majority Croissant type."""
    assert _infer_field_type(["2024-01-01", "2024-02-01", "hello"]) == "sc:Date"


def test_infer_field_type_struct() -> None:
    """Dict-valued records produce a nested fields schema, not a primitive type."""
    values = [
        {"value": 37.5, "unit": "Cel", "system": "http://unitsofmeasure.org"},
        {"value": 36.9, "unit": "Cel", "system": "http://unitsofmeasure.org"},
        {"value": 38.1, "unit": "Cel"},
    ]
    result = _infer_field_type(values)
    assert isinstance(result, dict), "struct values must return a dict schema"
    assert result["is_array"] is False
    fields = result["fields"]
    assert "value" in fields
    assert "unit" in fields
    assert fields["value"] == "cr:Float64"
    assert fields["unit"] == "sc:Text"


def test_infer_field_type_array_of_struct() -> None:
    """Lists containing dicts produce is_array=True with sub-field schema."""
    values = [
        [
            {"system": "http://loinc.org", "code": "8310-5"},
            {"system": "http://snomed.info", "code": "386725004"},
        ],
        [{"system": "http://loinc.org", "code": "8867-4"}],
    ]
    result = _infer_field_type(values)
    assert isinstance(result, dict), "array-of-struct must return a dict schema"
    assert result["is_array"] is True
    assert "system" in result["fields"]
    assert "code" in result["fields"]


def test_infer_field_type_array_of_primitives() -> None:
    """Lists of scalars return {"type": ..., "is_array": True} preserving cardinality."""
    values = [["tag1", "tag2"], ["tag3"]]
    result = _infer_field_type(values)
    assert isinstance(result, dict)
    assert result["is_array"] is True
    assert result["type"] == "sc:Text"


def test_extract_ndjson_basic(tmp_path: Path) -> None:
    p = tmp_path / "Patient.ndjson"
    _ndjson(p, _PATIENTS)

    meta = FHIRHandler().extract(make_source(p))

    assert meta["fhir_resource_type"] == "Patient"
    assert meta["encoding_format"] == "application/fhir+ndjson"
    assert meta["num_rows"] == 5
    assert meta["column_types"]["birthDate"] == "sc:Date"
    assert meta["column_types"]["active"] == "sc:Boolean"
    assert meta["column_types"]["id"] == "sc:Text"
    assert "resourceType" not in meta["column_types"]


def test_extract_ndjson_gz(tmp_path: Path) -> None:
    p = tmp_path / "MimicPatient.ndjson.gz"
    _ndjson_gz(p, _PATIENTS)

    meta = FHIRHandler().extract(make_source(p))

    assert meta["fhir_resource_type"] == "Patient"
    # Format media type only; the generator adds the compression one.
    assert meta["encoding_format"] == "application/fhir+ndjson"
    assert meta["num_rows"] == 5
    assert len(meta["sha256"]) == 64


def test_extract_ndjson_observation_datetime(tmp_path: Path) -> None:
    p = tmp_path / "Observation.ndjson"
    _ndjson(p, _OBSERVATIONS)

    meta = FHIRHandler().extract(make_source(p))

    assert meta["fhir_resource_type"] == "Observation"
    assert meta["column_types"]["effectiveDateTime"] == "sc:DateTime"
    assert meta["column_types"]["status"] == "sc:Text"


def test_extract_ndjson_counts_all_rows(tmp_path: Path) -> None:
    """num_rows is the true total — all records are counted and used for inference."""
    n = 500
    records = [
        {"resourceType": "Patient", "id": f"p{i}", "gender": "male"} for i in range(n)
    ]
    p = tmp_path / "large.ndjson"
    _ndjson(p, records)

    meta = FHIRHandler().extract(make_source(p))
    assert meta["num_rows"] == n


_BUNDLE = {
    "resourceType": "Bundle",
    "type": "transaction",
    "entry": [
        {
            "resource": {
                "resourceType": "Patient",
                "id": "b1",
                "gender": "male",
                "birthDate": "1980-01-01",
                "active": True,
            }
        },
        {
            "resource": {
                "resourceType": "Condition",
                "id": "c1",
                "clinicalStatus": {},
                "onsetDateTime": "2010-03-01T00:00:00Z",
                "subject": {"reference": "Patient/b1"},
            }
        },
        {
            "resource": {
                "resourceType": "Condition",
                "id": "c2",
                "clinicalStatus": {},
                "onsetDateTime": "2020-06-15T00:00:00Z",
                "subject": {"reference": "Patient/b1"},
            }
        },
        {
            "resource": {
                "resourceType": "Observation",
                "id": "o1",
                "status": "final",
                "effectiveDateTime": "2024-01-01T09:00:00Z",
                "subject": {"reference": "Patient/b1"},
            }
        },
    ],
}


def test_extract_bundle_groups_by_resource_type(tmp_path: Path) -> None:
    p = tmp_path / "patient.json"
    p.write_text(json.dumps(_BUNDLE))

    meta = FHIRHandler().extract(make_source(p))

    assert "fhir_resource_groups" in meta
    assert "column_types" not in meta
    groups = meta["fhir_resource_groups"]
    assert set(groups.keys()) == {"Patient", "Condition", "Observation"}
    assert groups["Condition"]["num_rows"] == 2
    assert groups["Patient"]["column_types"]["birthDate"] == "sc:Date"
    assert groups["Condition"]["column_types"]["onsetDateTime"] == "sc:DateTime"


def test_extract_bundle_single_resource_returns_column_types(tmp_path: Path) -> None:
    """A .json containing a single resource (not Bundle) returns column_types."""
    single = {
        "resourceType": "Patient",
        "id": "x1",
        "gender": "female",
        "birthDate": "2000-01-01",
    }
    p = tmp_path / "single.json"
    p.write_text(json.dumps(single))

    meta = FHIRHandler().extract(make_source(p))

    assert "column_types" in meta
    assert meta["fhir_resource_type"] == "Patient"
    assert meta["column_types"]["birthDate"] == "sc:Date"


def test_build_croissant_bundle_skips_operation_outcome(tmp_path: Path) -> None:
    """OperationOutcome entries inside a Bundle must not produce a RecordSet."""
    bundle_with_error = {
        "resourceType": "Bundle",
        "type": "transaction-response",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "p1", "gender": "male"}},
            {
                "resource": {
                    "resourceType": "OperationOutcome",
                    "id": "err1",
                    "issue": [{"severity": "error", "code": "invalid"}],
                }
            },
        ],
    }
    p = tmp_path / "response.json"
    p.write_text(json.dumps(bundle_with_error))

    meta = FHIRHandler().extract(make_source(p))
    assert "OperationOutcome" in meta["fhir_resource_groups"], "extractor should see it"

    _, record_sets = FHIRHandler().build_croissant([meta], ["file_0"])
    rs_names = {rs.name for rs in record_sets}
    assert "OperationOutcome" not in rs_names, (
        "build_croissant must filter OperationOutcome"
    )
    assert "Patient" in rs_names


@pytest.mark.parametrize(
    "file_name,resource_type,expected",
    [
        # Standard bulk-export chunks — should merge
        ("Observation.000.ndjson", "Observation", True),
        ("Observation.001.ndjson", "Observation", True),
        ("Patient.000.ndjson", "Patient", True),
        # Distinct logical tables sharing a resourceType — must NOT merge
        ("MimicObservationLabevents.ndjson", "Observation", False),
        ("MimicObservationChartevents.ndjson", "Observation", False),
        ("MimicCondition.ndjson", "Condition", False),
        # Case-insensitive
        ("observation.000.ndjson", "Observation", True),
    ],
)
def test_is_bulk_chunk(file_name: str, resource_type: str, expected: bool) -> None:
    assert _is_bulk_chunk(file_name, resource_type) == expected


def test_build_croissant_merges_chunks(tmp_path: Path) -> None:
    """Two Observation chunk files → one FileSet + one merged RecordSet."""
    handler = FHIRHandler()
    metas = [
        {
            "file_name": "Observation.000.ndjson",
            "relative_path": "Observation.000.ndjson",
            "fhir_resource_type": "Observation",
            "column_types": {"id": "sc:Text", "status": "sc:Text"},
            "encoding_format": "application/fhir+ndjson",
            "num_rows": 10,
        },
        {
            "file_name": "Observation.001.ndjson",
            "relative_path": "Observation.001.ndjson",
            "fhir_resource_type": "Observation",
            "column_types": {
                "id": "sc:Text",
                "status": "sc:Text",
                "valueQuantity": "sc:Text",
            },
            "encoding_format": "application/fhir+ndjson",
            "num_rows": 8,
        },
    ]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(filesets) == 1
    assert len(record_sets) == 1
    assert record_sets[0].name == "Observation"
    assert "18 rows" in record_sets[0].description
    field_names = {f.name for f in record_sets[0].fields}
    assert field_names == {"id", "status", "valueQuantity"}


def test_build_croissant_keeps_distinct_tables_separate(tmp_path: Path) -> None:
    """Two files sharing resourceType but different stems → two RecordSets, no FileSet."""
    handler = FHIRHandler()
    metas = [
        {
            "file_name": "MimicObservationLabevents.ndjson",
            "relative_path": "fhir/MimicObservationLabevents.ndjson.gz",
            "fhir_resource_type": "Observation",
            "column_types": {"id": "sc:Text", "valueQuantity": "sc:Text"},
            "encoding_format": "application/gzip",
            "num_rows": 100,
        },
        {
            "file_name": "MimicObservationChartevents.ndjson",
            "relative_path": "fhir/MimicObservationChartevents.ndjson.gz",
            "fhir_resource_type": "Observation",
            "column_types": {"id": "sc:Text", "component": "sc:Text"},
            "encoding_format": "application/gzip",
            "num_rows": 50,
        },
    ]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(filesets) == 0
    assert len(record_sets) == 2
    ids = {rs.id for rs in record_sets}
    assert "MimicObservationLabevents" in ids
    assert "MimicObservationChartevents" in ids
    # Both carry the human-readable resourceType name
    assert all(rs.name == "Observation" for rs in record_sets)


def test_build_croissant_all_skipped_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All-OperationOutcome input produces empty results and logs a warning."""
    meta = {
        "file_name": "errors.ndjson",
        "fhir_resource_type": "OperationOutcome",
        "column_types": {"issue": "sc:Text"},
        "encoding_format": "application/fhir+ndjson",
        "num_rows": 3,
    }
    import logging

    with caplog.at_level(
        logging.WARNING, logger="croissant_baker.handlers.fhir_handler"
    ):
        filesets, record_sets = FHIRHandler().build_croissant([meta], ["file_0"])

    assert filesets == []
    assert record_sets == []
    assert any("no RecordSets" in msg for msg in caplog.messages)


@pytest.fixture
def mimiciv_fhir_path() -> Path:
    """Path to MIMIC-IV FHIR Demo dataset.

    Download with:
        wget -r -N -c -np https://physionet.org/files/mimic-iv-fhir-demo/2.1.0/ \\
             -P tests/data/input/mimiciv_fhir/
    """
    path = (
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
    if not path.exists():
        pytest.skip(f"MIMIC-IV FHIR demo not found at {path}")
    return path


def test_mimiciv_fhir_handler_all_files(mimiciv_fhir_path: Path) -> None:
    """Every readable .ndjson.gz in MIMIC-IV FHIR produces valid metadata."""
    handler = FHIRHandler()
    files = sorted(mimiciv_fhir_path.glob("*.ndjson.gz"))
    assert len(files) >= 25, f"Too few NDJSON files ({len(files)}) — re-run wget"

    failures = []
    for f in files:
        assert handler.claims(make_source(f))
        try:
            meta = handler.extract(make_source(f))
            assert "column_types" in meta
            assert meta["num_rows"] > 0
            assert meta["fhir_resource_type"]
            assert len(meta["sha256"]) == 64
        except Exception as exc:
            failures.append(f"{f.name}: {exc}")

    assert not failures, (
        f"{len(failures)} file(s) failed — incomplete download, re-run wget:\n"
        + "\n".join(failures)
    )


def test_mimiciv_fhir_patient_fields(mimiciv_fhir_path: Path) -> None:
    """MimicPatient.ndjson.gz contains expected FHIR Patient fields."""
    handler = FHIRHandler()
    meta = handler.extract(make_source(mimiciv_fhir_path / "MimicPatient.ndjson.gz"))

    assert meta["fhir_resource_type"] == "Patient"
    assert meta["num_rows"] == 100
    col_types = meta["column_types"]
    assert "id" in col_types
    assert "gender" in col_types
    assert col_types.get("birthDate") == "sc:Date"


def test_mimiciv_fhir_observation_labevents(mimiciv_fhir_path: Path) -> None:
    """MimicObservationLabevents.ndjson.gz handles large files (>100K records)."""
    handler = FHIRHandler()
    meta = handler.extract(
        make_source(mimiciv_fhir_path / "MimicObservationLabevents.ndjson.gz")
    )
    assert meta["fhir_resource_type"] == "Observation"
    assert meta["num_rows"] > 100_000
    assert "effectiveDateTime" in meta["column_types"]
