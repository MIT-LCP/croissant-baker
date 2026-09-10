"""Tests for DICOM file handler."""

from pathlib import Path

import pytest
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

from croissant_baker.handlers.dicom_handler import (
    DICOMHandler,
    collect_dicom_summary,
)
from croissant_baker.sources import make_source

from tests.helpers import bake


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


WSI_SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.77.1.6"


def _make_wsi_dicom(
    path: Path,
    flavor: str = "VOLUME",
    total_columns: int = 98304,
    total_rows: int = 65536,
    imaged_volume_width: float = 24.5,
    imaged_volume_height: float = 16.4,
    container_identifier: str = "SLIDE-0001",
    optical_paths: int = 1,
    num_frames: int = 12,
    pixel_spacing=(0.00025, 0.00025),
    top_level_pixel_spacing=None,
) -> Path:
    """Write a minimal VL Whole Slide Microscopy Image instance to *path*.

    A real slide is a tiled multi-frame pyramid of gigabytes; everything the
    handler reads lives in the header, so the synthetic instance carries the
    header tags and no frames at all.
    """
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = WSI_SOP_CLASS_UID
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\x00" * 128)

    ds.SOPClassUID = WSI_SOP_CLASS_UID
    ds.SOPInstanceUID = generate_uid()
    ds.Modality = "SM"
    ds.ImageType = ["ORIGINAL", "PRIMARY", flavor, "NONE"]
    ds.Rows = 512
    ds.Columns = 512
    ds.NumberOfFrames = num_frames
    ds.BitsAllocated = 8
    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = "YBR_FULL_422"
    ds.Manufacturer = "TestScanner"

    if total_columns is not None:
        ds.TotalPixelMatrixColumns = total_columns
    if total_rows is not None:
        ds.TotalPixelMatrixRows = total_rows
    if imaged_volume_width is not None:
        ds.ImagedVolumeWidth = imaged_volume_width
    if imaged_volume_height is not None:
        ds.ImagedVolumeHeight = imaged_volume_height
    if container_identifier is not None:
        ds.ContainerIdentifier = container_identifier
    if top_level_pixel_spacing is not None:
        ds.PixelSpacing = list(top_level_pixel_spacing)

    if pixel_spacing is not None:
        measures = Dataset()
        measures.PixelSpacing = list(pixel_spacing)
        shared = Dataset()
        shared.PixelMeasuresSequence = [measures]
        ds.SharedFunctionalGroupsSequence = [shared]

    if optical_paths:
        ds.OpticalPathSequence = [Dataset() for _ in range(optical_paths)]

    pydicom.dcmwrite(str(path), ds)
    return path


def test_a_whole_slide_instance_reports_the_image_flavor_from_image_type(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = _make_wsi_dicom(tmp_path / "label.dcm", flavor="LABEL")
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["wsi_flavor"] == "LABEL"


def test_a_whole_slide_instance_reports_the_total_pixel_matrix_size(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = _make_wsi_dicom(tmp_path / "slide.dcm", total_columns=4096, total_rows=2048)
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["total_pixel_matrix_columns"] == 4096
    assert props["total_pixel_matrix_rows"] == 2048


def test_a_whole_slide_instance_reports_the_imaged_volume_in_millimetres(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = _make_wsi_dicom(
        tmp_path / "slide.dcm", imaged_volume_width=15.0, imaged_volume_height=10.0
    )
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["imaged_volume_width"] == pytest.approx(15.0)
    assert props["imaged_volume_height"] == pytest.approx(10.0)


def test_a_whole_slide_instance_reports_the_container_identifier(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = _make_wsi_dicom(tmp_path / "slide.dcm", container_identifier="S24-12345-A")
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["container_identifier"] == "S24-12345-A"


def test_a_whole_slide_instance_counts_its_optical_paths(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = _make_wsi_dicom(tmp_path / "fluor.dcm", optical_paths=4)
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["optical_path_count"] == 4


def test_whole_slide_properties_are_none_when_the_slide_omits_them(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    """Only the SOP class is guaranteed; a LABEL image routinely omits the
    imaged volume and the container id, and must still read as a slide."""
    f = _make_wsi_dicom(
        tmp_path / "sparse.dcm",
        total_columns=None,
        total_rows=None,
        imaged_volume_width=None,
        imaged_volume_height=None,
        container_identifier=None,
        optical_paths=0,
    )
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["total_pixel_matrix_columns"] is None
    assert props["total_pixel_matrix_rows"] is None
    assert props["imaged_volume_width"] is None
    assert props["imaged_volume_height"] is None
    assert props["container_identifier"] is None
    assert props["optical_path_count"] is None


def test_a_non_whole_slide_instance_carries_no_whole_slide_keys(
    handler: DICOMHandler, dicom_file: Path
) -> None:
    """A CT slice must extract exactly the dict it extracted before whole
    slide support existed, so that every committed golden stays byte-identical."""
    props = handler.extract(make_source(dicom_file))["dicom_properties"]
    for key in (
        "wsi_flavor",
        "total_pixel_matrix_columns",
        "total_pixel_matrix_rows",
        "imaged_volume_width",
        "imaged_volume_height",
        "container_identifier",
        "optical_path_count",
    ):
        assert key not in props


def test_a_slide_reads_pixel_spacing_from_the_shared_functional_groups(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    """A slide is multi-frame, so it states its physical scale one nesting
    down; reading only the top level leaves every slide without one."""
    f = _make_wsi_dicom(tmp_path / "slide.dcm", pixel_spacing=(0.00025, 0.00025))
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["pixel_spacing"] == pytest.approx([0.00025, 0.00025])


def test_a_top_level_pixel_spacing_wins_over_the_functional_group_one(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = _make_wsi_dicom(
        tmp_path / "slide.dcm",
        pixel_spacing=(0.00025, 0.00025),
        top_level_pixel_spacing=(0.5, 0.5),
    )
    props = handler.extract(make_source(f))["dicom_properties"]
    assert props["pixel_spacing"] == pytest.approx([0.5, 0.5])


def test_a_slide_stating_no_pixel_spacing_anywhere_reports_none(
    handler: DICOMHandler, tmp_path: Path
) -> None:
    f = _make_wsi_dicom(tmp_path / "label.dcm", flavor="LABEL", pixel_spacing=None)
    props = handler.extract(make_source(f))["dicom_properties"]
    assert "pixel_spacing" not in props
