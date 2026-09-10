"""What a vendor whole-slide TIFF says about itself, read from its header alone."""

from __future__ import annotations

import io

import pytest
import tifffile

from croissant_baker.handlers import wsi

from tests.helpers import tiff_bytes, wsi_bytes


def open_bytes(data: bytes) -> tifffile.TiffFile:
    """The open TIFF the handler would hand the reader."""
    return tifffile.TiffFile(io.BytesIO(data))


# --------------------------------------------------------------------------
# The synthetic slides, checked against tifffile's own vendor properties
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("vendor", "flag"),
    [
        ("aperio", "is_svs"),
        ("hamamatsu", "is_ndpi"),
        ("leica", "is_scn"),
        ("ventana", "is_bif"),
        ("akoya", "is_qpi"),
    ],
)
def test_each_synthetic_slide_trips_the_tifffile_property_it_is_recognised_by(
    vendor: str, flag: str
) -> None:
    """The reader asks tifffile which vendor wrote a file, so a fixture that
    does not trip the property proves nothing about the vendor it claims."""
    with open_bytes(wsi_bytes(vendor)) as tif:
        assert getattr(tif.pages.first, flag) is True


def read_bytes(data: bytes) -> wsi.SlideHeader:
    """The header the handler would get for a slide holding ``data``."""
    with open_bytes(data) as tif:
        return wsi.read(tif)


# --------------------------------------------------------------------------
# Which vendor wrote the file
# --------------------------------------------------------------------------


@pytest.mark.parametrize("vendor", ["aperio", "hamamatsu", "leica", "ventana", "akoya"])
def test_every_vendor_is_named_by_the_signal_it_writes(vendor: str) -> None:
    assert read_bytes(wsi_bytes(vendor)).vendor == vendor


def test_a_tiff_no_vendor_signed_has_no_vendor() -> None:
    """A plain TIFF renamed to a vendor extension is still described, because
    its tags say as much about it as any other TIFF's do."""
    assert read_bytes(tiff_bytes()).vendor is None
