"""Tests for NIfTI file handler."""

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from croissant_baker.handlers.nifti_handler import (
    NIfTIHandler,
    collect_nifti_summary,
)
from croissant_baker.sources import make_source

from tests.helpers import bake_validated, record_sets


def _make_nifti(
    path: Path, shape=(64, 64, 30), zooms=(1.0, 1.0, 3.0), dtype=np.int16
) -> Path:
    """Write a minimal NIfTI-1 file to *path* and return it."""
    data = np.zeros(shape, dtype=dtype)
    affine = np.eye(4)
    img = nib.Nifti1Image(data, affine)
    img.header.set_zooms(zooms)
    nib.save(img, str(path))
    return path


def _make_nifti_4d(
    path: Path, shape=(64, 64, 30, 120), zooms=(2.0, 2.0, 3.0, 2.0)
) -> Path:
    """Write a minimal 4D fMRI NIfTI file (TR=2s) to *path* and return it."""
    data = np.zeros(shape, dtype=np.float32)
    affine = np.eye(4)
    img = nib.Nifti1Image(data, affine)
    img.header.set_zooms(zooms)
    nib.save(img, str(path))
    return path


@pytest.fixture
def handler() -> NIfTIHandler:
    return NIfTIHandler()


@pytest.fixture
def nifti_3d(tmp_path: Path) -> Path:
    return _make_nifti(tmp_path / "T1.nii.gz")


@pytest.fixture
def nifti_4d(tmp_path: Path) -> Path:
    return _make_nifti_4d(tmp_path / "bold.nii.gz")


def test_extract_metadata_3d(handler: NIfTIHandler, nifti_3d: Path) -> None:
    meta = handler.extract(make_source(nifti_3d))

    # The logical name: a wrapped file and its twin name one record set.
    assert meta["file_name"] == "T1.nii"
    # The format media type; the generator adds application/gzip alongside.
    assert meta["encoding_format"] == "application/x-nifti"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64

    props = meta["nifti_properties"]
    assert props["dim_x"] == 64
    assert props["dim_y"] == 64
    assert props["dim_z"] == 30
    assert "dim_t" not in props
    assert props["voxel_spacing_x"] == pytest.approx(1.0)
    assert props["voxel_spacing_y"] == pytest.approx(1.0)
    assert props["voxel_spacing_z"] == pytest.approx(3.0)
    assert props["ndim"] == 3
    assert props["nifti_version"] == 1


def test_extract_metadata_uncompressed_nii(
    handler: NIfTIHandler, tmp_path: Path
) -> None:
    f = _make_nifti(tmp_path / "T1.nii")
    meta = handler.extract(make_source(f))
    assert meta["encoding_format"] == "application/x-nifti"
    assert meta["nifti_properties"]["dim_z"] == 30


def test_extract_metadata_4d(handler: NIfTIHandler, nifti_4d: Path) -> None:
    meta = handler.extract(make_source(nifti_4d))
    props = meta["nifti_properties"]

    assert props["dim_x"] == 64
    assert props["dim_y"] == 64
    assert props["dim_z"] == 30
    assert props["dim_t"] == 120
    assert props["ndim"] == 4
    assert props["tr_seconds"] == pytest.approx(2.0)


def test_extract_metadata_dtype(handler: NIfTIHandler, tmp_path: Path) -> None:
    f = _make_nifti(tmp_path / "float.nii.gz", dtype=np.float32)
    meta = handler.extract(make_source(f))
    assert "float32" in meta["nifti_properties"]["data_dtype"]


def test_collect_nifti_summary_3d() -> None:
    metas = [
        {
            "nifti_properties": {
                "dim_x": 256,
                "dim_y": 256,
                "dim_z": 154,
                "ndim": 3,
                "data_dtype": "uint8",
                "voxel_spacing_x": 1.0,
                "voxel_spacing_y": 1.0,
                "voxel_spacing_z": 1.0,
                "nifti_version": 1,
            }
        },
        {
            "nifti_properties": {
                "dim_x": 128,
                "dim_y": 128,
                "dim_z": 90,
                "ndim": 3,
                "data_dtype": "int16",
                "voxel_spacing_x": 2.0,
                "voxel_spacing_y": 2.0,
                "voxel_spacing_z": 2.0,
                "nifti_version": 1,
            }
        },
    ]
    summary = collect_nifti_summary(metas)

    assert summary["num_files"] == 2
    assert summary["ndim_max"] == 3
    assert summary["dim_x_range"] == (128, 256)
    assert summary["dim_y_range"] == (128, 256)
    assert summary["dim_z_range"] == (90, 154)
    assert "dim_t_range" not in summary
    assert summary["dtype_counts"] == {"uint8": 1, "int16": 1}


def test_collect_nifti_summary_4d() -> None:
    metas = [
        {
            "nifti_properties": {
                "dim_x": 64,
                "dim_y": 64,
                "dim_z": 30,
                "dim_t": 120,
                "ndim": 4,
                "data_dtype": "float32",
                "tr_seconds": 2.0,
            }
        },
        {
            "nifti_properties": {
                "dim_x": 64,
                "dim_y": 64,
                "dim_z": 30,
                "dim_t": 200,
                "ndim": 4,
                "data_dtype": "float32",
                "tr_seconds": 1.5,
            }
        },
    ]
    summary = collect_nifti_summary(metas)

    assert summary["ndim_max"] == 4
    assert summary["dim_t_range"] == (120, 200)
    assert summary["tr_range"] == (pytest.approx(1.5), pytest.approx(2.0))


def _nifti_meta(name: str, ndim: int = 3, dim_t: int = None) -> dict:
    props = {
        "dim_x": 64,
        "dim_y": 64,
        "dim_z": 30,
        "ndim": ndim,
        "data_dtype": "int16",
        "nifti_version": 1,
        "voxel_spacing_x": 1.0,
        "voxel_spacing_y": 1.0,
        "voxel_spacing_z": 3.0,
    }
    if dim_t is not None:
        props["dim_t"] = dim_t
        props["tr_seconds"] = 2.0
    return {
        "file_name": name,
        "encoding_format": "application/x-nifti+gzip",
        "nifti_properties": props,
    }


def test_the_dtype_list_does_not_follow_discovery_order(
    handler: NIfTIHandler,
) -> None:
    """Batch order is rglob order, which differs between filesystems, so a
    mixed batch listed in the order it arrived describes one directory two
    ways. The committed SPECT corpus holds one dtype, so no golden can catch
    this."""
    metas = [_nifti_meta("a.nii.gz"), _nifti_meta("b.nii.gz")]
    metas[0]["nifti_properties"]["data_dtype"] = "uint8"
    metas[1]["nifti_properties"]["data_dtype"] = "float32"
    ids = ["file_0", "file_1"]

    forward = handler.build_croissant(metas, ids)
    backward = handler.build_croissant(metas[::-1], ids[::-1])

    for built in (forward, backward):
        record_set = built.record_sets[0]
        dtype_field = next(f for f in record_set.fields if f.name == "data_dtype")
        assert record_set.description == "2 NIfTI files (64x64x30): float32, uint8"
        assert dtype_field.description == (
            "NIfTI datatype; stored data type (float32, uint8)"
        )


def test_a_mixed_dtype_bake_describes_itself_one_way(
    tmp_path: Path, reverse_discovery: bool
) -> None:
    """The same directory, reached in either order, bakes to one description."""
    # Local, since the header field tests in #152 add their own helpers
    # import on the line a top-level one would take.
    from tests.helpers import bake

    _make_nifti(tmp_path / "a.nii", dtype=np.uint8)
    _make_nifti(tmp_path / "b.nii", dtype=np.float32)

    document = bake(tmp_path)

    (record_set,) = document["recordSet"]
    assert record_set["description"] == "2 NIfTI files (64x64x30): float32, uint8"


def test_build_croissant_returns_fileset_and_recordset(handler: NIfTIHandler) -> None:
    metas = [_nifti_meta("T1.nii.gz"), _nifti_meta("T2.nii.gz")]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(filesets) == 1
    assert len(record_sets) == 1


def test_build_croissant_fileset_includes(handler: NIfTIHandler) -> None:
    metas = [_nifti_meta("T1.nii.gz")]
    filesets, _ = handler.build_croissant(metas, ["file_0"])
    # The handler states the format glob only; the generator widens it to cover
    # every registered compression.
    assert filesets[0].includes == ["**/*.nii"]
    assert "**/*.nii" in filesets[0].includes


def test_build_croissant_recordset_name(handler: NIfTIHandler) -> None:
    metas = [_nifti_meta("T1.nii.gz")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    assert record_sets[0].name == "nifti"


def test_build_croissant_3d_fields(handler: NIfTIHandler) -> None:
    metas = [_nifti_meta("T1.nii.gz")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    field_names = {f.name for f in record_sets[0].fields}
    assert {
        "dim_x",
        "dim_y",
        "dim_z",
        "voxel_spacing",
        "data_dtype",
        "nifti_version",
    } <= field_names
    assert "tr_seconds" not in field_names


def test_build_croissant_4d_includes_tr(handler: NIfTIHandler) -> None:
    metas = [_nifti_meta("bold.nii.gz", ndim=4, dim_t=120)]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    field_names = {f.name for f in record_sets[0].fields}
    assert "tr_seconds" in field_names


#: Every header field the NIfTI record set can declare: Croissant type and the
#: header slot its description names. tr_seconds appears only for 4D batches.
NIFTI_HEADER_FIELDS = {
    "nifti/dim_x": ("sc:Integer", "NIfTI dim[1]"),
    "nifti/dim_y": ("sc:Integer", "NIfTI dim[2]"),
    "nifti/dim_z": ("sc:Integer", "NIfTI dim[3]"),
    "nifti/voxel_spacing": ("sc:Text", "NIfTI pixdim[1:4]"),
    "nifti/data_dtype": ("sc:Text", "NIfTI datatype"),
    "nifti/nifti_version": ("sc:Integer", "Inferred from sizeof_hdr"),
    "nifti/tr_seconds": ("sc:Float", "NIfTI pixdim[4]"),
}


@pytest.mark.parametrize(
    "metas",
    [
        [_nifti_meta("T1.nii.gz")],
        [_nifti_meta("T1.nii.gz"), _nifti_meta("bold.nii.gz", ndim=4, dim_t=120)],
    ],
    ids=["3d", "with_optional_tr"],
)
def test_build_croissant_header_fields_name_the_file_set_without_an_extract(
    handler: NIfTIHandler, metas: list
) -> None:
    """A content extract would hand a consumer the whole volume for a field
    that describes one header slot, so each header field names only the
    FileSet. The optional TR field follows the same shape."""
    _, record_sets_ = handler.build_croissant(
        metas, [f"file_{i}" for i in range(len(metas))]
    )

    sources = {f.id: f.source.to_json() for f in record_sets_[0].fields}

    expected_ids = set(NIFTI_HEADER_FIELDS)
    if len(metas) == 1:
        expected_ids.discard("nifti/tr_seconds")
    assert sources == {
        field_id: {"fileSet": {"@id": "nifti-files"}} for field_id in expected_ids
    }


def test_compressed_nifti_header_fields_keep_their_schema_without_an_extract(
    tmp_path: Path,
) -> None:
    """A gzipped 4D volume beside a plain 3D one: every header field, the
    optional TR included, keeps its type and description, and the manifest
    validates without a content extract."""
    _make_nifti(tmp_path / "T1.nii", dtype=np.float32)
    _make_nifti_4d(tmp_path / "bold.nii.gz", shape=(8, 8, 4, 5))

    (nifti,) = record_sets(bake_validated(tmp_path))
    fields = {f["@id"]: f for f in nifti["field"]}
    assert set(fields) == set(NIFTI_HEADER_FIELDS)
    for field_id, (data_type, description) in NIFTI_HEADER_FIELDS.items():
        field = fields[field_id]
        assert field["source"] == {"fileSet": {"@id": "nifti-files"}}, field_id
        assert field["dataType"] == data_type, field_id
        assert field["description"].startswith(description), field_id
        assert field["name"] == field_id.split("/", 1)[1]
        assert "isArray" not in field, field_id
    assert nifti["description"] == (
        "2 NIfTI files (8-64x8-64x4-30, 5 volumes): float32"
    )


@pytest.mark.parametrize(
    "shape, zooms, dims_note",
    [((5,), (1.5,), "5"), ((5, 4), (1.5, 2.0), "5x4")],
    ids=["1d", "2d"],
)
def test_a_volume_under_three_dimensions_states_only_the_axes_it_has(
    handler: NIfTIHandler, tmp_path: Path, shape, zooms, dims_note
) -> None:
    path = _make_nifti(tmp_path / "slice.nii", shape=shape, zooms=zooms)

    meta = handler.extract(make_source(path))
    props = meta["nifti_properties"]
    axes = ["x", "y", "z"][: len(shape)]
    built = handler.build_croissant([meta], ["file_0"])

    assert props["ndim"] == len(shape)
    assert {k for k in props if k.startswith("dim_")} == {f"dim_{a}" for a in axes}
    assert {k for k in props if k.startswith("voxel_spacing_")} == {
        f"voxel_spacing_{a}" for a in axes
    }
    assert f"({dims_note}): int16" in built.record_sets[0].description


def test_a_4d_volume_with_no_repetition_time_states_none(
    handler: NIfTIHandler, tmp_path: Path
) -> None:
    path = _make_nifti_4d(
        tmp_path / "bold.nii", shape=(4, 4, 2, 3), zooms=(2.0, 2.0, 3.0, 0.0)
    )

    props = handler.extract(make_source(path))["nifti_properties"]

    assert props["dim_t"] == 3
    assert "tr_seconds" not in props


def test_4d_volumes_of_different_lengths_state_the_range(
    handler: NIfTIHandler,
) -> None:
    metas = [
        _nifti_meta("short.nii.gz", ndim=4, dim_t=120),
        _nifti_meta("long.nii.gz", ndim=4, dim_t=200),
    ]

    built = handler.build_croissant(metas, ["file_0", "file_1"])

    assert "120-200 volumes" in built.record_sets[0].description


def test_an_empty_batch_summarises_to_nothing() -> None:
    assert collect_nifti_summary([]) == {}
