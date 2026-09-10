"""What the whole-slide handler claims, reads and refuses."""

from __future__ import annotations

from pathlib import Path

import pytest

from croissant_baker.handlers.wsi_handler import WSIHandler
from croissant_baker.sources import make_source

from tests.helpers import APERIO_SVS, WRAPPER_SUFFIXES, wsi_bytes, write_wrapped

#: The vendor whose scanner writes each extension. A slide has to be the
#: format its name claims: tifffile reads a little-endian classic TIFF named
#: ``.ndpi`` with the 64-bit offsets a real NDPI has, and finds no page in a
#: file that does not have them.
VENDOR_EXTENSIONS = {
    ".svs": "aperio",
    ".ndpi": "hamamatsu",
    ".scn": "leica",
    ".bif": "ventana",
    ".qptiff": "akoya",
}


@pytest.fixture
def handler() -> WSIHandler:
    return WSIHandler()


# --------------------------------------------------------------------------
# Claiming
# --------------------------------------------------------------------------


#: Classic TIFF and BigTIFF, in both byte orders. Every vendor here writes a
#: TIFF container, and three of the five reach BigTIFF on a routine slide.
TIFF_HEADERS = {
    "classic little-endian": b"II*\x00",
    "classic big-endian": b"MM\x00*",
    "bigtiff little-endian": b"II+\x00",
    "bigtiff big-endian": b"MM\x00+",
}


@pytest.mark.parametrize("extension", WSIHandler.EXTENSIONS)
@pytest.mark.parametrize("header", list(TIFF_HEADERS.values()), ids=list(TIFF_HEADERS))
def test_every_declared_extension_is_claimed_at_every_tiff_magic(
    handler: WSIHandler, dataset: Path, extension: str, header: bytes
) -> None:
    path = write_wrapped(dataset, f"slide{extension}", header)

    assert handler.claims(make_source(path)) is True


@pytest.mark.parametrize("extension", WSIHandler.EXTENSIONS)
def test_a_vendor_extension_over_bytes_that_are_not_a_tiff_is_not_claimed(
    handler: WSIHandler, dataset: Path, extension: str
) -> None:
    """The extension is a filename, and a rename is free. Claiming this file
    would promise an extraction that then fails the whole batch."""
    path = write_wrapped(dataset, f"slide{extension}", b"<!DOCTYPE html><html>")

    assert handler.claims(make_source(path)) is False


@pytest.mark.parametrize("extension", WSIHandler.EXTENSIONS)
def test_a_shouted_extension_is_the_same_extension(
    handler: WSIHandler, dataset: Path, extension: str
) -> None:
    path = write_wrapped(dataset, f"SLIDE{extension.upper()}", b"II*\x00")

    assert handler.claims(make_source(path)) is True


@pytest.mark.parametrize("suffix", WRAPPER_SUFFIXES)
def test_a_compressed_slide_is_claimed_through_its_wrapper(
    handler: WSIHandler, dataset: Path, suffix: str
) -> None:
    """A whole-slide file is large, so it arrives compressed more often than
    most. The handler is given the logical name and the decompressed bytes."""
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS, suffix)

    assert handler.claims(make_source(path, Path("slide.svs"))) is True


def test_a_plain_tiff_is_left_to_the_image_handler(
    handler: WSIHandler, dataset: Path
) -> None:
    """``.tif`` is not a vendor extension, and nothing in the bytes of a
    pyramidal TIFF says the file holds a slide rather than a satellite scene."""
    path = write_wrapped(dataset, "scene.tif", APERIO_SVS)

    assert handler.claims(make_source(path)) is False


# --------------------------------------------------------------------------
# Reading one file
# --------------------------------------------------------------------------


def test_a_slide_is_described_by_the_keys_the_generator_needs(
    handler: WSIHandler, dataset: Path
) -> None:
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS)

    meta = handler.extract(make_source(path, Path("slide.svs")))

    assert meta["file_name"] == "slide.svs"
    assert meta["file_size"] == len(APERIO_SVS)
    assert len(meta["sha256"]) == 64
    assert (meta["width"], meta["height"]) == (256, 256)


@pytest.mark.parametrize(
    ("extension", "vendor"), list(VENDOR_EXTENSIONS.items()), ids=VENDOR_EXTENSIONS
)
def test_every_vendor_extension_is_a_tiff_container(
    handler: WSIHandler, dataset: Path, extension: str, vendor: str
) -> None:
    """No vendor media type is registered, and a made-up one would be a
    statement no reader can act on."""
    path = write_wrapped(dataset, f"slide{extension}", wsi_bytes(vendor))

    meta = handler.extract(make_source(path, Path(f"slide{extension}")))

    assert meta["encoding_format"] == "image/tiff"


def test_the_slide_header_reaches_the_metadata(
    handler: WSIHandler, dataset: Path
) -> None:
    path = write_wrapped(dataset, "slide.svs", APERIO_SVS)

    meta = handler.extract(make_source(path, Path("slide.svs")))

    assert meta["slide"].vendor == "aperio"
    assert meta["slide"].level_count == 2


def test_bytes_that_are_not_a_tiff_raise_a_value_error_naming_the_file(
    handler: WSIHandler, dataset: Path
) -> None:
    """The message becomes the reason detail a user reads in ``--report``."""
    path = write_wrapped(dataset, "slide.svs", b"\x00\xff not a slide \xfe\x00")

    with pytest.raises(ValueError) as caught:
        handler.extract(make_source(path, Path("nested/slide.svs")))

    assert "nested/slide.svs" in str(caught.value)


def test_a_missing_slide_raises_file_not_found(
    handler: WSIHandler, dataset: Path
) -> None:
    source = make_source(dataset / "gone" / "slide.svs", Path("slide.svs"))

    with pytest.raises(FileNotFoundError):
        handler.extract(source)


def test_a_refused_vendor_document_is_logged_against_the_file(
    handler: WSIHandler, dataset: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The scan report clears the reason once a file is described, so a
    partial refusal on a described file has nowhere else to be seen."""
    from tests.helpers import wsi_bytes
    from tests.test_wsi import SCN_MALFORMED

    path = write_wrapped(dataset, "slide.scn", wsi_bytes("leica", xml=SCN_MALFORMED))

    with caplog.at_level("WARNING", logger="croissant_baker.handlers.wsi_handler"):
        meta = handler.extract(make_source(path, Path("slide.scn")))

    assert meta["slide"].refusal
    assert [r for r in caplog.records if "slide.scn" in r.message]
