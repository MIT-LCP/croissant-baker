"""Read what a vendor whole-slide TIFF states about the slide it holds.

Pure: builds no Croissant, opens no file, and never decodes a pixel. Five
scanner vendors write their microscopy metadata into a TIFF container, each in
a private shape, and this module reads the shapes tifffile already identifies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: The vendors this module names. The order is tifffile's own series dispatch
#: order, so a file that trips two properties is attributed the same way here
#: as it is there.
APERIO = "aperio"
LEICA = "leica"
AKOYA = "akoya"
HAMAMATSU = "hamamatsu"
VENTANA = "ventana"

#: Vendor -> the ``TiffPage`` property tifffile recognises it by. Reusing
#: those properties rather than reimplementing them keeps one definition of
#: what an Aperio file is, in the library that tracks the vendors' output.
_VENDOR_FLAGS = (
    (APERIO, "is_svs"),
    (LEICA, "is_scn"),
    (AKOYA, "is_qpi"),
    (HAMAMATSU, "is_ndpi"),
    (VENTANA, "is_bif"),
)


@dataclass(frozen=True)
class SlideHeader:
    """What one whole-slide file states about itself.

    Attributes:
        vendor: The scanner vendor that wrote the file, None when no vendor
            signature is present and the file is a plain TIFF.
    """

    vendor: Optional[str] = None


def read(tif) -> SlideHeader:
    """The slide header of an open TIFF.

    Args:
        tif: An open :class:`tifffile.TiffFile`.
    """
    return SlideHeader(vendor=_vendor(tif.pages.first))


def _vendor(page) -> Optional[str]:
    """The vendor that signed ``page``, or None for an unsigned TIFF."""
    for vendor, flag in _VENDOR_FLAGS:
        if getattr(page, flag, False):
            return vendor
    return None
