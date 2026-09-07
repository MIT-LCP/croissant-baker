"""Tests for image file handler."""

from pathlib import Path

import numpy as np
import pytest
import tifffile

import croissant_baker.handlers.image_handler as image_handler_module
from croissant_baker.handlers.image_handler import (
    ImageHandler,
    collect_image_summary,
)
from croissant_baker.sources import make_source


@pytest.fixture
def handler() -> ImageHandler:
    return ImageHandler()


# Minimal magic-byte stubs per supported extension. These are not full
# images — they only need enough bytes to satisfy can_handle's check.
_IMAGE_STUBS = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff\xe0",
    ".jpeg": b"\xff\xd8\xff\xe0",
    ".gif": b"GIF89a",
    ".bmp": b"BM\x00\x00\x00\x00",
    ".webp": b"RIFF\x00\x00\x00\x00WEBP",
    ".tiff": b"II*\x00",
    ".tif": b"MM\x00*",
    ".ico": b"\x00\x00\x01\x00",
}


@pytest.mark.parametrize(
    "filename",
    [
        "photo.jpg",
        "photo.jpeg",
        "photo.JPG",  # case-insensitive suffix
        "scan.png",
        "scan.PNG",
        "frame.gif",
        "icon.bmp",
        "hero.webp",
        "satellite.tiff",
        "satellite.tif",
        "satellite.TIFF",
        "image.ico",
    ],
)
def test_can_handle_accepts_supported_extensions_with_magic(
    handler: ImageHandler, tmp_path: Path, filename: str
) -> None:
    """Files whose extension is supported AND whose content matches the
    extension's magic bytes are accepted."""
    p = tmp_path / filename
    p.write_bytes(_IMAGE_STUBS[p.suffix.lower()])
    assert handler.claims(make_source(p)) is True


def test_a_renamed_file_is_declined_at_debug(
    handler: ImageHandler, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The generator owns the user-facing warning, so the handler's note is
    debug — asserted, or a regression to WARNING doubles every skip."""
    impostor = tmp_path / "fake.png"
    impostor.write_bytes(b"<!DOCTYPE html><html></html>")

    with caplog.at_level("DEBUG", logger="croissant_baker.handlers.image_handler"):
        assert handler.claims(make_source(impostor)) is False

    assert [
        r
        for r in caplog.records
        if r.levelname == "DEBUG"
        and impostor.name in r.message
        and "magic bytes" in r.message
    ], caplog.records


@pytest.fixture
def glaucoma_image_path() -> Path:
    """Path to a sample JPG from the glaucoma fundus dataset."""
    p = (
        Path(__file__).parent
        / "data"
        / "input"
        / "glaucoma_fundus"
        / "Images"
        / "0_0.jpg"
    )
    if not p.exists():
        pytest.skip(f"Glaucoma fundus image not found at {p}")
    return p


def test_extract_metadata_jpg(handler: ImageHandler, glaucoma_image_path: Path) -> None:
    meta = handler.extract(make_source(glaucoma_image_path))

    assert meta["file_name"] == "0_0.jpg"
    assert meta["encoding_format"] == "image/jpeg"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64

    props = meta["image_properties"]
    assert props["width"] > 0
    assert props["height"] > 0
    assert props["num_bands"] in (1, 3, 4)
    assert props["image_format"] == "JPEG"


@pytest.fixture
def satellite_tiff_path() -> Path:
    """Path to a sample TIFF from the satellite dataset."""
    p = (
        Path(__file__).parent
        / "data"
        / "input"
        / "satellite_public_health"
        / "images"
        / "5001"
        / "image_2016-01-03.tiff"
    )
    if not p.exists():
        pytest.skip(f"Satellite TIFF not found at {p}")
    return p


def test_extract_metadata_tiff(
    handler: ImageHandler, satellite_tiff_path: Path
) -> None:
    meta = handler.extract(make_source(satellite_tiff_path))

    assert meta["file_name"] == "image_2016-01-03.tiff"
    assert meta["encoding_format"] == "image/tiff"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64

    props = meta["image_properties"]
    assert props["width"] > 0
    assert props["height"] > 0
    # Sentinel-2 images have 12 bands
    assert props["num_bands"] == 12
    assert props["image_format"] == "TIFF"


@pytest.fixture
def separate_planar_tiff_path(tmp_path: Path) -> Path:
    """Create a multi-band TIFF that forces the tifffile fallback path."""
    path = tmp_path / "separate_planar.tiff"
    data = np.zeros((12, 5, 7), dtype=np.uint8)
    tifffile.imwrite(
        str(path),
        data,
        photometric="minisblack",
        planarconfig="separate",
    )
    return path


def test_extract_metadata_separate_planar_tiff(
    handler: ImageHandler,
    separate_planar_tiff_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for TIFFs whose band axis is stored first."""

    def _force_tifffile_fallback(_path: Path) -> None:
        raise RuntimeError("force tifffile fallback")

    monkeypatch.setattr(
        image_handler_module,
        "_read_with_pillow",
        _force_tifffile_fallback,
    )

    meta = handler.extract(make_source(separate_planar_tiff_path))

    assert meta["file_name"] == "separate_planar.tiff"
    assert meta["encoding_format"] == "image/tiff"
    assert meta["file_size"] > 0
    assert len(meta["sha256"]) == 64

    props = meta["image_properties"]
    assert props["width"] == 7
    assert props["height"] == 5
    assert props["num_bands"] == 12
    assert props["image_format"] == "TIFF"


def test_collect_image_summary() -> None:
    metas = [
        {
            "image_properties": {
                "width": 100,
                "height": 200,
                "num_bands": 3,
                "image_format": "JPEG",
            }
        },
        {
            "image_properties": {
                "width": 640,
                "height": 480,
                "num_bands": 3,
                "image_format": "JPEG",
            }
        },
        {
            "image_properties": {
                "width": 256,
                "height": 256,
                "num_bands": 12,
                "image_format": "TIFF",
            }
        },
    ]
    summary = collect_image_summary(metas)

    assert summary["num_images"] == 3
    assert summary["width_range"] == (100, 640)
    assert summary["height_range"] == (200, 480)
    assert summary["num_bands_range"] == (3, 12)
    assert summary["format_counts"] == {"JPEG": 2, "TIFF": 1}


def _img_meta(name, fmt="JPEG", mime="image/jpeg", w=100, h=100, bands=3):
    return {
        "file_name": name,
        "encoding_format": mime,
        "image_properties": {
            "width": w,
            "height": h,
            "num_bands": bands,
            "image_format": fmt,
        },
    }


def test_image_build_croissant(handler: ImageHandler) -> None:
    metas = [_img_meta("a.jpg"), _img_meta("b.jpg")]
    filesets, record_sets = handler.build_croissant(metas, ["file_0", "file_1"])

    assert len(filesets) == 1
    assert len(record_sets) == 1
    assert record_sets[0].name == "images"
    assert "**/*.jpg" in filesets[0].includes


def test_image_build_croissant_multiband(handler: ImageHandler) -> None:
    metas = [
        _img_meta(f"tile_{i}.tif", fmt="TIFF", mime="image/tiff", bands=12)
        for i in range(3)
    ]
    _, record_sets = handler.build_croissant(metas, [f"file_{i}" for i in range(3)])

    assert "band" in record_sets[0].description


def test_a_bigtiff_is_claimed_and_described(
    handler: ImageHandler, tmp_path: Path
) -> None:
    """Regression test for #93, which made claims() check magic bytes.

    BigTIFF is classic TIFF with a 64-bit offset field, which any writer
    switches to at 4 GiB — where whole-slide imaging, EM volumes and geospatial
    rasters all live. It differs in one byte, 0x2b against 0x2a, so a magic
    check listing only 0x2a would reject a valid .tiff. The version byte is
    asserted before the handler is asked, since a fixture written as classic
    TIFF would let the claim pass for the wrong reason.
    """
    path = tmp_path / "tissue.tiff"
    tifffile.imwrite(
        str(path),
        np.zeros((16, 16), np.uint16),
        photometric="minisblack",
        bigtiff=True,
    )
    assert path.read_bytes()[:4] == b"II+\x00"

    source = make_source(path)
    assert handler.claims(source) is True

    props = handler.extract(source)["image_properties"]

    assert (props["width"], props["height"]) == (16, 16)
    # BigTIFF is a TIFF variant; a second token here would reach the format
    # breakdown the record-set description reports.
    assert props["image_format"] == "TIFF"
