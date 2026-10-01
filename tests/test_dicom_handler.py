"""Tests for DICOM file handler."""

from pathlib import Path

import numpy as np
import pytest
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian, RLELossless

from croissant_baker.handlers.dicom_handler import (
    DICOMHandler,
    collect_dicom_summary,
)
from croissant_baker.sources import make_source

from tests.helpers import bake, bake_validated, record_sets, write_wrapped


def _make_dicom(
    path: Path,
    modality: str = "CT",
    rows: int = 512,
    columns: int = 512,
    num_frames: int = 1,
    bits: int = 16,
) -> Path:
    """Write a minimal valid DICOM file to *path* and return it."""
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\x00" * 128)

    ds.Modality = modality
    ds.Rows = rows
    ds.Columns = columns
    ds.NumberOfFrames = num_frames
    ds.BitsAllocated = bits
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelSpacing = [0.5, 0.5]
    ds.SliceThickness = 1.5
    ds.Manufacturer = "TestMaker"
    ds.StudyDescription = "Test Study"
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = generate_uid()

    pydicom.dcmwrite(str(path), ds)
    return path


@pytest.fixture
def handler() -> DICOMHandler:
    return DICOMHandler()


@pytest.fixture
def dicom_file(tmp_path: Path) -> Path:
    return _make_dicom(tmp_path / "test.dcm")


def test_can_handle_magic_bytes(handler: DICOMHandler, tmp_path: Path) -> None:
    """Files with no extension but valid DICOM magic bytes are accepted."""
    no_ext = tmp_path / "dicom_no_ext"
    _make_dicom(tmp_path / "tmp.dcm")
    src = tmp_path / "tmp.dcm"
    no_ext.write_bytes(src.read_bytes())
    assert handler.claims(make_source(no_ext)) is True


def test_cannot_handle_non_dicom_no_extension(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = tmp_path / "notdicom"
    f.write_bytes(b"\x00" * 132 + b"NOPE")
    assert handler.claims(make_source(f)) is False


def test_cannot_handle_dcm_extension_without_preamble(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    """A .dcm file that lacks the DICM preamble (e.g. a DICOMDIR fragment) is rejected."""
    f = tmp_path / "fragment.dcm"
    f.write_bytes(b"\x00" * 132 + b"NOPE")
    assert handler.claims(make_source(f)) is False


def test_extract_metadata(handler: DICOMHandler, dicom_file: Path) -> None:
    meta = handler.extract(make_source(dicom_file))

    assert meta["file_name"] == "test.dcm"
    assert meta["encoding_format"] == "application/dicom"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64

    props = meta["dicom_properties"]
    assert props["rows"] == 512
    assert props["columns"] == 512
    assert props["num_frames"] == 1
    assert props["bits_allocated"] == 16
    assert props["modality"] == "CT"
    assert props["photometric_interpretation"] == "MONOCHROME2"
    assert props["slice_thickness"] == pytest.approx(1.5)
    assert props["manufacturer"] == "TestMaker"


def test_extract_metadata_mr(handler: DICOMHandler, tmp_path: Path) -> None:
    f = _make_dicom(tmp_path / "mr.dcm", modality="MR", rows=256, columns=256, bits=12)
    meta = handler.extract(make_source(f))
    props = meta["dicom_properties"]
    assert props["modality"] == "MR"
    assert props["rows"] == 256
    assert props["bits_allocated"] == 12


def test_extract_metadata_multiframe(handler: DICOMHandler, tmp_path: Path) -> None:
    f = _make_dicom(tmp_path / "cine.dcm", num_frames=30)
    meta = handler.extract(make_source(f))
    assert meta["dicom_properties"]["num_frames"] == 30


def test_collect_dicom_summary() -> None:
    metas = [
        {
            "dicom_properties": {
                "rows": 512,
                "columns": 512,
                "num_frames": 1,
                "bits_allocated": 16,
                "modality": "CT",
            }
        },
        {
            "dicom_properties": {
                "rows": 256,
                "columns": 256,
                "num_frames": 30,
                "bits_allocated": 12,
                "modality": "MR",
            }
        },
        {
            "dicom_properties": {
                "rows": 512,
                "columns": 512,
                "num_frames": 1,
                "bits_allocated": 16,
                "modality": "CT",
            }
        },
    ]
    summary = collect_dicom_summary(metas)

    assert summary["num_files"] == 3
    assert summary["rows_range"] == (256, 512)
    assert summary["columns_range"] == (256, 512)
    assert summary["frames_range"] == (1, 30)
    assert summary["modality_counts"] == {"CT": 2, "MR": 1}
    assert set(summary["bits_allocated_values"]) == {12, 16}


def _dicom_meta(
    name: str, modality: str = "CT", rows: int = 512, cols: int = 512
) -> dict:
    return {
        "file_name": name,
        "encoding_format": "application/dicom",
        "dicom_properties": {
            "rows": rows,
            "columns": cols,
            "num_frames": 1,
            "bits_allocated": 16,
            "modality": modality,
        },
    }


def test_build_croissant_returns_fileset_and_recordset(handler: DICOMHandler) -> None:
    metas = [_dicom_meta("a.dcm"), _dicom_meta("b.dcm")]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(filesets) == 1
    assert len(record_sets) == 1


def test_build_croissant_fileset_includes(handler: DICOMHandler) -> None:
    metas = [_dicom_meta("a.dcm")]
    filesets, _ = handler.build_croissant(metas, ["file_0"])
    assert "**/*.dcm" in filesets[0].includes


def test_build_croissant_recordset_name(handler: DICOMHandler) -> None:
    metas = [_dicom_meta("a.dcm")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    assert record_sets[0].name == "dicom"


def test_build_croissant_fields(handler: DICOMHandler) -> None:
    metas = [_dicom_meta("a.dcm")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    field_names = {f.name for f in record_sets[0].fields}
    assert {
        "modality",
        "rows",
        "columns",
        "num_frames",
        "bits_allocated",
    } <= field_names


def test_build_croissant_description_contains_modality(handler: DICOMHandler) -> None:
    metas = [_dicom_meta("a.dcm", modality="PT")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    assert "PT" in record_sets[0].description


def test_the_modality_breakdown_does_not_follow_discovery_order(
    handler: DICOMHandler,
) -> None:
    """Batch order is rglob order, which differs between filesystems, so a
    mixed batch listed in the order it arrived describes one directory two
    ways. The committed SPECT corpus holds one modality, so no golden can
    catch this."""
    metas = [
        _dicom_meta("a.dcm", modality="MR"),
        _dicom_meta("b.dcm", modality="CT"),
        _dicom_meta("c.dcm", modality="CT"),
        {"file_name": "d.dcm", "dicom_properties": {"rows": 512, "columns": 512}},
    ]
    ids = ["file_0", "file_1", "file_2", "file_3"]

    forward = handler.build_croissant(metas, ids)
    backward = handler.build_croissant(metas[::-1], ids[::-1])

    for built in (forward, backward):
        assert built.file_sets[0].description == (
            "4 DICOM file(s) (CT (2), MR (1), no modality (1))"
        )
        assert built.record_sets[0].description == (
            "4 DICOM files (512x512): CT (2), MR (1), no modality (1)"
        )


def test_files_without_a_modality_are_counted_last() -> None:
    """A writer's own lowercase code sorts after the uppercase ones, and the
    files with no Modality must still close the list whatever the codes
    spell."""
    metas = [
        {"dicom_properties": {}},
        {"dicom_properties": {"modality": "xa"}},
        {"dicom_properties": {"modality": "CT"}},
    ]

    summary = collect_dicom_summary(metas)

    assert list(summary["modality_counts"]) == ["CT", "xa", None]
    assert sum(summary["modality_counts"].values()) == summary["num_files"]


@pytest.mark.parametrize("reverse", [False, True])
def test_a_modality_spelled_unknown_is_kept_apart_from_missing_ones(
    handler: DICOMHandler, reverse: bool
) -> None:
    """Lowercase "unknown" is not a valid CS value, but the handler reads
    what is there. Its count must not replace the count of files with no
    Modality, and together they must add up to num_files."""
    metas = [
        _dicom_meta("a.dcm", modality="unknown"),
        {"file_name": "b.dcm", "dicom_properties": {"rows": 512, "columns": 512}},
        _dicom_meta("c.dcm", modality="CT"),
    ]
    ids = ["file_0", "file_1", "file_2"]
    if reverse:
        metas, ids = metas[::-1], ids[::-1]

    summary = collect_dicom_summary(metas)
    counts = summary["modality_counts"]
    built = handler.build_croissant(metas, ids)

    assert list(counts.items()) == [("CT", 1), ("unknown", 1), (None, 1)]
    assert sum(counts.values()) == summary["num_files"]
    assert built.file_sets[0].description == (
        "3 DICOM file(s) (CT (1), unknown (1), no modality (1))"
    )


def test_a_mixed_modality_bake_describes_itself_one_way(
    tmp_path: Path, reverse_discovery: bool
) -> None:
    """The same directory, reached in either order, bakes to one description."""
    _make_dicom(tmp_path / "a.dcm", modality="MR")
    _make_dicom(tmp_path / "b.dcm", modality="CT")
    _make_dicom(tmp_path / "c.dcm", modality="CT")

    document = bake(tmp_path)

    (record_set,) = document["recordSet"]
    assert record_set["description"] == "3 DICOM files (512x512): CT (2), MR (1)"


def test_a_bake_says_how_many_dcm_files_lacked_the_preamble(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A directory of DICOMDIR fragments and broken exports bakes the valid
    files and says, once, how many it passed over. Without the line, a
    half-described directory looks complete."""
    _make_dicom(tmp_path / "good.dcm")
    (tmp_path / "fragment_a.dcm").write_bytes(b"\x00" * 256)
    (tmp_path / "fragment_b.dcm").write_bytes(b"random bytes that are not dicom")

    bake(tmp_path)

    assert (
        "skipped 2 DICOM file(s) without the DICM preamble" in capsys.readouterr().out
    )


#: Every header field the DICOM record set declares: Croissant type and the
#: tag its description names. A consumer reads the value with a DICOM reader.
DICOM_HEADER_FIELDS = {
    "dicom/modality": ("sc:Text", "DICOM Modality (0008,0060)"),
    "dicom/rows": ("sc:Integer", "DICOM Rows (0028,0010)"),
    "dicom/columns": ("sc:Integer", "DICOM Columns (0028,0011)"),
    "dicom/num_frames": ("sc:Integer", "DICOM NumberOfFrames (0028,0008)"),
    "dicom/bits_allocated": ("sc:Integer", "DICOM BitsAllocated (0028,0100)"),
    "dicom/patient_id": ("sc:Text", "DICOM PatientID (0010,0020)"),
    "dicom/study_instance_uid": ("sc:Text", "DICOM StudyInstanceUID (0020,000D)"),
    "dicom/series_instance_uid": (
        "sc:Text",
        "DICOM SeriesInstanceUID (0020,000E)",
    ),
}


def _make_rle_dicom(path: Path) -> Path:
    """Write a DICOM whose pixel data is RLE Lossless encapsulated."""
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.Modality = "CT"
    ds.Rows = 4
    ds.Columns = 6
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.SOPInstanceUID = generate_uid()
    ds.compress(RLELossless, np.zeros((4, 6), dtype=np.uint16))
    ds.save_as(str(path), enforce_file_format=True)
    return path


def test_build_croissant_header_fields_name_the_file_set_without_an_extract(
    handler: DICOMHandler,
) -> None:
    """A content extract would hand a consumer the whole file for a field that
    describes one tag, so each header field names only the FileSet."""
    _, record_sets_ = handler.build_croissant([_dicom_meta("a.dcm")], ["file_0"])

    sources = {f.id: f.source.to_json() for f in record_sets_[0].fields}

    assert sources == {
        field_id: {"fileSet": {"@id": "dicom-files"}}
        for field_id in DICOM_HEADER_FIELDS
    }


def test_compressed_dicom_header_fields_keep_their_schema_without_an_extract(
    tmp_path: Path,
) -> None:
    """RLE pixel data and a gzip wrapper change nothing about the header
    fields. The files carry no PatientID or instance UIDs, and those fields
    are declared all the same."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    _make_rle_dicom(dataset / "rle.dcm")
    plain = _make_dicom(tmp_path / "plain.dcm")
    write_wrapped(dataset, "wrapped.dcm", plain.read_bytes(), ".gz")

    doc = bake_validated(dataset)

    (dicom,) = record_sets(doc)
    fields = {f["@id"]: f for f in dicom["field"]}
    assert set(fields) == set(DICOM_HEADER_FIELDS)
    for field_id, (data_type, description) in DICOM_HEADER_FIELDS.items():
        field = fields[field_id]
        assert field["source"] == {"fileSet": {"@id": "dicom-files"}}, field_id
        assert field["dataType"] == data_type, field_id
        assert field["description"].startswith(description), field_id
        assert field["name"] == field_id.split("/", 1)[1]
        assert "isArray" not in field, field_id
    assert dicom["description"] == "2 DICOM files (4-512x6-512): CT (2)"
