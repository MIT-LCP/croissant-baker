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
