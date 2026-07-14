"""Tests for OME-Zarr file handler."""

from pathlib import Path

import pytest

from croissant_baker.handlers.ome_zarr_handler import OMEZarrHandler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ZARR_DEMO = Path(__file__).parent / "data" / "input" / "zarr_demo"


@pytest.fixture
def handler() -> OMEZarrHandler:
    return OMEZarrHandler()


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("image.ome.zarr", True),
        ("image.OME.ZARR", True),
        ("data.ZARR", True),
        ("data.zarr", True),
        ("image.nii.gz", False),
        ("data.csv", False),
        ("scan.dcm", False),
    ],
)
def test_can_handle(handler: OMEZarrHandler, name: str, expected: bool) -> None:
    assert handler.can_handle(Path(name)) == expected


# ---------------------------------------------------------------------------
# extract_metadata
# ---------------------------------------------------------------------------


def test_extract_metadata_basic(handler: OMEZarrHandler) -> None:
    """Test that extract_metadata returns expected keys for OME-Zarr."""
    meta = handler.extract_metadata(ZARR_DEMO / "ome-zarr-0-4.zarr")

    assert meta["file_name"] == "ome-zarr-0-4.zarr"
    assert "ome_zarr_properties" in meta


def test_extract_metadata_multiscales(handler: OMEZarrHandler) -> None:
    """Test that multiscale metadata is extracted."""
    meta = handler.extract_metadata(ZARR_DEMO / "ome-zarr-0-4.zarr")
    props = meta["ome_zarr_properties"]

    assert "multiscales" in props
    assert props["ome_zarr_version"] == "0.4"


def test_extract_metadata_axes(handler: OMEZarrHandler) -> None:
    """Test that axes info is extracted."""
    meta = handler.extract_metadata(ZARR_DEMO / "ome-zarr-0-4.zarr")
    props = meta["ome_zarr_properties"]

    assert "axes" in props
    # Demo has t, c, z, y, x axes
    axis_names = [a["name"] for a in props["axes"]]
    assert "x" in axis_names
    assert "y" in axis_names


# The `omero` metadata is transitional; support for parsing in Croissant is yet TODO
#
# def test_extract_metadata_channels(handler: OMEZarrHandler) -> None:
#     """Test that channel info is extracted from omero metadata."""
#     meta = handler.extract_metadata(ZARR_DEMO / "ome-zarr-0-4.zarr")
#     props = meta["ome_zarr_properties"]

#     assert "channels" in props
#     assert len(props["channels"]) > 0


def test_extract_metadata_file_not_found(handler: OMEZarrHandler) -> None:
    with pytest.raises(FileNotFoundError):
        handler.extract_metadata(Path("/nonexistent/image.zarr"))


# ---------------------------------------------------------------------------
# build_croissant
# ---------------------------------------------------------------------------


def _ome_zarr_meta(name: str, version: str = "0.4", num_scales: int = 5) -> dict:
    """Helper to create mock OME-Zarr metadata for testing."""
    return {
        "file_name": name,
        "encoding_format": "application/x+zarr",
        "ome_zarr_properties": {
            "ome_zarr_version": version,
            "num_scales": num_scales,
            "axes": [
                {"name": "t", "type": "time"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space"},
                {"name": "y", "type": "space"},
                {"name": "x", "type": "space"},
            ],
        },
    }


def test_build_croissant_returns_fileset_and_recordset(handler: OMEZarrHandler) -> None:
    metas = [_ome_zarr_meta("a.zarr"), _ome_zarr_meta("b.zarr")]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(filesets) == 1
    assert len(record_sets) == 1


def test_build_croissant_fileset_includes(handler: OMEZarrHandler) -> None:
    metas = [_ome_zarr_meta("a.zarr")]
    filesets, _ = handler.build_croissant(metas, ["file_0"])
    assert "**/*.zarr" in filesets[0].includes


def test_build_croissant_recordset_name(handler: OMEZarrHandler) -> None:
    metas = [_ome_zarr_meta("a.zarr")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    assert record_sets[0].name == "ome_zarr"


def test_build_croissant_fields(handler: OMEZarrHandler) -> None:
    metas = [_ome_zarr_meta("a.zarr")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    field_names = {f.name for f in record_sets[0].fields}
    assert {"ome_zarr_version", "num_scales", "axes"} <= field_names


def test_build_croissant_description_contains_version(handler: OMEZarrHandler) -> None:
    metas = [_ome_zarr_meta("a.zarr", version="0.4")]
    _, record_sets = handler.build_croissant(metas, ["file_0"])
    assert "0.4" in record_sets[0].description
