"""Read what a vendor whole-slide TIFF states about the slide it holds.

Pure: builds no Croissant, opens no file, and never decodes a pixel. Five
scanner vendors write their microscopy metadata into a TIFF container, each in
a private shape, and this module reads the shapes tifffile already identifies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

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

#: The pictures of the slide, rather than of the tissue, that a scanner files
#: alongside the pyramid. tifffile names the series it recognises after these,
#: and anything it could not place is left unnamed rather than guessed at.
ASSOCIATED_KINDS = ("label", "macro", "overview", "thumbnail")


@dataclass(frozen=True)
class SlideHeader:
    """What one whole-slide file states about itself.

    Attributes:
        vendor: The scanner vendor that wrote the file, None when no vendor
            signature is present and the file is a plain TIFF.
        width: Base level width in pixels.
        height: Base level height in pixels.
        level_count: Pyramid levels, the base level included. One for a slide
            stored at a single resolution.
        level_dimensions: ``(width, height)`` per level, largest first.
        tile_width: Tile width of the base level, None for a stripped image.
        tile_height: Tile height of the base level, None for a stripped image.
        compression: The base level's compression, lowercased.
        associated_images: The kinds of associated image the file carries,
            drawn from :data:`ASSOCIATED_KINDS`, empty when it carries none.
    """

    vendor: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    level_count: int = 0
    level_dimensions: Tuple[Tuple[int, int], ...] = ()
    tile_width: Optional[int] = None
    tile_height: Optional[int] = None
    compression: Optional[str] = None
    associated_images: Tuple[str, ...] = ()


def read(tif) -> SlideHeader:
    """The slide header of an open TIFF.

    Args:
        tif: An open :class:`tifffile.TiffFile`.
    """
    page = tif.pages.first
    series = _series(tif)
    levels = _levels(tif, series)
    return SlideHeader(
        vendor=_vendor(page),
        width=levels[0][0] if levels else None,
        height=levels[0][1] if levels else None,
        level_count=len(levels),
        level_dimensions=levels,
        # Zero is how a stripped page reports a tile size it does not have.
        tile_width=getattr(page, "tilewidth", 0) or None,
        tile_height=getattr(page, "tilelength", 0) or None,
        compression=_compression(page),
        associated_images=_associated_images(series),
    )


def _vendor(page) -> Optional[str]:
    """The vendor that signed ``page``, or None for an unsigned TIFF."""
    for vendor, flag in _VENDOR_FLAGS:
        if getattr(page, flag, False):
            return vendor
    return None


def _compression(page) -> Optional[str]:
    """The base page's compression, by the name the TIFF specification gives it."""
    compression = getattr(page, "compression", None)
    if compression is None:
        return None
    return str(getattr(compression, "name", compression)).lower()


def _series(tif) -> tuple:
    """tifffile's own reading of the file's structure, or nothing.

    Building a series parses the vendor's XML, so a malformed document raises
    here rather than returning nothing. The file is still described from its
    tags in that case.
    """
    try:
        return tuple(tif.series)
    except Exception as exc:
        logger.debug("tifffile could not build a series for this slide: %s", exc)
        return ()


def _associated_images(series: tuple) -> Tuple[str, ...]:
    """The associated image kinds tifffile named, in a stable order.

    Only the vendor series builders name a series, so a file whose signature
    tifffile did not recognise reports none rather than a guess.
    """
    named = {s.name.lower() for s in series[1:] if s.name.lower() in ASSOCIATED_KINDS}
    return tuple(kind for kind in ASSOCIATED_KINDS if kind in named)


def _levels(tif, series: tuple) -> Tuple[Tuple[int, int], ...]:
    """The pyramid, largest level first.

    tifffile builds a vendor-aware series for every format here, and its
    answer already leaves out the label, macro and thumbnail pages, so it is
    preferred. It falls back to a plain series for a file whose vendor
    signature is missing or unreadable, and that series reports one level for
    a slide that has several, so the page walk wins whenever it finds more.
    """
    by_page = _levels_from_pages(tif)
    by_series = _levels_from_series(series)
    return by_series if len(by_series) >= len(by_page) else by_page


def _levels_from_series(series: tuple) -> Tuple[Tuple[int, int], ...]:
    """The levels of the first series, through tifffile's vendor knowledge."""
    if not series:
        return ()
    return tuple(
        (int(level.keyframe.imagewidth), int(level.keyframe.imagelength))
        for level in series[0].levels
    )


def _levels_from_pages(tif) -> Tuple[Tuple[int, int], ...]:
    """The levels a page walk finds: tiled, same layout, strictly smaller.

    Label, macro and thumbnail pages are stored in strips rather than tiles in
    every vendor's output, which is what keeps them out of this.
    """
    page = tif.pages.first
    if page is None:
        return ()
    dimensions = [(int(page.imagewidth), int(page.imagelength))]
    if not page.is_tiled:
        return tuple(dimensions)

    layout = (page.photometric, page.samplesperpixel)
    for index in range(1, len(tif.pages)):
        other = tif.pages[index]
        if not getattr(other, "is_tiled", False):
            continue
        # A TiffFrame carries no tags of its own, so it reports neither, and
        # a file of frames is uniform rather than pyramidal anyway.
        if (
            getattr(other, "photometric", None),
            getattr(other, "samplesperpixel", None),
        ) != layout:
            continue
        width, height = int(other.imagewidth), int(other.imagelength)
        if width >= dimensions[-1][0] or height >= dimensions[-1][1]:
            continue
        dimensions.append((width, height))
    return tuple(dimensions)
