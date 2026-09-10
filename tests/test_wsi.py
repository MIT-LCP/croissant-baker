"""What a vendor whole-slide TIFF says about itself, read from its header alone."""

from __future__ import annotations

import io

import pytest
import tifffile

from tests.helpers import wsi_bytes


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
